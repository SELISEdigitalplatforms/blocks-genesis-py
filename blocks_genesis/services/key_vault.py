from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from fastapi_blocks.core.config import settings

class AzureKeyVaultService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.client = SecretClient(vault_url=settings.AZURE_VAULT_URL, credential=DefaultAzureCredential())
        self._initialized = True

    def get_secret(self, name: str) -> str:
        return self.client.get_secret(name).value
