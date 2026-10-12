import hmac
import json
import os
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from store import Store


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *_):
        pass  # Do not log authorization headers or personal payloads.

    def respond(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.dispatch()

    def do_PUT(self):
        self.dispatch()

    def do_DELETE(self):
        self.dispatch()

    def dispatch(self):
        url = urlsplit(self.path)
        if self.command == "GET" and url.path == "/healthz":
            try:
                with self.server.store.connect() as db:
                    db.execute("SELECT 1").fetchone()
                return self.respond(200, {"ok": True})
            except sqlite3.Error:
                return self.respond(503, {"error": "Database unavailable"})
        if not hmac.compare_digest(
            self.headers.get("Authorization", "").encode(),
            ("Bearer " + self.server.token).encode(),
        ):
            return self.respond(401, {"error": "Unauthorized"})
        try:
            if self.command == "GET" and url.path == "/api/notes":
                query = parse_qs(url.query)
                limit = int(query.get("limit", ["100"])[0])
                offset = int(query.get("offset", ["0"])[0])
                if not 1 <= limit <= 1000 or offset < 0:
                    raise ValueError("Invalid pagination")
                return self.respond(200, {"notes": self.server.store.list_notes(limit, offset)})
            match = re.fullmatch(r"/api/notes/([A-Za-z0-9_-]{1,100})", url.path)
            if match and self.command == "PUT":
                size = int(self.headers.get("Content-Length", "0"))
                if self.headers.get("Transfer-Encoding") or not 0 < size <= 65536:
                    return self.respond(413, {"error": "Body must be 1–65536 bytes with Content-Length"})
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise ValueError("Incomplete body")
                body = json.loads(raw)
                if not isinstance(body, dict) or set(body) != {"text"}:
                    raise ValueError("Expected an object with only text")
                if not isinstance(body["text"], str) or not 1 <= len(body["text"]) <= 10000:
                    raise ValueError("Text must be 1–10000 characters")
                return self.respond(200, {"note": self.server.store.put_note(match[1], body["text"])})
            if match and self.command == "DELETE":
                deleted = self.server.store.delete_note(match[1])
                return self.respond(200 if deleted else 404, {"deleted": deleted})
            return self.respond(404, {"error": "Not found"})
        except (ValueError, UnicodeError):
            return self.respond(400, {"error": "Invalid request"})
        except (TimeoutError, OSError):
            return self.respond(408, {"error": "Request timed out"})
        except sqlite3.Error:
            return self.respond(503, {"error": "Database unavailable"})


def make_server(host, port, database, token):
    if len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError("Token must be at least 32 ASCII characters without whitespace")
    store = Store(database)
    server = ThreadingHTTPServer((host, port), Handler)
    server.store = store
    server.token = token
    return server


if __name__ == "__main__":
    token = Path(os.environ.get("TOKEN_FILE", "/run/secrets/api_token")).read_text().strip()
    make_server("0.0.0.0", int(os.environ.get("PORT", "8080")),
                os.environ.get("DATABASE_PATH", "/data/app.sqlite3"), token).serve_forever()
