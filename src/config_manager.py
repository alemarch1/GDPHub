# Centralized configuration manager for GDPHub.
# Provides get/set operations backed by SQLite, with values stored as
# serialized JSON strings to preserve structure (dicts, lists, etc.).

import json
from typing import Any, Optional
from sqlmodel import Session, select
from database import engine
from models import Configuration

class ConfigManager:
    """
    Handles configuration management using SQLite as the backend.
    Values are stored as JSON strings to maintain structure.
    """
    
    @staticmethod
    def get_config(key: str, default: Any = None) -> Any:
        """
        Retrieves a configuration value by key. 
        Automatically deserializes JSON if the key exists.
        """
        try:
            with Session(engine) as session:
                config = session.get(Configuration, key)
                if config:
                    return json.loads(config.value)
                return default
        except Exception as e:
            print(f"Error fetching config '{key}': {e}")
            return default

    @staticmethod
    def set_config(key: str, value: Any):
        """
        Sets a configuration value by key. 
        Automatically serializes the value to JSON.
        Uses UPSERT logic (Update if exists, Insert otherwise).
        """
        try:
            with Session(engine) as session:
                json_value = json.dumps(value)
                config = session.get(Configuration, key)
                if config:
                    config.value = json_value
                    session.add(config)
                else:
                    new_config = Configuration(key=key, value=json_value)
                    session.add(new_config)
                session.commit()
        except Exception as e:
            print(f"Error setting config '{key}': {e}")
            raise e

# Convenience functions for easy import
def get_config(key: str, default: Any = None) -> Any:
    return ConfigManager.get_config(key, default)

def set_config(key: str, value: Any):
    ConfigManager.set_config(key, value)
