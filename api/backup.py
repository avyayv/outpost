"""Consistent SQLite export; restore only while the API service is stopped."""
import os
import sqlite3
import sys
from contextlib import closing
import tempfile
from pathlib import Path


def check(db):
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("SQLite integrity check failed")
    if db.execute("PRAGMA user_version").fetchone()[0] != 1:
        raise ValueError("Unsupported backup schema version")
    columns = [row[1] for row in db.execute("PRAGMA table_info(notes)")]
    if columns != ["id", "text", "updated_at"]:
        raise ValueError("Not an outpost notes database")


def export(database, output):
    with tempfile.TemporaryDirectory() as directory:
        snapshot = Path(directory) / "backup.sqlite3"
        with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
                check(target)
        with snapshot.open("rb") as stream:
            import shutil
            shutil.copyfileobj(stream, output)


def restore(database, stream):
    with tempfile.TemporaryDirectory() as directory:
        snapshot = Path(directory) / "restore.sqlite3"
        with snapshot.open("wb") as output:
            import shutil
            shutil.copyfileobj(stream, output)
        with closing(sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True)) as source:
            check(source)
            with closing(sqlite3.connect(database)) as target:
                source.backup(target)
                check(target)


if __name__ == "__main__":
    database = os.environ.get("DATABASE_PATH", "/data/app.sqlite3")
    if sys.argv[1:] == ["export"]:
        export(database, sys.stdout.buffer)
    elif sys.argv[1:] == ["restore"]:
        restore(database, sys.stdin.buffer)
    else:
        raise SystemExit("Usage: backup.py export|restore")
