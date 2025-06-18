import logging
import threading
from datetime import datetime
from queue import Queue, Empty
from pymongo import MongoClient, ASCENDING, DESCENDING

from blocks_genesis.auth.blocks_context import BlocksContextManager
from blocks_genesis.core.secret_loader import get_blocks_secret
from blocks_genesis.lmt.activity import Activity


class MongoBatchLogger:
    def __init__(self, batch_size=50, flush_interval_sec=2.0):
        self.batch_size = batch_size
        self.flush_interval_sec = flush_interval_sec
        self.blocks_secret = get_blocks_secret()
        # Lazy initialization of MongoDB connection
        mongo_client = MongoClient(self.blocks_secret.LogConnectionString)
        db = mongo_client[self.blocks_secret.LogDatabaseName]

        if self.blocks_secret.ServiceName not in db.list_collection_names():
            db.create_collection(
                self.blocks_secret.ServiceName,
                timeseries={
                    "timeField": "Timestamp",
                    "metaField": "TenantId",
                    "granularity": "minutes"
                }
            )
            db[self.blocks_secret.ServiceName].create_index(
                [("TenantId", ASCENDING), ("Timestamp", DESCENDING)],
                name="Tenant_Timestamp_Index"
            )

        self.collection = db[self.blocks_secret.ServiceName]
        self.queue = Queue()
        self._stop_event = threading.Event()
        self.worker_thread = threading.Thread(target=self._background_worker, daemon=True)
        self.worker_thread.start()

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
        print(f"[TraceContextFilter] TenantId: {record.TenantId}")
        record.TraceId = Activity.get_trace_id()
        record.SpanId = Activity.get_span_id()
        return True
