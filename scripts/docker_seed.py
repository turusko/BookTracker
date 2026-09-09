"""Import the local SQLite database into a new Docker volume only."""

import os
from pathlib import Path
import sqlite3
import tempfile


def seed(source, destination):
    source = Path(source)
    destination = Path(destination)
    if destination.exists():
        print("Keeping the existing Docker database; local database was not imported.")
        return
    if not source.is_file():
        raise FileNotFoundError(f"Current database not found: {source}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="import-", suffix=".db", dir=destination.parent)
    os.close(fd)
    try:
        original = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
        snapshot = sqlite3.connect(temporary)
        try:
            original.backup(snapshot)
            result = snapshot.execute("PRAGMA integrity_check").fetchall()
            if result != [("ok",)]:
                raise RuntimeError(f"Database integrity check failed: {result}")
            tables = {row[0] for row in snapshot.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"books", "wishlist"}.issubset(tables):
                raise RuntimeError("Source is not a BookTracker database; import cancelled.")
        finally:
            snapshot.close()
            original.close()
        # A hard link publishes the completed snapshot without overwriting existing data.
        os.link(temporary, destination)
        print("Imported current database into Docker. Original file is unchanged.")
    finally:
        Path(temporary).unlink(missing_ok=True)


if __name__ == "__main__":
    seed("/import/data/kobo_deals.db", "/app/data/kobo_deals.db")
