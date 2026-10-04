"""AES-GCM envelope, byte compatible with genesis-net `CryptoService`.

Ports `f9eb915`. A provider's signing secret and certificate passphrase are stored
encrypted under the tenant salt, so Python has to open exactly the box .NET closed:

    base64( [0x01] [12-byte nonce] [ciphertext] [16-byte tag] )
    key = SHA256( utf8( tenant salt ) )

Decrypt never raises. A bad box is a configuration fault, not a bad token -- if it threw,
the whole validation would abort and a valid token would come back as 401 "issuer
mismatch", sending people to check issuer settings that were never wrong.
"""
import base64

import pytest

from blocks_genesis._utilities.crypto_service import CryptoService

SALT = "8f14e45fceea167a5a36dedd4bea2543"


def test_a_value_round_trips():
    box = CryptoService.encrypt("s3cr3t-signing-key", SALT)

    assert CryptoService.decrypt(box, SALT) == "s3cr3t-signing-key"


def test_the_envelope_has_the_dotnet_layout():
    raw = base64.b64decode(CryptoService.encrypt("abc", SALT))

    assert raw[0] == 1                      # version byte
    assert len(raw) == 1 + 12 + len("abc") + 16


def test_the_same_value_encrypts_differently_every_time():
    """A fresh nonce each time, so two identical secrets do not look identical at rest."""
    assert CryptoService.encrypt("abc", SALT) != CryptoService.encrypt("abc", SALT)


def test_an_empty_string_round_trips():
    assert CryptoService.decrypt(CryptoService.encrypt("", SALT), SALT) == ""


def test_unicode_round_trips():
    assert CryptoService.decrypt(CryptoService.encrypt("pässwörd-✓", SALT), SALT) == "pässwörd-✓"


# ---------------------------------------------------------------------------
# Every failure is None, never an exception
# ---------------------------------------------------------------------------

def test_the_wrong_salt_gives_nothing():
    """Regenerating a tenant's salt makes every stored secret unreadable at once.
    The symptom is a 401 carrying a perfectly valid token."""
    box = CryptoService.encrypt("abc", SALT)

    assert CryptoService.decrypt(box, "a-different-salt") is None


def test_a_tampered_box_gives_nothing():
    raw = bytearray(base64.b64decode(CryptoService.encrypt("abc", SALT)))
    raw[-1] ^= 0xFF                          # break the tag
    tampered = base64.b64encode(bytes(raw)).decode()

    assert CryptoService.decrypt(tampered, SALT) is None


def test_an_unknown_version_byte_gives_nothing():
    raw = bytearray(base64.b64decode(CryptoService.encrypt("abc", SALT)))
    raw[0] = 2
    future = base64.b64encode(bytes(raw)).decode()

    assert CryptoService.decrypt(future, SALT) is None


def test_a_short_buffer_gives_nothing():
    """Checked before slicing, so a truncated value is malformed input, not a crash."""
    assert CryptoService.decrypt(base64.b64encode(b"\x01short").decode(), SALT) is None


@pytest.mark.parametrize("bad", ["", "   ", "not base64 at all!!", None])
def test_junk_gives_nothing(bad):
    assert CryptoService.decrypt(bad, SALT) is None


@pytest.mark.parametrize("salt", ["", "   ", None])
def test_no_salt_gives_nothing(salt):
    assert CryptoService.decrypt(CryptoService.encrypt("abc", SALT), salt) is None


# ---------------------------------------------------------------------------
# Encrypt is strict, the way .NET is
# ---------------------------------------------------------------------------

def test_encrypt_refuses_a_missing_value():
    with pytest.raises(ValueError):
        CryptoService.encrypt(None, SALT)


@pytest.mark.parametrize("salt", ["", "   ", None])
def test_encrypt_refuses_a_blank_salt(salt):
    with pytest.raises(ValueError):
        CryptoService.encrypt("abc", salt)


# ---------------------------------------------------------------------------
# The key is derived, never used raw
# ---------------------------------------------------------------------------

def test_the_key_is_the_sha256_of_the_salt():
    """So the stored salt and the AES key are never the same bytes, and any length of
    salt yields a valid AES-256 key."""
    import hashlib

    assert CryptoService._derive_key(SALT) == hashlib.sha256(SALT.encode("utf-8")).digest()
    assert len(CryptoService._derive_key("x")) == 32
