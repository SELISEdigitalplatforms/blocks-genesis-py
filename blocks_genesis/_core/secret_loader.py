from typing import Dict, Optional, List
import logging
from blocks_genesis._core.blocks_secret import BlocksSecret
from blocks_genesis._core.azure_key_vault import AzureKeyVault

logger = logging.getLogger(__name__)

# Module-level private variable to hold the immutable singleton secret
_loaded_secret: Optional[BlocksSecret] = None

class SecretLoader:
    def __init__(self, service_name: str = "blocks_service"):
        self.vault = AzureKeyVault()
        self.service_name = service_name

    async def load_secrets(self, fields: Optional[List[str]] = None) -> None:
        global _loaded_secret
        if _loaded_secret is not None:
            logger.debug("Secrets already loaded, skipping reload.")
            return

        fields = fields or list(BlocksSecret.model_fields.keys())
        try:
            logger.info("Loading secrets from Azure Key Vault...")
            raw_secrets: Dict[str, str] = await self.vault.get_secrets(fields)

            processed_secrets: Dict[str, object] = {
                key: (
                    value.lower() == "true"
                    if isinstance(value, str) and value.lower() in ["true", "false"]
                    else value
                )
                for key, value in raw_secrets.items()
            }

            secret = BlocksSecret(**processed_secrets)
            secret.ServiceName = self.service_name

            _loaded_secret = secret
            logger.info("Secrets loaded successfully")
        except Exception:
            logger.exception("Failed to load secrets")
            raise

    async def close(self) -> None:
        if hasattr(self.vault, "close") and callable(self.vault.close):
            await self.vault.close()

def get_blocks_secret() -> BlocksSecret:
    if _loaded_secret is None:
        raise Exception("Secrets not loaded")
    return _loaded_secret
