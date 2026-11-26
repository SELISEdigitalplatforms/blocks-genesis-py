"""A tenant whose root records include disabled duplicates (same TenantId) resolves to its
enabled record, by id and by domain, whatever the insertion order.

The collection is an in-memory fake: find_one returns the first document in insertion order
(Mongo's natural order) that matches the filter. It evaluates only the operators the tenant
service uses.
"""
import asyncio
from unittest.mock import MagicMock

from blocks_genesis._tenant.tenant import Tenant
from blocks_genesis._tenant.tenant_service import TenantCacheUpdateMessage, TenantService

ENABLED = {"IsDisabled": {"$ne": True}}
_MISSING = object()


def _matches(doc, query):
    """Implicit AND over the query's fields, plus $or."""
    for key, condition in query.items():
        if key == "$or":
            if not any(_matches(doc, branch) for branch in condition):
                return False
        elif not _matches_value(doc.get(key, _MISSING), condition):
            return False
    return True


def _matches_value(value, condition):
    if not (isinstance(condition, dict) and condition and all(k.startswith("$") for k in condition)):
        return value == condition
    for op, arg in condition.items():
        if op == "$ne":
            # As in Mongo, a missing field is not equal to the value.
            if value == arg:
                return False
        elif op == "$in":
            if value not in arg:
                return False
        elif op == "$elemMatch":
            if not isinstance(value, list) or not any(
                isinstance(item, dict) and _matches(item, arg) for item in value
            ):
                return False
        else:
            raise NotImplementedError(op)
    return True


class FakeTenants:
    def __init__(self, *docs):
        self.docs, self.queries = list(docs), []

    async def find_one(self, query, *args, **kwargs):
        self.queries.append(query)
        return next((d for d in self.docs if _matches(d, query)), None)


class FakeClock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


def _svc(fake, clock=None):
    s = TenantService.__new__(TenantService)
    s.cache = MagicMock()
    s.database = MagicMock()
    s.database.__getitem__.return_value = fake
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


def _disabled(rid="d1", tid="t1", **overrides):
    doc = {
        "_id": rid,
        "TenantId": tid,
        "DBName": "old",
        "DbConnectionString": "mongodb://old",
        "IsRootTenant": False,
        "IsDisabled": True,
    }
    doc.update(overrides)
    return doc


def _enabled(rid="e1", tid="t1", **overrides):
    doc = {
        "_id": rid,
        "TenantId": tid,
        "DBName": "db",
        "DbConnectionString": "mongodb://live",
        "IsRootTenant": False,
        "IsDisabled": False,
    }
    doc.update(overrides)
    return doc


def _upsert(tid="t1"):
    return TenantCacheUpdateMessage(
        action="upsert", tenant=Tenant(_id=tid, TenantId=tid)
    ).model_dump_json(by_alias=True)


def _verified(domain="https://x.com"):
    return [{"Domain": domain, "IsDomainVerified": True}]


def _claimed(domain="https://x.com"):
    return [{"Domain": domain}]


async def test_fake_returns_the_first_match_in_insertion_order():
    fake = FakeTenants(_disabled(), _enabled())
    id_query = {"$or": [{"_id": "t1"}, {"TenantId": "t1"}]}

    # The 4.0.1 query returns the disabled duplicate: the fake reproduces the incident.
    assert (await fake.find_one(id_query))["_id"] == "d1"
    assert (await fake.find_one({**id_query, **ENABLED}))["_id"] == "e1"
    # A record without IsDisabled counts as enabled, as in Mongo.
    assert await FakeTenants({"_id": "r1", "TenantId": "t1"}).find_one({**id_query, **ENABLED})


async def _assert_resolves_to_the_enabled_record(fake):
    s = _svc(fake)

    tenant = await s.get_tenant("t1")

    assert tenant is not None
    assert not tenant.is_disabled
    assert tenant.db_connection_string == "mongodb://live"
    assert await s.get_db_connection("t1") == ("db", "mongodb://live")
    assert "t1" in s._tenant_cache
    assert not s._is_known_unknown(s._unknown_tenants, "t1")


async def test_id_lookup_returns_the_enabled_record_behind_a_disabled_duplicate():
    await _assert_resolves_to_the_enabled_record(FakeTenants(_disabled(), _enabled()))


async def test_id_lookup_skips_two_disabled_duplicates():
    await _assert_resolves_to_the_enabled_record(
        FakeTenants(_disabled("d1"), _disabled("d2"), _enabled())
    )


async def test_id_lookup_skips_a_disabled_record_matched_by_id():
    s = _svc(FakeTenants(_disabled("t1"), _enabled("r2")))

    tenant = await s.get_tenant("t1")

    assert tenant is not None and tenant.id == "r2"


async def test_id_lookup_by_record_id_still_finds_an_enabled_record():
    s = _svc(FakeTenants({"_id": "r9", "TenantId": "t9"}))

    assert (await s._find_tenant("r9")).tenant_id == "t9"


async def test_tenant_with_only_disabled_records_stays_unknown():
    clock = FakeClock()
    fake = FakeTenants(_disabled("d1"), _disabled("d2"))
    s = _svc(fake, clock)

    assert await s.get_tenant("t1") is None
    assert await s.get_db_connection("t1") == (None, None)
    assert "t1" not in s._tenant_cache
    reads = len(fake.queries)

    clock.now = 29
    assert await s.get_tenant("t1") is None
    assert len(fake.queries) == reads


async def test_cache_update_rereads_the_enabled_duplicate():
    s = _svc(FakeTenants(_disabled(), _enabled()))

    await s._handle_update(_upsert("t1"))

    cached = s._tenant_cache.get("t1")
    assert cached is not None and cached.db_connection_string == "mongodb://live"


async def test_stale_tenant_is_kept_when_a_disabled_duplicate_exists():
    clock = FakeClock()
    s = _svc(FakeTenants(_disabled(), _enabled()), clock)
    s._tenant_cache["t1"] = Tenant(**_enabled())
    s._tenant_loaded_at["t1"] = 0
    clock.now = 60

    tenant = await s.get_tenant("t1")

    assert tenant is not None and tenant.db_connection_string == "mongodb://live"
    assert "t1" in s._tenant_cache
    assert s._tenant_loaded_at["t1"] == 60


async def test_domain_lookup_skips_a_disabled_duplicate_that_verified_the_domain():
    s = _svc(FakeTenants(
        _disabled(Applications=_verified()), _enabled(Applications=_verified())
    ))

    tenant = await s.get_tenant_by_domain("x.com")

    assert tenant is not None and tenant.id == "e1"
    assert s._tenant_cache.get("t1") is tenant


async def test_domain_lookup_prefers_an_enabled_claimant_over_a_disabled_verified_owner():
    fake = FakeTenants(_disabled(Applications=_verified()), _enabled(Applications=_claimed()))
    s = _svc(fake)

    tenant = await s.get_tenant_by_domain("x.com")

    assert tenant is not None and tenant.id == "e1"
    assert len(fake.queries) == 2
    assert "IsDomainVerified" not in fake.queries[-1]["Applications"]["$elemMatch"]


async def test_domain_lookup_with_only_disabled_owners_is_none():
    s = _svc(FakeTenants(_disabled(Applications=_verified())))

    assert await s.get_tenant_by_domain("x.com") is None
    assert "t1" not in s._tenant_cache
    assert s._is_known_unknown(s._unknown_domains, "x.com")


async def test_every_tenant_query_excludes_disabled_records():
    fake = FakeTenants(_disabled(), _enabled(Applications=_claimed()))
    s = _svc(fake)

    await s.get_tenant("t1")
    await s._handle_update(_upsert("t1"))
    s._tenant_cache.clear()
    await s.get_tenant_by_domain("x.com")

    assert len(fake.queries) == 4
    assert all(query.get("IsDisabled") == {"$ne": True} for query in fake.queries)
