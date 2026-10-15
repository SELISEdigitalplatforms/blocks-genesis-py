"""Log collection setup never drops an existing collection, so a name clash cannot delete log history."""

from unittest.mock import MagicMock, call

import pytest
from pymongo.errors import CollectionInvalid, OperationFailure

from blocks_genesis._lmt.mongo_log_exporter import RETENTION_SECONDS, MongoBatchLogger

TIME_SERIES = call(
    "svc",
    timeseries={"timeField": "Timestamp", "metaField": "TenantId", "granularity": "minutes"},
    expireAfterSeconds=RETENTION_SECONDS,
)
TTL_INDEX = call([("Timestamp", 1)], name="Timestamp_TTL", expireAfterSeconds=RETENTION_SECONDS)
PLAIN_WARNING = (
    "[MongoBatchLogger] log collection is a plain collection, not time-series: "
    "kept with a TTL index; migrate it to time-series\n"
)


def _logger():
    return MongoBatchLogger.__new__(MongoBatchLogger)


def _db(names, kind=None):
    """A database whose collection 'svc' is listed by name as `names`, and has type `kind` when listed."""
    db = MagicMock()
    coll = MagicMock()
    coll.list_indexes.return_value = []
    db.__getitem__.return_value = coll
    db.list_collection_names.return_value = names
    db.list_collections.side_effect = lambda **k: iter([{"name": "svc", "type": kind}] if kind else [])
    return db, coll


def _ttl_calls(coll):
    return [c for c in coll.create_index.call_args_list if "expireAfterSeconds" in c.kwargs]


def test_existing_plain_collection_is_kept_with_a_ttl_index(capsys):
    db, coll = _db(["svc"], "collection")

    _logger()._create_collection_for_logs(db, "svc")

    db.drop_collection.assert_not_called()
    coll.drop.assert_not_called()
    db.create_collection.assert_not_called()
    assert _ttl_calls(coll) == [TTL_INDEX]
    assert capsys.readouterr() == ("", PLAIN_WARNING)


def test_missing_collection_is_created_as_time_series(capsys):
    db, coll = _db([])

    _logger()._create_collection_for_logs(db, "svc")

    assert db.create_collection.call_args_list == [TIME_SERIES]
    db.drop_collection.assert_not_called()
    assert _ttl_calls(coll) == []
    assert capsys.readouterr() == ("", "")


def test_existing_time_series_collection_is_used_as_is(capsys):
    db, coll = _db(["svc"], "timeseries")

    _logger()._create_collection_for_logs(db, "svc")

    db.create_collection.assert_not_called()
    db.drop_collection.assert_not_called()
    assert _ttl_calls(coll) == []
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "race",
    [CollectionInvalid("collection svc already exists"), OperationFailure("Collection already exists", code=48)],
)
def test_collection_created_by_someone_else_first_counts_as_success(race, capsys):
    db, coll = _db([], "timeseries")
    db.create_collection.side_effect = race

    _logger()._create_collection_if_not_exists(db, "svc")

    db.drop_collection.assert_not_called()
    assert _ttl_calls(coll) == []
    assert capsys.readouterr() == ("", "")


def test_other_create_failure_stays_non_fatal(capsys):
    db, coll = _db([])
    db.create_collection.side_effect = OperationFailure("mongodb://user:sentinel-pw@db.internal", code=13)

    _logger()._create_collection_for_logs(db, "svc")

    db.drop_collection.assert_not_called()
    out, err = capsys.readouterr()
    assert out == ""
    assert err == "[MongoBatchLogger] log collection setup failed (OperationFailure)\n"


COMPOUND_INDEX = call(
    [("TenantId", 1), ("Timestamp", -1)],
    name="svc_Index",
    partialFilterExpression={"TenantId": {"$exists": True}},
)


def test_plain_collection_gets_the_ttl_and_the_tenant_index(capsys):
    db, coll = _db(["svc"], "collection")

    _logger()._create_collection_for_logs(db, "svc")

    assert coll.create_index.call_args_list == [TTL_INDEX, COMPOUND_INDEX]
    assert capsys.readouterr() == ("", PLAIN_WARNING)


def test_ttl_index_failure_still_creates_the_tenant_index(capsys):
    db, coll = _db(["svc"], "collection")
    failures = iter([OperationFailure("mongodb://user:sentinel-pw@db.internal", code=85)])

    def create_index(*args, **kwargs):
        if "expireAfterSeconds" in kwargs:
            raise next(failures)

    coll.create_index.side_effect = create_index

    _logger()._create_collection_for_logs(db, "svc")

    assert coll.create_index.call_args_list == [TTL_INDEX, COMPOUND_INDEX]
    db.drop_collection.assert_not_called()
    coll.drop.assert_not_called()
    assert capsys.readouterr() == (
        "",
        "[MongoBatchLogger] log collection is a plain collection, not time-series: "
        "kept without a TTL index (OperationFailure); migrate it to time-series\n",
    )
