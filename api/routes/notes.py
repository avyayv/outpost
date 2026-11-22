"""Example resource. Copy this file to add your own; delete it if you don't need notes."""
from app import HTTPError, route

ID = r"(?P<id>[A-Za-z0-9_-]{1,100})"


def migrate(db):
    db.execute("CREATE TABLE IF NOT EXISTS notes (id TEXT PRIMARY KEY, text TEXT NOT NULL, updated_at TEXT NOT NULL)")


@route("GET", r"/api/notes")
def list_notes(req):
    limit = int(req.query.get("limit", "100"))
    offset = int(req.query.get("offset", "0"))
    if not 1 <= limit <= 1000 or offset < 0:
        raise ValueError("Invalid pagination")
    with req.db() as db:
        rows = db.execute("SELECT * FROM notes ORDER BY updated_at DESC, id LIMIT ? OFFSET ?", (limit, offset))
        return {"notes": [dict(row) for row in rows]}


@route("PUT", rf"/api/notes/{ID}")
def put_note(req):
    body = req.json(max_bytes=65536)
    if not isinstance(body, dict) or set(body) != {"text"}:
        raise ValueError("Expected an object with only text")
    if not isinstance(body["text"], str) or not 1 <= len(body["text"]) <= 10000:
        raise HTTPError(400, "Text must be 1–10000 characters")
    with req.db() as db:
        db.execute(
            "INSERT INTO notes VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now')) "
            "ON CONFLICT(id) DO UPDATE SET text=excluded.text, updated_at=excluded.updated_at",
            (req.params["id"], body["text"]),
        )
        return {"note": dict(db.execute("SELECT * FROM notes WHERE id=?", (req.params["id"],)).fetchone())}


@route("DELETE", rf"/api/notes/{ID}")
def delete_note(req):
    with req.db() as db:
        deleted = db.execute("DELETE FROM notes WHERE id=?", (req.params["id"],)).rowcount > 0
    return (200 if deleted else 404), {"deleted": deleted}
