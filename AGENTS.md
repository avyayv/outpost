# Outpost app — agent guide

This directory is a deployed API. Changing it is expected: add, edit or remove endpoints whenever the user needs one, then test and deploy.

## Layout

- `api/routes/*.py` — **all endpoints live here.** Every module is imported at startup (names starting with `_` are skipped, use those for helpers). One file per resource.
- `api/app.py` — `route`, `HTTPError`, and the schema/backup hooks. Rarely edited.
- `api/server.py`, `api/store.py`, `api/backup.py` — HTTP, SQLite and backup plumbing. Rarely edited.
- `tests/` — stdlib `unittest`.

## Add an endpoint

```python
# api/routes/things.py
from app import HTTPError, route

def migrate(db):               # runs at every startup; must be idempotent
    db.execute("CREATE TABLE IF NOT EXISTS things (id TEXT PRIMARY KEY, body TEXT NOT NULL)")

def check(db):                 # optional; runs before backup/restore
    pass

@route("GET", r"/api/things/(?P<id>[A-Za-z0-9_-]{1,100})")
def get_thing(req):
    with req.db() as db:       # one transaction
        row = db.execute("SELECT * FROM things WHERE id=?", (req.params["id"],)).fetchone()
    if row is None:
        raise HTTPError(404, "Not found")
    return dict(row)           # or (status, dict)
```

`req` has `method`, `path`, `query` (dict of last values), `params` (named regex groups), `headers`, `json(max_bytes=...)` and `db()`. Routes need the bearer token unless `@route(..., public=True)`. `ValueError`/`KeyError`/`TypeError` become 400. `/healthz` is built in.

## Rules

- Parameterized SQL only. Validate every input field.
- Schema changes go in `migrate(db)`, written so they are safe to rerun on live data (`CREATE TABLE IF NOT EXISTS`, check `PRAGMA table_info` before `ALTER TABLE`). Never drop or rewrite user data without a backup.
- Keep response shapes stable once a client uses them.

## Ship it

```sh
PYTHONPATH=api python3 -m unittest discover -s tests -v
outpost backup backups/before-$(date +%Y%m%d%H%M%S).sqlite3   # before any schema change
outpost deploy
curl -fsS https://$APP_DOMAIN/healthz
```

`outpost status` and `outpost logs` show the server state. `.env`, `outpost.json` and `secrets/` are local-only: never commit or print them.
