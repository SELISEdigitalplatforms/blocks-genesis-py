"""Grant storage and send-side grant construction."""

import json

import pytest

from blocks_genesis._auth.blocks_context import BlocksContextManager
from blocks_genesis._delegation import constants
from blocks_genesis._delegation.context import AuthClaimsContext, DelegatedTokenContext
from blocks_genesis._delegation.grant_factory import DelegationGrantFactory
from blocks_genesis._delegation.grant_store import DelegationGrantRecord, DelegationGrantStore


class FakeCache:
    """Records the exact key, value and TTL that reach Redis."""

    def __init__(self):
        self.values = {}
        self.ttls = {}
        self.removed = []
        self.set_calls = 0
        self.get_calls = 0
        self.raise_on_remove = None

    async def add_string_value_async(self, key, value, key_life_span=None):
        self.set_calls += 1
        self.values[key] = value
        self.ttls[key] = key_life_span
        return True

    async def get_string_value_async(self, key):
        self.get_calls += 1
        return self.values.get(key)

    async def remove_key_async(self, key):
        if self.raise_on_remove is not None:
            raise self.raise_on_remove
        self.removed.append(key)
        self.values.pop(key, None)
        return True


def authenticated_context(tenant_id="tenant-1", user_id="user-1", org_id="org-1", authenticated=True):
    return BlocksContextManager.create(
        tenant_id=tenant_id,
        roles=["admin"],
        user_id=user_id,
        is_authenticated=authenticated,
        organization_id=org_id,
    )


@pytest.fixture(autouse=True)
def clean_ambient_state():
    BlocksContextManager.clear_context()
    DelegatedTokenContext.clear()
    AuthClaimsContext.clear()
    yield
    BlocksContextManager.clear_context()
    DelegatedTokenContext.clear()
    AuthClaimsContext.clear()


# --------------------------------------------------------------------------- store


async def test_create_writes_pascal_case_record_with_two_day_ttl():
    cache = FakeCache()
    store = DelegationGrantStore(cache)

    delegation_id = await store.create_async(authenticated_context(), "3", "stamp-9")

    key = constants.grant_key(delegation_id)
    assert cache.ttls[key] == constants.DEFAULT_GRANT_TTL_SECONDS

    payload = json.loads(cache.values[key])
    # PascalCase names are the wire contract shared with blocks-genesis-net and blocks-iam.
    assert payload == {
        "TenantId": "tenant-1",
        "UserId": "user-1",
        "OrganizationId": "org-1",
        "TokenVersion": "3",
        "SecurityStamp": "stamp-9",
    }


async def test_create_honours_ttl_override():
    cache = FakeCache()
    store = DelegationGrantStore(cache)

    delegation_id = await store.create_async(authenticated_context(), "1", "s", ttl_seconds=6 * 3600)

    assert cache.ttls[constants.grant_key(delegation_id)] == 6 * 3600


async def test_create_falls_back_to_default_ttl_when_override_is_not_positive():
    cache = FakeCache()
    store = DelegationGrantStore(cache)

    delegation_id = await store.create_async(authenticated_context(), "1", "s", ttl_seconds=0)

    assert cache.ttls[constants.grant_key(delegation_id)] == constants.DEFAULT_GRANT_TTL_SECONDS


@pytest.mark.parametrize(
    "context",
    [
        None,
        authenticated_context(tenant_id=""),
        authenticated_context(user_id=""),
    ],
)
async def test_create_rejects_a_context_without_tenant_and_user(context):
    store = DelegationGrantStore(FakeCache())

    with pytest.raises(ValueError):
        await store.create_async(context, "1", "s")


async def test_get_round_trips_the_record():
    cache = FakeCache()
    store = DelegationGrantStore(cache)

    delegation_id = await store.create_async(authenticated_context(), "7", "stamp-7")
    record = await store.get_async(delegation_id)

    assert record == DelegationGrantRecord(
        TenantId="tenant-1", UserId="user-1", OrganizationId="org-1", TokenVersion="7", SecurityStamp="stamp-7"
    )


async def test_get_returns_none_for_a_malformed_id_without_touching_redis():
    cache = FakeCache()
    store = DelegationGrantStore(cache)

    assert await store.get_async("not-a-grant") is None
    assert cache.get_calls == 0


async def test_get_returns_none_when_stored_json_is_unreadable():
    cache = FakeCache()
    store = DelegationGrantStore(cache)
    delegation_id = "dg_" + "a" * 64
    cache.values[constants.grant_key(delegation_id)] = "{not-json"

    assert await store.get_async(delegation_id) is None


async def test_delete_removes_the_grant_key():
    cache = FakeCache()
    store = DelegationGrantStore(cache)

    delegation_id = await store.create_async(authenticated_context(), "1", "s")
    await store.delete_async(delegation_id)

    assert cache.removed == [constants.grant_key(delegation_id)]


async def test_delete_ignores_a_malformed_id():
    cache = FakeCache()
    store = DelegationGrantStore(cache)

    await store.delete_async("nonsense")
    await store.delete_async(None)

    assert cache.removed == []


async def test_delete_swallows_redis_failures_so_the_ttl_remains_the_backstop():
    cache = FakeCache()
    cache.raise_on_remove = TimeoutError("redis down")
    store = DelegationGrantStore(cache)

    await store.delete_async("dg_" + "b" * 64)  # must not raise


# --------------------------------------------------------------------------- factory


async def test_factory_returns_none_without_a_context():
    factory = DelegationGrantFactory(DelegationGrantStore(FakeCache()))

    assert await factory.create_for_send_async() is None


async def test_factory_returns_none_when_the_user_is_not_authenticated():
    BlocksContextManager.set_context(authenticated_context(authenticated=False))
    AuthClaimsContext.set({"token_version": 2, "security_stamp": "s"})

    cache = FakeCache()
    factory = DelegationGrantFactory(DelegationGrantStore(cache))

    assert await factory.create_for_send_async() is None
    assert cache.set_calls == 0


async def test_factory_returns_none_when_there_is_no_version_material():
    BlocksContextManager.set_context(authenticated_context())

    cache = FakeCache()
    factory = DelegationGrantFactory(DelegationGrantStore(cache))

    assert await factory.create_for_send_async() is None
    assert cache.set_calls == 0


async def test_factory_reads_version_material_from_the_validated_claims():
    BlocksContextManager.set_context(authenticated_context())
    # Numeric token_version is coerced to a string, matching the .NET claim value.
    AuthClaimsContext.set({"token_version": 4, "security_stamp": "stamp-4"})

    cache = FakeCache()
    factory = DelegationGrantFactory(DelegationGrantStore(cache))

    delegation_id = await factory.create_for_send_async()

    assert delegation_id is not None
    payload = json.loads(cache.values[constants.grant_key(delegation_id)])
    assert payload["TokenVersion"] == "4"
    assert payload["SecurityStamp"] == "stamp-4"


async def test_factory_forwards_the_ttl_override():
    BlocksContextManager.set_context(authenticated_context())
    AuthClaimsContext.set({"token_version": 1, "security_stamp": "s"})

    cache = FakeCache()
    factory = DelegationGrantFactory(DelegationGrantStore(cache))

    delegation_id = await factory.create_for_send_async(ttl_seconds=9 * 3600)

    assert cache.ttls[constants.grant_key(delegation_id)] == 9 * 3600


async def test_factory_chains_from_the_held_grant_for_worker_originated_sends():
    cache = FakeCache()
    store = DelegationGrantStore(cache)

    BlocksContextManager.set_context(authenticated_context())
    AuthClaimsContext.set({"token_version": 5, "security_stamp": "stamp-5"})
    held = await store.create_async(BlocksContextManager.get_context(), "5", "stamp-5")

    # A worker has no request claims, only the grant it is holding.
    AuthClaimsContext.clear()
    DelegatedTokenContext.set(held)

    factory = DelegationGrantFactory(store)
    chained = await factory.create_for_send_async()

    assert chained is not None and chained != held
    payload = json.loads(cache.values[constants.grant_key(chained)])
    assert payload["TokenVersion"] == "5"
    assert payload["SecurityStamp"] == "stamp-5"


async def test_factory_does_not_chain_a_grant_from_another_tenant():
    cache = FakeCache()
    store = DelegationGrantStore(cache)

    other_tenant_grant = await store.create_async(
        authenticated_context(tenant_id="other-tenant"), "5", "stamp-5"
    )

    BlocksContextManager.set_context(authenticated_context(tenant_id="tenant-1"))
    AuthClaimsContext.clear()
    DelegatedTokenContext.set(other_tenant_grant)

    factory = DelegationGrantFactory(store)

    assert await factory.create_for_send_async() is None


async def test_factory_returns_none_when_the_store_raises():
    class ExplodingStore(DelegationGrantStore):
        async def create_async(self, *args, **kwargs):
            raise TimeoutError("redis down")

    BlocksContextManager.set_context(authenticated_context())
    AuthClaimsContext.set({"token_version": 1, "security_stamp": "s"})

    factory = DelegationGrantFactory(ExplodingStore(FakeCache()))

    # A send must not fail because delegation could not be set up.
    assert await factory.create_for_send_async() is None


# --------------------------------------------------------------------------- context


def test_delegated_token_context_rejects_malformed_ids():
    DelegatedTokenContext.set("dg_short")
    assert DelegatedTokenContext.current() is None
    assert not DelegatedTokenContext.has_grant()

    valid = "dg_" + "c" * 64
    DelegatedTokenContext.set(valid)
    assert DelegatedTokenContext.current() == valid
    assert DelegatedTokenContext.has_grant()

    DelegatedTokenContext.clear()
    assert not DelegatedTokenContext.has_grant()


def test_auth_claims_context_coerces_version_material_to_strings():
    AuthClaimsContext.set({"token_version": 12, "security_stamp": "abc"})
    assert AuthClaimsContext.version_material() == ("12", "abc")

    AuthClaimsContext.set({})
    assert AuthClaimsContext.version_material() == (None, None)

    AuthClaimsContext.clear()
    assert AuthClaimsContext.version_material() == (None, None)
