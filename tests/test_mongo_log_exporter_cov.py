import pytest
from queue import Queue
from unittest.mock import MagicMock, patch
from blocks_genesis._lmt.mongo_log_exporter import (
    MongoBatchLogger, MongoHandler, TraceContextFilter,
)

ML = 'blocks_genesis._lmt.mongo_log_exporter.'


def _bl(batch_size=1):
    b = MongoBatchLogger.__new__(MongoBatchLogger)
    b.batch_size = batch_size
    b.flush_interval_sec = 0.01
    b.collection = MagicMock()
    b.queue = Queue()
    return b


@patch(ML + 'threading')
@patch(ML + 'MongoClient')
@patch(ML + 'get_blocks_secret')
def test_init(mock_gs, mock_mc, mock_thr):
    b = MongoBatchLogger()
    mock_thr.Thread.assert_called()


def test_create_collection_for_logs_exception():
    b = _bl()
    b._create_collection_if_not_exists = MagicMock(side_effect=Exception('x'))
    b._create_collection_for_logs(MagicMock(), 'c')


def test_create_collection_not_exists():
    b = _bl(); db = MagicMock()
    b._collection_exists = MagicMock(return_value=False)
    b._create_collection_if_not_exists(db, 'c')
    db.create_collection.assert_called()


def test_create_collection_exists_not_ts():
    b = _bl(); db = MagicMock()
    b._collection_exists = MagicMock(return_value=True)
    b._is_time_series_collection = MagicMock(return_value=False)
    b._create_collection_if_not_exists(db, 'c')
    db.drop_collection.assert_called()


def test_create_collection_exists_ts():
    b = _bl(); db = MagicMock()
    b._collection_exists = MagicMock(return_value=True)
    b._is_time_series_collection = MagicMock(return_value=True)
    b._create_collection_if_not_exists(db, 'c')
    db.drop_collection.assert_not_called()


def test_create_collection_exception():
    b = _bl(); db = MagicMock()
    b._collection_exists = MagicMock(side_effect=Exception('x'))
    with pytest.raises(Exception):
        b._create_collection_if_not_exists(db, 'c')


def test_collection_exists():
    b = _bl(); db = MagicMock(); db.list_collection_names.return_value = ['c']
    assert b._collection_exists(db, 'c') is True


def test_is_time_series():
    b = _bl(); db = MagicMock(); db.list_collections.return_value = iter([{'type': 'timeseries'}])
    assert b._is_time_series_collection(db, 'c') is True
    db2 = MagicMock(); db2.list_collections.return_value = iter([])
    assert b._is_time_series_collection(db2, 'c') is False


def test_create_index_needed():
    b = _bl(); db = MagicMock(); coll = MagicMock(); db.__getitem__.return_value = coll
    coll.list_indexes.return_value = []
    b._create_index_if_needed(db, 'c')
    coll.create_index.assert_called()


def test_create_index_exists_by_name():
    b = _bl(); db = MagicMock(); coll = MagicMock(); db.__getitem__.return_value = coll
    coll.list_indexes.return_value = [{'name': 'c_Index'}]
    b._create_index_if_needed(db, 'c')
    coll.create_index.assert_not_called()


def test_create_index_exists_by_keys():
    b = _bl(); db = MagicMock(); coll = MagicMock(); db.__getitem__.return_value = coll
    coll.list_indexes.return_value = [{'name': 'other', 'key': {'TenantId': 1, 'Timestamp': -1}}]
    b._create_index_if_needed(db, 'c')
    coll.create_index.assert_not_called()


def test_create_index_diff_name_exception():
    b = _bl(); db = MagicMock(); coll = MagicMock(); db.__getitem__.return_value = coll
    coll.list_indexes.side_effect = Exception('Index already exists with a different name')
    b._create_index_if_needed(db, 'c')


def test_create_index_other_exception():
    b = _bl(); db = MagicMock(); coll = MagicMock(); db.__getitem__.return_value = coll
    coll.list_indexes.side_effect = Exception('boom')
    with pytest.raises(Exception):
        b._create_index_if_needed(db, 'c')


def test_enqueue():
    b = _bl(); b.queue = MagicMock()
    r = MagicMock(); r.levelname = 'INFO'; r.getMessage.return_value = 'm'
    r.TenantId = 'tid'; r.name = 'n'; r.TraceId = 't'; r.SpanId = 's'
    b.enqueue(r)
    b.queue.put.assert_called()


def test_enqueue_fallbacks():
    b = _bl(); b.queue = MagicMock()
    with patch(ML + 'Activity') as ma:
        ma.get_trace_id.return_value = 'at'; ma.get_span_id.return_value = 'as'
        r = MagicMock(); r.levelname = 'INFO'; r.getMessage.return_value = 'm'
        r.TenantId = None; r.name = 'n'; r.TraceId = None; r.SpanId = None
        b.enqueue(r)
        b.queue.put.assert_called()


def test_worker_insert():
    b = _bl(1); b.queue.put({'d': 1})
    b._stop_event = MagicMock(); b._stop_event.is_set.side_effect = [False, False, True]
    b._background_worker()
    b.collection.insert_many.assert_called()


def test_worker_empty():
    b = _bl(1)
    b._stop_event = MagicMock(); b._stop_event.is_set.side_effect = [False, True]
    b._background_worker()
    b.collection.insert_many.assert_not_called()


def test_worker_insert_exception():
    b = _bl(1); b.queue.put({'d': 1})
    b.collection.insert_many.side_effect = Exception('x')
    b._stop_event = MagicMock(); b._stop_event.is_set.side_effect = [False, False, True]
    b._background_worker()


def test_worker_shutdown_flush():
    b = _bl(100); b.queue.put({'d': 1})
    b._stop_event = MagicMock(); b._stop_event.is_set.side_effect = [False, False, True]
    b._background_worker()
    b.collection.insert_many.assert_called()


def test_worker_shutdown_flush_exception():
    b = _bl(100); b.queue.put({'d': 1})
    b.collection.insert_many.side_effect = Exception('x')
    b._stop_event = MagicMock(); b._stop_event.is_set.side_effect = [False, False, True]
    b._background_worker()


def test_stop():
    b = _bl(); b._stop_event = MagicMock(); b.worker_thread = MagicMock()
    b.stop()
    b._stop_event.set.assert_called(); b.worker_thread.join.assert_called()


def test_mongo_handler_init_creates_logger():
    MongoHandler._mongo_logger = None
    with patch(ML + 'MongoBatchLogger') as mbl:
        h = MongoHandler()
        assert h.mongo_logger is not None


def test_mongo_handler_singleton():
    MongoHandler._mongo_logger = MagicMock()
    h = MongoHandler()
    assert h.mongo_logger is MongoHandler._mongo_logger
    MongoHandler._mongo_logger = None


def test_mongo_handler_emit_success():
    MongoHandler._mongo_logger = MagicMock()
    h = MongoHandler()
    h.emit(MagicMock())
    h.mongo_logger.enqueue.assert_called()
    MongoHandler._mongo_logger = None


def test_mongo_handler_emit_exception():
    MongoHandler._mongo_logger = MagicMock()
    h = MongoHandler()
    h.mongo_logger.enqueue.side_effect = Exception('x')
    h.handleError = MagicMock()
    h.emit(MagicMock())
    h.handleError.assert_called()
    MongoHandler._mongo_logger = None


@patch(ML + 'Activity')
@patch(ML + 'BlocksContextManager')
def test_trace_context_filter_with_context(mock_bcm, mock_act):
    ctx = MagicMock(); ctx.tenant_id = 'tid'
    mock_bcm.get_context.return_value = ctx
    mock_act.get_trace_id.return_value = 'tr'; mock_act.get_span_id.return_value = 'sp'
    r = MagicMock()
    assert TraceContextFilter().filter(r) is True
    assert r.TenantId == 'tid'


@patch(ML + 'Activity')
@patch(ML + 'BlocksContextManager')
def test_trace_context_filter_no_context(mock_bcm, mock_act):
    mock_bcm.get_context.return_value = None
    r = MagicMock()
    assert TraceContextFilter().filter(r) is True
    assert r.TenantId == 'miscellaneous'
