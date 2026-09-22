import base64
import hashlib
import os
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Envelope: version | nonce | ciphertext | tag, base64. Byte compatible with the .NET
# CryptoService, because a provider's secrets are written by one runtime and read by the
# other.
#
# The version byte is the escape hatch: changing cipher or key derivation later becomes a
# branch at decode time rather than a migration everyone has to coordinate.
_ENVELOPE_V1 = 1
_NONCE_BYTES = 12   # the GCM standard, and AesGcm.NonceByteSizes maximum
_TAG_BYTES = 16     # AesGcm.TagByteSizes maximum


class CryptoService:
    @staticmethod
    def hash_string(value: str, salt: str = None) -> str:
        """
        Hash a string with optional salt, compatible with C# implementation.
        
        Args:
            value: String to hash
            salt: Optional salt string (None treated as empty string)
            
        Returns:
            Lowercase hex string without separators
        """
        value_bytes = value.encode('utf-8')

        salt_bytes = (salt or "").encode('utf-8')
        salted_value = value_bytes + salt_bytes
        return CryptoService.hash_bytes(salted_value)

    @staticmethod
    def compute_hmac_sha256(message: str, key: str, make_base64: bool = False) -> str:
        """
        Compute HMAC-SHA256 of a message with a key.
        Args:
            message: Message string (None treated as empty string)
            key: Key string (None treated as empty string)
            make_base64: If True, return base64 encoded string, else hex
        Returns:
            Base64 string or lowercase hex string without separators
        """
        import hmac
        safe_message = (message or "").encode('utf-8')
        safe_key = (key or "").encode('utf-8')
        h = hmac.new(safe_key, safe_message, hashlib.sha256)
        hash_bytes = h.digest()
        if make_base64:
            return base64.b64encode(hash_bytes).decode('utf-8')
        return hash_bytes.hex().lower()

    @staticmethod
    def constant_time_equals(left: str, right: str) -> bool:
        """
        Compare two strings in constant time.
        Args:
            left: First string (None treated as empty string)
            right: Second string (None treated as empty string)
        Returns:
            True if equal, False otherwise
        """
        import hmac
        left_bytes = (left or "").encode('utf-8')
        right_bytes = (right or "").encode('utf-8')
        return hmac.compare_digest(left_bytes, right_bytes)
    
    @staticmethod
    def hash_bytes(value: bytes, make_base64: bool = False) -> str:
        """
        Hash bytes with SHA256, compatible with C# implementation.
        
        Args:
            value: Bytes to hash
            make_base64: If True, return base64 encoded string, else hex
            
        Returns:
            Base64 string or lowercase hex string without separators
        """
        hash_bytes = hashlib.sha256(value).digest()
        if make_base64:
            return base64.b64encode(hash_bytes).decode('utf-8')

        return hash_bytes.hex().lower()

    @staticmethod
    def _derive_key(key_material: str) -> bytes:
        """Derived rather than used raw, so the stored salt and the AES key are never the
        same bytes, and any length of salt yields a valid AES-256 key."""
        return hashlib.sha256(key_material.encode('utf-8')).digest()

    @staticmethod
    def encrypt(plain_text: str, key_material: str) -> str:
        """Encrypt with AES-256-GCM under a key derived from key_material.

        The key is a parameter rather than a property so the key source stays the
        caller's decision.
        """
        if plain_text is None:
            raise ValueError("plain_text cannot be None")
        if not key_material or not key_material.strip():
            raise ValueError("key_material cannot be empty")

        nonce = os.urandom(_NONCE_BYTES)
        sealed = AESGCM(CryptoService._derive_key(key_material)).encrypt(
            nonce, plain_text.encode('utf-8'), None
        )

        return base64.b64encode(bytes([_ENVELOPE_V1]) + nonce + sealed).decode('utf-8')

    @staticmethod
    def decrypt(envelope: str, key_material: str) -> Optional[str]:
        """Reverse encrypt. Returns None on any failure and never raises.

        A malformed or unopenable box is a configuration fault, not a bad token. Raising
        here would abort validation and report a valid token as an issuer mismatch.
        """
        if not envelope or not envelope.strip():
            return None
        if not key_material or not key_material.strip():
            return None

        try:
            raw = base64.b64decode(envelope, validate=True)
        except Exception:
            return None

        # A short buffer is malformed input, not a cryptographic failure: check before slicing.
        if len(raw) < 1 + _NONCE_BYTES + _TAG_BYTES or raw[0] != _ENVELOPE_V1:
            return None

        nonce = raw[1:1 + _NONCE_BYTES]
        sealed = raw[1 + _NONCE_BYTES:]

        try:
            plain = AESGCM(CryptoService._derive_key(key_material)).decrypt(nonce, sealed, None)
        except Exception:
            # The tag did not verify: tampered with, or the key material is wrong. Both
            # are the caller's to report -- this layer only says no.
            return None

        return plain.decode('utf-8')
