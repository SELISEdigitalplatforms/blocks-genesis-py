# mongo_trace_exporter.py

from datetime import datetime
import threading
from queue import Queue, Empty
from pymongo import MongoClient, errors
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from blocks_genesis.core.secret_loader import get_blocks_secret
import time


class MongoDBTraceExporter(SpanExporter):
    def __init__(self, flush_interval: float = 3.0, batch_size: int = 1000, queue_size: int = 10000):
        self._blocks_secret = get_blocks_secret()
        self._service_name = self._blocks_secret.ServiceName
        self._client = MongoClient(self._blocks_secret.TraceConnectionString)
        self._db = self._client[self._blocks_secret.TraceDatabaseName]
        
        self._queue = Queue(maxsize=queue_size)
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        
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
            for span in spans:
                baggage_items = self._extract_baggage_from_span(span)
                tenant_id = baggage_items.get("TenantId") or "miscellaneous"

                doc = self._build_document(span, baggage_items, tenant_id)
                self._queue.put_nowait((tenant_id, doc))
            return SpanExportResult.SUCCESS
        except Exception as ex:
            print(f"[MongoExporter] Export failed: {ex}")
            return SpanExportResult.FAILURE

    def _build_document(self, span, baggage_items, tenant_id: str):
        # Build ParentId in W3C trace context format
        if span.parent:
            parent_span_id = format(span.parent.span_id, "016x")
            parent_id = f"00-{format(span.context.trace_id, '032x')}-{parent_span_id}-01"
        else:
            parent_span_id = "0000000000000000"
            parent_id = ""
        
        return {
            "Timestamp": datetime.fromtimestamp(span.end_time / 1_000_000_000),
            "TraceId": format(span.context.trace_id, "032x"),
            "SpanId": format(span.context.span_id, "016x"),
            "ParentSpanId": parent_span_id,
            "ParentId": parent_id,
            "OperationName": span.name,
            "Kind": str(span.kind),
            "StartTime": datetime.fromtimestamp(span.start_time / 1_000_000_000),
            "EndTime": datetime.fromtimestamp(span.end_time / 1_000_000_000),
            "Duration": (span.end_time - span.start_time) / 1e6,
            "Attributes": {k: v for k, v in span.attributes.items() if not k.startswith("baggage.")},
            "Baggage": baggage_items,
            "Status": str(span.status.status_code),
            "StatusDescription": span.status.description,
            "ServiceName": self._service_name,
            "TenantId": tenant_id,
        }

    def _run(self):
        buffer_by_tenant = {}

        while not self._stop_event.is_set():
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
                buffer_by_tenant.clear()

        self._flush_remaining()

    def _flush_to_mongo(self, batches):
        for tenant_id, docs in batches.items():
            try:
                self._db[tenant_id].insert_many(docs, ordered=False)
            except errors.PyMongoError as ex:
                print(f"[MongoExporter] Failed to insert docs for tenant '{tenant_id}': {ex}")

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
