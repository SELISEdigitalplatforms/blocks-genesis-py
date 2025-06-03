from typing import Dict, Any, Optional
import logging
from blocks_genesis.core.blocks_secret import BlocksSecret, blocks_secret_instance
from blocks_genesis.core.azure_key_vault import AzureKeyVault

# Set up logging
logger = logging.getLogger(__name__)

class SecretLoader:
    def __init__(self, sevice_name: str = "blocks_service"):
        """Initialize the SecretLoader with a service name"""
        self.vault = AzureKeyVault()
        self._secrets = None
        self.service_name = sevice_name
    
    async def load_secrets(self):
        """Load secrets from Azure Key Vault"""
        try:
            logger.info("Loading secrets from Azure Key Vault...")
            
            # Get the fields you need
            fields = list(BlocksSecret.__fields__.keys())
            
            # Fetch secrets from vault
            raw_secrets = await self.vault.get_secrets(fields)
            
            # Process the secrets
            processed_secrets = {}
            for key, value in raw_secrets.items():
                if isinstance(value, str) and value.lower() in ["true", "false"]:
                    processed_secrets[key] = value.lower() == "true"
                else:
                    processed_secrets[key] = value
            
            # Create BlocksSecret instance
            secret = BlocksSecret(**processed_secrets)
            
            # Set global instance
            global blocks_secret_instance
            blocks_secret_instance = secret
            blocks_secret_instance.ServiceName = self.service_name
            
            self._secrets = secret
            logger.info("✅ Secrets loaded successfully")
            
        except Exception as e:
            logger.error(f"❌ Failed to load secrets: {e}")
            raise e
    
    @property
    def secrets(self) -> BlocksSecret:
        """Get the loaded secrets"""
        if self._secrets is None:
            raise ValueError("Secrets not loaded. Call load_secrets() first.")
        return self._secrets
    
    def is_loaded(self) -> bool:
        """Check if secrets are loaded"""
        return self._secrets is not None

