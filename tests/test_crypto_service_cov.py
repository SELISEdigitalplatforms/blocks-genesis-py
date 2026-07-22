import base64
from blocks_genesis._utilities.crypto_service import CryptoService


def test_compute_hmac_sha256_hex():
    r = CryptoService.compute_hmac_sha256('msg', 'key', make_base64=False)
    assert r == r.lower() and len(r) == 64


def test_compute_hmac_sha256_base64():
    r = CryptoService.compute_hmac_sha256('msg', 'key', make_base64=True)
    assert base64.b64decode(r)


def test_compute_hmac_sha256_none_inputs():
    r = CryptoService.compute_hmac_sha256(None, None)
    assert len(r) == 64


def test_constant_time_equals_true():
    assert CryptoService.constant_time_equals('abc', 'abc') is True


def test_constant_time_equals_false():
    assert CryptoService.constant_time_equals('abc', 'abd') is False


def test_constant_time_equals_none():
    assert CryptoService.constant_time_equals(None, '') is True


def test_hash_bytes_base64():
    assert base64.b64decode(CryptoService.hash_bytes(b'data', make_base64=True))
