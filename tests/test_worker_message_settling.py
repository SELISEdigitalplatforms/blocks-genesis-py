"""Both workers settle every message exactly once, clear their context, and release its grant safely."""

import contextlib
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

ABSENT = object()
ENVELOPE = json.dumps({"type": "OrderPlaced", "body": "{}"})
INVALID_SECURITY_CONTEXTS = ["{bad", "[1]", "null", '{"Roles": "x"}']
MISSING_OR_EMPTY_SECURITY_CONTEXTS = [ABSENT, "", "{}"]


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


def grant_store(result=None, error=None):
    get_async = AsyncMock(side_effect=error) if error else AsyncMock(return_value=result)
    return MagicMock(get_async=get_async, delete_async=AsyncMock())


def security_context_header(ctx=None):
    return json.dumps(BlocksContextManager.create_sanitized_for_transport(ctx or context()))


def failing_handler(error=None):
    async def handler(event_type, body):
        raise error or RuntimeError("handler failed")

    return handler


def assert_context_cleared():
    assert BlocksContextManager.get_context() is None
    assert DelegatedTokenContext.current() is None


def records_at(caplog, level):
    return [r for r in caplog.records if r.levelno == level]


# ------------------------------------------------------------------ shared parser


@pytest.mark.parametrize("raw", [None, "", "{}"])
def test_parser_treats_missing_or_empty_as_valid_without_context(raw):
    from blocks_genesis._message.security_context_header import parse_security_context_header

    assert parse_security_context_header(raw) == (True, None)


@pytest.mark.parametrize("raw", INVALID_SECURITY_CONTEXTS, ids=["bad-json", "list", "null", "validation"])
def test_parser_rejects_an_invalid_header(raw):
    from blocks_genesis._message.security_context_header import parse_security_context_header

    assert parse_security_context_header(raw) == (False, None)


def test_parser_restores_a_valid_header():
    from blocks_genesis._message.security_context_header import parse_security_context_header

    valid, ctx = parse_security_context_header(security_context_header())

    assert valid is True
    assert ctx.tenant_id == "tenant-1"
    assert ctx.user_id == "user-1"


# ------------------------------------------------------------------ RabbitMQ worker


def rabbit_message(security_context=None, body=ENVELOPE, grant_id=GRANT_ID, baggage=b"{}"):
    if security_context is None:
        security_context = security_context_header().encode()
    headers = {"TenantId": b"tenant-1", "TraceId": b"", "SpanId": b"", "Baggage": baggage}
    if security_context is not ABSENT:
        headers["SecurityContext"] = (
            security_context.encode() if isinstance(security_context, str) else security_context
        )
    if grant_id is not None:
        headers[constants.DELEGATION_GRANT_HEADER] = grant_id.encode()

    message = MagicMock()
    message.message_id = "msg-1"
    message.headers = headers
    message.body = body.encode() if isinstance(body, str) else body
    message.ack = AsyncMock()
    return message


def rabbit_worker(handler=None):
    from blocks_genesis._message.rabbit_mq.rabbit_message_worker import RabbitMessageWorker

    worker = RabbitMessageWorker.__new__(RabbitMessageWorker)
    worker._tracer = MagicMock()
    worker._consumer = MagicMock()
    worker._consumer.process_message = AsyncMock(side_effect=handler)
    return worker


async def run_rabbit(worker, message, store, provider=None):
    from blocks_genesis._message.message_configuration import ConsumerSubscription

    with patch(RMW + "get_delegation_grant_store", return_value=store), patch(
        RMW + "get_delegated_token_provider", return_value=provider or MagicMock()
    ):
        await worker._process_message(message, ConsumerSubscription.bind_to_queue("orders.queue", 3))


async def test_rabbit_undecodable_body_is_acked_and_context_cleared(caplog):
    worker = rabbit_worker()
    message = rabbit_message(body=b"\xff\xfe")

    with caplog.at_level(logging.INFO):
        await run_rabbit(worker, message, grant_store(record()))

    message.ack.assert_awaited_once()
    worker._consumer.process_message.assert_not_awaited()
    assert_context_cleared()
    assert len(records_at(caplog, logging.ERROR)) == 1


async def test_rabbit_undecodable_header_is_acked_and_context_cleared(caplog):
    worker = rabbit_worker()
    message = rabbit_message(security_context=b"\xff")

    with caplog.at_level(logging.INFO):
        await run_rabbit(worker, message, grant_store(record()))

    message.ack.assert_awaited_once()
    worker._consumer.process_message.assert_not_awaited()
    assert_context_cleared()
    assert len(records_at(caplog, logging.ERROR)) == 1


async def test_rabbit_next_message_starts_with_a_clean_context():
    seen = []
    worker = rabbit_worker(lambda event_type, body: seen.append(BlocksContextManager.get_context()))
    store = grant_store(record())

    # The first message must not leave its context behind, whatever happens to it.
    with contextlib.suppress(Exception):
        await run_rabbit(worker, rabbit_message(body=b"\xff\xfe"), store)
    await run_rabbit(worker, rabbit_message(security_context=ABSENT, grant_id=None), store)

    assert seen == [None]


async def test_rabbit_handler_failure_releases_the_grant_after_the_ack():
    order = []
    worker = rabbit_worker(failing_handler())
    message = rabbit_message()
    message.ack = AsyncMock(side_effect=lambda: order.append("ack"))
    store = grant_store(record())
    store.delete_async = AsyncMock(side_effect=lambda grant_id: order.append("delete"))
    provider = MagicMock()

    await run_rabbit(worker, message, store, provider)

    message.ack.assert_awaited_once()
    store.delete_async.assert_awaited_once_with(GRANT_ID)
    provider.invalidate.assert_called_once_with(GRANT_ID)
    assert order == ["ack", "delete"]


@pytest.mark.parametrize("raw", INVALID_SECURITY_CONTEXTS, ids=["bad-json", "list", "null", "validation"])
async def test_rabbit_invalid_security_context_acks_without_running_the_handler(caplog, raw):
    worker = rabbit_worker()
    message = rabbit_message(security_context=raw.encode())
    store = grant_store(record())

    with caplog.at_level(logging.INFO):
        await run_rabbit(worker, message, store)

    message.ack.assert_awaited_once()
    worker._consumer.process_message.assert_not_awaited()
    store.get_async.assert_not_awaited()
    store.delete_async.assert_not_awaited()
    assert any("security_context_invalid" in r.getMessage() for r in records_at(caplog, logging.ERROR))
    assert raw not in caplog.text
    assert_context_cleared()


@pytest.mark.parametrize("raw", MISSING_OR_EMPTY_SECURITY_CONTEXTS, ids=["absent", "empty", "empty-object"])
async def test_rabbit_missing_or_empty_security_context_runs_the_handler(raw):
    seen = []
    worker = rabbit_worker(lambda event_type, body: seen.append(BlocksContextManager.get_context()))
    security_context = raw if raw is ABSENT else raw.encode()
    message = rabbit_message(security_context=security_context, grant_id=None)

    await run_rabbit(worker, message, grant_store(record()))

    worker._consumer.process_message.assert_awaited_once()
    assert seen == [None]
    message.ack.assert_awaited_once()


@pytest.mark.parametrize("baggage", [b"[1, 2]", b'"x"'], ids=["list", "string"])
async def test_rabbit_non_object_baggage_is_ignored(caplog, baggage):
    worker = rabbit_worker()
    message = rabbit_message(baggage=baggage)

    with caplog.at_level(logging.INFO):
        await run_rabbit(worker, message, grant_store(record()))

    worker._consumer.process_message.assert_awaited_once()
    message.ack.assert_awaited_once()
    assert len(records_at(caplog, logging.WARNING)) == 1


async def test_rabbit_release_failure_still_acks_and_clears_context(caplog):
    worker = rabbit_worker()
    message = rabbit_message()
    store = grant_store(record())
    store.delete_async = AsyncMock(side_effect=RuntimeError("store unavailable"))

    with caplog.at_level(logging.INFO):
        await run_rabbit(worker, message, store)

    message.ack.assert_awaited_once()
    store.delete_async.assert_awaited_once_with(GRANT_ID)
    assert_context_cleared()
    assert GRANT_ID not in caplog.text


async def test_rabbit_ack_failure_keeps_the_grant_and_clears_context():
    worker = rabbit_worker()
    message = rabbit_message()
    message.ack = AsyncMock(side_effect=RuntimeError("channel closed"))
    store = grant_store(record())

    with pytest.raises(RuntimeError):
        await run_rabbit(worker, message, store)

    worker._consumer.process_message.assert_awaited_once()
    store.delete_async.assert_not_awaited()
    assert_context_cleared()


# ------------------------------------------------------------------ Azure worker


def azure_message(security_context=None, body=ENVELOPE, grant_id=GRANT_ID, baggage="{}", delivery_count=0):
    if security_context is None:
        security_context = security_context_header()
    properties = {"TraceId": "", "SpanId": "", "TenantId": "tenant-1", "Baggage": baggage}
    if security_context is not ABSENT:
        properties["SecurityContext"] = security_context
    if grant_id is not None:
        properties[constants.DELEGATION_GRANT_HEADER] = grant_id

    message = MagicMock()
    message.message_id = "msg-1"
    message.delivery_count = delivery_count
    message.application_properties = properties
    message.body = [body.encode() if isinstance(body, str) else body]
    return message


def azure_worker(handler=None):
    from blocks_genesis._message.azure.azure_message_worker import AzureMessageWorker

    worker = AzureMessageWorker.__new__(AzureMessageWorker)
    worker._logger = logging.getLogger("test")
    worker._consumer = MagicMock()
    worker._consumer.process_message = AsyncMock(side_effect=handler)
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


async def run_azure(worker, receiver, message, store, provider=None, **kwargs):
    kwargs.setdefault("max_delivery_count", 2)
    if kwargs["max_delivery_count"] is ABSENT:
        del kwargs["max_delivery_count"]

    with patch(AMW + "get_delegation_grant_store", return_value=store), patch(
        AMW + "get_delegated_token_provider", return_value=provider or MagicMock()
    ):
        await worker.message_handler(receiver, message, **kwargs)


@pytest.mark.parametrize("delivery_count", [0, None])
async def test_azure_failure_before_the_final_attempt_is_abandoned_once(delivery_count):
    worker = azure_worker(failing_handler())
    receiver = azure_receiver()
    message = azure_message(delivery_count=delivery_count)
    store = grant_store(record())

    await run_azure(worker, receiver, message, store)

    receiver.abandon_message.assert_awaited_once_with(message)
    receiver.dead_letter_message.assert_not_awaited()
    receiver.complete_message.assert_not_awaited()
    store.delete_async.assert_not_awaited()


async def test_azure_failure_on_the_final_attempt_is_dead_lettered_and_releases_the_grant():
    order = []
    worker = azure_worker(failing_handler(RuntimeError("handler failed")))
    receiver = azure_receiver()
    receiver.dead_letter_message = AsyncMock(side_effect=lambda *a, **kw: order.append("dead_letter"))
    message = azure_message(delivery_count=1)
    store = grant_store(record())
    store.delete_async = AsyncMock(side_effect=lambda grant_id: order.append("delete"))
    provider = MagicMock()

    await run_azure(worker, receiver, message, store, provider)

    receiver.dead_letter_message.assert_awaited_once_with(
        message, reason="ProcessingFailed", error_description="RuntimeError; attempt 2 of 2"
    )
    receiver.abandon_message.assert_not_awaited()
    store.delete_async.assert_awaited_once_with(GRANT_ID)
    provider.invalidate.assert_called_once_with(GRANT_ID)
    assert order == ["dead_letter", "delete"]


async def test_azure_final_attempt_without_max_is_abandoned():
    worker = azure_worker(failing_handler())
    receiver = azure_receiver()
    message = azure_message(delivery_count=9)

    await run_azure(worker, receiver, message, grant_store(record()), max_delivery_count=ABSENT)

    receiver.abandon_message.assert_awaited_once_with(message)
    receiver.dead_letter_message.assert_not_awaited()


async def test_azure_final_attempt_failure_before_the_grant_check_keeps_the_grant():
    worker = azure_worker()
    receiver = azure_receiver()
    message = azure_message(delivery_count=1)
    store = grant_store(error=RuntimeError("store unavailable"))

    await run_azure(worker, receiver, message, store)

    worker._consumer.process_message.assert_not_awaited()
    receiver.dead_letter_message.assert_awaited_once_with(
        message, reason="ProcessingFailed", error_description="RuntimeError; attempt 2 of 2"
    )
    receiver.abandon_message.assert_not_awaited()
    store.delete_async.assert_not_awaited()


async def test_azure_dead_letter_failure_keeps_the_grant(caplog):
    worker = azure_worker(failing_handler())
    receiver = azure_receiver()
    receiver.dead_letter_message = AsyncMock(side_effect=RuntimeError("lock lost"))
    message = azure_message(delivery_count=1)
    store = grant_store(record())

    with caplog.at_level(logging.INFO):
        await run_azure(worker, receiver, message, store)

    receiver.dead_letter_message.assert_awaited_once()
    receiver.abandon_message.assert_not_awaited()
    store.delete_async.assert_not_awaited()
    assert len(records_at(caplog, logging.WARNING)) == 1


@pytest.mark.parametrize("raw", INVALID_SECURITY_CONTEXTS, ids=["bad-json", "list", "null", "validation"])
async def test_azure_invalid_security_context_is_dead_lettered(caplog, raw):
    worker = azure_worker()
    receiver = azure_receiver()
    message = azure_message(security_context=raw)
    store = grant_store(record())

    with caplog.at_level(logging.INFO):
        await run_azure(worker, receiver, message, store)

    receiver.dead_letter_message.assert_awaited_once_with(
        message, reason="InvalidSecurityContext", error_description="security_context_invalid"
    )
    worker._consumer.process_message.assert_not_awaited()
    receiver.complete_message.assert_not_awaited()
    receiver.abandon_message.assert_not_awaited()
    store.get_async.assert_not_awaited()
    store.delete_async.assert_not_awaited()
    assert any("security_context_invalid" in r.getMessage() for r in records_at(caplog, logging.ERROR))
    assert raw not in caplog.text
    assert_context_cleared()


async def test_azure_invalid_security_context_on_the_final_attempt_is_dead_lettered_once():
    worker = azure_worker()
    receiver = azure_receiver()
    message = azure_message(security_context="{bad", delivery_count=1)

    await run_azure(worker, receiver, message, grant_store(record()))

    receiver.dead_letter_message.assert_awaited_once_with(
        message, reason="InvalidSecurityContext", error_description="security_context_invalid"
    )
    receiver.abandon_message.assert_not_awaited()


@pytest.mark.parametrize("raw", MISSING_OR_EMPTY_SECURITY_CONTEXTS, ids=["absent", "empty", "empty-object"])
async def test_azure_missing_or_empty_security_context_runs_the_handler(raw):
    seen = []
    worker = azure_worker(lambda event_type, body: seen.append(BlocksContextManager.get_context()))
    receiver = azure_receiver()
    message = azure_message(security_context=raw, grant_id=None)

    await run_azure(worker, receiver, message, grant_store(record()))

    assert seen == [None]
    receiver.complete_message.assert_awaited_once_with(message)
    receiver.dead_letter_message.assert_not_awaited()


async def test_azure_non_object_baggage_is_ignored(caplog):
    worker = azure_worker()
    receiver = azure_receiver()
    message = azure_message(baggage="[1, 2]")

    with caplog.at_level(logging.INFO):
        await run_azure(worker, receiver, message, grant_store(record()))

    worker._consumer.process_message.assert_awaited_once()
    receiver.complete_message.assert_awaited_once_with(message)
    receiver.abandon_message.assert_not_awaited()
    # Only the worker's own warnings: OpenTelemetry warns about the OK status description.
    assert len([r for r in records_at(caplog, logging.WARNING) if r.name == "test"]) == 1


async def test_azure_undecodable_property_is_dead_lettered_and_context_cleared():
    worker = azure_worker()
    receiver = azure_receiver()
    message = azure_message(delivery_count=0)
    message.application_properties = {
        b"SecurityContext": b"\xff",
        constants.DELEGATION_GRANT_HEADER.encode(): GRANT_ID.encode(),
    }
    store = grant_store(record())

    await run_azure(worker, receiver, message, store)

    receiver.dead_letter_message.assert_awaited_once()
    assert receiver.dead_letter_message.await_args.kwargs["reason"] == "UndecodableMessage"
    receiver.abandon_message.assert_not_awaited()
    worker._consumer.process_message.assert_not_awaited()
    store.delete_async.assert_not_awaited()
    assert_context_cleared()
    assert "msg-1" not in worker._active_message_renewals


async def test_azure_undecodable_body_after_a_passed_check_releases_the_grant():
    worker = azure_worker()
    receiver = azure_receiver()
    message = azure_message(body=b"\xff\xfe", delivery_count=0)
    store = grant_store(record())
    provider = MagicMock()

    await run_azure(worker, receiver, message, store, provider)

    receiver.dead_letter_message.assert_awaited_once()
    assert receiver.dead_letter_message.await_args.kwargs["reason"] == "UndecodableMessage"
    receiver.abandon_message.assert_not_awaited()
    worker._consumer.process_message.assert_not_awaited()
    store.delete_async.assert_awaited_once_with(GRANT_ID)
    provider.invalidate.assert_called_once_with(GRANT_ID)
    assert_context_cleared()


async def test_azure_handler_unicode_error_takes_the_failure_path():
    # Only the worker's own decoding counts as undecodable; a handler's error is retried as usual.
    error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    worker = azure_worker(failing_handler(error))
    receiver = azure_receiver()
    message = azure_message(delivery_count=0)

    await run_azure(worker, receiver, message, grant_store(record()))

    receiver.abandon_message.assert_awaited_once_with(message)
    receiver.dead_letter_message.assert_not_awaited()


async def test_azure_release_failure_after_complete_does_not_settle_again(caplog):
    worker = azure_worker()
    receiver = azure_receiver()
    message = azure_message()
    store = grant_store(record())
    store.delete_async = AsyncMock(side_effect=RuntimeError("store unavailable"))

    with caplog.at_level(logging.INFO):
        await run_azure(worker, receiver, message, store)

    receiver.complete_message.assert_awaited_once_with(message)
    receiver.abandon_message.assert_not_awaited()
    receiver.dead_letter_message.assert_not_awaited()
    assert GRANT_ID not in caplog.text
    assert_context_cleared()


async def test_azure_run_passes_each_entity_max_delivery_count():
    from blocks_genesis._message.azure.azure_message_worker import AzureMessageWorker
    from blocks_genesis._message.message_configuration import (
        AzureServiceBusConfiguration,
        MessageConfiguration,
    )

    config = MessageConfiguration(connection="sb://h", service_name="svc")
    config.azure_service_bus_configuration = AzureServiceBusConfiguration(
        queues=["q1"],
        topics=["t1"],
        queue_max_delivery_count=3,
        topic_subscription_max_delivery_count=5,
    )
    worker = AzureMessageWorker(config)
    worker._service_bus_client = MagicMock()
    worker.safe_receiver_wrapper = AsyncMock()

    await worker.run()

    calls = {c.args[1]: c.kwargs.get("max_delivery_count") for c in worker.safe_receiver_wrapper.await_args_list}
    assert calls == {"q1": 3, "t1": 5}
