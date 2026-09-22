"""The provider row, the algorithm enum, the selector and the store.

Ports genesis-net `f9eb915` and `8b85991`.

Story: ABC Ltd (ABC001) trusts two Auth0 applications and one Okta tenant. Sara signs in
through Auth0. Which row validated her token, and with which key, is decided here.
"""
import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from blocks_genesis._auth import third_party_selector as sel
from blocks_genesis._auth.jwt_signing_algorithm import (
    JwtSigningAlgorithm,
    is_single_key_source,
    is_symmetric,
    to_wire_names,
)
from blocks_genesis._auth.third_party_provider import (
    ThirdPartyClaimsMapping,
    ThirdPartyJwtProvider,
)
from blocks_genesis._auth.third_party_provider_store import (
    CACHE_LIFETIME_SECONDS,
    COLLECTION_NAME,
    ThirdPartyJwtProviderStore,
)
from blocks_genesis._auth.third_party_selector import ThirdPartyProviderSelection as Outcome

AUTH0 = "https://abc.eu.auth0.com/"
OKTA = "https://abc.okta.com"


def _row(key="auth0-main", issuer=AUTH0, audiences=("api://klax",), **kwargs):
    return ThirdPartyJwtProvider(
        _id=key,
        TenantId="ABC001",
        Key=key,
        IsActive=True,
        Issuer=issuer,
        Audiences=list(audiences),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# The algorithm enum -- the numbers are the stored contract
# ---------------------------------------------------------------------------

def test_the_stored_numbers_never_move():
    assert [
        JwtSigningAlgorithm.UNSPECIFIED, JwtSigningAlgorithm.RS256, JwtSigningAlgorithm.RS384,
        JwtSigningAlgorithm.RS512, JwtSigningAlgorithm.ES256, JwtSigningAlgorithm.ES384,
        JwtSigningAlgorithm.ES512, JwtSigningAlgorithm.PS256, JwtSigningAlgorithm.PS384,
        JwtSigningAlgorithm.PS512, JwtSigningAlgorithm.HS256, JwtSigningAlgorithm.HS384,
        JwtSigningAlgorithm.HS512,
    ] == list(range(13))


def test_only_the_hmac_family_is_symmetric():
    assert is_symmetric(JwtSigningAlgorithm.HS256)
    assert is_symmetric(JwtSigningAlgorithm.HS512)
    assert not is_symmetric(JwtSigningAlgorithm.RS256)
    assert not is_symmetric(JwtSigningAlgorithm.ES384)
    assert not is_symmetric(JwtSigningAlgorithm.PS512)


def test_wire_names_match_what_pyjwt_expects():
    assert to_wire_names([JwtSigningAlgorithm.RS256, JwtSigningAlgorithm.HS512]) == ["RS256", "HS512"]


def test_unspecified_contributes_no_wire_name():
    assert to_wire_names([JwtSigningAlgorithm.UNSPECIFIED]) == []
    assert to_wire_names(None) == []


def test_wire_names_are_deduplicated():
    assert to_wire_names([JwtSigningAlgorithm.RS256, JwtSigningAlgorithm.RS256]) == ["RS256"]


@pytest.mark.parametrize("algorithms", [
    [],
    None,
    [JwtSigningAlgorithm.UNSPECIFIED],
    [JwtSigningAlgorithm.RS256, JwtSigningAlgorithm.UNSPECIFIED],
    [JwtSigningAlgorithm.RS256, JwtSigningAlgorithm.HS256],
])
def test_a_row_without_one_clear_key_source_is_refused(algorithms):
    assert is_single_key_source(algorithms) is False


@pytest.mark.parametrize("algorithms", [
    [JwtSigningAlgorithm.RS256],
    [JwtSigningAlgorithm.RS256, JwtSigningAlgorithm.PS512, JwtSigningAlgorithm.ES256],
    [JwtSigningAlgorithm.HS256, JwtSigningAlgorithm.HS384],
])
def test_one_family_is_one_key_source(algorithms):
    assert is_single_key_source(algorithms) is True


# ---------------------------------------------------------------------------
# The row
# ---------------------------------------------------------------------------

def test_the_row_reads_the_bson_names():
    row = ThirdPartyJwtProvider(**{
        "_id": "p1", "TenantId": "ABC001", "Key": "auth0-main", "IsActive": True,
        "Issuer": AUTH0, "Audiences": ["api://klax"], "Algorithms": [1, 10],
        "JwksUrl": "https://abc.eu.auth0.com/.well-known/jwks.json",
        "SigningSecretCipher": "box", "DefaultOrganizationId": "support",
        "ClaimsMapping": {"UserId": "sub", "Roles": "realm_access.roles"},
    })

    assert row.key == "auth0-main"
    assert row.algorithms == [JwtSigningAlgorithm.RS256, JwtSigningAlgorithm.HS256]
    assert row.default_organization_id == "support"
    assert row.claims_mapping.user_id == "sub"
    assert row.claims_mapping.roles == "realm_access.roles"


@pytest.mark.parametrize("stored", [None, "", "   "])
def test_a_blank_organization_becomes_default_on_assignment(stored):
    """Normalised once here, not at each place that reads an organization -- some of
    which collapse a blank to "default" while others deny it outright."""
    assert _row(DefaultOrganizationId=stored).default_organization_id == "default"


def test_a_missing_organization_field_becomes_default():
    assert _row().default_organization_id == "default"


def test_an_unknown_algorithm_number_is_dropped_not_fatal():
    """One row written by a newer service must not lock every provider out of this process."""
    assert _row(Algorithms=[1, 99, 10]).algorithms == [
        JwtSigningAlgorithm.RS256, JwtSigningAlgorithm.HS256
    ]


def test_unknown_fields_are_ignored():
    assert _row(SomethingAddedLater="x").key == "auth0-main"


def test_a_blank_mapping_identifies_nobody():
    assert ThirdPartyClaimsMapping().is_configured() is False
    assert ThirdPartyClaimsMapping(UserId="sub").is_configured() is True
    assert ThirdPartyClaimsMapping(Roles="groups").is_configured() is True


# ---------------------------------------------------------------------------
# The selector
# ---------------------------------------------------------------------------

def test_no_rows_at_all():
    assert sel.select([], AUTH0, ["api://klax"], None).outcome is Outcome.NO_PROVIDERS
    assert sel.select(None, AUTH0, ["api://klax"], None).outcome is Outcome.NO_PROVIDERS


def test_issuer_and_audience_identify_one_row_without_the_header():
    auth0, okta = _row("auth0-main"), _row("okta-hr", issuer=OKTA)

    result = sel.select([auth0, okta], AUTH0, ["api://klax"], None)

    assert result.provider is auth0
    assert result.outcome is Outcome.SELECTED
    assert result.candidate_count == 1


def test_the_issuer_is_matched_exactly():
    """Auth0's issuer carries a trailing slash. Okta's does not. One character decides."""
    result = sel.select([_row(issuer=AUTH0)], "https://abc.eu.auth0.com", ["api://klax"], None)

    assert result.outcome is Outcome.ISSUER_UNMATCHED


def test_the_issuer_is_case_sensitive():
    result = sel.select([_row(issuer=AUTH0)], AUTH0.upper(), ["api://klax"], None)

    assert result.outcome is Outcome.ISSUER_UNMATCHED


def test_an_unmatched_audience_is_refused():
    result = sel.select([_row()], AUTH0, ["api://something-else"], None)

    assert result.outcome is Outcome.AUDIENCE_UNMATCHED
    assert result.candidate_count == 1


def test_an_empty_audience_list_disables_the_audience_filter():
    row = _row(audiences=())

    assert sel.select([row], AUTH0, ["anything"], None).provider is row
    assert sel.select([row], AUTH0, None, None).provider is row


def test_two_identical_rows_need_the_header():
    a, b = _row("auth0-app1"), _row("auth0-app2")

    result = sel.select([a, b], AUTH0, ["api://klax"], None)

    assert result.outcome is Outcome.AMBIGUOUS_NO_HEADER
    assert result.candidate_count == 2


def test_the_header_picks_between_two_identical_rows():
    a, b = _row("auth0-app1"), _row("auth0-app2")

    assert sel.select([a, b], AUTH0, ["api://klax"], "auth0-app2").provider is b


def test_the_header_can_never_reach_outside_the_candidates():
    """Okta is not a candidate for an Auth0 token, so naming it must not select it."""
    a, b = _row("auth0-app1"), _row("auth0-app2")
    okta = _row("okta-hr", issuer=OKTA)

    result = sel.select([a, b, okta], AUTH0, ["api://klax"], "okta-hr")

    assert result.outcome is Outcome.AMBIGUOUS_HEADER_UNMATCHED
    assert result.provider is None


def test_the_header_is_ignored_when_one_candidate_remains():
    row = _row("auth0-main")

    assert sel.select([row], AUTH0, ["api://klax"], "something-else").provider is row


def test_a_token_with_no_issuer_reaches_only_issuerless_rows():
    blank = _row("inhouse", issuer="", audiences=())

    assert sel.select([_row(), blank], None, None, None).provider is blank
    assert sel.select([_row(), blank], "", None, None).provider is blank


def test_a_token_with_no_issuer_and_no_issuerless_row_is_refused():
    result = sel.select([_row()], None, None, None)

    assert result.outcome is Outcome.ISSUER_ABSENT_UNMATCHED


def test_a_blank_issuer_is_not_a_wildcard():
    """The two sets are disjoint. A token that names an issuer must never be handed to
    whichever row left the field blank -- for an HMAC row that means trying its shared
    secret against a token it was never meant to see."""
    blank = _row("inhouse", issuer="", audiences=())

    result = sel.select([blank], AUTH0, ["api://klax"], None)

    assert result.provider is None
    assert result.outcome is Outcome.ISSUER_UNMATCHED


def test_two_issuerless_rows_also_need_the_header():
    a = _row("inhouse-a", issuer="", audiences=())
    b = _row("inhouse-b", issuer="", audiences=())

    assert sel.select([a, b], None, None, None).outcome is Outcome.AMBIGUOUS_NO_HEADER
    assert sel.select([a, b], None, None, "inhouse-b").provider is b


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

def _database(rows, fail=False):
    cursor = MagicMock()
    if fail:
        cursor.to_list = AsyncMock(side_effect=RuntimeError("mongo is down"))
    else:
        cursor.to_list = AsyncMock(return_value=rows)
    collection = MagicMock()
    collection.find = MagicMock(return_value=cursor)
    database = MagicMock()
    database.__getitem__ = MagicMock(return_value=collection)
    return database, collection


@pytest.mark.asyncio
async def test_the_store_reads_active_rows_for_one_tenant():
    database, collection = _database([{"_id": "p1", "TenantId": "ABC001", "Key": "auth0-main"}])

    rows = await ThirdPartyJwtProviderStore(database).get_active("ABC001")

    database.__getitem__.assert_called_with(COLLECTION_NAME)
    collection.find.assert_called_once_with({"TenantId": "ABC001", "IsActive": True})
    assert [r.key for r in rows] == ["auth0-main"]


@pytest.mark.asyncio
async def test_a_blank_tenant_reads_nothing():
    database, collection = _database([])

    assert await ThirdPartyJwtProviderStore(database).get_active("") == []
    collection.find.assert_not_called()


@pytest.mark.asyncio
async def test_the_second_read_comes_from_the_cache():
    database, collection = _database([{"_id": "p1", "TenantId": "ABC001", "Key": "auth0-main"}])
    store = ThirdPartyJwtProviderStore(database)

    await store.get_active("ABC001")
    await store.get_active("ABC001")

    assert collection.find.call_count == 1


@pytest.mark.asyncio
async def test_invalidate_forces_a_reload():
    database, collection = _database([{"_id": "p1", "TenantId": "ABC001", "Key": "auth0-main"}])
    store = ThirdPartyJwtProviderStore(database)

    await store.get_active("ABC001")
    store.invalidate("ABC001")
    await store.get_active("ABC001")

    assert collection.find.call_count == 2


@pytest.mark.asyncio
async def test_the_cache_expires_after_a_minute():
    database, collection = _database([{"_id": "p1", "TenantId": "ABC001", "Key": "auth0-main"}])
    store = ThirdPartyJwtProviderStore(database)

    await store.get_active("ABC001")
    rows, loaded_at = store._cache["ABC001"]
    store._cache["ABC001"] = (rows, loaded_at - CACHE_LIFETIME_SECONDS - 1)
    await store.get_active("ABC001")

    assert collection.find.call_count == 2


@pytest.mark.asyncio
async def test_a_burst_on_a_cold_cache_is_one_query():
    database, collection = _database([{"_id": "p1", "TenantId": "ABC001", "Key": "auth0-main"}])
    store = ThirdPartyJwtProviderStore(database)

    await asyncio.gather(*(store.get_active("ABC001") for _ in range(10)))

    assert collection.find.call_count == 1


@pytest.mark.asyncio
async def test_an_outage_refuses_rather_than_failing_open():
    """An empty list rejects the request. The alternative is falling through to some
    other validator because Mongo blinked."""
    database, _ = _database(None, fail=True)

    assert await ThirdPartyJwtProviderStore(database).get_active("ABC001") == []


@pytest.mark.asyncio
async def test_the_root_database_is_resolved_lazily():
    """Reading the vault in the constructor would make building this store depend on
    secrets already being loaded -- a startup ordering trap for every host."""
    from blocks_genesis._auth import third_party_provider_store as mod

    store = ThirdPartyJwtProviderStore()          # no vault read here
    database, _ = _database([])

    with patch.object(mod, "get_blocks_secret") as secret, \
         patch.object(mod, "AsyncIOMotorClient") as client:
        secret.return_value = MagicMock(
            DatabaseConnectionString="mongodb://main", RootDatabaseName="root"
        )
        client.return_value.__getitem__.return_value = database
        await store.get_active("ABC001")

    client.assert_called_once_with("mongodb://main")


def test_the_module_singleton_can_be_reset():
    from blocks_genesis._auth.third_party_provider_store import (
        get_third_party_provider_store,
        initialize_third_party_provider_store,
        reset_third_party_provider_store,
    )

    reset_third_party_provider_store()
    assert get_third_party_provider_store() is None

    created = initialize_third_party_provider_store(MagicMock())
    assert get_third_party_provider_store() is created
    assert initialize_third_party_provider_store(MagicMock()) is created

    reset_third_party_provider_store()
    assert get_third_party_provider_store() is None
