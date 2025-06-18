from pathlib import Path
import json
import os

_app_configurations = None

def load_configurations(config_dir: str, env: str = None):
    global _app_configurations 
    env = env or os.getenv("APP_ENV", "dev")
    path = Path(config_dir) / f"{env}.json"
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path) as f:
        _app_configurations = json.load(f)  

def get_configurations():
    if _app_configurations is None:
        raise Exception("Configurations not loaded")
    return _app_configurations
