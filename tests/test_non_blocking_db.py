"""The subscription repository reads Mongo off the event loop.

The collections from the database provider are synchronous PyMongo. A find, and the
cursor walk that fetches its rows, run on a worker thread, so a slow database answer
stalls only the request waiting for it, not every request on the process.
"""
import asyncio
import threading
from unittest.mock import AsyncMock, patch

import pytest

from blocks_genesis._subscription import repository

REPO = "blocks_genesis._subscription.repository."

# How long a blocked find waits for the loop to run another task. Only reached when
# the find is on the loop thread, which is the failure this file guards against.
_WAIT_SECONDS = 1.0


class _BlockingCursor:
    """Records the thread every cursor step runs on, the way a PyMongo cursor fetches lazily."""

    def __init__(self, collection, rows):
        self._collection = collection
        self._rows = rows

    def sort(self, field, direction=1):
        self._collection.threads.append(threading.get_ident())
        return self

    def __iter__(self):
        self._collection.threads.append(threading.get_ident())
        return iter(self._rows)


class _BlockingCollection:
    """A find that holds its thread until another task on the loop releases it."""

    def __init__(self, rows):
        self.rows = rows
        self.released = threading.Event()
        self.released_while_in_flight = None
        self.threads = []

    def find(self, filt, sort=None):
        self.threads.append(threading.get_ident())
        self.released_while_in_flight = self.released.wait(_WAIT_SECONDS)
        return _BlockingCursor(self, self.rows)


class _FailingCollection:
    def find(self, filt, sort=None):
        raise RuntimeError("database down")


def _provider(collection):
    provider = AsyncMock()
    provider.get_collection = AsyncMock(return_value=collection)
    return provider


_READS = [
    pytest.param(lambda: repository.get_current_usage_docs("t1", "default"), id="current_usage"),
    pytest.param(
        lambda: repository.get_scoped_usage_docs("t1", "default", ["u1"]), id="scoped_usage"
    ),
    pytest.param(
        lambda: repository.get_current_entitlement_docs("t1", "default"), id="entitlements"
    ),
]


@pytest.mark.parametrize("read", _READS)
async def test_read_leaves_the_loop_free_while_the_database_answers(read):
    rows = [{"_id": 1}, {"_id": 2}]
    collection = _BlockingCollection(rows)

    async def release():
        await asyncio.sleep(0)
        collection.released.set()

    with patch(REPO + "DbContext") as db_context:
        db_context.get_provider.return_value = _provider(collection)
        docs, _ = await asyncio.gather(read(), release())

    assert docs == rows
    assert collection.released_while_in_flight is True
    loop_thread = threading.get_ident()
    assert collection.threads and loop_thread not in collection.threads


@pytest.mark.parametrize("read", _READS)
async def test_database_error_reaches_the_caller_unchanged(read):
    with patch(REPO + "DbContext") as db_context:
        db_context.get_provider.return_value = _provider(_FailingCollection())
        with pytest.raises(RuntimeError, match="database down"):
            await read()
