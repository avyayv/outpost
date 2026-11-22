import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app import migrate


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            migrate(db)

    @contextmanager
    def connect(self):
        """One transaction: commits on success, rolls back on error."""
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()
