from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientResponseError

from blocks_genesis._auth.blocks_context import BlocksContext
from blocks_genesis._subscription.client import SubscriptionClient
from blocks_genesis._subscription.models import EntitlementsSnapshot, UsageResult

SC = "blocks_genesis._subscription.client."


def _ctx(tenant_id="t1", oauth_token="tok"):
    return BlocksContext(tenant_id=tenant_id, oauth_token=oauth_token, is_authenticated=True)


class _FakeResponse:
    def __init__(self, status, json_body=None, text_body=""):
        self.status = status
        self._json_body = json_body
        self._text_body = text_body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._json_body

    async def text(self):
        return self._text_body

    def raise_for_status(self):
        if self.status >= 400:
            raise ClientResponseError(request_info=MagicMock(), history=(), status=self.status)


def _client(get_response=None, post_response=None):
    c = SubscriptionClient.__new__(SubscriptionClient)
    c._base_url = "https://utilities.seliseblocks.com/api"
    session = MagicMock()
    if get_response is not None:
        session.get = MagicMock(return_value=get_response)
    if post_response is not None:
        session.post = MagicMock(return_value=post_response)
    c._session = session
    return c


# ---------------- get_instance / initialize ----------------


def test_get_instance_not_initialized():
    SubscriptionClient._instance = None
    with pytest.raises(RuntimeError):
        SubscriptionClient.get_instance()


def test_initialize_and_get_instance():
    SubscriptionClient._instance = None
    with patch(SC + "aiohttp.ClientSession"):
        SubscriptionClient.initialize("https://utilities.seliseblocks.com/api")
    assert SubscriptionClient.get_instance() is SubscriptionClient._instance
    SubscriptionClient._instance = None


def test_initialize_idempotent():
    SubscriptionClient._instance = None
    with patch(SC + "aiohttp.ClientSession"):
        SubscriptionClient.initialize("https://utilities.seliseblocks.com/api")
        first = SubscriptionClient._instance
        SubscriptionClient.initialize("https://utilities.seliseblocks.com/api")
    assert SubscriptionClient._instance is first
    SubscriptionClient._instance = None


@pytest.mark.asyncio
async def test_close():
    c = _client()
    c._session.close = AsyncMock()
    await c.close()
    c._session.close.assert_awaited_once()


# ---------------- _headers ----------------


def test_headers_missing_context():
    c = _client()
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = None
        with pytest.raises(RuntimeError):
            c._headers()


def test_headers_missing_oauth_token():
    c = _client()
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = _ctx(oauth_token="")
        with pytest.raises(RuntimeError):
            c._headers()


def test_headers_oauth_token_override():
    c = _client()
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = _ctx(oauth_token="")
        headers = c._headers(oauth_token="real-bearer-token")
    assert headers["Authorization"] == "Bearer real-bearer-token"


def test_headers_oauth_token_override_with_no_context_at_all():
    c = _client()
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = None
        with pytest.raises(RuntimeError):
            # oauth_token override alone isn't enough -- tenant_id still needs to come from somewhere.
            c._headers(oauth_token="real-bearer-token")


def test_headers_oauth_token_and_tenant_override_with_no_context_at_all():
    c = _client()
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = None
        headers = c._headers(tenant_id="project-key", oauth_token="real-bearer-token")
    assert headers["Authorization"] == "Bearer real-bearer-token"
    assert headers["x-blocks-key"] == "project-key"


def test_headers_user_wise_default():
    c = _client()
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = _ctx(tenant_id="ctx-tenant")
        headers = c._headers()
    assert headers["x-blocks-key"] == "ctx-tenant"
    assert headers["Authorization"] == "Bearer tok"


def test_headers_tenant_override():
    c = _client()
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = _ctx(tenant_id="ctx-tenant")
        headers = c._headers(tenant_id="other-project-key")
    assert headers["x-blocks-key"] == "other-project-key"


def test_headers_tenant_override_with_no_context_tenant():
    c = _client()
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = _ctx(tenant_id="")
        headers = c._headers(tenant_id="only-known-project-key")
    assert headers["x-blocks-key"] == "only-known-project-key"


def test_headers_no_tenant_anywhere():
    c = _client()
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = _ctx(tenant_id="")
        with pytest.raises(RuntimeError):
            c._headers()


# ---------------- get_usage_current ----------------


@pytest.mark.asyncio
async def test_get_usage_current_200():
    body = {
        "data": [
            {
                "allowed": True,
                "meterKey": "ai-messages",
                "used": 5,
                "remaining": 95,
                "overage": 0,
                "replayed": False,
            },
            {
                "allowed": False,
                "meterKey": "exports",
                "used": 10,
                "remaining": 0,
                "overage": 0,
                "replayed": False,
            },
        ]
    }
    resp = _FakeResponse(200, json_body=body)
    c = _client(get_response=resp)
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = _ctx()
        result = await c.get_usage_current()
    assert len(result) == 2
    assert all(isinstance(r, UsageResult) for r in result)
    assert result[0].meter_key == "ai-messages"
    assert result[1].allowed is False


@pytest.mark.asyncio
async def test_get_usage_current_404_returns_empty_list():
    resp = _FakeResponse(404)
    c = _client(get_response=resp)
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = _ctx()
        result = await c.get_usage_current()
    assert result == []


@pytest.mark.asyncio
async def test_get_usage_current_200_with_subscription_not_found_envelope_returns_empty_list():
    # Some errors may arrive as HTTP 200 + success:false instead of a real 404 status.
    body = {
        "success": False,
        "data": None,
        "error": {
            "code": "subscription_not_found",
            "message": "This organization has no active subscription.",
            "fields": None,
            "traceId": "0HNO2JTUFHLHL:00000009",
        },
        "meta": {
            "correlationId": "0HNO2JTUFHLHL:00000009",
            "timestampUtc": "2026-08-25T17:34:28.6524789Z",
            "replayed": False,
        },
    }
    resp = _FakeResponse(200, json_body=body)
    c = _client(get_response=resp)
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = _ctx()
        result = await c.get_usage_current()
    assert result == []


@pytest.mark.asyncio
async def test_get_usage_current_oauth_token_override_forwarded():
    body = {"data": []}
    resp = _FakeResponse(200, json_body=body)
    c = _client(get_response=resp)
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = _ctx(oauth_token="")
        await c.get_usage_current(oauth_token="real-bearer-token")
    _, kwargs = c._session.get.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer real-bearer-token"


@pytest.mark.asyncio
async def test_get_usage_current_503_returns_none():
    resp = _FakeResponse(503, text_body="unavailable")
    c = _client(get_response=resp)
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = _ctx()
        result = await c.get_usage_current()
    assert result is None


@pytest.mark.asyncio
async def test_get_usage_current_organization_id_query_param():
    body = {"data": []}
    resp = _FakeResponse(200, json_body=body)
    c = _client(get_response=resp)
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = _ctx()
        await c.get_usage_current(organization_id="org-x")
    _, kwargs = c._session.get.call_args
    assert kwargs["params"] == {"organizationId": "org-x"}


@pytest.mark.asyncio
async def test_get_usage_current_no_organization_id_omits_query_param():
    body = {"data": []}
    resp = _FakeResponse(200, json_body=body)
    c = _client(get_response=resp)
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = _ctx()
        await c.get_usage_current()
    _, kwargs = c._session.get.call_args
    assert kwargs["params"] is None


# ---------------- get_entitlements ----------------


@pytest.mark.asyncio
async def test_get_entitlements_200():
    body = {
        "data": {
            "hasSubscription": True,
            "status": "Active",
            "planCode": "pro",
            "entitlements": [
                {"key": "chat", "allowed": True, "reason": "Allowed", "limitKind": "Count", "limit": 100, "used": 1, "remaining": 99}
            ],
        }
    }
    resp = _FakeResponse(200, json_body=body)
    c = _client(get_response=resp)
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = _ctx()
        result = await c.get_entitlements()
    assert isinstance(result, EntitlementsSnapshot)
    assert result.has_subscription is True
    assert result.entitlements[0].key == "chat"


@pytest.mark.asyncio
async def test_get_entitlements_503_returns_none():
    resp = _FakeResponse(503, text_body="unavailable")
    c = _client(get_response=resp)
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = _ctx()
        result = await c.get_entitlements()
    assert result is None


@pytest.mark.asyncio
async def test_get_entitlements_fresh_query_param():
    body = {"data": {"hasSubscription": False, "entitlements": []}}
    resp = _FakeResponse(200, json_body=body)
    c = _client(get_response=resp)
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = _ctx()
        await c.get_entitlements(fresh=True)
    _, kwargs = c._session.get.call_args
    assert kwargs["params"] == {"fresh": "true"}


# ---------------- record_usage ----------------


@pytest.mark.asyncio
async def test_record_usage_200_allowed_true():
    body = {
        "data": {
            "allowed": True,
            "meterKey": "ai-messages",
            "used": 5,
            "remaining": 95,
            "overage": 0,
            "replayed": False,
        }
    }
    resp = _FakeResponse(200, json_body=body)
    c = _client(post_response=resp)
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = _ctx()
        result = await c.record_usage("ai-messages", "idem-1")
    assert result.allowed is True
    assert result.replayed is False


@pytest.mark.asyncio
async def test_record_usage_200_allowed_false_not_raised():
    body = {
        "data": {
            "allowed": False,
            "meterKey": "ai-messages",
            "used": 100,
            "remaining": 0,
            "overage": 0,
            "replayed": False,
        }
    }
    resp = _FakeResponse(200, json_body=body)
    c = _client(post_response=resp)
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = _ctx()
        result = await c.record_usage("ai-messages", "idem-1")
    assert isinstance(result, UsageResult)
    assert result.allowed is False


@pytest.mark.asyncio
async def test_record_usage_replayed_true():
    body = {
        "data": {
            "allowed": True,
            "meterKey": "ai-messages",
            "used": 5,
            "remaining": 95,
            "overage": 0,
            "replayed": True,
        }
    }
    resp = _FakeResponse(200, json_body=body)
    c = _client(post_response=resp)
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = _ctx()
        result = await c.record_usage("ai-messages", "idem-1")
    assert result.replayed is True


@pytest.mark.asyncio
async def test_record_usage_404_normalizes_to_denied():
    resp = _FakeResponse(404)
    c = _client(post_response=resp)
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = _ctx()
        result = await c.record_usage("ai-messages", "idem-1")
    assert isinstance(result, UsageResult)
    assert result.allowed is False
    assert result.replayed is False
    assert result.used == 0
    assert result.remaining == 0


@pytest.mark.asyncio
async def test_record_usage_200_with_subscription_not_found_envelope_normalizes_to_denied():
    body = {
        "success": False,
        "data": None,
        "error": {"code": "subscription_not_found", "message": "This organization has no active subscription."},
        "meta": {"correlationId": "c1", "timestampUtc": "2026-08-25T17:34:28Z", "replayed": False},
    }
    resp = _FakeResponse(200, json_body=body)
    c = _client(post_response=resp)
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = _ctx()
        result = await c.record_usage("ai-messages", "idem-1")
    assert isinstance(result, UsageResult)
    assert result.allowed is False


@pytest.mark.asyncio
async def test_record_usage_oauth_token_override_forwarded():
    body = {
        "data": {
            "allowed": True,
            "meterKey": "ai-messages",
            "used": 1,
            "remaining": 99,
            "overage": 0,
            "replayed": False,
        }
    }
    resp = _FakeResponse(200, json_body=body)
    c = _client(post_response=resp)
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = _ctx(oauth_token="")
        await c.record_usage("ai-messages", "idem-1", oauth_token="real-bearer-token")
    _, kwargs = c._session.post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer real-bearer-token"


@pytest.mark.asyncio
async def test_record_usage_400_returns_none():
    resp = _FakeResponse(400, text_body="missing idempotencyKey")
    c = _client(post_response=resp)
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = _ctx()
        result = await c.record_usage("ai-messages", "")
    assert result is None


@pytest.mark.asyncio
async def test_record_usage_429_returns_none():
    resp = _FakeResponse(429, text_body="rate limited")
    c = _client(post_response=resp)
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = _ctx()
        result = await c.record_usage("ai-messages", "idem-1")
    assert result is None


@pytest.mark.asyncio
async def test_record_usage_503_returns_none():
    resp = _FakeResponse(503, text_body="unavailable")
    c = _client(post_response=resp)
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = _ctx()
        result = await c.record_usage("ai-messages", "idem-1")
    assert result is None


@pytest.mark.asyncio
async def test_record_usage_missing_context_raises():
    c = _client()
    with patch(SC + "BlocksContextManager") as MBCM:
        MBCM.get_context.return_value = None
        with pytest.raises(RuntimeError):
            await c.record_usage("ai-messages", "idem-1")
