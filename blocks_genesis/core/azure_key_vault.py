from azure.identity import ClientSecretCredential
from azure.keyvault.secrets import SecretClient
from typing import List, Dict
from env_vault_config import EnvVaultConfig


class AzureKeyVault:
    def __init__(self):
        required_keys = ["KEY_VAULT_URL", "TENANT_ID", "CLIENT_ID", "CLIENT_SECRET"]
        config = EnvVaultConfig.get_config(required_keys)

        self.vault_url = config["KEY_VAULT_URL"]
        tenant_id = config["TENANT_ID"]
        client_id = config["CLIENT_ID"]
        client_secret = config["CLIENT_SECRET"]

        credential = ClientSecretCredential(tenant_id, client_id, client_secret)
        self.secret_client = SecretClient(vault_url=self.vault_url, credential=credential)

    async def get_secrets(self, keys: List[str]) -> Dict[str, str]:
        secrets: Dict[str, str] = {}

        for key in keys:
            value = await self.get_secret_value(key)
            if value:
                secrets[key] = value

        return secrets

    async def get_secret_value(self, key: str) -> str:
        try:
            secret = self.secret_client.get_secret(key)
            return secret.value
        except Exception as e:
            print(f"[Warning] Could not retrieve secret '{key}': {e}")
            return ""
