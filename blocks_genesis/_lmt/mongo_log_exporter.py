import logging
import threading
from datetime import datetime
from queue import Queue, Empty
from pymongo import MongoClient, ASCENDING, DESCENDING

from blocks_genesis._auth.blocks_context import BlocksContextManager
from blocks_genesis._core.secret_loader import get_blocks_secret
from blocks_genesis._lmt.activity import Activity


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
        self.queue = Queue()
        self._stop_event = threading.Event()
        self.worker_thread = threading.Thread(target=self._background_worker, daemon=True)
        self.worker_thread.start()

    def _create_collection_for_logs(self, db, collection_name: str):
        try:
            self._create_collection_if_not_exists(db, collection_name)
            self._create_index_if_needed(db, collection_name)
        except Exception as ex:
            # Keep startup resilient; logger handler should not crash app bootstrap.
            print(ex)

    def _create_collection_if_not_exists(self, db, collection_name: str):
        try:
            collection_exists = self._collection_exists(db, collection_name)
            if not collection_exists:
                db.create_collection(
                    collection_name,
                    timeseries={
                        "timeField": "Timestamp",
                        "metaField": "TenantId",
                        "granularity": "minutes",
                    },
                    expireAfterSeconds=90 * 24 * 60 * 60,
                )
                return

            if not self._is_time_series_collection(db, collection_name):
                db.drop_collection(collection_name)
                db.create_collection(
                    collection_name,
                    timeseries={
                        "timeField": "Timestamp",
                        "metaField": "TenantId",
                        "granularity": "minutes",
                    },
                    expireAfterSeconds=90 * 24 * 60 * 60,
                )
        except Exception:
            raise

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
            "Timestamp": datetime.now(),
            "Level": record.levelname,
            "Message": record.getMessage(),
            "TenantId": record.TenantId or "miscellaneous",
            "LoggerName": record.name,
            "TraceId": record.TraceId or Activity.get_trace_id(),
            "SpanId": record.SpanId or Activity.get_span_id(),
        }
        self.queue.put(doc)

    def _background_worker(self):
        batch = []
        while not self._stop_event.is_set():
            try:
                doc = self.queue.get(timeout=self.flush_interval_sec)
                batch.append(doc)
            except Empty:
                pass

            if batch and (len(batch) >= self.batch_size or self._stop_event.is_set()):
                try:
                    self.collection.insert_many(batch)
                except Exception as e:
                    print(f"[MongoBatchLogger] Insert error: {e}")
                batch.clear()

        # flush remaining logs on shutdown
        if batch:
            try:
                self.collection.insert_many(batch)
            except Exception as e:
                print(f"[MongoBatchLogger] Insert error on shutdown: {e}")

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
