"""Mongo routing across three connections.

Ports genesis-net `fe09629`: a database handle is keyed by connection AND database name
together, and the tenant is resolved on every call.

Story: ABC Ltd (ABC001) lives on the dev cluster, XYZ Ltd (XYZ001) on main. Both
databases are called "blocksdb". Before this, the second lookup matched on the name alone
and got the first one's handle -- so one customer's write landed in the other's cluster,
with no error and nothing in the log.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from blocks_genesis._database import mongo_context as mc
from blocks_genesis._database.mongo_context import MongoDbContextProvider

P = "blocks_genesis._database.mongo_context."

DEV = "mongodb://dev-cluster"
MAIN = "mongodb://main-cluster"


@pytest.fixture(autouse=True)
def _clean_caches():
    mc._databases.clear()
    mc._clients.clear()
    yield
    mc._databases.clear()
    mc._clients.clear()


def _provider(tenants=None):
    with patch(P + "get_tenant_service", return_value=tenants or MagicMock()), \
         patch(P + "register"), patch(P + "MongoEventSubscriber"):
        return MongoDbContextProvider()


def _client_factory():
    """A MongoClient stand-in where each connection yields its own database objects."""
    made = {}

    def factory(connection_string, **_kwargs):
        if connection_string not in made:
            client = MagicMock(name=connection_string)
            client.__getitem__.side_effect = lambda name: MagicMock(
                name=f"{connection_string}/{name}"
            )
            made[connection_string] = client
        return made[connection_string]

    factory.made = made
    return factory


# ---------------------------------------------------------------------------
# The bug this change fixes
# ---------------------------------------------------------------------------

def test_same_database_name_on_two_clusters_gives_two_handles():
    provider = _provider()
    factory = _client_factory()

    with patch(P + "MongoClient", side_effect=factory):
        abc = provider.get_database_by_connection(DEV, "blocksdb")
        xyz = provider.get_database_by_connection(MAIN, "blocksdb")

    assert abc is not xyz


def test_the_same_connection_and_name_reuse_one_handle():
    provider = _provider()
    factory = _client_factory()

    with patch(P + "MongoClient", side_effect=factory):
        first = provider.get_database_by_connection(DEV, "blocksdb")
        second = provider.get_database_by_connection(DEV, "blocksdb")

    assert first is second


def test_the_database_name_is_matched_exactly():
    """Keys used to be lowercased, which merged two differently-named databases."""
    provider = _provider()
    factory = _client_factory()

    with patch(P + "MongoClient", side_effect=factory):
        lower = provider.get_database_by_connection(DEV, "blocksdb")
        upper = provider.get_database_by_connection(DEV, "BlocksDb")

    assert lower is not upper


# ---------------------------------------------------------------------------
# One client per connection
# ---------------------------------------------------------------------------

def test_one_client_per_connection_is_reused():
    provider = _provider()
    factory = _client_factory()

    with patch(P + "MongoClient", side_effect=factory) as ctor:
        provider.get_database_by_connection(DEV, "blocksdb")
        provider.get_database_by_connection(DEV, "other")
        provider.get_database_by_connection(MAIN, "blocksdb")

    assert ctor.call_count == 2
    assert set(factory.made) == {DEV, MAIN}


def test_the_client_carries_the_dotnet_settings():
    provider = _provider()

    with patch(P + "MongoClient", side_effect=_client_factory()) as ctor:
        provider.get_database_by_connection(DEV, "blocksdb")

    _args, kwargs = ctor.call_args
    assert kwargs["retryReads"] is True
    assert kwargs["retryWrites"] is True
    assert kwargs["serverSelectionTimeoutMS"] == 15000
    assert kwargs["connectTimeoutMS"] == 10000


# ---------------------------------------------------------------------------
# The tenant is resolved on every call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_tenant_is_resolved_on_every_call():
    tenants = MagicMock()
    tenants.get_db_connection = AsyncMock(return_value=("blocksdb", DEV))
    provider = _provider(tenants)

    with patch(P + "MongoClient", side_effect=_client_factory()):
        await provider.get_database("ABC001")
        await provider.get_database("ABC001")

    assert tenants.get_db_connection.await_count == 2


@pytest.mark.asyncio
async def test_moving_a_tenant_changes_the_cluster_on_the_next_call():
    """ABC Ltd moves from the dev cluster to main while the process stays alive."""
    tenants = MagicMock()
    tenants.get_db_connection = AsyncMock(return_value=("blocksdb", DEV))
    provider = _provider(tenants)
    factory = _client_factory()

    with patch(P + "MongoClient", side_effect=factory):
        before = await provider.get_database("ABC001")
        tenants.get_db_connection.return_value = ("blocksdb", MAIN)
        after = await provider.get_database("ABC001")

    assert before is not after


@pytest.mark.asyncio
async def test_two_tenants_on_two_clusters_never_mix():
    tenants = MagicMock()
    placement = {"ABC001": ("blocksdb", DEV), "XYZ001": ("blocksdb", MAIN)}
    tenants.get_db_connection = AsyncMock(side_effect=lambda t: placement[t])
    provider = _provider(tenants)

    with patch(P + "MongoClient", side_effect=_client_factory()):
        abc = await provider.get_database("ABC001")
        xyz = await provider.get_database("XYZ001")
        abc_again = await provider.get_database("ABC001")

    assert abc is not xyz
    assert abc is abc_again


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_tenant_with_no_placement_is_an_error_not_a_fallback():
    tenants = MagicMock()
    tenants.get_db_connection = AsyncMock(return_value=(None, None))
    provider = _provider(tenants)

    with pytest.raises(ValueError):
        await provider.get_database("ABC001")


@pytest.mark.asyncio
async def test_no_tenant_at_all_gives_nothing():
    provider = _provider()

    with patch(P + "BlocksContextManager") as bcm:
        bcm.get_context.return_value = None
        assert await provider.get_database(None) is None


def test_blank_arguments_are_refused():
    provider = _provider()

    with pytest.raises(ValueError):
        provider.get_database_by_connection("", "blocksdb")
    with pytest.raises(ValueError):
        provider.get_database_by_connection(DEV, "")
