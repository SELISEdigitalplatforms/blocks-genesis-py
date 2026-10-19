"""Card #48: cached tenants are revalidated, unknown ones are remembered, and workers refuse
messages for a tenant that is missing or disabled.

Time comes from a fake clock on the service, so no test sleeps.
"""
import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from blocks_genesis._auth.blocks_context import BlocksContext, BlocksContextManager
from blocks_genesis._delegation import constants
from blocks_genesis._delegation.context import AuthClaimsContext, DelegatedTokenContext
from blocks_genesis._delegation.grant_store import DelegationGrantRecord
from blocks_genesis._tenant import tenant_service as ts
from blocks_genesis._tenant.tenant import Tenant
from blocks_genesis._tenant.tenant_service import TenantCacheUpdateMessage, TenantService

TENANT_LOGGER = "blocks_genesis._tenant.tenant_service"
ID_QUERY = {"$or": [{"_id": "t1"}, {"TenantId": "t1"}]}
X_COM = [{"Domain": "https://x.com"}]


class FakeClock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


# ---------------------------------------------------------------------------
# Tenant service
# ---------------------------------------------------------------------------

def _svc(find_one=None, clock=None):
    s = TenantService.__new__(TenantService)
    s.cache = MagicMock()
    s.database = MagicMock()
    s.database.__getitem__.return_value.find_one = find_one or AsyncMock(return_value=None)
    s._tenant_cache = {}
    s._root_tenant_id = None
    s._tenant_load_in_progress = {}
    s._update_channel = "tenant::updates"
    s._collection_name = "Tenants"
    s._initialized = False
    s._is_subscribed = False
    s._disposed = False
    s._initialize_lock = asyncio.Lock()
    s._tenant_load_lock = asyncio.Lock()
    s._tenant_loaded_at = {}
    s._unknown_tenants = {}
    s._unknown_domains = {}
    s._clock = clock or FakeClock()
    return s


def _doc(tid="t1", **overrides):
    doc = {
        "_id": tid,
        "TenantId": tid,
        "DBName": "db",
        "DbConnectionString": "mongodb://real",
        "IsRootTenant": False,
    }
    doc.update(overrides)
    return doc


def _cache(s, tid="t1", at=0, **overrides):
    tenant = Tenant(**_doc(tid, **overrides))
    s._tenant_cache[tid] = tenant
    s._tenant_loaded_at[tid] = at
    return tenant


def _upsert(tid="t1", **overrides):
    return TenantCacheUpdateMessage(
        action="upsert", tenant=Tenant(**_doc(tid, **overrides))
    ).model_dump_json(by_alias=True)


def _routed(by_id=None, verified=None, unverified=None):
    """A find_one that answers the id query and the two domain queries separately."""
    answers = {"id": by_id, "verified": verified, "unverified": unverified}

    async def route(query, *args, **kwargs):
        if "$or" in query:
            return answers["id"]
        if query["Applications"]["$elemMatch"].get("IsDomainVerified"):
            return answers["verified"]
        return answers["unverified"]

    find_one = AsyncMock(side_effect=route)
    find_one.answers = answers
    return find_one


def _domain_queries(find_one):
    return [c.args[0] for c in find_one.await_args_list if "$or" not in c.args[0]]


async def test_fresh_cached_tenant_is_served_without_a_read():
    clock = FakeClock()
    find_one = AsyncMock(return_value=_doc())
    s = _svc(find_one, clock)
    cached = _cache(s)
    clock.now = 59

    assert await s.get_tenant("t1") is cached
    find_one.assert_not_awaited()


async def test_stale_tenant_is_reread_after_the_interval():
    clock = FakeClock()
    find_one = AsyncMock(return_value=_doc(DbConnectionString="new"))
    s = _svc(find_one, clock)
    _cache(s, DbConnectionString="old")
    clock.now = 60

    tenant = await s.get_tenant("t1")

    assert tenant.db_connection_string == "new"
    find_one.assert_awaited_once_with(ID_QUERY)
    assert s._tenant_loaded_at["t1"] == 60


async def _assert_stale_tenant_is_dropped(found):
    clock = FakeClock()
    find_one = AsyncMock(return_value=found)
    s = _svc(find_one, clock)
    _cache(s)
    clock.now = 60

    assert await s.get_tenant("t1") is None
    assert "t1" not in s._tenant_cache
    assert await s.get_db_connection("t1") == (None, None)
    clock.now = 61
    assert await s.get_tenant("t1") is None
    assert find_one.await_count == 1


async def test_stale_tenant_disabled_in_database_is_dropped():
    await _assert_stale_tenant_is_dropped(_doc(IsDisabled=True))


async def test_stale_tenant_missing_from_database_is_dropped():
    await _assert_stale_tenant_is_dropped(None)


async def test_failed_reread_keeps_the_cached_copy_for_another_interval(caplog):
    caplog.set_level(logging.WARNING, logger=TENANT_LOGGER)
    clock = FakeClock()
    find_one = AsyncMock(side_effect=Exception("mongodb://secret-host"))
    s = _svc(find_one, clock)
    cached = _cache(s)
    clock.now = 60

    assert await s.get_tenant("t1") is cached

    warnings = [r for r in caplog.records if r.name == TENANT_LOGGER and r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "t1" in warnings[0].getMessage()
    assert "Exception" in warnings[0].getMessage()
    assert "secret-host" not in caplog.text

    clock.now = 119
    assert await s.get_tenant("t1") is cached
    assert find_one.await_count == 1
    clock.now = 120
    assert await s.get_tenant("t1") is cached
    assert find_one.await_count == 2


async def test_concurrent_stale_reads_share_one_database_read():
    clock = FakeClock()
    gate = asyncio.Event()

    async def slow_read(query):
        await gate.wait()
        return _doc(DbConnectionString="new")

    find_one = AsyncMock(side_effect=slow_read)
    s = _svc(find_one, clock)
    _cache(s, DbConnectionString="old")
    clock.now = 60

    reads = asyncio.gather(*(s.get_tenant("t1") for _ in range(5)))
    for _ in range(20):
        await asyncio.sleep(0)
    gate.set()
    results = await reads

    find_one.assert_awaited_once()
    assert [t.db_connection_string for t in results] == ["new"] * 5


async def test_cold_load_of_disabled_tenant_is_none_and_not_cached():
    clock = FakeClock()
    find_one = AsyncMock(return_value=_doc(IsDisabled=True))
    s = _svc(find_one, clock)

    assert await s.get_tenant("t1") is None
    assert "t1" not in s._tenant_cache
    clock.now = 1
    assert await s.get_tenant("t1") is None
    assert find_one.await_count == 1


async def test_unknown_tenant_is_negatively_cached():
    clock = FakeClock()
    find_one = AsyncMock(return_value=None)
    s = _svc(find_one, clock)

    for now in (0, 10, 29):
        clock.now = now
        assert await s.get_tenant("t1") is None
    assert find_one.await_count == 1

    clock.now = 30
    assert await s.get_tenant("t1") is None
    assert find_one.await_count == 2


async def test_negative_cache_is_bounded_and_hashed(monkeypatch):
    monkeypatch.setattr(ts, "UNKNOWN_TENANT_CACHE_MAX_ENTRIES", 3)
    s = _svc(AsyncMock(return_value=None))

    for i in range(4):
        await s.get_tenant(f"unknown-{i}")
    assert 0 < len(s._unknown_tenants) <= 3

    s._unknown_tenants.clear()
    long_id = "x" * 10_000
    await s.get_tenant(long_id)
    assert [len(k) for k in s._unknown_tenants] == [32]
    assert all(isinstance(k, bytes) for k in s._unknown_tenants)


async def test_cold_read_failure_is_not_negatively_cached():
    find_one = AsyncMock(side_effect=Exception("mongodb://secret-host"))
    s = _svc(find_one)

    assert await s.get_tenant("t1") is None
    assert await s.get_tenant("t1") is None
    assert find_one.await_count == 2
    assert not s._unknown_tenants

    with pytest.raises(ts.TenantReadError) as raised:
        await s.resolve_tenant("t1")
    assert "secret-host" not in str(raised.value)


async def test_update_message_clears_the_negative_entry():
    find_one = AsyncMock(return_value=None)
    s = _svc(find_one)
    assert await s.get_tenant("t9") is None
    assert s._unknown_tenants

    find_one.return_value = _doc("t9")
    await s._handle_update(_upsert("t9"))
    reads = find_one.await_count

    tenant = await s.get_tenant("t9")
    assert tenant is not None and tenant.tenant_id == "t9"
    assert find_one.await_count == reads
    assert not s._unknown_tenants


async def test_cancelled_waiter_does_not_cancel_the_shared_load():
    gate = asyncio.Event()

    async def slow_read(query):
        await gate.wait()
        return _doc()

    find_one = AsyncMock(side_effect=slow_read)
    s = _svc(find_one)

    first = asyncio.create_task(s.get_tenant("t1"))
    second = asyncio.create_task(s.get_tenant("t1"))
    for _ in range(20):
        await asyncio.sleep(0)
    load = s._tenant_load_in_progress["t1"]

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    gate.set()

    tenant = await second
    assert tenant is not None and tenant.tenant_id == "t1"
    assert load.done() and not load.cancelled()
    assert "t1" not in s._tenant_load_in_progress
    find_one.assert_awaited_once()


async def test_update_message_clears_the_negative_entry_of_the_requested_id():
    find_one = AsyncMock(return_value=None)
    s = _svc(find_one)
    assert await s.get_tenant("alias") is None
    assert s._unknown_tenants

    # The database matches the requested id on _id; its TenantId is different.
    find_one.return_value = _doc("alias", TenantId="t1")
    await s._handle_update(_upsert("alias"))

    assert "t1" in s._tenant_cache
    assert not s._unknown_tenants


async def test_update_message_stamps_the_load_time():
    clock = FakeClock(100)
    find_one = AsyncMock(return_value=_doc())
    s = _svc(find_one, clock)

    await s._handle_update(_upsert())
    assert s._tenant_loaded_at["t1"] == 100

    clock.now = 159
    assert await s.get_tenant("t1") is not None
    assert find_one.await_count == 1


async def test_stale_domain_match_disabled_in_database_is_not_returned():
    clock = FakeClock()
    find_one = _routed(by_id=_doc(IsDisabled=True, Applications=X_COM))
    s = _svc(find_one, clock)
    _cache(s, Applications=X_COM)
    clock.now = 60

    assert await s.get_tenant_by_domain("x.com") is None
    assert "t1" not in s._tenant_cache


async def test_stale_domain_match_still_enabled_is_returned_after_reread():
    clock = FakeClock()
    find_one = _routed(by_id=_doc(Applications=X_COM, DbConnectionString="new"))
    s = _svc(find_one, clock)
    _cache(s, Applications=X_COM, DbConnectionString="old")
    clock.now = 60

    tenant = await s.get_tenant_by_domain("x.com")

    assert tenant is not None and tenant.db_connection_string == "new"
    assert _domain_queries(find_one) == []


async def test_unknown_domain_is_negatively_cached():
    clock = FakeClock()
    find_one = _routed()
    s = _svc(find_one, clock)

    assert await s.get_tenant_by_domain("x.com") is None
    clock.now = 29
    assert await s.get_tenant_by_domain("x.com") is None
    assert find_one.await_count == 2

    clock.now = 30
    assert await s.get_tenant_by_domain("x.com") is None
    assert find_one.await_count == 4


async def test_disabled_domain_owner_is_not_returned():
    find_one = _routed(
        verified=_doc(IsDisabled=True, Applications=[{"Domain": "https://x.com", "IsDomainVerified": True}])
    )
    s = _svc(find_one)

    assert await s.get_tenant_by_domain("x.com") is None
    assert "t1" not in s._tenant_cache
    assert len(_domain_queries(find_one)) == 1


async def test_update_message_clears_the_domain_negative_cache():
    find_one = _routed()
    s = _svc(find_one)
    assert await s.get_tenant_by_domain("x.com") is None
    assert s._unknown_domains

    find_one.answers["id"] = _doc(Applications=X_COM)
    await s._handle_update(_upsert(Applications=X_COM))
    assert not s._unknown_domains
    domain_reads = len(_domain_queries(find_one))

    tenant = await s.get_tenant_by_domain("x.com")
    assert tenant is not None and tenant.tenant_id == "t1"
    assert len(_domain_queries(find_one)) == domain_reads


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------

GRANT_ID = "dg_" + "a" * 64
AMW = "blocks_genesis._message.azure.azure_message_worker."
RMW = "blocks_genesis._message.rabbit_mq.rabbit_message_worker."
TC = "blocks_genesis._message.tenant_check."
ABSENT = object()
ENVELOPE = json.dumps({"type": "OrderPlaced", "body": "{}"})


@pytest.fixture(autouse=True)
def clean_ambient_state():
    BlocksContextManager.clear_context()
    DelegatedTokenContext.clear()
    AuthClaimsContext.clear()
    yield
    BlocksContextManager.clear_context()
    DelegatedTokenContext.clear()
    AuthClaimsContext.clear()


def record(**kw):
    values = {"TenantId": "tenant-1", "UserId": "user-1", "OrganizationId": "org-1"}
    values.update(kw)
    return DelegationGrantRecord(**values)


def context(**kw):
    values = {
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "organization_id": "org-1",
        "is_authenticated": True,
        "roles": ["admin"],
    }
    values.update(kw)
    return BlocksContext(**values)


def grant_store(result=None):
    return MagicMock(get_async=AsyncMock(return_value=result), delete_async=AsyncMock())


def security_context_header(ctx=None):
    return json.dumps(BlocksContextManager.create_sanitized_for_transport(ctx or context()))


def assert_context_cleared():
    assert BlocksContextManager.get_context() is None
    assert DelegatedTokenContext.current() is None


def tenant_lookup(result=None, error=None):
    resolve = AsyncMock(side_effect=error) if error else AsyncMock(return_value=result)
    return MagicMock(resolve_tenant=resolve)


def enabled_tenant():
    return Tenant(**_doc("tenant-1"))


def rabbit_message(security_context=None, grant_id=GRANT_ID):
    if security_context is None:
        security_context = security_context_header()
    headers = {"TenantId": b"tenant-1", "TraceId": b"", "SpanId": b"", "Baggage": b"{}"}
    if security_context is not ABSENT:
        headers["SecurityContext"] = security_context.encode()
    if grant_id is not None:
        headers[constants.DELEGATION_GRANT_HEADER] = grant_id.encode()

    message = MagicMock()
    message.message_id = "msg-1"
    message.headers = headers
    message.body = ENVELOPE.encode()
    message.ack = AsyncMock()
    return message


def rabbit_worker():
    from blocks_genesis._message.rabbit_mq.rabbit_message_worker import RabbitMessageWorker

    worker = RabbitMessageWorker.__new__(RabbitMessageWorker)
    worker._tracer = MagicMock()
    worker._consumer = MagicMock()
    worker._consumer.process_message = AsyncMock()
    return worker


async def run_rabbit(worker, message, store):
    from blocks_genesis._message.message_configuration import ConsumerSubscription

    with patch(RMW + "get_delegation_grant_store", return_value=store), patch(
        RMW + "get_delegated_token_provider", return_value=MagicMock()
    ):
        await worker._process_message(message, ConsumerSubscription.bind_to_queue("orders.queue", 3))


def azure_message(security_context=None, grant_id=GRANT_ID, delivery_count=0):
    if security_context is None:
        security_context = security_context_header()
    properties = {"TraceId": "", "SpanId": "", "TenantId": "tenant-1", "Baggage": "{}"}
    if security_context is not ABSENT:
        properties["SecurityContext"] = security_context
    if grant_id is not None:
        properties[constants.DELEGATION_GRANT_HEADER] = grant_id

    message = MagicMock()
    message.message_id = "msg-1"
    message.delivery_count = delivery_count
    message.application_properties = properties
    message.body = [ENVELOPE.encode()]
    return message


def azure_worker():
    from blocks_genesis._message.azure.azure_message_worker import AzureMessageWorker

    worker = AzureMessageWorker.__new__(AzureMessageWorker)
    worker._logger = logging.getLogger("test")
    worker._consumer = MagicMock()
    worker._consumer.process_message = AsyncMock()
    worker._service_bus_client = None
    worker._receivers = []
    worker._active_message_renewals = {}
    worker._tracer = MagicMock()
    worker.start_auto_renewal_task = AsyncMock()
    return worker


def azure_receiver():
    receiver = MagicMock()
    receiver.complete_message = AsyncMock()
    receiver.abandon_message = AsyncMock()
    receiver.dead_letter_message = AsyncMock()
    return receiver


async def run_azure(worker, receiver, message, store, max_delivery_count=2):
    with patch(AMW + "get_delegation_grant_store", return_value=store), patch(
        AMW + "get_delegated_token_provider", return_value=MagicMock()
    ):
        await worker.message_handler(receiver, message, max_delivery_count=max_delivery_count)


async def test_azure_worker_dead_letters_an_unavailable_tenant():
    worker, receiver, message, store = azure_worker(), azure_receiver(), azure_message(), grant_store(record())

    with patch(TC + "get_tenant_service", return_value=tenant_lookup(None)):
        await run_azure(worker, receiver, message, store)

    worker._consumer.process_message.assert_not_awaited()
    receiver.dead_letter_message.assert_awaited_once_with(
        message, reason="TenantUnavailable", error_description="tenant_unavailable"
    )
    receiver.complete_message.assert_not_awaited()
    receiver.abandon_message.assert_not_awaited()
    store.get_async.assert_not_awaited()
    store.delete_async.assert_not_awaited()
    assert_context_cleared()


async def test_rabbit_worker_acks_an_unavailable_tenant_without_handling(caplog):
    worker, message, store = rabbit_worker(), rabbit_message(), grant_store(record())

    with patch(TC + "get_tenant_service", return_value=tenant_lookup(None)):
        await run_rabbit(worker, message, store)

    message.ack.assert_awaited_once()
    worker._consumer.process_message.assert_not_awaited()
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "msg-1" in errors[0] and "tenant_unavailable" in errors[0] and "tenant-1" in errors[0]
    store.delete_async.assert_not_awaited()
    assert_context_cleared()


@pytest.mark.parametrize("transport", ["azure", "rabbit"])
async def test_workers_run_the_handler_for_an_available_tenant(transport):
    lookup = tenant_lookup(enabled_tenant())
    store = grant_store(record())

    with patch(TC + "get_tenant_service", return_value=lookup):
        if transport == "azure":
            worker, receiver, message = azure_worker(), azure_receiver(), azure_message()
            await run_azure(worker, receiver, message, store)
            receiver.complete_message.assert_awaited_once_with(message)
        else:
            worker, message = rabbit_worker(), rabbit_message()
            await run_rabbit(worker, message, store)
            message.ack.assert_awaited_once()

    lookup.resolve_tenant.assert_awaited_once_with("tenant-1")
    worker._consumer.process_message.assert_awaited_once()


@pytest.mark.parametrize("transport", ["azure", "rabbit"])
async def test_workers_skip_the_check_without_a_context_tenant(transport):
    lookup = tenant_lookup(None)
    store = grant_store()

    with patch(TC + "get_tenant_service", return_value=lookup):
        if transport == "azure":
            worker, receiver = azure_worker(), azure_receiver()
            await run_azure(worker, receiver, azure_message(security_context=ABSENT, grant_id=None), store)
            receiver.complete_message.assert_awaited_once()
        else:
            worker = rabbit_worker()
            await run_rabbit(worker, rabbit_message(security_context=ABSENT, grant_id=None), store)

    lookup.resolve_tenant.assert_not_awaited()
    worker._consumer.process_message.assert_awaited_once()


async def test_azure_worker_abandons_when_the_tenant_cannot_be_read():
    error = ts.TenantReadError("tenant-1")

    worker, receiver, message, store = azure_worker(), azure_receiver(), azure_message(), grant_store(record())
    with patch(TC + "get_tenant_service", return_value=tenant_lookup(error=error)):
        await run_azure(worker, receiver, message, store)
    receiver.abandon_message.assert_awaited_once_with(message)
    receiver.dead_letter_message.assert_not_awaited()
    worker._consumer.process_message.assert_not_awaited()

    worker, receiver = azure_worker(), azure_receiver()
    message = azure_message(delivery_count=1)
    with patch(TC + "get_tenant_service", return_value=tenant_lookup(error=error)):
        await run_azure(worker, receiver, message, store)
    receiver.dead_letter_message.assert_awaited_once()
    assert receiver.dead_letter_message.await_args.kwargs["reason"] == "ProcessingFailed"
    receiver.abandon_message.assert_not_awaited()
    worker._consumer.process_message.assert_not_awaited()
    store.delete_async.assert_not_awaited()


async def test_rabbit_worker_acks_when_the_tenant_cannot_be_read():
    worker, message, store = rabbit_worker(), rabbit_message(), grant_store(record())

    with patch(TC + "get_tenant_service", return_value=tenant_lookup(error=ts.TenantReadError("tenant-1"))):
        await run_rabbit(worker, message, store)

    message.ack.assert_awaited_once()
    worker._consumer.process_message.assert_not_awaited()
    store.delete_async.assert_not_awaited()


async def test_worker_stops_handling_within_the_interval_after_disable():
    clock = FakeClock()
    find_one = AsyncMock(return_value=_doc("tenant-1"))
    service = _svc(find_one, clock)
    worker = azure_worker()

    with patch(TC + "get_tenant_service", return_value=service):
        first = azure_receiver()
        await run_azure(worker, first, azure_message(grant_id=None), grant_store())
        first.complete_message.assert_awaited_once()

        find_one.return_value = _doc("tenant-1", IsDisabled=True)
        clock.now = 59
        second = azure_receiver()
        await run_azure(worker, second, azure_message(grant_id=None), grant_store())
        second.complete_message.assert_awaited_once()

        clock.now = 60
        third = azure_receiver()
        await run_azure(worker, third, azure_message(grant_id=None), grant_store())
        third.complete_message.assert_not_awaited()
        third.dead_letter_message.assert_awaited_once()
        assert third.dead_letter_message.await_args.kwargs["reason"] == "TenantUnavailable"

    assert worker._consumer.process_message.await_count == 2
