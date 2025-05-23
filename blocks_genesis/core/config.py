from dotenv import load_dotenv
import os

load_dotenv()

class Settings:
    AZURE_VAULT_URL = os.getenv("AZURE_VAULT_URL")
    REDIS_URL = os.getenv("REDIS_URL")

settings = Settings()
