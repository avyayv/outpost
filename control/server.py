"""Generic management API. Never imports the application's routes or schema."""
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LEASE_SECONDS = 120


class Queue:
    def __init__(self, path):
        self.path = str(path)
        with closing(self.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, idem TEXT UNIQUE, request TEXT, status TEXT, created REAL, lease TEXT, expires REAL, result TEXT)')

    def connect(self):
        return sqlite3.connect(self.path, timeout=15)

    @staticmethod
    def expire(db):
        db.execute("UPDATE jobs SET status='failed',result=? WHERE status='running' AND expires<?",
                   (json.dumps({'error': 'Worker lease expired; inspect local worktree before retrying'}), time.time()))

    def submit(self, request, idem):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            self.expire(db)
            old = db.execute('SELECT id,request FROM jobs WHERE idem=?', (idem,)).fetchone()
            if old:
                if old[1] != request:
                    raise ValueError('Idempotency key already used for another request')
                return old[0]
            active = db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            daily = db.execute('SELECT count(*) FROM jobs WHERE created>?', (time.time() - 86400,)).fetchone()[0]
            if active >= 20 or daily >= 100:
                raise OverflowError('Change queue limit reached')
            job = uuid.uuid4().hex
            db.execute("INSERT INTO jobs VALUES (?,?,?,'queued',?,NULL,NULL,NULL)", (job, idem, request, time.time()))
            return job

    def get(self, job):
        with closing(self.connect()) as db, db:
            self.expire(db)
            row = db.execute('SELECT id,request,status,result FROM jobs WHERE id=?', (job,)).fetchone()
            if not row:
                return None
            return dict(id=row[0], request=row[1], status=row[2], result=json.loads(row[3]) if row[3] else None)

    def claim(self):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            self.expire(db)
            row = db.execute("SELECT id,request FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if not row:
                return None
            lease = secrets.token_hex(32)
            db.execute("UPDATE jobs SET status='running',lease=?,expires=? WHERE id=?", (lease, time.time() + LEASE_SECONDS, row[0]))
            return dict(id=row[0], request=row[1], lease=lease)

    def update(self, job, lease, result=None):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            self.expire(db)
            row = db.execute('SELECT status,lease FROM jobs WHERE id=?', (job,)).fetchone()
            if not row or row[0] != 'running' or not hmac.compare_digest(row[1], lease):
                raise LookupError('Lease is no longer valid')
            if result is None:
                db.execute('UPDATE jobs SET expires=? WHERE id=?', (time.time() + LEASE_SECONDS, job))
            else:
                db.execute('UPDATE jobs SET status=?,result=? WHERE id=?', (result['status'], json.dumps(result), job))


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *_):
        pass

    def respond(self, status, data):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def dispatch(self):
        if self.path == '/healthz' and self.command == 'GET':
            return self.respond(200, {'ok': True})
        worker = self.path.startswith('/_outpost/worker/')
        token = self.server.worker_token if worker else self.server.change_token
        if not hmac.compare_digest(self.headers.get('Authorization', '').encode(), ('Bearer ' + token).encode()):
            return self.respond(401, {'error': 'Unauthorized'})
        try:
            body = None
            if self.command == 'POST':
                size = int(self.headers.get('Content-Length', '0'))
                if self.headers.get('Transfer-Encoding') or not 0 < size <= 16384:
                    return self.respond(413, {'error': 'Invalid request size'})
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise ValueError('Incomplete body')
                body = json.loads(raw)
                if not isinstance(body, dict):
                    raise ValueError('Expected JSON object')
            if self.path == '/_outpost/changes' and self.command == 'POST':
                if set(body) != {'request', 'idempotencyKey'} or not isinstance(body['request'], str) or not 1 <= len(body['request'].strip()) <= 8000:
                    raise ValueError('Expected request (1–8000 chars) and idempotencyKey')
                if not isinstance(body['idempotencyKey'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', body['idempotencyKey']):
                    raise ValueError('Invalid idempotencyKey')
                job = self.server.queue.submit(body['request'], body['idempotencyKey'])
                return self.respond(202, self.server.queue.get(job))
            match = re.fullmatch(r'/_outpost/changes/([a-f0-9]{32})', self.path)
            if match and self.command == 'GET':
                job = self.server.queue.get(match[1])
                return self.respond(200 if job else 404, job or {'error': 'Not found'})
            if self.path == '/_outpost/worker/claim' and self.command == 'POST' and body == {}:
                return self.respond(200, {'job': self.server.queue.claim()})
            match = re.fullmatch(r'/_outpost/worker/([a-f0-9]{32})/(heartbeat|complete)', self.path)
            if match and self.command == 'POST':
                if not isinstance(body.get('lease'), str):
                    raise ValueError('Missing lease')
                result = None
                if match[2] == 'complete':
                    result = body.get('result')
                    if not isinstance(result, dict) or result.get('status') not in ('review_ready', 'failed') or len(json.dumps(result)) > 8000:
                        raise ValueError('Invalid completion result')
                self.server.queue.update(match[1], body['lease'], result)
                return self.respond(200, {'ok': True})
            return self.respond(404, {'error': 'Not found'})
        except (ValueError, UnicodeError):
            return self.respond(400, {'error': 'Invalid request'})
        except LookupError:
            return self.respond(409, {'error': 'Lease expired or invalid'})
        except OverflowError:
            return self.respond(429, {'error': 'Queue limit reached'})
        except sqlite3.Error:
            return self.respond(503, {'error': 'Queue unavailable'})
        except (TimeoutError, OSError):
            return self.respond(408, {'error': 'Request timed out'})


def make_server(host, port, database, change_token, worker_token):
    if change_token == worker_token or any(len(t) < 32 or not t.isascii() or any(c.isspace() for c in t) for t in (change_token, worker_token)):
        raise ValueError('Two distinct strong tokens required')
    server = ThreadingHTTPServer((host, port), Handler)
    server.queue = Queue(database)
    server.change_token = change_token
    server.worker_token = worker_token
    return server


if __name__ == '__main__':
    make_server('0.0.0.0', 8090, '/queue/jobs.sqlite3',
                Path('/run/secrets/change_token').read_text().strip(),
                Path('/run/secrets/worker_token').read_text().strip()).serve_forever()
