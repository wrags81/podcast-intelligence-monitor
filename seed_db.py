#!/usr/bin/env python3
"""
One-time database seeder. Runs seed.sql into episodes.db if the DB is empty.
Safe to run multiple times — will skip if data already exists.
"""
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "episodes.db"
SEED_PATH = BASE_DIR / "seed.sql"

def main():
    if not SEED_PATH.exists():
        print("No seed.sql found — skipping.")
        return

    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] if \
        conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='episodes'").fetchone() else 0

    if count > 0:
        print(f"DB already has {count} episodes — skipping seed.")
        conn.close()
        return

    print(f"Seeding database from {SEED_PATH}...")
    with open(SEED_PATH, "r") as f:
        conn.executescript(f.read())
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    print(f"Done — {count} episodes loaded.")
    conn.close()

if __name__ == "__main__":
    main()
