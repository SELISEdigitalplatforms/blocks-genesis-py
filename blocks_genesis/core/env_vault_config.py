from dotenv import load_dotenv
import os
from typing import Dict, List


class EnvVaultConfig:
    @staticmethod
    def get_config(keys: List[str]) -> Dict[str, str]:
        # Load .env variables into os.environ
        load_dotenv()

        config = {key: os.getenv(key) for key in keys}
        missing = [k for k, v in config.items() if not v]
        if missing:
            raise EnvironmentError(f"Missing environment variables: {', '.join(missing)}")

        return config
