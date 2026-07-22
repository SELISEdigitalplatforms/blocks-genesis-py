import pytest
from queue import Queue
from unittest.mock import MagicMock, patch
from pymongo import errors
from opentelemetry.sdk.trace.export import SpanExportResult
from blocks_genesis._lmt.mongo_trace_exporter import MongoDBTraceExporter

MT = 'blocks_genesis._lmt.mongo_trace_exporter.'


def _exp():
    e = MongoDBTraceExporter.__new__(MongoDBTraceExporter)
    e._service_name = 'svc'; e._db = MagicMock()
    e._queue = Queue(maxsize=100); e._batch_size = 1000; e._flush_interval = 0.01
    e._client = MagicMock(); e._stop_event = MagicMock(); e._worker_thread = MagicMock()
    return e


def _span(parent=None):
    s = MagicMock()
    s.parent = parent
    s.context.trace_id = 1; s.context.span_id = 2
    s.end_time = 1_000_000_000; s.start_time = 0
    s.name = 'op'; s.kind = 'INTERNAL'
    s.attributes = {'baggage.TenantId': 'tid', 'k': 2}
    s.status.status_code = 'OK'; s.status.description = 'd'
    return s


@patch(MT + 'threading')
@patch(MT + 'MongoClient')
@patch(MT + 'get_blocks_secret')
def test_init(mock_gs, mock_mc, mock_thr):
    MongoDBTraceExporter()
    mock_thr.Thread.assert_called()


def test_extract_baggage():
    e = _exp(); s = MagicMock(); s.attributes = {'baggage.X': 'v', 'other': 1}
    assert e._extract_baggage_from_span(s) == {'X': 'v'}


def test_export_success():
    e = _exp(); e._queue = MagicMock()
    assert e.export([_span()]) == SpanExportResult.SUCCESS


def test_export_exception():
    e = _exp(); e._extract_baggage_from_span = MagicMock(side_effect=Exception('x'))
    assert e.export([MagicMock()]) == SpanExportResult.FAILURE


def test_build_document_with_parent():
    e = _exp(); p = MagicMock(); p.span_id = 5
    doc = e._build_document(_span(parent=p), {'X': 1}, 'tid')
    assert doc['ParentId'].startswith('00-')


def test_build_document_no_parent():
    e = _exp()
    doc = e._build_document(_span(parent=None), {}, 'tid')
    assert doc['ParentId'] == '' and doc['ParentSpanId'] == '0000000000000000'


def test_run_flushes():
    e = _exp(); e._batch_size = 1; e._queue = Queue()
    e._queue.put(('tid', {'d': 1}))
    e._stop_event.is_set.side_effect = [False, True]
    e._flush_to_mongo = MagicMock(); e._flush_remaining = MagicMock()
    e._run()
    e._flush_to_mongo.assert_called()


def test_run_inner_empty():
    e = _exp(); e._batch_size = 100; e._queue = Queue()
    e._queue.put(('tid', {'d': 1}))
    e._stop_event.is_set.side_effect = [False, True]
    e._flush_to_mongo = MagicMock(); e._flush_remaining = MagicMock()
    e._run()
    e._flush_to_mongo.assert_called()


def test_run_outer_empty():
    e = _exp(); e._batch_size = 1; e._queue = Queue()
    e._stop_event.is_set.side_effect = [False, True]
    e._flush_to_mongo = MagicMock(); e._flush_remaining = MagicMock()
    e._run()
    e._flush_to_mongo.assert_not_called()


def test_flush_to_mongo_success():
    e = _exp(); coll = MagicMock(); e._db.__getitem__.return_value = coll
    e._flush_to_mongo({'tid': [{'d': 1}]})
    coll.insert_many.assert_called()


def test_flush_to_mongo_error():
    e = _exp(); coll = MagicMock(); coll.insert_many.side_effect = errors.PyMongoError('x')
    e._db.__getitem__.return_value = coll
    e._flush_to_mongo({'tid': [{'d': 1}]})


def test_flush_remaining_with_docs():
    e = _exp(); e._queue = Queue(); e._queue.put(('tid', {'d': 1}))
    e._flush_to_mongo = MagicMock()
    e._flush_remaining()
    e._flush_to_mongo.assert_called()


def test_flush_remaining_empty():
    e = _exp(); e._queue = Queue()
    e._flush_to_mongo = MagicMock()
    e._flush_remaining()
    e._flush_to_mongo.assert_not_called()


def test_force_flush_empty():
    e = _exp(); e._queue = Queue()
    e._flush_remaining = MagicMock()
    assert e.force_flush(1000) is True
    e._flush_remaining.assert_called()


def test_force_flush_with_pending():
    e = _exp(); e._queue = Queue(); e._queue.put(('t', {}))
    e._flush_remaining = MagicMock()
    assert e.force_flush(50) is True


def test_shutdown():
    e = _exp(); e._flush_remaining = MagicMock()
    e.shutdown()
    e._stop_event.set.assert_called()
    e._worker_thread.join.assert_called()
    e._client.close.assert_called()


from queue import Empty


def test_run_inner_appends_second():
    e = _exp(); e._batch_size = 100; e._queue = Queue()
    e._queue.put(('tid', {'d': 1}))
    e._queue.put(('tid2', {'d': 2}))
    e._stop_event.is_set.side_effect = [False, True]
    e._flush_to_mongo = MagicMock(); e._flush_remaining = MagicMock()
    e._run()
    e._flush_to_mongo.assert_called()


def test_flush_remaining_race_empty():
    e = _exp()
    q = MagicMock(); q.empty.side_effect = [False, True]
    q.get_nowait.side_effect = Empty()
    e._queue = q
    e._flush_to_mongo = MagicMock()
    e._flush_remaining()
    e._flush_to_mongo.assert_not_called()
