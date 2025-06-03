import logging
import threading
from datetime import datetime
from queue import Queue, Empty
from pymongo import MongoClient, ASCENDING, DESCENDING
from opentelemetry.trace import get_current_span

# === Mongo Config ===
MONGO_URI = "mongodb://localhost:27017"
DB_NAME = "Logs"
COLLECTION_NAME = "Logs"

mongo_client = MongoClient(MONGO_URI)
db = mongo_client[DB_NAME]

def ensure_collection():
    if COLLECTION_NAME not in db.list_collection_names():
        db.create_collection(
            COLLECTION_NAME,
            timeseries={
                "timeField": "Timestamp",
                "metaField": "TenantId",
                "granularity": "minutes"
            }
        )
        db[COLLECTION_NAME].create_index(
            [("TenantId", ASCENDING), ("Timestamp", DESCENDING)],
            name="Tenant_Timestamp_Index"
        )

ensure_collection()

def get_trace_context():
    span = get_current_span()
    ctx = span.get_span_context() if span else None
    trace_id = f"{ctx.trace_id:032x}" if ctx and ctx.trace_id else None
    span_id = f"{ctx.span_id:016x}" if ctx and ctx.span_id else None
    tenant_id = span.attributes.get("tenant_id", "Misc") if span else "Misc"
    return tenant_id, trace_id, span_id


class MongoBatchLogger:
    def __init__(self, batch_size=50, flush_interval_sec=2.0):
        self.batch_size = batch_size
        self.flush_interval_sec = flush_interval_sec
        self.collection = db[COLLECTION_NAME]
        self.queue = Queue()
        self._stop_event = threading.Event()
        self.worker_thread = threading.Thread(target=self._background_worker, daemon=True)
        self.worker_thread.start()

    def enqueue(self, record: logging.LogRecord):
        tenant_id, trace_id, span_id = get_trace_context()
        doc = {
            "Timestamp": datetime.now(),
            "Level": record.levelname,
            "Message": record.getMessage(),
            "TenantId": tenant_id,
            "TraceId": trace_id,
            "SpanId": span_id
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
        # Singleton pattern for the batch logger
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
        tenant_id, trace_id, span_id = get_trace_context()
        record.TenantId = tenant_id
        record.TraceId = trace_id
        record.SpanId = span_id
        return True
