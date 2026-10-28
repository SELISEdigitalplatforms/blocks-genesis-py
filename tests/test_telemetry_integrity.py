"""Telemetry integrity: exporters keep running, stay bounded, expire data and keep status output free of detail."""

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from queue import Queue
from unittest.mock import MagicMock, call, patch

import pytest
from opentelemetry.sdk.trace.export import SpanExportResult
from pymongo.errors import BulkWriteError, CollectionInvalid, OperationFailure

from blocks_genesis._lmt import log_config, mongo_log_exporter, mongo_trace_exporter
from blocks_genesis._lmt.activity import Activity
from blocks_genesis._lmt.mongo_log_exporter import MongoBatchLogger
from blocks_genesis._lmt.mongo_trace_exporter import MongoDBTraceExporter

SECRET = "mongodb://user:sentinel-pw-3e1a@db.internal"
TRACE_LOGGER = "blocks_genesis._lmt.mongo_trace_exporter"
ML = "blocks_genesis._lmt.mongo_log_exporter."
LC = "blocks_genesis._lmt.log_config."
AC = "blocks_genesis._lmt.activity."

TTL_INDEX = call([("Timestamp", 1)], name="Timestamp_TTL", expireAfterSeconds=7776000)
TRACE_ID_INDEX = call([("TraceId", 1), ("Timestamp", -1)], name="tid_Index")


def _exp():
    e = MongoDBTraceExporter.__new__(MongoDBTraceExporter)
    e._service_name = "svc"
    e._db = MagicMock()
    e._queue = Queue(maxsize=100)
    e._batch_size = 1000
    e._flush_interval = 0.01
    e._client = MagicMock()
    e._stop_event = MagicMock()
    e._worker_thread = MagicMock()
    e._ensured_collections = set()
    return e


def _span(parent=None):
    s = MagicMock()
    s.parent = parent
    s.context.trace_id = 1
    s.context.span_id = 2
    s.end_time = 1_000_000_000
    s.start_time = 0
    s.name = "op"
    s.kind = "INTERNAL"
    s.attributes = {"baggage.TenantId": "tid", "k": 2}
    s.status.status_code = "OK"
    s.status.description = "d"
    return s


def _bl(batch_size=1):
    b = MongoBatchLogger.__new__(MongoBatchLogger)
    b.batch_size = batch_size
    b.flush_interval_sec = 0.01
    b.collection = MagicMock()
    b.queue = Queue()
    b._dropped = 0
    b._dropped_lock = threading.Lock()
    b._last_drop_report = float("-inf")
    return b


def _record(msg, args=None):
    record = logging.LogRecord("x", logging.INFO, __file__, 1, msg, args, None)
    record.TenantId = "t"
    record.TraceId = "tr"
    record.SpanId = "sp"
    return record


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.name == TRACE_LOGGER and r.levelno == logging.WARNING]


# ------------------------------------------------------------------ trace exporter


def test_run_survives_an_unexpected_error_and_keeps_exporting(caplog, capsys):
    e = _exp()
    e._batch_size = 1
    e._queue = Queue()
    e._queue.put(("t", {"n": 1}))
    e._queue.put(("t", {"n": 2}))
    e._stop_event.is_set.side_effect = [False, False, True]
    e._flush_to_mongo = MagicMock(side_effect=[RuntimeError(SECRET), None])
    e._flush_remaining = MagicMock()

    e._run()

    assert e._flush_to_mongo.call_count == 2
    assert e._flush_to_mongo.call_args_list[1].args[0] == {"t": [{"n": 2}]}
    errors = [r.getMessage() for r in caplog.records if r.name == TRACE_LOGGER and r.levelno == logging.ERROR]
    assert any("RuntimeError" in m for m in errors)
    assert "sentinel-pw" not in caplog.text
    assert capsys.readouterr().out == ""


def test_flush_continues_after_one_collection_fails(caplog, capsys):
    e = _exp()
    broken, working = MagicMock(), MagicMock()
    broken.insert_many.side_effect = TypeError(SECRET)
    e._db.__getitem__.side_effect = lambda name: {"t1": broken, "t2": working}[name]
    e._db.list_collections.return_value = []

    e._flush_to_mongo({"t1": [{}, {}], "t2": [{}]})

    working.insert_many.assert_called_once()
    warning = next(m for m in _warnings(caplog) if "t1" in m)
    assert "2" in warning and "TypeError" in warning
    assert "sentinel-pw" not in caplog.text
    assert capsys.readouterr().out == ""


def _dead_thread():
    thread = threading.Thread(target=lambda: None)
    thread.start()
    thread.join()
    return thread


def test_export_restarts_a_dead_worker_thread(caplog, capsys):
    e = _exp()
    e._stop_event = threading.Event()
    dead = _dead_thread()
    e._worker_thread = dead
    e._run = MagicMock()
    e._queue = Queue()

    assert e.export([_span()]) == SpanExportResult.SUCCESS

    assert e._worker_thread is not dead
    e._worker_thread.join(timeout=5)
    e._run.assert_called_once()
    assert any("restarted" in m for m in _warnings(caplog))
    assert capsys.readouterr().out == ""


def test_export_does_not_restart_after_shutdown(capsys):
    e = _exp()
    e._stop_event = threading.Event()
    e._stop_event.set()
    dead = _dead_thread()
    e._worker_thread = dead
    e._run = MagicMock()
    e._queue = Queue()

    e.export([_span()])

    assert e._worker_thread is dead
    e._run.assert_not_called()
    assert capsys.readouterr().out == ""


def test_full_queue_returns_failure_and_warns_once_per_interval(caplog, capsys):
    e = _exp()
    e._queue = Queue(maxsize=1)
    e._queue.put(("t", {}))

    assert e.export([_span()]) == SpanExportResult.FAILURE
    assert e.export([_span()]) == SpanExportResult.FAILURE
    assert len([m for m in _warnings(caplog) if "full" in m]) == 1

    e._last_queue_full_log -= mongo_trace_exporter._QUEUE_FULL_LOG_INTERVAL_SECONDS
    assert e.export([_span()]) == SpanExportResult.FAILURE
    assert len([m for m in _warnings(caplog) if "full" in m]) == 2
    assert capsys.readouterr().out == ""


def test_export_error_is_logged_not_printed(caplog, capsys):
    e = _exp()
    e._extract_baggage_from_span = MagicMock(side_effect=Exception(SECRET))

    assert e.export([_span()]) == SpanExportResult.FAILURE

    assert "Exception" in caplog.text
    assert "sentinel-pw" not in caplog.text
    assert capsys.readouterr().out == ""


def test_trace_timestamps_are_utc_aware(capsys):
    doc = _exp()._build_document(_span(), {}, "tid")

    assert doc["Timestamp"] == doc["EndTime"] == datetime(1970, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
    assert doc["StartTime"] == datetime(1970, 1, 1, tzinfo=timezone.utc)
    for key in ("Timestamp", "StartTime", "EndTime"):
        assert doc[key].utcoffset() == timedelta(0)
    assert capsys.readouterr().out == ""


# ------------------------------------------------------------------ trace collections


def _flush_twice(e):
    e._flush_to_mongo({"tid": [{}]})
    e._flush_to_mongo({"tid": [{}]})


def test_missing_collection_is_created_as_time_series_once(capsys):
    e = _exp()
    coll = MagicMock()
    e._db.__getitem__.return_value = coll
    e._db.list_collections.return_value = []
    order = []
    e._db.create_collection.side_effect = lambda *a, **k: order.append("create")
    coll.insert_many.side_effect = lambda *a, **k: order.append("insert")

    _flush_twice(e)

    e._db.create_collection.assert_called_once_with(
        "tid",
        timeseries={"timeField": "Timestamp", "metaField": "TraceId", "granularity": "minutes"},
        expireAfterSeconds=7776000,
    )
    assert coll.create_index.call_args_list == [TRACE_ID_INDEX]
    assert order == ["create", "insert", "insert"]
    assert mongo_trace_exporter.RETENTION_SECONDS is mongo_log_exporter.RETENTION_SECONDS
    assert capsys.readouterr().out == ""


def test_existing_plain_collection_gets_ttl_and_trace_id_indexes_once(capsys):
    e = _exp()
    coll = MagicMock()
    e._db.__getitem__.return_value = coll
    e._db.list_collections.return_value = [{"name": "tid", "type": "collection"}]

    _flush_twice(e)

    e._db.create_collection.assert_not_called()
    assert coll.create_index.call_args_list == [TTL_INDEX, TRACE_ID_INDEX]
    assert coll.insert_many.call_count == 2
    assert capsys.readouterr().out == ""


def test_existing_time_series_collection_gets_no_ttl_index(capsys):
    e = _exp()
    coll = MagicMock()
    e._db.__getitem__.return_value = coll
    e._db.list_collections.return_value = [{"name": "tid", "type": "timeseries"}]

    _flush_twice(e)

    e._db.create_collection.assert_not_called()
    assert all("expireAfterSeconds" not in c.kwargs for c in coll.create_index.call_args_list)
    assert coll.create_index.call_args_list == [TRACE_ID_INDEX]
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "race, existing, indexes",
    [
        (CollectionInvalid("collection tid already exists"), "timeseries", [TRACE_ID_INDEX]),
        (OperationFailure("Collection already exists", code=48), "collection", [TTL_INDEX, TRACE_ID_INDEX]),
    ],
)
def test_collection_created_by_someone_else_first_counts_as_success(race, existing, indexes, caplog, capsys):
    e = _exp()
    coll = MagicMock()
    e._db.__getitem__.return_value = coll
    e._db.list_collections.side_effect = [[], [{"name": "tid", "type": existing}]]
    e._db.create_collection.side_effect = race

    e._flush_to_mongo({"tid": [{}]})

    assert coll.create_index.call_args_list == indexes
    coll.insert_many.assert_called_once()
    assert _warnings(caplog) == []
    assert capsys.readouterr().out == ""


def test_index_failure_is_logged_and_export_continues(caplog, capsys):
    e = _exp()
    coll = MagicMock()
    e._db.__getitem__.return_value = coll
    e._db.list_collections.return_value = [{"name": "tid", "type": "collection"}]
    coll.create_index.side_effect = OperationFailure(SECRET)

    e._flush_to_mongo({"tid": [{}]})
    calls = coll.create_index.call_count
    e._flush_to_mongo({"tid": [{}]})

    assert coll.insert_many.call_count == 2
    assert coll.create_index.call_count == calls
    assert any("OperationFailure" in m and "tid" in m for m in _warnings(caplog))
    assert "sentinel-pw" not in caplog.text
    assert capsys.readouterr().out == ""


def test_collection_create_failure_is_logged_and_export_continues(caplog, capsys):
    e = _exp()
    coll = MagicMock()
    e._db.__getitem__.return_value = coll
    e._db.list_collections.return_value = []
    e._db.create_collection.side_effect = OperationFailure(SECRET, code=13)

    e._flush_to_mongo({"tid": [{}]})
    e._flush_to_mongo({"tid": [{}]})

    assert coll.insert_many.call_count == 2
    e._db.create_collection.assert_called_once()
    assert any("OperationFailure" in m for m in _warnings(caplog))
    assert "sentinel-pw" not in caplog.text
    assert capsys.readouterr().out == ""


# ------------------------------------------------------------------ log batcher


def _start_worker(b):
    b._stop_event = threading.Event()
    worker = threading.Thread(target=b._background_worker, daemon=True)
    worker.start()
    return worker


def _stop_worker(b, worker):
    b._stop_event.set()
    worker.join(timeout=5)


def test_partial_batch_is_written_within_the_flush_interval(capsys):
    b = _bl(batch_size=50)
    b.flush_interval_sec = 0.05
    worker = _start_worker(b)
    try:
        doc = {"Message": "m"}
        b.queue.put(doc)
        deadline = time.monotonic() + 1
        while not b.collection.insert_many.called and time.monotonic() < deadline:
            time.sleep(0.005)
        written_before_stop = b.collection.insert_many.called and not b._stop_event.is_set()
    finally:
        _stop_worker(b, worker)

    assert written_before_stop
    assert b.collection.insert_many.call_args_list[0].args[0] == [doc]
    assert capsys.readouterr().out == ""


def test_trickle_cannot_hold_a_partial_batch(capsys):
    b = _bl(batch_size=50)
    b.flush_interval_sec = 0.2
    worker = _start_worker(b)
    try:
        first_put = time.monotonic()
        b.queue.put({"Message": "m0"})
        written_at = None
        # Records keep arriving faster than the interval, so only an age-based flush can write
        # before the batch fills (50 x 0.05 s = 2.5 s, past the window).
        while time.monotonic() - first_put < 2.0:
            time.sleep(0.05)
            if b.collection.insert_many.called:
                written_at = time.monotonic()
                break
            b.queue.put({"Message": "more"})
    finally:
        _stop_worker(b, worker)

    assert written_at is not None and written_at - first_put < 1.5
    assert capsys.readouterr().out == ""


def test_failed_insert_is_retried_once(capsys):
    b = _bl()
    b.collection.insert_many.side_effect = [Exception(SECRET), None]

    b._insert_batch([{}])

    assert b.collection.insert_many.call_count == 2
    assert capsys.readouterr() == ("", "")


def test_insert_failing_twice_drops_with_count_only(capsys):
    b = _bl()
    b.collection.insert_many.side_effect = [Exception(SECRET), Exception(SECRET)]

    b._insert_batch([{}, {}, {}])

    assert b.collection.insert_many.call_count == 2
    assert capsys.readouterr() == ("", "[MongoBatchLogger] dropped 3 log records after a failed insert\n")


def test_partial_write_is_not_retried(capsys):
    b = _bl()
    b.collection.insert_many.side_effect = BulkWriteError({"writeErrors": [{}], "nInserted": 1})

    b._insert_batch([{}, {}])

    assert b.collection.insert_many.call_count == 1
    out, err = capsys.readouterr()
    assert out == "" and "dropped 2" in err


@patch(ML + "threading")
@patch(ML + "MongoClient")
@patch(ML + "get_blocks_secret")
def test_queue_is_bounded(mock_gs, mock_mc, mock_thr, capsys):
    assert MongoBatchLogger().queue.maxsize == 10000
    assert capsys.readouterr().out == ""


def test_full_queue_drops_the_newest_and_counts(capsys):
    b = _bl()
    b.queue = Queue(maxsize=2)

    def enqueue_all():
        for i in range(5):
            b.enqueue(_record(f"m{i}"))

    feeder = threading.Thread(target=enqueue_all, daemon=True)
    feeder.start()
    feeder.join(timeout=2)
    assert not feeder.is_alive(), "enqueue blocked on a full queue"

    assert [b.queue.get_nowait()["Message"] for _ in range(2)] == ["m0", "m1"]
    assert b._dropped == 3
    b._report_dropped(force=True)
    assert capsys.readouterr() == ("", "[MongoBatchLogger] dropped 3 log records: queue full\n")
    assert b._dropped == 0


def test_drop_report_is_rate_limited(capsys):
    b = _bl()
    b.flush_interval_sec = 60
    b._dropped = 1
    b._report_dropped()
    assert capsys.readouterr().err == "[MongoBatchLogger] dropped 1 log records: queue full\n"

    b._dropped = 1
    b._report_dropped()
    assert capsys.readouterr().err == ""

    b._report_dropped(force=True)
    assert capsys.readouterr() == ("", "[MongoBatchLogger] dropped 1 log records: queue full\n")


def test_log_timestamp_is_utc_aware(capsys):
    b = _bl()

    b.enqueue(_record("m"))

    assert b.queue.get_nowait()["Timestamp"].utcoffset() == timedelta(0)
    assert capsys.readouterr().out == ""


def test_collection_setup_failure_goes_to_stderr_without_detail(capsys):
    b = _bl()
    b._create_collection_if_not_exists = MagicMock(side_effect=Exception(SECRET))

    b._create_collection_for_logs(MagicMock(), "c")

    out, err = capsys.readouterr()
    assert out == ""
    assert "Exception" in err and "sentinel-pw" not in err


# ------------------------------------------------------------------ console formatter

FORGED = "bob\r\n[2026-10-15 00:00:00] [INFO] [t] [x] admin signed in"


def _console_formatter():
    root = logging.getLogger()
    saved, level = root.handlers[:], root.level
    try:
        with patch(LC + "MongoHandler"), patch(LC + "TraceContextFilter"):
            log_config.configure_logger()
            return root.handlers[0].formatter
    finally:
        root.handlers[:] = saved
        root.setLevel(level)


def test_console_formatter_escapes_line_breaks():
    formatter = _console_formatter()

    line = formatter.format(_record("login failed for %s", (FORGED,)))

    assert isinstance(formatter, log_config.SingleLineFormatter)
    assert "\r" not in line and "\n" not in line
    assert "bob\\r\\n[2026-10-15" in line


def test_console_formatter_keeps_plain_messages():
    assert _console_formatter().format(_record("hello")).endswith("] hello")


def test_mongo_log_keeps_the_original_message():
    b = _bl()

    b.enqueue(_record("login failed for %s", (FORGED,)))

    assert "\r\n" in b.queue.get_nowait()["Message"]


# ------------------------------------------------------------------ activity


def _child_activity(parent_attributes):
    parent = MagicMock()
    parent.attributes = parent_attributes
    child = MagicMock()
    child.is_recording.return_value = True
    with patch(AC + "get_current"), patch(AC + "get_current_span", return_value=parent), \
            patch(AC + "_tracer") as tracer, patch(AC + "trace"), patch(AC + "attach"):
        tracer.start_span.return_value = child
        return Activity("child"), parent, child


def test_child_activity_copies_no_plain_parent_attributes():
    activity, parent, child = _child_activity(
        {"http.url": "https://x/a?token=sentinel-query-91c2", "http.target": "/a?token=sentinel-query-91c2"}
    )

    assert activity._find_root_attributes(parent) == {}
    child.set_attribute.assert_not_called()
    assert json.dumps(activity.get_all_root_attributes()) == "{}"


def test_child_activity_copies_only_baggage_keys():
    activity, parent, child = _child_activity({"baggage.TenantId": "t", "http.url": "https://x/?token=s"})

    assert activity._find_root_attributes(parent) == {"TenantId": "t"}
    child.set_attribute.assert_called_once_with("baggage.TenantId", "t")
