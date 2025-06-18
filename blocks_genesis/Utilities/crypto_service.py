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