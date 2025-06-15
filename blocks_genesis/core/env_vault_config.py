from dotenv import load_dotenv
import os
from typing import Dict, List

class EnvVaultConfig:
    @staticmethod
    def get_config(keys: List[str] = None) -> Dict[str, str]:
        load_dotenv()
        
        # Get all environment variables
        all_env = dict(os.environ)
        
        # # Print all environment variables
        # for key, value in all_env.items():
        #     print(f"{key}={value}")
        
        if keys:
            config = {key: os.environ.get(key) for key in keys}
            missing = [k for k, v in config.items() if not v]
            if missing:
                raise EnvironmentError(f"Missing environment variables: {', '.join(missing)}")
            return config
        
        return all_env
