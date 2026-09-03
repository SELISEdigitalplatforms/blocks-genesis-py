"""Both transports: the send stamps the header, the worker reads it, the settle releases it."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from blocks_genesis._auth.blocks_context import BlocksContextManager
from blocks_genesis._delegation import constants
from blocks_genesis._delegation.context import AuthClaimsContext, DelegatedTokenContext
from blocks_genesis._message.consumer_message import ConsumerMessage

GRANT_ID = "dg_" + "a" * 64

AMC = "blocks_genesis._message.azure.azure_message_client."
RMC = "blocks_genesis._message.rabbit_mq.rabbit_message_client."
AMW = "blocks_genesis._message.azure.azure_message_worker."
RMW = "blocks_genesis._message.rabbit_mq.rabbit_message_worker."


@pytest.fixture(autouse=True)
def clean_ambient_state():
    BlocksContextManager.clear_context()
    DelegatedTokenContext.clear()
    AuthClaimsContext.clear()
    yield
    BlocksContextManager.clear_context()
    DelegatedTokenContext.clear()
    AuthClaimsContext.clear()


def consumer_message(**overrides):
    payload = {
        "consumer_name": "orders",
        "payload": {"value": "v"},
        "payload_type": "OrderPlaced",
    }
    payload.update(overrides)
    return ConsumerMessage(**payload)


def grant_factory(grant_id):
    factory = MagicMock()
    factory.create_for_send_async = AsyncMock(return_value=grant_id)
    return factory


# ------------------------------------------------------------------ Azure send


async def azure_send(grant_id, message):
    from blocks_genesis._message.azure.azure_message_client import AzureMessageClient

    client = AzureMessageClient.__new__(AzureMessageClient)

    sender = MagicMock()
    sender.send_messages = AsyncMock()
    client._get_sender = AsyncMock(return_value=sender)

    factory = grant_factory(grant_id)

    activity = MagicMock()
    activity.__enter__ = MagicMock(return_value=activity)
    activity.__exit__ = MagicMock(return_value=False)
    activity.get_trace_id = MagicMock(return_value="trace")
    activity.get_span_id = MagicMock(return_value="span")
    activity.get_all_root_attributes = MagicMock(return_value={})

    with patch(AMC + "Activity", return_value=activity), patch(
        AMC + "get_delegation_grant_factory", return_value=factory
    ), patch(
        AMC + "BlocksContextManager.get_context",
        return_value=BlocksContextManager.create(tenant_id="tenant-1", user_id="user-1", is_authenticated=True),
    ), patch(AMC + "ServiceBusMessage") as sb_message:
        await client._send_to_azure_bus_async(message)

    return sb_message.call_args.kwargs["application_properties"], factory


async def test_azure_send_stamps_the_delegation_grant_header():
    properties, _ = await azure_send(GRANT_ID, consumer_message())

    assert properties[constants.DELEGATION_GRANT_HEADER] == GRANT_ID
    # SecurityContext is still sent: it remains the context and tracing channel.
    assert "SecurityContext" in properties


async def test_azure_send_omits_the_header_when_there_is_no_grant():
    properties, _ = await azure_send(None, consumer_message())

    assert constants.DELEGATION_GRANT_HEADER not in properties


async def test_azure_send_passes_the_ttl_override_to_the_factory():
    _, factory = await azure_send(GRANT_ID, consumer_message(delegation_ttl_seconds=5 * 3600))

    factory.create_for_send_async.assert_awaited_once_with(5 * 3600)


# ------------------------------------------------------------------ Rabbit send


async def rabbit_send(grant_id, message):
    from blocks_genesis._message.rabbit_mq.rabbit_message_client import RabbitMessageClient
    from blocks_genesis._message.message_configuration import (
        MessageConfiguration,
        RabbitMqConfiguration,
    )

    client = RabbitMessageClient.__new__(RabbitMessageClient)
    client._message_config = MessageConfiguration(rabbit_mq_configuration=RabbitMqConfiguration())
    client._ensure_initialized_async = AsyncMock()

    default_exchange = MagicMock()
    default_exchange.publish = AsyncMock()
    channel = MagicMock()
    channel.default_exchange = default_exchange
    client._rabbit_mq_service = MagicMock(channel=channel)

    factory = grant_factory(grant_id)

    activity = MagicMock()
    activity.__enter__ = MagicMock(return_value=activity)
    activity.__exit__ = MagicMock(return_value=False)
    activity.get_all_root_attributes = MagicMock(return_value={})

    with patch(RMC + "Activity", return_value=activity) as activity_cls, patch(
        RMC + "get_delegation_grant_factory", return_value=factory
    ), patch(
        RMC + "BlocksContextManager.get_context",
        return_value=BlocksContextManager.create(tenant_id="tenant-1", user_id="user-1", is_authenticated=True),
    ), patch(RMC + "aio_pika.Message") as pika_message:
        activity_cls.get_trace_id = MagicMock(return_value="trace")
        activity_cls.get_span_id = MagicMock(return_value="span")
        await client._send_message_async(message)

    return pika_message.call_args.kwargs["headers"], factory


async def test_rabbit_send_stamps_the_delegation_grant_header():
    headers, _ = await rabbit_send(GRANT_ID, consumer_message())

    assert headers[constants.DELEGATION_GRANT_HEADER] == GRANT_ID
    assert "SecurityContext" in headers


async def test_rabbit_send_omits_the_header_when_there_is_no_grant():
    headers, _ = await rabbit_send(None, consumer_message())

    assert constants.DELEGATION_GRANT_HEADER not in headers


async def test_rabbit_send_passes_the_ttl_override_to_the_factory():
    _, factory = await rabbit_send(GRANT_ID, consumer_message(delegation_ttl_seconds=7 * 3600))

    factory.create_for_send_async.assert_awaited_once_with(7 * 3600)


# ------------------------------------------------------------------ Rabbit worker


def rabbit_incoming(body, grant_id):
    headers = {
        "TenantId": b"tenant-1",
        "TraceId": b"",
        "SpanId": b"",
        "SecurityContext": json.dumps({"tenant_id": "tenant-1", "user_id": "user-1"}).encode(),
        "Baggage": b"{}",
    }
    if grant_id is not None:
        headers[constants.DELEGATION_GRANT_HEADER] = grant_id.encode()

    message = MagicMock()
    message.headers = headers
    message.body = body.encode()
    message.ack = AsyncMock()
    return message


async def run_rabbit_worker(body, grant_id, observed=None):
    from blocks_genesis._message.rabbit_mq.rabbit_message_worker import RabbitMessageWorker
    from blocks_genesis._message.message_configuration import ConsumerSubscription

    worker = RabbitMessageWorker.__new__(RabbitMessageWorker)
    worker._tracer = MagicMock()
    span = MagicMock()
    span.__enter__ = MagicMock(return_value=span)
    span.__exit__ = MagicMock(return_value=False)
    worker._tracer.start_as_current_span = MagicMock(return_value=span)

    async def process_message(event_type, event_body):
        if observed is not None:
            observed.append(DelegatedTokenContext.current())
        if event_type == "Explode":
            raise RuntimeError("handler failed")

    worker._consumer = MagicMock(process_message=process_message)

    store = MagicMock(delete_async=AsyncMock())
    provider = MagicMock(invalidate=MagicMock())

    subscription = ConsumerSubscription.bind_to_queue("orders.queue", 3)
    message = rabbit_incoming(body, grant_id)

    with patch(RMW + "get_delegation_grant_store", return_value=store), patch(
        RMW + "get_delegated_token_provider", return_value=provider
    ):
        await worker._process_message(message, subscription)

    return message, store, provider


async def test_rabbit_worker_releases_the_grant_after_a_successful_settle():
    envelope = json.dumps({"type": "OrderPlaced", "body": "{}"})
    message, store, provider = await run_rabbit_worker(envelope, GRANT_ID)

    message.ack.assert_awaited_once()
    store.delete_async.assert_awaited_once_with(GRANT_ID)
    provider.invalidate.assert_called_once_with(GRANT_ID)


async def test_rabbit_worker_retains_the_grant_when_the_handler_fails():
    envelope = json.dumps({"type": "Explode", "body": "{}"})
    message, store, provider = await run_rabbit_worker(envelope, GRANT_ID)

    # The ack still happens (existing behaviour), but a failed run must keep its grant so a
    # redelivery can still mint a token.
    message.ack.assert_awaited_once()
    store.delete_async.assert_not_awaited()
    provider.invalidate.assert_not_called()


async def test_rabbit_worker_exposes_the_grant_to_the_handler():
    observed = []
    envelope = json.dumps({"type": "OrderPlaced", "body": "{}"})

    await run_rabbit_worker(envelope, GRANT_ID, observed=observed)

    assert observed == [GRANT_ID]


async def test_rabbit_worker_does_not_touch_the_store_without_a_grant():
    envelope = json.dumps({"type": "OrderPlaced", "body": "{}"})
    _, store, provider = await run_rabbit_worker(envelope, None)

    store.delete_async.assert_not_awaited()
    provider.invalidate.assert_not_called()


async def test_rabbit_worker_ignores_a_malformed_grant_header():
    observed = []
    envelope = json.dumps({"type": "OrderPlaced", "body": "{}"})

    # A malformed id fails closed: the handler sees no grant at all.
    await run_rabbit_worker(envelope, "dg_short", observed=observed)

    assert observed == [None]
