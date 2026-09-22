"""End-to-end validation of an external token, with real keys and real signatures.

Ports genesis-net `f9eb915` and `8b85991`. Nothing is mocked below the JWT library, so
these hold the parts that matter: the algorithm comes from configuration and never from
the token, the key source follows the algorithm, and the claim mapping reads what the
provider was configured to read.

Story: ABC Ltd (ABC001) trusts Auth0. Sara's token is signed by Auth0's key.
"""
import base64
import datetime
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from blocks_genesis._auth import auth
from blocks_genesis._auth.third_party_provider import ThirdPartyJwtProvider
from blocks_genesis._tenant.tenant import Tenant
from blocks_genesis._utilities.crypto_service import CryptoService

AUTH0 = "https://abc.eu.auth0.com/"
AUDIENCE = "api://klax"
SALT = "8f14e45fceea167a5a36dedd4bea2543"


# ---------------------------------------------------------------------------
# Real key material, built once
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "abc.eu.auth0.com")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    return {
        "private_pem": key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        "public_pem": key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ),
        "cert_pem": certificate.public_bytes(serialization.Encoding.PEM),
        "cert_der": certificate.public_bytes(serialization.Encoding.DER),
        "pfx": pkcs12.serialize_key_and_certificates(
            b"abc", key, certificate, None, serialization.NoEncryption()
        ),
        "pfx_locked": pkcs12.serialize_key_and_certificates(
            b"abc", key, certificate, None,
            serialization.BestAvailableEncryption(b"s3cret"),
        ),
    }


def _tenant(salt=SALT):
    return Tenant(_id="ABC001", TenantId="ABC001", TenantSalt=salt, IsThirdPartyJwtEnabled=True)


def _provider(**kwargs):
    defaults = dict(
        _id="p1", TenantId="ABC001", Key="auth0-main", IsActive=True,
        Issuer=AUTH0, Audiences=[AUDIENCE], Algorithms=[1],
        ClaimsMapping={"UserId": "sub"},
    )
    defaults.update(kwargs)
    return ThirdPartyJwtProvider(**defaults)


def _request(headers=None):
    request = MagicMock()
    request.url = "https://api.klax.io/x"
    request.headers = headers or {}
    request.query_params = {}
    return request


def _sign(keypair, algorithm="RS256", key=None, **overrides):
    now = datetime.datetime.now(datetime.timezone.utc)
    claims = {
        "sub": "auth0|sara_123",
        "iss": AUTH0,
        "aud": AUDIENCE,
        "iat": now,
        "nbf": now,
        "exp": now + datetime.timedelta(minutes=30),
    }
    claims.update(overrides)
    signing_key = key if key is not None else keypair["private_pem"]
    return jwt.encode(claims, signing_key, algorithm=algorithm)


async def _validate(token, provider, tenant=None, headers=None):
    store = MagicMock()
    store.get_active = AsyncMock(return_value=[provider])
    with patch.object(auth, "get_third_party_provider_store", return_value=store):
        return await auth.validate_with_fallback(
            token, tenant or _tenant(), _request(headers), None
        )


# ---------------------------------------------------------------------------
# The happy paths, one per key source
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_rs256_token_validates_against_an_uploaded_certificate(keypair):
    provider = _provider(PublicCertificatePath="https://abc.example/auth0.crt")

    with patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock) as fetch:
        fetch.return_value = keypair["cert_pem"]
        result = await _validate(_sign(keypair), provider)

    assert result is not None
    assert result.claims["sub"] == "auth0|sara_123"
    assert result.provider.key == "auth0-main"


@pytest.mark.asyncio
async def test_an_hs256_token_validates_against_the_encrypted_secret(keypair):
    secret = "the-shared-secret"
    provider = _provider(
        Algorithms=[10], SigningSecretCipher=CryptoService.encrypt(secret, SALT)
    )

    result = await _validate(_sign(keypair, algorithm="HS256", key=secret), provider)

    assert result is not None
    assert result.claims["sub"] == "auth0|sara_123"


@pytest.mark.asyncio
async def test_a_jwks_provider_is_preferred_over_a_certificate(keypair):
    """A JWKS carries several keys, so it survives a rotation untouched."""
    provider = _provider(
        JwksUrl="https://abc.eu.auth0.com/.well-known/jwks.json",
        PublicCertificatePath="https://abc.example/auth0.crt",
    )
    signing_key = MagicMock()
    signing_key.key = keypair["public_pem"]

    with patch.object(auth, "PyJWKClient") as client, \
         patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock) as fetch:
        client.return_value.get_signing_key_from_jwt.return_value = signing_key
        result = await _validate(_sign(keypair), provider)

    assert result is not None
    fetch.assert_not_awaited()


# ---------------------------------------------------------------------------
# Algorithm confusion -- the attack the pinning exists for
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_hs256_token_signed_with_the_public_key_is_refused(keypair):
    """The classic algorithm-confusion attack: take the RSA public key, use its bytes
    as an HMAC secret, sign HS256. The provider is pinned to RS256, so the token's own
    alg header buys the attacker nothing."""
    # Built by hand: PyJWT refuses to sign HS256 with a PEM key, so signing it through
    # the library would test the library's guard rather than ours.
    def b64(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    exp = int((datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=30)).timestamp())
    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps(
        {"sub": "auth0|attacker", "iss": AUTH0, "aud": AUDIENCE, "exp": exp}
    ).encode())
    signature = hmac.new(
        keypair["public_pem"], f"{header}.{body}".encode(), hashlib.sha256
    ).digest()
    forged = f"{header}.{body}.{b64(signature)}"

    provider = _provider(PublicCertificatePath="https://abc.example/auth0.crt")

    with patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock) as fetch:
        fetch.return_value = keypair["cert_pem"]
        assert await _validate(forged, provider) is None


@pytest.mark.asyncio
async def test_a_token_signed_by_the_wrong_key_is_refused(keypair):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pem = other.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    provider = _provider(PublicCertificatePath="https://abc.example/auth0.crt")

    with patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock) as fetch:
        fetch.return_value = keypair["cert_pem"]
        assert await _validate(_sign(keypair, key=other_pem), provider) is None


# ---------------------------------------------------------------------------
# What the token has to get right
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_expired_token_is_refused(keypair):
    now = datetime.datetime.now(datetime.timezone.utc)
    provider = _provider(PublicCertificatePath="https://abc.example/auth0.crt")

    with patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock) as fetch:
        fetch.return_value = keypair["cert_pem"]
        token = _sign(keypair, exp=now - datetime.timedelta(hours=1))
        assert await _validate(token, provider) is None


@pytest.mark.asyncio
async def test_a_token_just_past_expiry_is_still_accepted(keypair):
    """genesis-net pins ClockSkew to zero on the primary path only, so the third-party
    path keeps the framework's five minutes. Both runtimes accept the same tokens."""
    now = datetime.datetime.now(datetime.timezone.utc)
    provider = _provider(PublicCertificatePath="https://abc.example/auth0.crt")

    with patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock) as fetch:
        fetch.return_value = keypair["cert_pem"]
        token = _sign(keypair, exp=now - datetime.timedelta(minutes=2))
        assert await _validate(token, provider) is not None


@pytest.mark.asyncio
async def test_the_wrong_audience_is_refused(keypair):
    provider = _provider(PublicCertificatePath="https://abc.example/auth0.crt")

    with patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock) as fetch:
        fetch.return_value = keypair["cert_pem"]
        assert await _validate(_sign(keypair, aud="api://somewhere-else"), provider) is None


@pytest.mark.asyncio
async def test_an_empty_audience_list_accepts_any_audience(keypair):
    provider = _provider(Audiences=[], PublicCertificatePath="https://abc.example/auth0.crt")

    with patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock) as fetch:
        fetch.return_value = keypair["cert_pem"]
        assert await _validate(_sign(keypair, aud="anything-at-all"), provider) is not None


# ---------------------------------------------------------------------------
# Rows that cannot be validated safely
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("algorithms", [[], [0], [1, 10]])
async def test_a_row_without_one_clear_key_source_is_refused(keypair, algorithms):
    provider = _provider(
        Algorithms=algorithms, PublicCertificatePath="https://abc.example/auth0.crt"
    )

    assert await _validate(_sign(keypair), provider) is None


@pytest.mark.asyncio
async def test_an_asymmetric_row_with_no_key_source_is_refused(keypair):
    assert await _validate(_sign(keypair), _provider()) is None


@pytest.mark.asyncio
async def test_an_hmac_row_with_no_secret_is_refused(keypair):
    provider = _provider(Algorithms=[10])

    assert await _validate(_sign(keypair, algorithm="HS256", key="x"), provider) is None


@pytest.mark.asyncio
async def test_a_regenerated_tenant_salt_refuses_the_token(keypair):
    """The only symptom is a 401 carrying a perfectly valid token. Re-save the provider
    to re-encrypt its secret."""
    secret = "the-shared-secret"
    provider = _provider(
        Algorithms=[10], SigningSecretCipher=CryptoService.encrypt(secret, SALT)
    )
    token = _sign(keypair, algorithm="HS256", key=secret)

    assert await _validate(token, provider, tenant=_tenant(salt="a-new-salt")) is None


@pytest.mark.asyncio
async def test_a_tenant_with_no_salt_refuses_the_token(keypair):
    provider = _provider(
        Algorithms=[10], SigningSecretCipher=CryptoService.encrypt("s", SALT)
    )

    assert await _validate("tok", provider, tenant=_tenant(salt="")) is None


@pytest.mark.asyncio
async def test_a_certificate_that_will_not_load_is_refused(keypair):
    provider = _provider(PublicCertificatePath="https://abc.example/auth0.crt")

    with patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock) as fetch:
        fetch.return_value = b"this is not a certificate"
        assert await _validate(_sign(keypair), provider) is None


@pytest.mark.asyncio
async def test_a_certificate_that_cannot_be_fetched_is_refused(keypair):
    provider = _provider(PublicCertificatePath="https://abc.example/auth0.crt")

    with patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock) as fetch:
        fetch.side_effect = RuntimeError("404")
        assert await _validate(_sign(keypair), provider) is None


# ---------------------------------------------------------------------------
# The certificate loader
# ---------------------------------------------------------------------------

def test_the_loader_reads_pem(keypair):
    assert auth.create_certificate(keypair["cert_pem"]) is not None


def test_the_loader_reads_der(keypair):
    assert auth.create_certificate(keypair["cert_der"]) is not None


def test_the_loader_reads_pkcs12(keypair):
    assert auth.create_certificate(keypair["pfx"]) is not None


def test_the_loader_reads_a_pkcs12_with_a_passphrase(keypair):
    assert auth.create_certificate(keypair["pfx_locked"], "s3cret") is not None


def test_the_loader_refuses_a_pkcs12_with_the_wrong_passphrase(keypair):
    assert auth.create_certificate(keypair["pfx_locked"], "wrong") is None


def test_the_loader_returns_the_same_certificate_from_every_format(keypair):
    pem = auth.create_certificate(keypair["cert_pem"])
    der = auth.create_certificate(keypair["cert_der"])
    pfx = auth.create_certificate(keypair["pfx"])

    assert pem.fingerprint(hashes.SHA256()) == der.fingerprint(hashes.SHA256())
    assert pem.fingerprint(hashes.SHA256()) == pfx.fingerprint(hashes.SHA256())


# ---------------------------------------------------------------------------
# Reading iss and aud without validating
# ---------------------------------------------------------------------------

def test_routing_reads_a_single_string_audience(keypair):
    assert auth._read_token_routing(_sign(keypair)) == (AUTH0, [AUDIENCE])


def test_routing_reads_a_list_audience(keypair):
    issuer, audiences = auth._read_token_routing(_sign(keypair, aud=["a", "b"]))

    assert issuer == AUTH0
    assert audiences == ["a", "b"]


def test_routing_of_an_unreadable_token_selects_nothing():
    assert auth._read_token_routing("not-a-token") == ("", [])


def test_routing_of_a_token_with_no_issuer(keypair):
    issuer, _ = auth._read_token_routing(jwt.encode({"sub": "x"}, "k", algorithm="HS256"))

    assert issuer == ""


# ---------------------------------------------------------------------------
# Claim mapping
# ---------------------------------------------------------------------------

def _map(claims, **mapping):
    return auth.map_third_party_claims(claims, _provider(ClaimsMapping=mapping))


def test_sub_resolves_through_the_standard_subject_claim():
    assert _map({"sub": "auth0|sara_123"}, UserId="sub")["subject"] == "auth0|sara_123"


def test_a_uri_claim_name_is_read_literally_and_never_split():
    """Every namespaced OIDC claim is a URI, and they are full of dots."""
    claims = {"https://klax.io/user_id": "sara", "https": "not this"}

    assert _map(claims, UserId="https://klax.io/user_id")["subject"] == "sara"


def test_a_dotted_mapping_reads_inside_a_json_object_claim():
    claims = {"realm_access": {"roles": ["reader", "writer"]}}

    assert _map(claims, Roles="realm_access.roles")["roles"] == ["reader", "writer"]


def test_a_dotted_mapping_reads_inside_a_json_string_claim():
    claims = {"realm_access": json.dumps({"roles": ["reader"]})}

    assert _map(claims, Roles="realm_access.roles")["roles"] == ["reader"]


def test_a_dotted_mapping_against_a_non_json_claim_is_empty():
    assert _map({"realm_access": "plain text"}, Name="realm_access.roles")["display_name"] == ""


def test_x_dot_sub_is_a_different_claim_from_sub():
    assert _map({"sub": "auth0|sara_123"}, UserId="x.sub")["subject"] == ""


def test_a_roles_array_claim_needs_no_parsing():
    assert _map({"groups": ["admin", "ops"]}, Roles="groups")["roles"] == ["admin", "ops"]


def test_a_scalar_roles_claim_is_one_role_not_two():
    """Whether "admin manager" is one role or two cannot be decided by inspection."""
    assert _map({"role": "admin manager"}, Roles="role")["roles"] == ["admin manager"]


def test_the_standard_email_claim_wins_over_the_mapping():
    claims = {"email": "sara@abc.example", "other": "nope@x.example"}

    assert _map(claims, Email="other")["email"] == "sara@abc.example"


def test_username_mapped_to_email_uses_the_standard_email_claim():
    assert _map({"email": "sara@abc.example"}, UserName="EMAIL")["user_name"] == "sara@abc.example"


def test_an_unresolvable_mapping_is_an_empty_field_not_a_crash():
    mapped = _map({"sub": "x"}, UserId="sub", Name="nothing.here", Roles="missing")

    assert mapped["display_name"] == ""
    assert mapped["roles"] == []


def test_a_blank_mapping_identifies_nobody():
    assert auth.map_third_party_claims({"sub": "x"}, _provider(ClaimsMapping={})) is None


# ---------------------------------------------------------------------------
# The remaining branches: cached certificates, passphrases, and the header miss
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_cached_certificate_is_not_fetched_again(keypair):
    """Cached per provider, not per tenant: a tenant may trust several, and a
    tenant-wide slot would hand one provider's certificate to another."""
    provider = _provider(PublicCertificatePath="https://abc.example/auth0.crt")
    cache = MagicMock()
    cache.get_bytes_value.return_value = keypair["cert_pem"]
    cache.add_bytes_value_async = AsyncMock()

    store = MagicMock()
    store.get_active = AsyncMock(return_value=[provider])
    with patch.object(auth, "get_third_party_provider_store", return_value=store), \
         patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock) as fetch:
        result = await auth.validate_with_fallback(
            _sign(keypair), _tenant(), _request(), cache
        )

    assert result is not None
    fetch.assert_not_awaited()
    cache.get_bytes_value.assert_called_once_with("tpprovcert::ABC001::auth0-main")


@pytest.mark.asyncio
async def test_a_freshly_fetched_certificate_is_cached_for_an_hour(keypair):
    provider = _provider(PublicCertificatePath="https://abc.example/auth0.crt")
    cache = MagicMock()
    cache.get_bytes_value.return_value = None
    cache.add_bytes_value_async = AsyncMock()

    store = MagicMock()
    store.get_active = AsyncMock(return_value=[provider])
    with patch.object(auth, "get_third_party_provider_store", return_value=store), \
         patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock) as fetch:
        fetch.return_value = keypair["cert_pem"]
        await auth.validate_with_fallback(_sign(keypair), _tenant(), _request(), cache)

    cache.add_bytes_value_async.assert_awaited_once_with(
        "tpprovcert::ABC001::auth0-main", keypair["cert_pem"], 3600
    )


@pytest.mark.asyncio
async def test_an_encrypted_passphrase_opens_a_locked_pkcs12(keypair):
    provider = _provider(
        PublicCertificatePath="https://abc.example/auth0.pfx",
        PublicCertificatePasswordCipher=CryptoService.encrypt("s3cret", SALT),
    )

    with patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock) as fetch:
        fetch.return_value = keypair["pfx_locked"]
        assert await _validate(_sign(keypair), provider) is not None


@pytest.mark.asyncio
async def test_a_passphrase_that_will_not_decrypt_refuses_the_token(keypair):
    provider = _provider(
        PublicCertificatePath="https://abc.example/auth0.pfx",
        PublicCertificatePasswordCipher="not-a-valid-envelope",
    )

    assert await _validate(_sign(keypair), provider) is None


@pytest.mark.asyncio
async def test_an_unreadable_jwks_refuses_the_token(keypair):
    provider = _provider(JwksUrl="https://abc.eu.auth0.com/.well-known/jwks.json")

    with patch.object(auth, "PyJWKClient") as client:
        client.side_effect = RuntimeError("unreachable")
        assert await _validate(_sign(keypair), provider) is None


@pytest.mark.asyncio
async def test_the_header_naming_a_non_candidate_is_refused(keypair):
    """Okta is not a candidate for an Auth0 token, so naming it must not select it."""
    rows = [
        _provider(Key="auth0-app1", PublicCertificatePath="https://abc.example/a.crt"),
        _provider(_id="p2", Key="auth0-app2", PublicCertificatePath="https://abc.example/b.crt"),
    ]
    store = MagicMock()
    store.get_active = AsyncMock(return_value=rows)

    with patch.object(auth, "get_third_party_provider_store", return_value=store):
        result = await auth.validate_with_fallback(
            _sign(keypair), _tenant(), _request({"x-blocks-idp": "okta-hr"}), None
        )

    assert result is None


@pytest.mark.asyncio
async def test_the_header_picks_between_two_indistinguishable_rows(keypair):
    rows = [
        _provider(Key="auth0-app1"),
        _provider(_id="p2", Key="auth0-app2",
                  PublicCertificatePath="https://abc.example/b.crt"),
    ]
    store = MagicMock()
    store.get_active = AsyncMock(return_value=rows)

    with patch.object(auth, "get_third_party_provider_store", return_value=store), \
         patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock) as fetch:
        fetch.return_value = keypair["cert_pem"]
        result = await auth.validate_with_fallback(
            _sign(keypair), _tenant(), _request({"x-blocks-idp": "auth0-app2"}), None
        )

    assert result is not None
    assert result.provider.key == "auth0-app2"


def test_a_dotted_mapping_whose_property_is_missing_is_empty():
    assert _map({"realm_access": {"other": 1}}, Name="realm_access.roles")["display_name"] == ""


def test_a_roles_mapping_whose_json_is_broken_is_empty():
    assert _map({"realm_access": "{not json"}, Roles="realm_access.roles")["roles"] == []


def test_a_mapping_naming_a_claim_that_is_absent_is_empty():
    assert _map({"sub": "x"}, UserId="sub", Email="nope")["email"] == ""
