"""The request tenant is bound to the token's tenant, including under impersonation.

Tenants: A is the home tenant that signs every token here, B and D are other enabled
tenants, C is the tenant A impersonates. Each tenant has a record id that differs from
its tenant id, so both forms are exercised.

Signature checks are mocked to succeed: these tests are about which tenant a verified
token may act for, not about the signature itself.
"""
import logging

import jwt
import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock, MagicMock, patch

from blocks_genesis._auth import auth
from blocks_genesis._auth.blocks_context import BlocksContextManager
from blocks_genesis._tenant.tenant import Tenant

TENANTS = {key: Tenant(_id=f"id-{key}", TenantId=key) for key in ("A", "B", "C", "D")}


class _TenantService:
    async def get_tenant(self, tenant_id):
        for tenant in TENANTS.values():
            if tenant_id in (tenant.tenant_id, tenant.id):
                return tenant
        return None


def _token(**claims):
    return jwt.encode({"user_id": "u1", "roles": ["admin"], **claims}, "test-signing-key-of-at-least-32-bytes", algorithm="HS256")


def _impersonation(**overrides):
    claims = {"impersonated": True, "tenant_id": "C", "original_tenant_id": "A"}
    claims.update(overrides)
    return _token(**{k: v for k, v in claims.items() if v is not None})


async def _authenticate(token, header_tenant):
    """Run authenticate() for a request whose x-blocks-key resolved to header_tenant.

    Returns the tenant the signature was checked against and the context that was set.
    """
    BlocksContextManager.set_context(
        BlocksContextManager.create(tenant_id=header_tenant, original_tenant_id=header_tenant)
    )
    verified_with = []

    async def verify(tok, signing_tenant, cache_client, request):
        verified_with.append(signing_tenant)
        return jwt.decode(tok, options={"verify_signature": False})

    with patch.object(auth, "extract_token_from_request", new_callable=AsyncMock) as extract, \
         patch.object(auth, "validate_jwt_token", side_effect=verify), \
         patch.object(auth, "AuthClaimsContext"), \
         patch.object(auth, "Activity"):
        extract.return_value = (token, False, "app.example")
        await auth.authenticate(MagicMock(), _TenantService(), MagicMock())

    return verified_with, BlocksContextManager.get_context()


async def _refused(token, header_tenant):
    with pytest.raises(HTTPException) as exc:
        await _authenticate(token, header_tenant)
    assert exc.value.status_code == 401
    return exc.value


@pytest.fixture(autouse=True)
def _clear_context():
    yield
    BlocksContextManager.clear_context()


# ---------------------------------------------------------------------------
# 1-3: tokens that are not impersonated
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_token_for_one_tenant_is_refused_on_another_even_if_it_verifies():
    exc = await _refused(_token(tenant_id="A"), "B")
    assert exc.detail == "Token validation failed"


@pytest.mark.asyncio
async def test_token_on_its_own_tenant_binds_both_ids_to_it():
    verified_with, ctx = await _authenticate(_token(tenant_id="A"), "A")
    assert verified_with == [TENANTS["A"]]
    assert ctx.tenant_id == "A"
    assert ctx.original_tenant_id == "A"


@pytest.mark.asyncio
async def test_token_claim_in_record_id_form_is_accepted():
    _, ctx = await _authenticate(_token(tenant_id="id-A"), "A")
    assert ctx.original_tenant_id == "A"


@pytest.mark.asyncio
async def test_request_tenant_is_canonical_when_the_header_uses_the_record_id():
    _, ctx = await _authenticate(_token(tenant_id="A"), "id-A")
    assert ctx.original_tenant_id == "A"


@pytest.mark.asyncio
async def test_token_without_tenant_claim_gets_no_tenant_id_fallback():
    _, ctx = await _authenticate(_token(), "A")
    assert ctx.tenant_id == ""
    assert ctx.original_tenant_id == "A"


@pytest.mark.asyncio
async def test_impersonated_false_string_is_not_impersonation():
    await _refused(_token(tenant_id="A", impersonated="false", original_tenant_id="A"), "B")


# ---------------------------------------------------------------------------
# 4-6: impersonation (A signs, C is the target)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_impersonation_with_an_unrelated_header_tenant_is_refused():
    exc = await _refused(_impersonation(), "D")
    assert exc.detail == "Token validation failed"


@pytest.mark.asyncio
async def test_impersonation_with_the_target_as_header():
    verified_with, ctx = await _authenticate(_impersonation(), "C")
    assert verified_with == [TENANTS["A"]]
    assert ctx.tenant_id == "C"
    assert ctx.original_tenant_id == "A"
    assert ctx.impersonated is True


@pytest.mark.asyncio
async def test_impersonation_with_the_signer_as_header():
    verified_with, ctx = await _authenticate(_impersonation(), "A")
    assert verified_with == [TENANTS["A"]]
    assert ctx.tenant_id == "C"
    assert ctx.original_tenant_id == "A"


@pytest.mark.asyncio
async def test_impersonated_true_string_is_impersonation():
    _, ctx = await _authenticate(_impersonation(impersonated="True"), "C")
    assert ctx.original_tenant_id == "A"
    await _refused(_impersonation(impersonated="True"), "D")


@pytest.mark.asyncio
async def test_signer_claim_in_record_id_form_is_canonical_in_the_context():
    _, ctx = await _authenticate(_impersonation(original_tenant_id="id-A"), "C")
    assert ctx.original_tenant_id == "A"


def test_original_tenant_claim_that_differs_from_the_signer_is_refused():
    payload = {"impersonated": True, "tenant_id": "C", "original_tenant_id": "B"}
    with pytest.raises(HTTPException) as exc:
        auth._bind_token_tenant(payload, TENANTS["C"], TENANTS["A"])
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_impersonation_without_original_tenant_claim_is_refused():
    await _refused(_impersonation(original_tenant_id=None), "C")


@pytest.mark.asyncio
async def test_impersonation_by_an_unknown_signer_is_refused_before_verification():
    token = _impersonation(original_tenant_id="nobody")
    with patch.object(auth, "validate_jwt_token", new_callable=AsyncMock) as verify, \
         patch.object(auth, "extract_token_from_request", new_callable=AsyncMock) as extract, \
         patch.object(auth, "AuthClaimsContext"), \
         patch.object(auth, "Activity"):
        verify.return_value = jwt.decode(token, options={"verify_signature": False})
        extract.return_value = (token, False, "app.example")
        BlocksContextManager.set_context(
            BlocksContextManager.create(tenant_id="C", original_tenant_id="C")
        )
        with pytest.raises(HTTPException) as exc:
            await auth.authenticate(MagicMock(), _TenantService(), MagicMock())

    assert exc.value.status_code == 401
    verify.assert_not_awaited()


@pytest.mark.asyncio
async def test_impersonation_without_target_tenant_claim_is_refused():
    await _refused(_impersonation(tenant_id=None), "A")


# ---------------------------------------------------------------------------
# 7: permissions under impersonation come from the signer, never the header
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("header_tenant", ["A", "C"])
async def test_permissions_under_impersonation_are_read_from_the_signer(header_tenant):
    await _authenticate(_impersonation(), header_tenant)

    collection = MagicMock()
    collection.count_documents = AsyncMock(return_value=1)
    db_context = MagicMock()
    db_context.get_collection = AsyncMock(return_value=collection)
    ctx = BlocksContextManager.get_context()

    await auth._check_permission("res", ctx.roles, ctx.permissions, ctx.tenant_id, db_context)

    db_context.get_collection.assert_awaited_once_with("Permissions", tenant_id="A")


# ---------------------------------------------------------------------------
# Refusals are logged with reason codes and ids, never the token
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refusal_is_logged_without_the_token(caplog):
    token = _token(tenant_id="A")
    with caplog.at_level(logging.INFO):
        await _refused(token, "B")

    security = [r.getMessage() for r in caplog.records if "[Security]" in r.getMessage()]
    assert any("tenant_binding_refused" in line for line in security)
    assert all(token not in line for line in security)


# ---------------------------------------------------------------------------
# Review follow-ups
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["false", "yes", 1])
async def test_context_impersonated_flag_uses_the_same_predicate_as_the_binding(value):
    _, ctx = await _authenticate(_token(tenant_id="A", impersonated=value), "A")
    assert ctx.impersonated is False


@pytest.mark.asyncio
async def test_unsigned_impersonation_token_with_a_list_signer_id_is_a_401_not_a_500():
    token = _impersonation(original_tenant_id=["A"])
    with patch.object(auth, "validate_jwt_token", new_callable=AsyncMock) as verify,          patch.object(auth, "extract_token_from_request", new_callable=AsyncMock) as extract:
        extract.return_value = (token, False, "app.example")
        BlocksContextManager.set_context(
            BlocksContextManager.create(tenant_id="C", original_tenant_id="C")
        )
        with pytest.raises(HTTPException) as exc:
            await auth.authenticate(MagicMock(), _TenantService(), MagicMock())

    assert exc.value.status_code == 401
    assert exc.value.detail == "Token validation failed"
    verify.assert_not_awaited()


@pytest.mark.parametrize("payload", [
    {"tenant_id": ["A"]},
    {"tenant_id": {"id": "A"}},
    {"impersonated": True, "tenant_id": ["C"], "original_tenant_id": "A"},
    {"impersonated": True, "tenant_id": "C", "original_tenant_id": ["A"]},
    {"impersonated": True, "tenant_id": "C", "original_tenant_id": {"id": "A"}},
])
def test_binding_refuses_claims_that_are_not_tenant_ids(payload):
    with pytest.raises(HTTPException) as exc:
        auth._bind_token_tenant(payload, TENANTS["A"], TENANTS["A"])
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_unknown_signer_log_truncates_the_unverified_id_and_is_not_a_warning(caplog):
    long_id = "x" * 500
    with caplog.at_level(logging.INFO):
        await _refused(_impersonation(original_tenant_id=long_id), "C")

    records = [r for r in caplog.records if "impersonation_signer_refused" in r.getMessage()]
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    assert "x" * 64 in records[0].getMessage()
    assert "x" * 65 not in records[0].getMessage()


@pytest.mark.asyncio
async def test_signer_header_accepts_any_target_until_the_iam_signer_allow_list_follow_up():
    """With the header naming the signer A (the genesis-net convention), the target
    claim is not checked against anything: A may name any tenant. Restricting which
    tenants may sign impersonation tokens is the IAM allow-list follow-up on #40."""
    _, ctx = await _authenticate(_impersonation(tenant_id="B"), "A")
    assert ctx.tenant_id == "B"
    assert ctx.original_tenant_id == "A"
