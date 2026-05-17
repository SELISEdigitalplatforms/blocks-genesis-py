import base64
import hashlib

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