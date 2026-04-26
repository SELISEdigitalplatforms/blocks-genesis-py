import logging
from azure.identity.aio import DefaultAzureCredential
from azure.keyvault.secrets.aio import SecretClient
from typing import List, Dict, Optional
from blocks_genesis._core.env_vault_config import EnvVaultConfig




class AzureKeyVault:
    """
    Azure Key Vault client for secret retrieval using DefaultAzureCredential.
    Supports both local development (az login) and server managed identity.
    """

    def __init__(self) -> None:
        self.vault_url: Optional[str] = None
        self.credential: Optional[DefaultAzureCredential] = None
        self.secret_client: Optional[SecretClient] = None

    @staticmethod
    def get_vault_config() -> Dict[str, str]:
        """Get Key Vault configuration from environment."""
        required_keys = ["KEYVAULT__KEYVAULTURL"]
        return EnvVaultConfig.get_config(required_keys)

    def _extract_vault_url(self, config: Dict[str, str]) -> None:
        """Extract and validate the Key Vault URL from config."""
        self.vault_url = config.get("KEYVAULT__KEYVAULTURL")
        if not self.vault_url:
            raise ValueError("Missing required Azure config value 'KEYVAULT__KEYVAULTURL'.")

    def _connect(self) -> None:
        """Establish connection to Azure Key Vault using DefaultAzureCredential."""
        self.credential = DefaultAzureCredential()
        self.secret_client = SecretClient(vault_url=self.vault_url, credential=self.credential)

    async def get_secrets(self, keys: List[str]) -> Dict[str, str]:
        """Public method for backward compatibility. Fetch secrets by keys."""
        return await self.fetch_secrets(keys)

    async def fetch_secrets(self, keys: List[str]) -> Dict[str, str]:
        """Fetch multiple secrets from Azure Key Vault."""
        config = self.get_vault_config()
        self._extract_vault_url(config)
        self._connect()
        return await self._get_secrets_from_vault(keys)

    async def _get_secrets_from_vault(self, keys: List[str]) -> Dict[str, str]:
        """Helper to fetch secrets from the vault."""
        secrets: Dict[str, str] = {}
        for key in keys:
            value = await self._get_secret(key)
            if value:
                secrets[key] = value
        return secrets

    async def _get_secret(self, key: str) -> str:
        """Fetch a single secret value by key."""
        try:
            secret = await self.secret_client.get_secret(key)
            return secret.value
        except Exception as exc:
            logging.warning("Error retrieving secret '%s' from Key Vault: %s", key, exc)
            return ""

    async def close(self) -> None:
        """Close credential and client sessions."""
        if self.credential:
            await self.credential.close()
        if self.secret_client:
            await self.secret_client.close()

