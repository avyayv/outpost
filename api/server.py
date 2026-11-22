"""HTTP plumbing. You normally don't edit this; add routes in api/routes/."""
import hmac
import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app import ROUTES, HTTPError, load_routes
from store import Store


class Request:
    def __init__(self, handler, url, params):
        self.method = handler.command
        self.path = url.path
        self.query = {key: values[-1] for key, values in parse_qs(url.query).items()}
        self.params = params
        self.headers = handler.headers
        self._handler = handler

    def db(self):
        """`with req.db() as db:` gives one SQLite transaction."""
        return self._handler.server.store.connect()

    def json(self, max_bytes=1_000_000):
        if self.headers.get("Transfer-Encoding"):
            raise HTTPError(411, "Content-Length required")
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= max_bytes:
            raise HTTPError(413, f"Body must be 1–{max_bytes} bytes")
        raw = self._handler.rfile.read(size)
        if len(raw) != size:
            raise ValueError("Incomplete body")
        return json.loads(raw)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *_):
        pass  # Do not log authorization headers or personal payloads.

    def respond(self, status, payload):
        data = json.dumps(payload, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.dispatch()

    do_POST = do_PUT = do_PATCH = do_DELETE = do_GET

    def dispatch(self):
        url = urlsplit(self.path)
        if self.command == "GET" and url.path == "/healthz":
            try:
                with self.server.store.connect() as db:
                    db.execute("SELECT 1").fetchone()
                return self.respond(200, {"ok": True})
            except sqlite3.Error:
                return self.respond(503, {"error": "Database unavailable"})
        matches = [(entry, match) for entry in ROUTES if (match := entry[1].fullmatch(url.path))]
        if not matches:
            return self.respond(404, {"error": "Not found"})
        found = next(((entry, match) for entry, match in matches if entry[0] == self.command), None)
        if found is None:
            return self.respond(405, {"error": "Method not allowed"})
        (_, _, handler, public), match = found
        if not public and not hmac.compare_digest(
            self.headers.get("Authorization", "").encode(),
            ("Bearer " + self.server.token).encode(),
        ):
            return self.respond(401, {"error": "Unauthorized"})
        try:
            result = handler(Request(self, url, match.groupdict()))
            status, payload = result if isinstance(result, tuple) else (200, result)
            return self.respond(status, payload)
        except HTTPError as error:
            return self.respond(error.status, {"error": error.message})
        except (ValueError, KeyError, TypeError, UnicodeError):
            return self.respond(400, {"error": "Invalid request"})
        except (TimeoutError, OSError):
            return self.respond(408, {"error": "Request timed out"})
        except sqlite3.Error:
            return self.respond(503, {"error": "Database unavailable"})


def make_server(host, port, database, token):
    if len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError("Token must be at least 32 ASCII characters without whitespace")
    load_routes()
    server = ThreadingHTTPServer((host, port), Handler)
    server.store = Store(database)
    server.token = token
    return server


if __name__ == "__main__":
    token = Path(os.environ.get("TOKEN_FILE", "/run/secrets/api_token")).read_text().strip()
    make_server("0.0.0.0", int(os.environ.get("PORT", "8080")),
                os.environ.get("DATABASE_PATH", "/data/app.sqlite3"), token).serve_forever()
