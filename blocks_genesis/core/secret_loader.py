from typing import Dict, Any
from blocks_secret import BlocksSecret, blocks_secret_instance
from azure_key_vault import AzureKeyVault


class SecretLoader:
    def __init__(self):
        self.vault = AzureKeyVault()
        self.load_secrets()

    async def load_secrets(self):
        fields = list(BlocksSecret.__fields__.keys())
        raw_secrets = await self.vault.get_secrets(fields)
        
        for key, value in raw_secrets.items():
            if isinstance(value, str) and value.lower() in ["true", "false"]:
                raw_secrets[key] = value.lower() == "true"

        secret = BlocksSecret(**raw_secrets)

        # Set singleton instance
        global blocks_secret_instance
        blocks_secret_instance = secret