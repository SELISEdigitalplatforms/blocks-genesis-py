"""A worker checks the message identity against its delegation grant before running the handler."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from blocks_genesis._auth.blocks_context import BlocksContext, BlocksContextManager
from blocks_genesis._delegation import constants
from blocks_genesis._delegation.context import AuthClaimsContext, DelegatedTokenContext
from blocks_genesis._delegation.grant_store import DelegationGrantRecord

GRANT_ID = "dg_" + "a" * 64

AMW = "blocks_genesis._message.azure.azure_message_worker."
RMW = "blocks_genesis._message.rabbit_mq.rabbit_message_worker."

MISMATCHES = [
    ({"tenant_id": "tenant-2"}, "tenant_mismatch"),
    ({"user_id": "user-2"}, "user_mismatch"),
    ({"organization_id": "org-2"}, "organization_mismatch"),
]


@pytest.fixture(autouse=True)
def clean_ambient_state():
    BlocksContextManager.clear_context()
    DelegatedTokenContext.clear()
    AuthClaimsContext.clear()
    yield
    BlocksContextManager.clear_context()
    DelegatedTokenContext.clear()
    AuthClaimsContext.clear()


@pytest.fixture
def binding():
    from blocks_genesis._delegation import grant_binding

    return grant_binding


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


def grant_store(result=None, error=None):
    get_async = AsyncMock(side_effect=error) if error else AsyncMock(return_value=result)
    return MagicMock(get_async=get_async, delete_async=AsyncMock())


def security_context_header(ctx):
    return json.dumps(BlocksContextManager.create_sanitized_for_transport(ctx))


# ------------------------------------------------------------------ shared check


async def test_no_grant_is_not_checked(binding):
    store = grant_store(record())

    for value in (None, ""):
        assert await binding.check_grant_binding(context(), value, store) is None

    store.get_async.assert_not_awaited()


async def test_malformed_grant_is_not_checked(binding):
    store = grant_store(record())

    assert await binding.check_grant_binding(context(), "dg_short", store) is None
    store.get_async.assert_not_awaited()


async def test_missing_record_is_refused(binding):
    store = grant_store(None)

    assert await binding.check_grant_binding(context(), GRANT_ID, store) == "grant_not_found"
    store.get_async.assert_awaited_once_with(GRANT_ID)


async def test_context_missing_is_refused(binding):
    assert await binding.check_grant_binding(None, GRANT_ID, grant_store(record())) == "context_missing"


@pytest.mark.parametrize("override, reason", MISMATCHES)
async def test_mismatch_is_refused(binding, override, reason):
    result = await binding.check_grant_binding(context(**override), GRANT_ID, grant_store(record()))

    assert result == reason


async def test_client_grant_naming_another_client_is_refused(binding):
    store = grant_store(record(UserId="", ClientId="client-1"))

    result = await binding.check_grant_binding(context(user_id="", client_id="client-2"), GRANT_ID, store)

    assert result == "client_mismatch"


@pytest.mark.parametrize(
    "grant, ctx",
    [
        (record(), context()),
        (record(UserId="", ClientId="client-1"), context(user_id="", client_id="client-1")),
        (
            record(TenantId="tenant-target", ImpersonationSessionId="session-1", OriginalTenantId="tenant-home"),
            context(
                tenant_id="tenant-target",
                impersonated=True,
                impersonation_session_id="session-1",
                original_tenant_id="tenant-home",
            ),
        ),
    ],
    ids=["user", "client", "impersonated"],
)
async def test_agreement_passes(binding, grant, ctx):
    assert await binding.check_grant_binding(ctx, GRANT_ID, grant_store(grant)) is None


async def test_user_grant_ignores_the_token_client_id(binding):
    result = await binding.check_grant_binding(context(client_id="portal"), GRANT_ID, grant_store(record()))

    assert result is None


async def test_empty_and_missing_organization_agree(binding):
    ctx = BlocksContext(tenant_id="tenant-1", user_id="user-1")

    result = await binding.check_grant_binding(ctx, GRANT_ID, grant_store(record(OrganizationId="")))

    assert result is None


async def test_store_failure_propagates(binding):
    store = grant_store(error=RuntimeError("store unavailable"))

    with pytest.raises(RuntimeError):
        await binding.check_grant_binding(context(), GRANT_ID, store)


async def test_refusal_log_has_reason_and_ids_only(binding, caplog):
    ctx = context(tenant_id="tenant-2", user_id="user-2")

    with caplog.at_level(logging.ERROR):
        result = await binding.check_grant_binding(ctx, GRANT_ID, grant_store(record()), "msg-1")

    assert result == "tenant_mismatch"
    text = caplog.text
    for expected in ("msg-1", "tenant_mismatch", "tenant-1", "tenant-2"):
        assert expected in text
    for absent in (GRANT_ID, "user-1", "user-2", security_context_header(ctx)):
        assert absent not in text


# ------------------------------------------------------------------ Azure worker


def azure_worker(observed):
    from blocks_genesis._message.azure.azure_message_worker import AzureMessageWorker

    worker = AzureMessageWorker.__new__(AzureMessageWorker)
    worker._logger = logging.getLogger("test")
    worker._consumer = MagicMock()
    worker._consumer.process_message = AsyncMock(
        side_effect=lambda event_type, body: observed.append(BlocksContextManager.get_context())
    )
    worker._service_bus_client = None
    worker._receivers = []
    worker._active_message_renewals = {}
    worker._tracer = MagicMock()
    worker.start_auto_renewal_task = AsyncMock()
    return worker


def azure_message(ctx, grant_id):
    properties = {
        "TraceId": "",
        "SpanId": "",
        "TenantId": ctx.tenant_id,
        "SecurityContext": security_context_header(ctx),
        "Baggage": "{}",
    }
    if grant_id is not None:
        properties[constants.DELEGATION_GRANT_HEADER] = grant_id

    message = MagicMock()
    message.message_id = "msg-1"
    message.application_properties = properties
    message.body = [json.dumps({"type": "OrderPlaced", "body": "{}"}).encode()]
    return message


def azure_receiver():
    receiver = MagicMock()
    receiver.complete_message = AsyncMock()
    receiver.abandon_message = AsyncMock()
    receiver.dead_letter_message = AsyncMock()
    return receiver


async def run_azure_worker(ctx, grant_id, store):
    observed = []
    worker = azure_worker(observed)
    message = azure_message(ctx, grant_id)
    receiver = azure_receiver()

    with patch(AMW + "get_delegation_grant_store", return_value=store), patch(
        AMW + "get_delegated_token_provider", return_value=MagicMock(invalidate=MagicMock())
    ):
        await worker.message_handler(receiver, message)

    return worker, message, receiver, observed


def assert_azure_refused(worker, message, receiver, store, reason):
    worker._consumer.process_message.assert_not_awaited()
    receiver.dead_letter_message.assert_awaited_once_with(
        message, reason="DelegationGrantMismatch", error_description=reason
    )
    receiver.complete_message.assert_not_awaited()
    receiver.abandon_message.assert_not_awaited()
    store.delete_async.assert_not_awaited()


@pytest.mark.parametrize("override, reason", MISMATCHES)
async def test_azure_worker_refuses_a_mismatched_identity(override, reason):
    store = grant_store(record())

    worker, message, receiver, _ = await run_azure_worker(context(**override), GRANT_ID, store)

    assert_azure_refused(worker, message, receiver, store, reason)


async def test_azure_worker_refuses_a_missing_grant_record():
    store = grant_store(None)

    worker, message, receiver, _ = await run_azure_worker(context(), GRANT_ID, store)

    assert_azure_refused(worker, message, receiver, store, "grant_not_found")


async def test_azure_worker_runs_the_handler_when_the_grant_agrees():
    store = grant_store(record())

    worker, message, receiver, observed = await run_azure_worker(context(), GRANT_ID, store)

    worker._consumer.process_message.assert_awaited_once()
    assert observed[0].tenant_id == "tenant-1"
    receiver.complete_message.assert_awaited_once_with(message)
    receiver.dead_letter_message.assert_not_awaited()
    store.delete_async.assert_awaited_once_with(GRANT_ID)


async def test_azure_worker_without_a_grant_runs_as_before():
    store = grant_store(record())

    worker, message, receiver, observed = await run_azure_worker(context(), None, store)

    # Unchanged on purpose: without a grant the identity still comes from the header alone.
    worker._consumer.process_message.assert_awaited_once()
    assert observed[0].is_authenticated is True
    receiver.complete_message.assert_awaited_once_with(message)
    store.get_async.assert_not_awaited()


async def test_azure_worker_abandons_when_the_grant_store_fails():
    observed = []
    worker = azure_worker(observed)
    message = azure_message(context(), GRANT_ID)
    receiver = azure_receiver()
    store = grant_store(error=RuntimeError("store unavailable"))

    with patch(AMW + "get_delegation_grant_store", return_value=store), patch(
        AMW + "get_delegated_token_provider", return_value=MagicMock(invalidate=MagicMock())
    ):
        with pytest.raises(RuntimeError):
            await worker.message_handler(receiver, message)

    worker._consumer.process_message.assert_not_awaited()
    receiver.abandon_message.assert_awaited_once_with(message)
    receiver.dead_letter_message.assert_not_awaited()
    store.delete_async.assert_not_awaited()


# ------------------------------------------------------------------ RabbitMQ worker


def rabbit_message(ctx, grant_id):
    headers = {
        "TenantId": ctx.tenant_id.encode(),
        "TraceId": b"",
        "SpanId": b"",
        "SecurityContext": security_context_header(ctx).encode(),
        "Baggage": b"{}",
    }
    if grant_id is not None:
        headers[constants.DELEGATION_GRANT_HEADER] = grant_id.encode()

    message = MagicMock()
    message.message_id = "msg-1"
    message.headers = headers
    message.body = json.dumps({"type": "OrderPlaced", "body": "{}"}).encode()
    message.ack = AsyncMock()
    return message


async def run_rabbit_worker(ctx, grant_id, store):
    from blocks_genesis._message.message_configuration import ConsumerSubscription
    from blocks_genesis._message.rabbit_mq.rabbit_message_worker import RabbitMessageWorker

    observed = []
    worker = RabbitMessageWorker.__new__(RabbitMessageWorker)
    worker._tracer = MagicMock()
    worker._consumer = MagicMock()
    worker._consumer.process_message = AsyncMock(
        side_effect=lambda event_type, body: observed.append(BlocksContextManager.get_context())
    )
    message = rabbit_message(ctx, grant_id)

    with patch(RMW + "get_delegation_grant_store", return_value=store), patch(
        RMW + "get_delegated_token_provider", return_value=MagicMock(invalidate=MagicMock())
    ):
        await worker._process_message(message, ConsumerSubscription.bind_to_queue("orders.queue", 3))

    return worker, message, observed


def assert_rabbit_refused(worker, message, store, caplog, reason):
    worker._consumer.process_message.assert_not_awaited()
    message.ack.assert_awaited_once()
    store.delete_async.assert_not_awaited()
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any(reason in r.getMessage() for r in errors)
    assert GRANT_ID not in caplog.text


@pytest.mark.parametrize("override, reason", MISMATCHES)
async def test_rabbit_worker_refuses_a_mismatched_identity(caplog, override, reason):
    store = grant_store(record())

    with caplog.at_level(logging.INFO):
        worker, message, _ = await run_rabbit_worker(context(**override), GRANT_ID, store)

    assert_rabbit_refused(worker, message, store, caplog, reason)


async def test_rabbit_worker_refuses_a_missing_grant_record(caplog):
    store = grant_store(None)

    with caplog.at_level(logging.INFO):
        worker, message, _ = await run_rabbit_worker(context(), GRANT_ID, store)

    assert_rabbit_refused(worker, message, store, caplog, "grant_not_found")


async def test_rabbit_worker_runs_the_handler_when_the_grant_agrees():
    store = grant_store(record())

    worker, message, observed = await run_rabbit_worker(context(), GRANT_ID, store)

    worker._consumer.process_message.assert_awaited_once()
    assert observed[0].tenant_id == "tenant-1"
    message.ack.assert_awaited_once()
    store.delete_async.assert_awaited_once_with(GRANT_ID)


async def test_rabbit_worker_without_a_grant_runs_as_before():
    store = grant_store(record())

    worker, message, observed = await run_rabbit_worker(context(), None, store)

    # Unchanged on purpose: without a grant the identity still comes from the header alone.
    worker._consumer.process_message.assert_awaited_once()
    assert observed[0].is_authenticated is True
    message.ack.assert_awaited_once()
    store.get_async.assert_not_awaited()


async def test_rabbit_worker_acks_without_handling_when_the_grant_store_fails():
    store = grant_store(error=RuntimeError("store unavailable"))

    worker, message, _ = await run_rabbit_worker(context(), GRANT_ID, store)

    worker._consumer.process_message.assert_not_awaited()
    message.ack.assert_awaited_once()
    store.delete_async.assert_not_awaited()
