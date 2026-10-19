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
        raise RuntimeError("Configurations not loaded")
    return _app_configurations

def is_local_deployment() -> bool:
    """True only when the loaded configuration sets is_local to true. No configuration means not local."""
    if _app_configurations is None:
        return False
    value = _app_configurations.get("is_local")
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")

def get_setting_list(key: str) -> list[str]:
    """Environment variable (comma-separated), else the configuration root (a list, or a comma-separated string)."""
    value = os.getenv(key, "")
    if not value.strip() and _app_configurations is not None:
        value = _app_configurations.get(key) or ""
    items = value.split(",") if isinstance(value, str) else value
    return [str(item).strip() for item in items if str(item).strip()]
