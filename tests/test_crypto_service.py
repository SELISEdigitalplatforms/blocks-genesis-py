from blocks_genesis._utilities.crypto_service import CryptoService
import base64
import hashlib

def test_hash_string_no_salt():
    value = 'test'
    expected = hashlib.sha256(b'test').hexdigest().lower()
    assert CryptoService.hash_string(value) == expected

def test_hash_string_with_salt():
    value = 'test'
    salt = 'salt'
    expected = hashlib.sha256(b'testsalt').hexdigest().lower()
    assert CryptoService.hash_string(value, salt) == expected

def test_hash_bytes_hex():
    value = b'abc'
    expected = hashlib.sha256(value).hexdigest().lower()
    assert CryptoService.hash_bytes(value) == expected

def test_hash_bytes_base64():
    value = b'abc'
    expected = base64.b64encode(hashlib.sha256(value).digest()).decode('utf-8')
    assert CryptoService.hash_bytes(value, make_base64=True) == expected 