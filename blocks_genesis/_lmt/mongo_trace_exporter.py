# mongo_trace_exporter.py

from datetime import datetime, timezone
import logging
import re
import threading
from queue import Queue, Empty, Full
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import CollectionInvalid, OperationFailure
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from blocks_genesis._core.secret_loader import get_blocks_secret
from blocks_genesis._lmt.mongo_log_exporter import RETENTION_SECONDS
import time

_logger = logging.getLogger(__name__)

_COLLECTION_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
_QUEUE_FULL_LOG_INTERVAL_SECONDS = 60
_NAMESPACE_EXISTS = 48
# The time-series options blocks-genesis-net uses for tenant trace collections (LmtConfiguration.CreateCollectionForTrace).
_TRACE_TIME_SERIES = {"timeField": "Timestamp", "metaField": "TraceId", "granularity": "minutes"}


def _trace_collection(tenant_id) -> str:
    """The tenant id names the trace collection only if it is a plain id; anything else goes to miscellaneous."""
    if isinstance(tenant_id, str) and _COLLECTION_NAME.fullmatch(tenant_id):
        return tenant_id
    return "miscellaneous"


class MongoDBTraceExporter(SpanExporter):
    _last_queue_full_log = float("-inf")

    def __init__(self, flush_interval: float = 3.0, batch_size: int = 1000, queue_size: int = 10000):
        self._blocks_secret = get_blocks_secret()
        self._service_name = self._blocks_secret.ServiceName
        self._client = MongoClient(self._blocks_secret.TraceConnectionString)
        self._db = self._client[self._blocks_secret.TraceDatabaseName]
        
        self._queue = Queue(maxsize=queue_size)
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._ensured_collections = set()
        
        self._stop_event = threading.Event()
        self._worker_thread = threading.Thread(target=self._run, daemon=True)
        self._worker_thread.start()

    def _extract_baggage_from_span(self, span) -> dict:
        baggage_items = {}

        for key in span.attributes:
            if key.startswith("baggage."):
                baggage_items[key[8:]] = span.attributes[key]

        return baggage_items

    def export(self, spans):
        try:
            self._ensure_worker()
            for span in spans:
                baggage_items = self._extract_baggage_from_span(span)
                tenant_id = _trace_collection(baggage_items.get("TenantId"))

                doc = self._build_document(span, baggage_items, tenant_id)
                self._queue.put_nowait((tenant_id, doc))
            return SpanExportResult.SUCCESS
        except Full:
            now = time.monotonic()
            if now - self._last_queue_full_log >= _QUEUE_FULL_LOG_INTERVAL_SECONDS:
                self._last_queue_full_log = now
                _logger.warning("Trace export queue is full; spans are being dropped")
            return SpanExportResult.FAILURE
        except Exception as ex:
            _logger.warning("Trace export failed (%s)", type(ex).__name__)
            return SpanExportResult.FAILURE

    def _ensure_worker(self):
        # The SDK never calls export concurrently on one exporter, so no lock is needed.
        if self._stop_event.is_set() or self._worker_thread.is_alive():
            return
        _logger.warning("Trace export thread was not running; restarted")
        self._worker_thread = threading.Thread(target=self._run, daemon=True)
        self._worker_thread.start()

    def _build_document(self, span, baggage_items, tenant_id: str):
        # Build ParentId in W3C trace context format
        if span.parent:
            parent_span_id = format(span.parent.span_id, "016x")
            parent_id = f"00-{format(span.context.trace_id, '032x')}-{parent_span_id}-01"
        else:
            parent_span_id = "0000000000000000"
            parent_id = ""
        
        return {
            "Timestamp": datetime.fromtimestamp(span.end_time / 1_000_000_000, tz=timezone.utc),
            "TraceId": format(span.context.trace_id, "032x"),
            "SpanId": format(span.context.span_id, "016x"),
            "ParentSpanId": parent_span_id,
            "ParentId": parent_id,
            "OperationName": span.name,
            "Kind": str(span.kind),
            "StartTime": datetime.fromtimestamp(span.start_time / 1_000_000_000, tz=timezone.utc),
            "EndTime": datetime.fromtimestamp(span.end_time / 1_000_000_000, tz=timezone.utc),
            "Duration": (span.end_time - span.start_time) / 1e6,
            "Attributes": {k: v for k, v in span.attributes.items() if not k.startswith("baggage.")},
            "Baggage": baggage_items,
            "Status": str(span.status.status_code),
            "StatusDescription": span.status.description,
            "ServiceName": self._service_name,
            "TenantId": tenant_id,
        }

    def _run(self):
        while not self._stop_event.is_set():
            try:
                self._export_one_batch()
            except Exception as ex:  # the thread must never die; that batch is lost
                _logger.error("Trace export loop error (%s); continuing", type(ex).__name__)
        try:
            self._flush_remaining()
        except Exception as ex:
            _logger.error("Final trace flush failed (%s)", type(ex).__name__)

    def _export_one_batch(self):
        buffer_by_tenant = {}
        try:
            tenant_id, doc = self._queue.get(timeout=self._flush_interval)
            buffer_by_tenant.setdefault(tenant_id, []).append(doc)

            while sum(len(docs) for docs in buffer_by_tenant.values()) < self._batch_size:
                tenant_id, doc = self._queue.get_nowait()
                buffer_by_tenant.setdefault(tenant_id, []).append(doc)
        except Empty:
            pass

        if buffer_by_tenant:
            self._flush_to_mongo(buffer_by_tenant)

    def _flush_to_mongo(self, batches):
        for tenant_id, docs in batches.items():
            try:
                self._ensure_collection(tenant_id)
                self._db[tenant_id].insert_many(docs, ordered=False)
            except Exception as ex:
                # Type name only: pymongo messages can carry hosts. The collection name is pattern-checked.
                _logger.warning("Dropped %d trace documents for collection '%s' (%s)", len(docs), tenant_id, type(ex).__name__)

    def _ensure_collection(self, collection_name: str):
        """Creates a missing trace collection as time-series; gives a plain one a TTL index. Both get the TraceId index."""
        if collection_name in self._ensured_collections:
            return
        self._ensured_collections.add(collection_name)  # one attempt per collection per process
        try:
            info = self._collection_info(collection_name)
            if info is None:
                info = self._create_time_series_collection(collection_name)
            collection = self._db[collection_name]
            if info.get("type") != "timeseries":
                # A time-series collection expires on its own and refuses a TTL index.
                collection.create_index([("Timestamp", ASCENDING)], name="Timestamp_TTL",
                                        expireAfterSeconds=RETENTION_SECONDS)
            collection.create_index([("TraceId", ASCENDING), ("Timestamp", DESCENDING)],
                                    name=f"{collection_name}_Index")
        except Exception as ex:
            _logger.warning("Could not prepare trace collection '%s' (%s)", collection_name, type(ex).__name__)

    def _collection_info(self, collection_name: str):
        return next(iter(self._db.list_collections(filter={"name": collection_name})), None)

    def _create_time_series_collection(self, collection_name: str) -> dict:
        try:
            self._db.create_collection(collection_name, timeseries=_TRACE_TIME_SERIES,
                                       expireAfterSeconds=RETENTION_SECONDS)
            return {"type": "timeseries"}
        except (CollectionInvalid, OperationFailure) as ex:
            if isinstance(ex, OperationFailure) and ex.code != _NAMESPACE_EXISTS:
                raise
            # Someone else created it first; index it according to what they made.
            return self._collection_info(collection_name) or {}

    def _flush_remaining(self):
        buffer_by_tenant = {}
        while not self._queue.empty():
            try:
                tenant_id, doc = self._queue.get_nowait()
                buffer_by_tenant.setdefault(tenant_id, []).append(doc)
            except Empty:
                break

        if buffer_by_tenant:
            self._flush_to_mongo(buffer_by_tenant)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        deadline = time.time() + (timeout_millis / 1000.0)
        while not self._queue.empty() and time.time() < deadline:
            time.sleep(0.1)
        self._flush_remaining()
        return True

    def shutdown(self):
        self._stop_event.set()
        self._worker_thread.join(timeout=self._flush_interval + 2)
        self._flush_remaining()
        self._client.close()
