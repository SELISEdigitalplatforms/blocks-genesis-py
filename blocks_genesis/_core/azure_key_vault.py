from azure.identity.aio import ClientSecretCredential
from azure.keyvault.secrets.aio import SecretClient
from typing import List, Dict
from blocks_genesis._core.env_vault_config import EnvVaultConfig


class AzureKeyVault:
    def __init__(self):
        required_keys = ["KEYVAULT__CLIENTID", "KEYVAULT__CLIENTSECRET", "KEYVAULT__KEYVAULTURL", "KEYVAULT__TENANTID"]
        config = EnvVaultConfig.get_config(required_keys)

        self.vault_url = config["KEYVAULT__KEYVAULTURL"]
        tenant_id = config["KEYVAULT__TENANTID"]
        client_id = config["KEYVAULT__CLIENTID"]
        client_secret = config["KEYVAULT__CLIENTSECRET"]

        self.credential = ClientSecretCredential(tenant_id, client_id, client_secret)
        self.secret_client = SecretClient(vault_url=self.vault_url, credential=self.credential)

    async def get_secrets(self, keys: List[str]) -> Dict[str, str]:
        secrets: Dict[str, str] = {}
        for key in keys:
            value = await self.get_secret_value(key)
            if value:
                secrets[key] = value
        return secrets

    async def get_secret_value(self, key: str) -> str:
        try:
            secret = await self.secret_client.get_secret(key)
            return secret.value
        except Exception as e:
            print(f"[Warning] Could not retrieve secret '{key}': {e}")
            return ""

    async def close(self):
        await self.credential.close()
        await self.secret_client.close()
