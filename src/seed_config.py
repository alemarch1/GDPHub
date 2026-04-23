# Bootstrap utility that migrates configuration from config.json into the
# SQLite database. Run once after initial setup or when config.json is updated.

import json
import os
import sys
from pathlib import Path

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), ".")))

from database import engine, CONFIG_FILE
from models import Configuration
from sqlmodel import Session, select

def seed_config():
    """Reads config.json and upserts each key-value pair into the Configuration table."""
    if not CONFIG_FILE.exists():
        print(f"No config file found at {CONFIG_FILE}")
        return

    print(f"Reading configuration from {CONFIG_FILE}...")
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config_data = json.load(f)

    with Session(engine) as session:
        count = 0
        for key, value in config_data.items():
            # Store value as JSON string to preserve structure
            json_value = json.dumps(value)

            # Upsert: update if exists, insert otherwise
            existing = session.get(Configuration, key)
            if existing:
                existing.value = json_value
                session.add(existing)
            else:
                new_entry = Configuration(key=key, value=json_value)
                session.add(new_entry)
            count += 1
        
        session.commit()
    
    print(f"Successfully migrated {count} configuration keys to the database.")

if __name__ == "__main__":
    seed_config()
