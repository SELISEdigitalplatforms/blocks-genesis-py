import logging
import sys
import threading
import time
from datetime import datetime, timezone
from queue import Queue, Empty, Full
from pymongo import MongoClient, ASCENDING, DESCENDING, errors

from blocks_genesis._auth.blocks_context import BlocksContextManager
from blocks_genesis._core.secret_loader import get_blocks_secret
from blocks_genesis._lmt.activity import Activity

RETENTION_SECONDS = 90 * 24 * 60 * 60
LOG_QUEUE_MAX_SIZE = 10_000
_NAMESPACE_EXISTS = 48


class MongoBatchLogger:
    def __init__(self, batch_size=50, flush_interval_sec=2.0):
        self.batch_size = batch_size
        self.flush_interval_sec = flush_interval_sec
        self.blocks_secret = get_blocks_secret()
        # Lazy initialization of MongoDB connection
        mongo_client = MongoClient(self.blocks_secret.LogConnectionString)
        db = mongo_client[self.blocks_secret.LogDatabaseName]
        collection_name = self.blocks_secret.ServiceName

        self._create_collection_for_logs(db, collection_name)

        self.collection = db[collection_name]
        self.queue = Queue(maxsize=LOG_QUEUE_MAX_SIZE)
        self._dropped = 0
        self._dropped_lock = threading.Lock()
        self._last_drop_report = float("-inf")
        self._stop_event = threading.Event()
        self.worker_thread = threading.Thread(target=self._background_worker, daemon=True)
        self.worker_thread.start()

    def _create_collection_for_logs(self, db, collection_name: str):
        try:
            self._create_collection_if_not_exists(db, collection_name)
            self._create_index_if_needed(db, collection_name)
        except Exception as ex:
            # Keep startup resilient; logger handler should not crash app bootstrap.
            # Type name only: the message can carry the connection string host.
            sys.stderr.write(f"[MongoBatchLogger] log collection setup failed ({type(ex).__name__})\n")

    def _create_collection_if_not_exists(self, db, collection_name: str):
        collection_exists = self._collection_exists(db, collection_name)
        if not collection_exists:
            try:
                db.create_collection(
                    collection_name,
                    timeseries={
                        "timeField": "Timestamp",
                        "metaField": "TenantId",
                        "granularity": "minutes",
                    },
                    expireAfterSeconds=RETENTION_SECONDS,
                )
                return
            except (errors.CollectionInvalid, errors.OperationFailure) as ex:
                if isinstance(ex, errors.OperationFailure) and ex.code != _NAMESPACE_EXISTS:
                    raise
                # Someone else created it first; treat it by what they made.

        if not self._is_time_series_collection(db, collection_name):
            # Never drop it: that would delete its log history. A TTL index keeps its documents expiring.
            try:
                db[collection_name].create_index(
                    [("Timestamp", ASCENDING)], name="Timestamp_TTL", expireAfterSeconds=RETENTION_SECONDS
                )
                kept = "kept with a TTL index"
            except Exception as ex:
                # Type name only, as above. Not raised, so the tenant index is still created.
                kept = f"kept without a TTL index ({type(ex).__name__})"
            sys.stderr.write(
                f"[MongoBatchLogger] log collection is a plain collection, not time-series: "
                f"{kept}; migrate it to time-series\n"
            )

    def _collection_exists(self, db, collection_name: str) -> bool:
        collections = db.list_collection_names(filter={"name": collection_name})
        return bool(collections)

    def _is_time_series_collection(self, db, collection_name: str) -> bool:
        collection_info = next(db.list_collections(filter={"name": collection_name}), None)
        return bool(collection_info and collection_info.get("type") == "timeseries")

    def _create_index_if_needed(self, db, collection_name: str):
        index_name = f"{collection_name}_Index"
        collection = db[collection_name]
        expected_key = [("TenantId", 1), ("Timestamp", -1)]

        try:
            existing_indexes = list(collection.list_indexes())

            index_with_same_name_exists = any(
                idx.get("name") == index_name for idx in existing_indexes
            )
            index_with_same_keys_exists = any(
                idx.get("name") != "_id_" and list(idx.get("key", {}).items()) == expected_key
                for idx in existing_indexes
            )

            if not index_with_same_name_exists and not index_with_same_keys_exists:
                collection.create_index(
                    [("TenantId", ASCENDING), ("Timestamp", DESCENDING)],
                    name=index_name,
                    partialFilterExpression={"TenantId": {"$exists": True}},
                )
        except Exception as ex:
            if "Index already exists with a different name" in str(ex):
                return
            raise

    def enqueue(self, record: logging.LogRecord):
        doc = {
            "Timestamp": datetime.now(timezone.utc),
            "Level": record.levelname,
            "Message": record.getMessage(),
            "TenantId": record.TenantId or "miscellaneous",
            "LoggerName": record.name,
            "TraceId": record.TraceId or Activity.get_trace_id(),
            "SpanId": record.SpanId or Activity.get_span_id(),
        }
        try:
            self.queue.put_nowait(doc)
        except Full:
            # Drop the newest record rather than block the thread that logged it.
            with self._dropped_lock:
                self._dropped += 1

    def _background_worker(self):
        # A batch is written when full, when its oldest record has waited flush_interval_sec, or on stop.
        batch, started = [], 0.0
        while not self._stop_event.is_set():
            timeout = self.flush_interval_sec if not batch else max(0.0, started + self.flush_interval_sec - time.monotonic())
            try:
                doc = self.queue.get(timeout=timeout)
                if not batch:
                    started = time.monotonic()
                batch.append(doc)
            except Empty:
                pass

            if batch and (len(batch) >= self.batch_size
                          or time.monotonic() - started >= self.flush_interval_sec
                          or self._stop_event.is_set()):
                self._insert_batch(batch)
                batch = []
            self._report_dropped()

        # flush remaining logs on shutdown
        if batch:
            self._insert_batch(batch)
        self._report_dropped(force=True)

    # Status goes to stderr, never through logging: this is the logging handler, so a logger would recurse.
    def _insert_batch(self, batch):
        for _ in range(2):
            try:
                self.collection.insert_many(batch)
                return
            except errors.BulkWriteError:
                break  # some records were written; a retry would duplicate them
            except Exception:
                pass
        sys.stderr.write(f"[MongoBatchLogger] dropped {len(batch)} log records after a failed insert\n")

    def _report_dropped(self, force=False):
        now = time.monotonic()
        if not force and now - self._last_drop_report < self.flush_interval_sec:
            return
        self._last_drop_report = now
        with self._dropped_lock:
            dropped, self._dropped = self._dropped, 0
        if dropped:
            sys.stderr.write(f"[MongoBatchLogger] dropped {dropped} log records: queue full\n")

    def stop(self):
        self._stop_event.set()
        self.worker_thread.join()


class MongoHandler(logging.Handler):
    _mongo_logger = None

    def __init__(self, batch_size=50, flush_interval_sec=2.0):
        super().__init__()
        if not MongoHandler._mongo_logger:
            MongoHandler._mongo_logger = MongoBatchLogger(batch_size, flush_interval_sec)
        self.mongo_logger = MongoHandler._mongo_logger

    def emit(self, record: logging.LogRecord):
        try:
            self.mongo_logger.enqueue(record)
        except Exception:
            self.handleError(record)


class TraceContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        """Add trace context to log records."""
        record.TenantId = BlocksContextManager.get_context().tenant_id if BlocksContextManager.get_context() else "miscellaneous"
        record.TraceId = Activity.get_trace_id()
        record.SpanId = Activity.get_span_id()
        return True
