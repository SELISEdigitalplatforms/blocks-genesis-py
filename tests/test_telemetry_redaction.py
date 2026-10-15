"""Logs and traces carry metadata, never bodies, credentials or grant ids (#42)."""

import json
import logging
import re
from queue import Queue
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.datastructures import URL
from starlette.requests import Request

from blocks_genesis._auth import auth
from blocks_genesis._auth.blocks_context import BlocksContext, BlocksContextManager
from blocks_genesis._auth.third_party_provider import ThirdPartyJwtProvider
from blocks_genesis._delegation import constants
from blocks_genesis._delegation.context import AuthClaimsContext, DelegatedTokenContext
from blocks_genesis._delegation.grant_store import DelegationGrantRecord
from blocks_genesis._tenant.tenant import Tenant

BODY_SENTINEL = "sentinel-body-7f3a"
QUERY_SENTINEL = "sentinel-query-91c2"
MESSAGE_SENTINEL = "sentinel-message-4b1d"
GRANT_ID = "dg_" + "5e" * 32
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZW50aW5lbCJ9.c2VudGluZWw"
ISSUER_SENTINEL = "https://sentinel-issuer.example/"
AUDIENCE_SENTINEL = "api://sentinel-audience"

AMW = "blocks_genesis._message.azure.azure_message_worker."
RMW = "blocks_genesis._message.rabbit_mq.rabbit_message_worker."
RC = "blocks_genesis._cache.redis_client."
T = "blocks_genesis._middlewares.tenant_middleware."

ENVELOPE = json.dumps({"type": "OrderPlaced", "body": json.dumps({"note": MESSAGE_SENTINEL})})


@pytest.fixture(autouse=True)
def clean_ambient_state():
    BlocksContextManager.clear_context()
    DelegatedTokenContext.clear()
    AuthClaimsContext.clear()
    yield
    BlocksContextManager.clear_context()
    DelegatedTokenContext.clear()
    AuthClaimsContext.clear()


# ------------------------------------------------------------------ exception middleware


def _starlette_request():
    body = json.dumps({"password": BODY_SENTINEL}).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "scheme": "https",
        "server": ("api.example", 443),
        "path": "/api/orders",
        "query_string": f"token={QUERY_SENTINEL}".encode(),
        "headers": [(b"content-type", b"application/json"), (b"host", b"api.example")],
    }
    return Request(scope, receive)


async def test_exception_log_has_method_path_and_type_but_no_body_or_query(caplog):
    from blocks_genesis._middlewares.global_exception_middleware import GlobalExceptionHandlerMiddleware

    middleware = GlobalExceptionHandlerMiddleware(MagicMock())
    with caplog.at_level(logging.DEBUG), patch(
        "blocks_genesis._middlewares.global_exception_middleware.Activity"
    ) as activity:
        activity.get_trace_id.return_value = "trace-1"
        response = await middleware.handle_exception(_starlette_request(), ValueError("boom"))

    assert response.status_code == 500
    message = "\n".join(r.getMessage() for r in caplog.records)
    for expected in ("POST", "/api/orders", "ValueError", "boom"):
        assert expected in message
    assert BODY_SENTINEL not in caplog.text
    assert QUERY_SENTINEL not in caplog.text
    assert "token=" not in caplog.text


async def test_exception_handler_does_not_read_the_body():
    from blocks_genesis._middlewares.global_exception_middleware import GlobalExceptionHandlerMiddleware

    request = MagicMock(spec=Request)
    request.headers = {"content-type": "application/json"}
    request.body = AsyncMock(return_value=b'{"a": 1}')
    request.url = URL("https://api.example/api/orders")
    request.method = "POST"

    with patch("blocks_genesis._middlewares.global_exception_middleware.Activity") as activity:
        activity.get_trace_id.return_value = "trace-1"
        await GlobalExceptionHandlerMiddleware(MagicMock()).handle_exception(request, ValueError("boom"))

    request.body.assert_not_awaited()


# ------------------------------------------------------------------ workers and client


def _context():
    return BlocksContext(
        tenant_id="tenant-1", user_id="user-1", organization_id="org-1", is_authenticated=True, roles=["admin"]
    )


def _security_context_header():
    return json.dumps(BlocksContextManager.create_sanitized_for_transport(_context()))


def _grant_store():
    record = DelegationGrantRecord(TenantId="tenant-1", UserId="user-1", OrganizationId="org-1")
    return MagicMock(get_async=AsyncMock(return_value=record), delete_async=AsyncMock())


def _rabbit_message(baggage="{}"):
    message = MagicMock()
    message.message_id = "msg-1"
    message.headers = {
        "TenantId": b"tenant-1",
        "TraceId": b"",
        "SpanId": b"",
        "Baggage": baggage.encode(),
        "SecurityContext": _security_context_header().encode(),
        constants.DELEGATION_GRANT_HEADER: GRANT_ID.encode(),
    }
    message.body = ENVELOPE.encode()
    message.ack = AsyncMock()
    return message


async def _run_rabbit(baggage="{}"):
    from blocks_genesis._message.message_configuration import ConsumerSubscription
    from blocks_genesis._message.rabbit_mq.rabbit_message_worker import RabbitMessageWorker

    worker = RabbitMessageWorker.__new__(RabbitMessageWorker)
    worker._tracer = MagicMock()
    worker._consumer = MagicMock()
    worker._consumer.process_message = AsyncMock()
    with patch(RMW + "get_delegation_grant_store", return_value=_grant_store()), patch(
        RMW + "get_delegated_token_provider", return_value=MagicMock()
    ):
        await worker._process_message(_rabbit_message(baggage), ConsumerSubscription.bind_to_queue("orders.queue", 3))
    worker._consumer.process_message.assert_awaited_once()
    return worker


def _azure_message(baggage="{}"):
    message = MagicMock()
    message.message_id = "msg-1"
    message.delivery_count = 0
    message.application_properties = {
        "TraceId": "",
        "SpanId": "",
        "TenantId": "tenant-1",
        "Baggage": baggage,
        "SecurityContext": _security_context_header(),
        constants.DELEGATION_GRANT_HEADER: GRANT_ID,
    }
    message.body = [ENVELOPE.encode()]
    return message


async def _run_azure(baggage="{}"):
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
    receiver = MagicMock(complete_message=AsyncMock(), abandon_message=AsyncMock(), dead_letter_message=AsyncMock())
    with patch(AMW + "get_delegation_grant_store", return_value=_grant_store()), patch(
        AMW + "get_delegated_token_provider", return_value=MagicMock()
    ):
        await worker.message_handler(receiver, _azure_message(baggage), max_delivery_count=2)
    worker._consumer.process_message.assert_awaited_once()
    return worker


def _span_attributes(worker):
    span = worker._tracer.start_as_current_span.return_value.__enter__.return_value
    return [call.args for call in span.set_attribute.call_args_list]


def _assert_metadata_only(attributes):
    keys = [key for key, _ in attributes]
    assert "message.body" not in keys
    assert "SecurityContext" not in keys
    for _, value in attributes:
        assert MESSAGE_SENTINEL not in str(value)
        assert GRANT_ID not in str(value)
        assert _security_context_header() not in str(value)

    values = dict(attributes)
    assert values["message.id"] == "msg-1"
    assert values["message.size"] == len(ENVELOPE.encode())
    assert values["message.type"] == "OrderPlaced"
    assert values["baggage.TenantId"] == "tenant-1"


async def test_rabbit_span_and_logs_carry_metadata_not_body(caplog):
    with caplog.at_level(logging.INFO):
        worker = await _run_rabbit()

    _assert_metadata_only(_span_attributes(worker))
    assert MESSAGE_SENTINEL not in caplog.text
    assert GRANT_ID not in caplog.text


async def test_azure_span_and_logs_carry_metadata_not_body(caplog):
    with caplog.at_level(logging.INFO):
        worker = await _run_azure()

    _assert_metadata_only(_span_attributes(worker))
    assert MESSAGE_SENTINEL not in caplog.text
    assert GRANT_ID not in caplog.text


@pytest.mark.parametrize("run", [_run_rabbit, _run_azure], ids=["rabbit", "azure"])
async def test_baggage_cannot_override_the_tenant(run):
    baggage = json.dumps(
        {"TenantId": "victim", "SecurityContext": "raw", "note": f"Bearer {JWT}", "token": "s", "list": [1, 2]}
    )

    attributes = _span_attributes(await run(baggage))

    assert [value for key, value in attributes if key == "baggage.TenantId"] == ["tenant-1"]
    assert all(key != "baggage.SecurityContext" for key, _ in attributes)
    values = dict(attributes)
    assert values["baggage.note"] == "Bearer [REDACTED]"
    assert values["baggage.token"] == "[REDACTED]"
    assert values["baggage.list"] == "[1, 2]"


def test_returned_message_log_has_size_not_body(caplog):
    from blocks_genesis._message.rabbit_mq.rabbit_message_client import RabbitMessageClient

    client = RabbitMessageClient.__new__(RabbitMessageClient)
    message = MagicMock(exchange="ex", routing_key="rk", headers={}, body=MESSAGE_SENTINEL.encode())

    with caplog.at_level(logging.DEBUG):
        client._on_message_returned("channel", message)

    assert MESSAGE_SENTINEL not in caplog.text
    assert "size=" in caplog.text


# ------------------------------------------------------------------ Redis


def _recorded_key(key):
    from blocks_genesis._cache.redis_client import RedisClient

    client = RedisClient.__new__(RedisClient)
    with patch(RC + "Activity") as activity, patch(RC + "BlocksContextManager") as context_manager:
        context_manager.get_context.return_value = None
        client._create_activity(key, "Get")
    calls = activity.start.return_value.set_property.call_args_list
    return next(call.args[1] for call in calls if call.args[0] == "key")


@pytest.mark.parametrize(
    "prefix", [constants.GRANT_KEY_PREFIX, constants.NONCE_KEY_PREFIX, constants.REDEMPTION_KEY_PREFIX]
)
def test_create_activity_hashes_credential_keys(prefix):
    recorded = _recorded_key(prefix + GRANT_ID)

    assert re.fullmatch(re.escape(prefix) + r"sha256:[0-9a-f]{12}", recorded)
    assert GRANT_ID not in recorded
    assert _recorded_key(prefix + GRANT_ID) == recorded
    assert _recorded_key(prefix + "dg_" + "6f" * 32) != recorded


def test_create_activity_keeps_ordinary_keys():
    assert _recorded_key("tenant:abc") == "tenant:abc"
    assert _recorded_key("cache:" + GRANT_ID) == "cache:[REDACTED]"


def test_byte_value_calls_do_not_print_keys(capsys, caplog):
    from blocks_genesis._cache.redis_client import RedisClient

    client = RedisClient.__new__(RedisClient)
    client._sync_client = MagicMock()
    client._sync_client.get.return_value = b"v"
    key = constants.GRANT_KEY_PREFIX + GRANT_ID

    with caplog.at_level(logging.DEBUG), patch(RC + "Activity"), patch(RC + "BlocksContextManager"):
        client.add_bytes_value(key, b"value", 60)
        client.get_bytes_value(key)

    assert capsys.readouterr().out == ""
    assert GRANT_ID not in caplog.text


# ------------------------------------------------------------------ redaction rule


@pytest.mark.parametrize(
    "name",
    [
        "proxy-authorization", "api_key", "apikey", "API-Key", "signature", "x-signature", "sig", "code",
        "session", "x-session-id", "jwt", "x-jwt-assertion", "credential", "credentials", "x-blocks-key",
    ],
)
def test_new_sensitive_names_are_redacted(name):
    from blocks_genesis._middlewares.tenant_middleware import is_sensitive_key

    assert is_sensitive_key(name) is True


@pytest.mark.parametrize("name", ["content-type", "content-encoding", "accept", "host", "zipcode", "design"])
def test_ordinary_names_are_kept(name):
    from blocks_genesis._middlewares.tenant_middleware import is_sensitive_key

    assert is_sensitive_key(name) is False


def test_sanitize_dict_redacts_secret_shaped_values():
    from blocks_genesis._middlewares.tenant_middleware import sanitize_dict

    result = sanitize_dict({"x-note": f"Bearer {JWT}", "q": f"id={GRANT_ID}", "ok": "v"})

    dumped = json.dumps(result)
    assert JWT not in dumped
    assert GRANT_ID not in dumped
    assert result["ok"] == "v"


def _middleware_request():
    request = MagicMock(spec=Request)
    request.url.path = "/api/x"
    request.headers = {"x-blocks-key": "key", "proxy-authorization": QUERY_SENTINEL}
    request.query_params = {"code": BODY_SENTINEL}
    request.base_url.hostname = "host"
    return request


async def _dispatch_tenant_middleware(activity):
    from blocks_genesis._middlewares.tenant_middleware import TenantValidationMiddleware

    middleware = TenantValidationMiddleware(MagicMock(), included_paths=["/api"])
    middleware._is_valid_origin_or_referer = MagicMock(return_value=True)
    tenant = MagicMock(is_disabled=False, is_root_tenant=False, tenant_id="tid", applications=[])
    response = MagicMock(status_code=200, headers={})
    response.body_iterator = None
    with patch(T + "Activity", activity), patch(T + "get_tenant_service") as tenant_service:
        tenant_service.return_value.get_tenant = AsyncMock(return_value=tenant)
        await middleware.dispatch(_middleware_request(), AsyncMock(return_value=response))


async def test_tenant_middleware_records_redacted_headers_and_query():
    activity = MagicMock()

    await _dispatch_tenant_middleware(activity)

    recorded = json.dumps([str(call) for call in activity.set_current_properties.call_args_list])
    assert QUERY_SENTINEL not in recorded
    assert BODY_SENTINEL not in recorded


async def test_tenant_middleware_does_not_record_the_security_context():
    activity = MagicMock()

    await _dispatch_tenant_middleware(activity)

    assert all(call.args[0] != "SecurityContext" for call in activity.set_current_property.call_args_list)


# ------------------------------------------------------------------ trace exporter


def _exporter():
    from blocks_genesis._lmt.mongo_trace_exporter import MongoDBTraceExporter

    exporter = MongoDBTraceExporter.__new__(MongoDBTraceExporter)
    exporter._service_name = "svc"
    exporter._db = MagicMock()
    exporter._queue = Queue(maxsize=10)
    exporter._worker_thread = MagicMock()
    exporter._stop_event = MagicMock()
    return exporter


def _exported(tenant_id):
    span = MagicMock()
    span.parent = None
    span.context.trace_id = 1
    span.context.span_id = 2
    span.end_time = 1_000_000_000
    span.start_time = 0
    span.attributes = {"baggage.TenantId": tenant_id, "k": 1}
    exporter = _exporter()

    exporter.export([span])

    return exporter._queue.get_nowait()


def test_a_valid_tenant_id_names_the_collection():
    tenant_id = "8656D85F-C3E0-48AA-9505-654505096AEC"

    collection, doc = _exported(tenant_id)

    assert collection == tenant_id
    assert doc["TenantId"] == tenant_id


@pytest.mark.parametrize(
    "tenant_id", ["a.b", "system.users", "x" * 65, "", "t$1", "tenant\n", ("t",), ["t"], 5, None]
)
def test_an_invalid_tenant_id_goes_to_miscellaneous(tenant_id):
    collection, doc = _exported(tenant_id)

    assert collection == "miscellaneous"
    assert doc["TenantId"] == "miscellaneous"


# ------------------------------------------------------------------ N-05: no print()


def _service_bus_config():
    from blocks_genesis._message.message_configuration import AzureServiceBusConfiguration, MessageConfiguration

    config = MessageConfiguration(connection="sb://sentinel-namespace", service_name="svc")
    config.azure_service_bus_configuration = AzureServiceBusConfiguration(queues=["q1"], topics=["t1"])
    return config


@pytest.mark.parametrize("exists", [True, False])
def test_service_bus_setup_does_not_print(exists, capsys):
    from blocks_genesis._message.azure.config_azure_service_bus import ConfigAzureServiceBus

    admin = MagicMock()
    if not exists:
        admin.get_queue.side_effect = Exception("missing")
        admin.get_topic.side_effect = Exception("missing")
        admin.get_subscription.side_effect = Exception("missing")
    with patch("blocks_genesis._message.azure.config_azure_service_bus.ServiceBusAdministrationClient") as client:
        client.from_connection_string.return_value = admin
        ConfigAzureServiceBus.configure_queue_and_topic(_service_bus_config())

    assert capsys.readouterr().out == ""


def test_service_bus_setup_failure_does_not_print_or_log_the_namespace(capsys, caplog):
    from blocks_genesis._message.azure.config_azure_service_bus import ConfigAzureServiceBus

    with caplog.at_level(logging.DEBUG), patch(
        "blocks_genesis._message.azure.config_azure_service_bus.ServiceBusAdministrationClient"
    ) as client:
        client.from_connection_string.side_effect = Exception("sb://sentinel-namespace")
        with pytest.raises(Exception):
            ConfigAzureServiceBus.configure_queue_and_topic(_service_bus_config())

    assert capsys.readouterr().out == ""
    assert "sentinel-namespace" not in caplog.text


def test_swagger_route_does_not_print(capsys):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from blocks_genesis._core import api

    app = FastAPI()
    with patch("blocks_genesis._core.api.FastAPIInstrumentor"):
        api.configure_genesis(app, show_docs=True)
        with TestClient(app) as client:
            assert client.get("/swagger/index.html").status_code == 200

    assert capsys.readouterr().out == ""


# ------------------------------------------------------------------ N-02: selection-failure log


def _provider(key, **kwargs):
    values = dict(
        _id=key,
        TenantId="ABC001",
        Key=key,
        IsActive=True,
        Issuer="https://abc.eu.auth0.com/",
        Audiences=["api://klax"],
        Algorithms=[1],
        JwksUrl="https://abc.eu.auth0.com/.well-known/jwks.json",
        ClaimsMapping={"UserId": "sub"},
    )
    values.update(kwargs)
    return ThirdPartyJwtProvider(**values)


def _tenant():
    return Tenant(_id="ABC001", TenantId="ABC001", IsThirdPartyJwtEnabled=True)


def _auth_request(url="https://api.klax.io/x"):
    request = MagicMock()
    request.url = URL(url)
    request.headers = {}
    request.cookies = {}
    request.query_params = {}
    return request


def _store(providers):
    return MagicMock(get_active=AsyncMock(return_value=list(providers)))


def _security_events(caplog):
    events = []
    for record in caplog.records:
        message = record.getMessage()
        if message.startswith("[Security]"):
            events.append(json.loads(message[len("[Security] "):]))
    return events


async def test_selection_failure_log_has_keys_not_provider_configuration(caplog):
    providers = [
        _provider(key, Issuer=ISSUER_SENTINEL, Audiences=[AUDIENCE_SENTINEL]) for key in ("auth0-app1", "auth0-app2")
    ]

    with caplog.at_level(logging.INFO), patch.object(
        auth, "get_third_party_provider_store", return_value=_store(providers)
    ), patch.object(auth, "_read_token_routing", return_value=(ISSUER_SENTINEL, ["api://other"])):
        assert await auth.validate_with_fallback("tok", _tenant(), _auth_request()) is None

    unmatched = [e for e in _security_events(caplog) if e["eventName"] == "third_party_provider_unmatched"]
    assert len(unmatched) == 1
    detail = json.dumps(unmatched[0]["detail"])
    assert "auth0-app1" in detail
    assert "auth0-app2" in detail
    assert ISSUER_SENTINEL not in caplog.text
    assert AUDIENCE_SENTINEL not in caplog.text


# ------------------------------------------------------------------ N-03: request URI claim


@patch("blocks_genesis._auth.auth.jwt")
@patch("blocks_genesis._auth.auth.create_certificate")
@patch("blocks_genesis._auth.auth.get_tenant_cert", new_callable=AsyncMock)
async def test_blocks_token_request_uri_is_path_only(mock_get_cert, mock_create_cert, mock_jwt):
    mock_get_cert.return_value = b"b"
    cert = MagicMock()
    cert.public_key.return_value.public_bytes.return_value.decode.return_value = "pem"
    mock_create_cert.return_value = cert
    mock_jwt.decode.return_value = {"sub": "u1"}
    request = MagicMock()
    request.url = URL(f"https://api.example/api/x?code={QUERY_SENTINEL}")

    result = await auth.validate_jwt_token("tok", MagicMock(), MagicMock(), request)

    assert result["request_uri"] == "/api/x"


async def test_third_party_claims_request_uri_is_path_only():
    provider = _provider("auth0-main")

    with patch.object(auth, "get_third_party_provider_store", return_value=_store([provider])), patch.object(
        auth, "_read_token_routing", return_value=(provider.issuer, list(provider.audiences))
    ), patch.object(auth, "_resolve_provider_key", new_callable=AsyncMock, return_value="a-key"), patch.object(
        auth.jwt, "decode", return_value={"sub": "auth0|sara_123"}
    ):
        result = await auth.validate_with_fallback(
            "tok", _tenant(), _auth_request(f"https://api.klax.io/x?code={QUERY_SENTINEL}")
        )

    assert result is not None
    assert result.claims[BlocksContext.REQUEST_URI_CLAIM] == "/x"


async def test_third_party_context_request_uri_is_path_only():
    tenant = _tenant()
    tenant_service = MagicMock()
    tenant_service.get_tenant = AsyncMock(return_value=tenant)
    accepted = auth.ThirdPartyValidation({"sub": "auth0|sara_123"}, _provider("auth0-main"))

    with patch.object(auth, "extract_token_from_request", new_callable=AsyncMock) as extract, patch.object(
        auth, "validate_jwt_token", new_callable=AsyncMock
    ), patch.object(auth, "validate_with_fallback", new_callable=AsyncMock, return_value=accepted), patch.object(
        auth, "_resolve_signing_tenant", new_callable=AsyncMock, return_value=tenant
    ):
        extract.return_value = ("tok", False, "abc.klax.io")
        BlocksContextManager.set_context(BlocksContextManager.create(tenant_id="ABC001", original_tenant_id="ABC001"))
        await auth.authenticate(
            _auth_request(f"https://api.klax.io/x?code={QUERY_SENTINEL}"), tenant_service, MagicMock()
        )

    assert BlocksContextManager.get_context().request_uri == "/x"
