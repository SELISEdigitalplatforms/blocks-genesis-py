"""A grant lives at most two days, and a grant whose message never left is deleted."""

import asyncio
import logging
from asyncio import Lock
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from blocks_genesis._auth.blocks_context import BlocksContextManager
from blocks_genesis._delegation import constants
from blocks_genesis._delegation.context import AuthClaimsContext, DelegatedTokenContext
from blocks_genesis._delegation.grant_store import DelegationGrantStore, set_delegation_grant_store
from blocks_genesis._message.consumer_message import ConsumerMessage

GRANT_ID = "dg_" + "a" * 64

AMC = "blocks_genesis._message.azure.azure_message_client."
RMC = "blocks_genesis._message.rabbit_mq.rabbit_message_client."

ONE_HOUR = 60 * 60
THIRTY_DAYS = 30 * 24 * ONE_HOUR


@pytest.fixture(autouse=True)
def clean_ambient_state():
    BlocksContextManager.clear_context()
    DelegatedTokenContext.clear()
    AuthClaimsContext.clear()
    yield
    BlocksContextManager.clear_context()
    DelegatedTokenContext.clear()
    AuthClaimsContext.clear()
    set_delegation_grant_store(None)


@pytest.fixture
def grant_store():
    store = MagicMock()
    store.delete_async = AsyncMock()
    set_delegation_grant_store(store)
    return store


class FakeCache:
    def __init__(self):
        self.ttls = {}

    async def add_string_value_async(self, key, value, key_life_span=None):
        self.ttls[key] = key_life_span
        return True


def authenticated_context():
    return BlocksContextManager.create(
        tenant_id="tenant-1",
        user_id="user-1",
        organization_id="org-1",
        is_authenticated=True,
    )


def consumer_message(**overrides):
    payload = {"consumer_name": "orders", "payload": {"value": "v"}, "payload_type": "OrderPlaced"}
    payload.update(overrides)
    return ConsumerMessage(**payload)


def grant_factory(grant_id):
    factory = MagicMock()
    factory.create_for_send_async = AsyncMock(return_value=grant_id)
    return factory


# ------------------------------------------------------------------ lifetime cap


async def test_a_requested_lifetime_above_two_days_is_capped():
    cache = FakeCache()
    store = DelegationGrantStore(cache)

    grant = await store.create_async(authenticated_context(), "1", "s", ttl_seconds=THIRTY_DAYS)

    assert cache.ttls[constants.grant_key(grant)] == constants.DEFAULT_GRANT_TTL_SECONDS


async def test_a_client_grant_lifetime_is_capped_too():
    cache = FakeCache()
    store = DelegationGrantStore(cache)

    grant = await store.create_for_client_async("tenant-1", "client-1", ttl_seconds=THIRTY_DAYS)

    assert cache.ttls[constants.grant_key(grant)] == constants.DEFAULT_GRANT_TTL_SECONDS


async def test_the_cap_is_two_days():
    assert constants.MAX_GRANT_TTL_SECONDS == 2 * 24 * ONE_HOUR == constants.DEFAULT_GRANT_TTL_SECONDS


async def test_a_lifetime_under_the_cap_is_kept():
    cache = FakeCache()
    store = DelegationGrantStore(cache)

    grant = await store.create_async(authenticated_context(), "1", "s", ttl_seconds=ONE_HOUR)

    assert cache.ttls[constants.grant_key(grant)] == ONE_HOUR


@pytest.mark.parametrize("requested", [0, None, -5])
async def test_no_positive_lifetime_gives_the_default(requested):
    cache = FakeCache()
    store = DelegationGrantStore(cache)

    grant = await store.create_async(authenticated_context(), "1", "s", ttl_seconds=requested)

    assert cache.ttls[constants.grant_key(grant)] == constants.DEFAULT_GRANT_TTL_SECONDS


async def test_a_capped_lifetime_is_logged_without_the_grant_id(caplog):
    store = DelegationGrantStore(FakeCache())

    with caplog.at_level(logging.DEBUG, logger="blocks_genesis._delegation.grant_store"):
        grant = await store.create_async(authenticated_context(), "1", "s", ttl_seconds=THIRTY_DAYS)

    assert any("capped" in record.getMessage() for record in caplog.records)
    assert grant not in caplog.text


# ------------------------------------------------------------------ Azure send


async def azure_send(grant_id, send_messages):
    from blocks_genesis._message.azure.azure_message_client import AzureMessageClient

    client = AzureMessageClient.__new__(AzureMessageClient)
    client._sender_locks = defaultdict(Lock)

    sender = MagicMock()
    sender.send_messages = send_messages
    client._get_sender = AsyncMock(return_value=sender)
    client._replace_sender = AsyncMock(return_value=sender)

    activity = MagicMock()
    activity.__enter__ = MagicMock(return_value=activity)
    activity.__exit__ = MagicMock(return_value=False)
    activity.get_trace_id = MagicMock(return_value="trace")
    activity.get_span_id = MagicMock(return_value="span")
    activity.get_all_root_attributes = MagicMock(return_value={})

    with patch(AMC + "Activity", return_value=activity), patch(
        AMC + "get_delegation_grant_factory", return_value=grant_factory(grant_id)
    ), patch(AMC + "BlocksContextManager.get_context", return_value=authenticated_context()), patch(
        AMC + "ServiceBusMessage"
    ):
        return await client._send_to_azure_bus_async(consumer_message())


async def test_azure_send_that_fails_both_attempts_deletes_the_grant_and_raises(grant_store):
    send = AsyncMock(side_effect=RuntimeError("down"))

    with pytest.raises(RuntimeError, match="down"):
        await azure_send(GRANT_ID, send)

    assert send.await_count == 2
    grant_store.delete_async.assert_awaited_once_with(GRANT_ID)


async def test_azure_send_that_succeeds_deletes_nothing(grant_store):
    assert await azure_send(GRANT_ID, AsyncMock()) is True

    grant_store.delete_async.assert_not_awaited()


async def test_azure_send_retry_that_succeeds_deletes_nothing(grant_store):
    send = AsyncMock(side_effect=[RuntimeError("blip"), None])

    assert await azure_send(GRANT_ID, send) is True

    grant_store.delete_async.assert_not_awaited()


async def test_azure_failed_send_without_a_grant_deletes_nothing(grant_store):
    with pytest.raises(RuntimeError):
        await azure_send(None, AsyncMock(side_effect=RuntimeError("down")))

    grant_store.delete_async.assert_not_awaited()


async def test_azure_failed_delete_does_not_mask_the_send_error(grant_store, caplog):
    grant_store.delete_async.side_effect = ValueError("redis gone")

    with pytest.raises(RuntimeError, match="down"):
        await azure_send(GRANT_ID, AsyncMock(side_effect=RuntimeError("down")))

    assert "ValueError" in caplog.text
    assert "redis gone" not in caplog.text
    assert GRANT_ID not in caplog.text


# ------------------------------------------------------------------ RabbitMQ send


def rabbit_client():
    from blocks_genesis._message.message_configuration import MessageConfiguration, RabbitMqConfiguration
    from blocks_genesis._message.rabbit_mq.rabbit_message_client import RabbitMessageClient

    client = RabbitMessageClient.__new__(RabbitMessageClient)
    client._message_config = MessageConfiguration(rabbit_mq_configuration=RabbitMqConfiguration())
    client._ensure_initialized_async = AsyncMock()
    client._rabbit_mq_service = MagicMock()
    return client


async def rabbit_send(grant_id, publish):
    client = rabbit_client()
    client._rabbit_mq_service.channel.default_exchange.publish = publish

    activity = MagicMock()
    activity.__enter__ = MagicMock(return_value=activity)
    activity.__exit__ = MagicMock(return_value=False)
    activity.get_all_root_attributes = MagicMock(return_value={})

    with patch(RMC + "Activity") as activity_cls, patch(
        RMC + "get_delegation_grant_factory", return_value=grant_factory(grant_id)
    ), patch(RMC + "BlocksContextManager.get_context", return_value=authenticated_context()):
        activity_cls.return_value = activity
        activity_cls.get_trace_id.return_value = "trace"
        activity_cls.get_span_id.return_value = "span"
        return await client._send_message_async(consumer_message())


async def test_rabbit_publish_that_raises_deletes_the_grant_and_raises(grant_store):
    with pytest.raises(RuntimeError, match="down"):
        await rabbit_send(GRANT_ID, AsyncMock(side_effect=RuntimeError("down")))

    grant_store.delete_async.assert_awaited_once_with(GRANT_ID)


async def test_rabbit_publish_that_succeeds_deletes_nothing(grant_store):
    assert await rabbit_send(GRANT_ID, AsyncMock()) is True

    grant_store.delete_async.assert_not_awaited()


async def test_rabbit_failed_delete_does_not_mask_the_publish_error(grant_store, caplog):
    grant_store.delete_async.side_effect = ValueError("redis gone")

    with pytest.raises(RuntimeError, match="down"):
        await rabbit_send(GRANT_ID, AsyncMock(side_effect=RuntimeError("down")))

    assert "ValueError" in caplog.text
    assert "redis gone" not in caplog.text


def returned_message(headers):
    message = MagicMock()
    message.exchange = ""
    message.routing_key = "orders"
    message.body = b"{}"
    message.headers = headers
    return message


async def settle_background_tasks():
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.parametrize("header_value", [GRANT_ID, GRANT_ID.encode()])
async def test_rabbit_returned_message_deletes_its_grant(grant_store, header_value):
    client = rabbit_client()

    # aio_pika 10 calls a return callback as (channel, IncomingMessage).
    client._on_message_returned(MagicMock(), returned_message({constants.DELEGATION_GRANT_HEADER: header_value}))
    await settle_background_tasks()

    grant_store.delete_async.assert_awaited_once_with(GRANT_ID)


async def test_rabbit_returned_message_without_a_grant_deletes_nothing(grant_store):
    client = rabbit_client()

    client._on_message_returned(MagicMock(), returned_message({"TenantId": "tenant-1"}))
    await settle_background_tasks()

    grant_store.delete_async.assert_not_awaited()


async def test_rabbit_returned_message_with_a_malformed_grant_deletes_nothing(grant_store):
    client = rabbit_client()

    client._on_message_returned(MagicMock(), returned_message({constants.DELEGATION_GRANT_HEADER: "not-a-grant"}))
    await settle_background_tasks()

    grant_store.delete_async.assert_not_awaited()


async def test_rabbit_returned_message_delete_failure_is_logged_not_raised(grant_store, caplog):
    grant_store.delete_async.side_effect = ValueError("redis gone")
    client = rabbit_client()

    client._on_message_returned(MagicMock(), returned_message({constants.DELEGATION_GRANT_HEADER: GRANT_ID}))
    await settle_background_tasks()

    grant_store.delete_async.assert_awaited_once_with(GRANT_ID)
    assert "ValueError" in caplog.text
    assert "redis gone" not in caplog.text
    assert GRANT_ID not in caplog.text
