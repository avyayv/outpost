import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise RuntimeError("Database is newer than this API")
            if version < 1:
                db.execute("CREATE TABLE notes (id TEXT PRIMARY KEY, text TEXT NOT NULL, updated_at TEXT NOT NULL)")
                db.execute("PRAGMA user_version=1")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def list_notes(self, limit=100, offset=0):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM notes ORDER BY updated_at DESC, id LIMIT ? OFFSET ?", (limit, offset)
            )]

    def put_note(self, note_id, text):
        with self.connect() as db:
            db.execute(
                "INSERT INTO notes VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now')) "
                "ON CONFLICT(id) DO UPDATE SET text=excluded.text, updated_at=excluded.updated_at",
                (note_id, text),
            )
            return dict(db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone())

    def delete_note(self, note_id):
        with self.connect() as db:
            return db.execute("DELETE FROM notes WHERE id=?", (note_id,)).rowcount > 0
