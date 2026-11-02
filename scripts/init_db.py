import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.db.init_db import init_database


if __name__ == "__main__":
    init_database()
    print("Database initialized successfully.")

