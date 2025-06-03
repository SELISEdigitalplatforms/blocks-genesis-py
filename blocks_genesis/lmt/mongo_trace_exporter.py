# mongo_trace_exporter.py

import threading
from queue import Queue, Empty
from pymongo import MongoClient
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult


class MongoDBTraceExporter(SpanExporter):
    def __init__(
        self,
        service_name: str,
        mongo_uri: str,
        database_name: str,
        flush_interval: float = 3.0,
        batch_size: int = 1000
    ):
        self._service_name = service_name
        self._client = MongoClient(mongo_uri)
        self._db = self._client[database_name]
        self._queue = Queue()
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._stop_event = threading.Event()
        self._worker_thread = threading.Thread(target=self._run, daemon=True)
        self._worker_thread.start()

    def export(self, spans):
        # Do not block request processing; enqueue quickly
        for span in spans:
            tenant_id = span.attributes.get("TenantId", "default")
            doc = self._build_document(span, tenant_id)
            self._queue.put((tenant_id, doc))
        return SpanExportResult.SUCCESS

    def _build_document(self, span, tenant_id: str):
        return {
            "Timestamp": span.end_time,
            "TraceId": format(span.context.trace_id, "032x"),
            "SpanId": format(span.context.span_id, "016x"),
            "ParentSpanId": format(span.parent.span_id, "016x") if span.parent else None,
            "Name": span.name,
            "Kind": str(span.kind),
            "StartTime": span.start_time,
            "EndTime": span.end_time,
            "Duration": (span.end_time - span.start_time) / 1e6,  # ms
            "Attributes": dict(span.attributes),
            "Status": str(span.status.status_code),
            "StatusDescription": span.status.description,
            "ServiceName": self._service_name,
            "TenantId": tenant_id,
            "Events": [e.name for e in span.events],
        }

    def _run(self):
        batch_by_tenant = {}

        while not self._stop_event.is_set():
            try:
                # Collect items up to batch size or timeout
                while len(batch_by_tenant) < self._batch_size:
                    tenant_id, doc = self._queue.get(timeout=self._flush_interval)
                    batch_by_tenant.setdefault(tenant_id, []).append(doc)
            except Empty:
                pass  # Time-based flush

            if batch_by_tenant:
                self._flush_to_mongo(batch_by_tenant)
                batch_by_tenant = {}

    def _flush_to_mongo(self, batches):
        for tenant_id, docs in batches.items():
            try:
                self._db[tenant_id].insert_many(docs)
            except Exception as ex:
                print(f"[MongoExporter] Failed to insert docs for {tenant_id}: {ex}")

    def shutdown(self):
        self._stop_event.set()
        self._worker_thread.join(timeout=5)
        self._client.close()
