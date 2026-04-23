import json
import os
import sys
from pathlib import Path

# Add src to path if needed (though usually we run from project root)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), ".")))

from database import engine, CONFIG_FILE
from models import Configuration
from sqlmodel import Session, select

def seed_config():
    if not CONFIG_FILE.exists():
        print(f"No config file found at {CONFIG_FILE}")
        return

    print(f"Reading configuration from {CONFIG_FILE}...")
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config_data = json.load(f)

    with Session(engine) as session:
        count = 0
        for key, value in config_data.items():
            # We store the value as a JSON string to maintain structure
            json_value = json.dumps(value)
            
            # SQLite UPSERT: INSERT ... ON CONFLICT(key) DO UPDATE SET value = excluded.value
            # In SQLModel/SQLAlchemy we can use session.get and then update, or raw SQL.
            # For simplicity and to follow the "UPSERT" requirement, I'll avoid raw SQL if possible
            # but I'll use the session to check and update.
            
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
