import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
import subprocess
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
from backup import export, restore
from server import make_server
from store import Store

TOKEN = "a" * 64


class APITest(unittest.TestCase):
    def test_init_scaffolds_an_app_from_an_installed_cli(self):
        if Path.cwd().joinpath("outpost.json").exists():
            self.skipTest("CLI init integration test runs in the repository, not an initialized app")
        cli = Path(__file__).resolve().parents[1] / "outpost"
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "HOME": directory}
            result = subprocess.run(
                [sys.executable, str(cli), "init", "--domain", "api.example.com", "--host", "example", "--name", "smoke"],
                cwd=directory, env=env, text=True, capture_output=True, check=True,
            )
            root = Path(directory)
            self.assertTrue((root / "api/server.py").is_file())
            self.assertTrue((root / "compose.yaml").is_file())
            self.assertTrue((root / "secrets/api_token").is_file())
            self.assertEqual((root / "secrets").stat().st_mode & 0o777, 0o700)
            self.assertIn("outpost deploy", result.stdout)
            self.assertEqual(json.loads((root / "outpost.json").read_text())["host"], "example")
            rerun = subprocess.run([sys.executable, str(cli), "init", "--domain", "api.example.com", "--host", "example", "--name", "smoke"], cwd=directory, text=True, capture_output=True)
            self.assertNotEqual(rerun.returncode, 0)
            self.assertIn("already contains Outpost files", rerun.stderr)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "app.sqlite3"
        self.server = make_server("127.0.0.1", 0, self.database, TOKEN)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.directory.cleanup()

    def request(self, path, method="GET", body=None, token=TOKEN):
        headers = {"Authorization": "Bearer " + token}
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, headers=headers, method=method)
        try:
            response = urllib.request.urlopen(req)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def test_auth_and_health(self):
        self.assertEqual(self.request("/healthz", token="")[0], 200)
        self.assertEqual(self.request("/api/notes", token="")[0], 401)
        self.assertEqual(self.request("/api/notes", token="bad")[0], 401)

    def test_crud_and_persistence(self):
        self.assertEqual(self.request("/api/notes/one", "PUT", {"text": "hello"})[0], 200)
        self.assertEqual(self.request("/api/notes/one", "PUT", {"text": "changed"})[0], 200)
        self.assertEqual(self.request("/api/notes")[1]["notes"][0]["text"], "changed")
        self.assertEqual(Store(self.database).list_notes()[0]["text"], "changed")
        self.assertEqual(self.request("/api/notes/one", "DELETE")[0], 200)
        self.assertEqual(self.request("/api/notes/one", "DELETE")[0], 404)

    def test_validation(self):
        for body in ({}, [], {"text": 1}, {"text": ""}, {"text": "a" * 10001}, {"text": "ok", "extra": True}):
            self.assertEqual(self.request("/api/notes/one", "PUT", body)[0], 400)
        for query in ("limit=0", "limit=1001", "offset=-1", "limit=x"):
            self.assertEqual(self.request("/api/notes?" + query)[0], 400)
        self.assertEqual(self.request("/api/notes/invalid.id", "PUT", {"text": "ok"})[0], 404)
        self.assertEqual(self.request("/api/notes/one", "PUT", {"text": "a" * 70000})[0], 413)

    def test_pagination(self):
        for index in range(3):
            self.server.store.put_note(str(index), "note")
        self.assertEqual(len(self.request("/api/notes?limit=2&offset=1")[1]["notes"]), 2)

    def test_backup_restore(self):
        self.server.store.put_note("one", "original")
        snapshot = io.BytesIO()
        export(self.database, snapshot)
        self.server.store.put_note("one", "changed")
        snapshot.seek(0)
        restore(self.database, snapshot)
        self.assertEqual(self.server.store.list_notes()[0]["text"], "original")
        with self.assertRaises(sqlite3.DatabaseError):
            restore(self.database, io.BytesIO(b"not a database"))
        self.assertEqual(self.server.store.list_notes()[0]["text"], "original")

    def test_future_migration_rejected(self):
        with sqlite3.connect(self.database) as db:
            db.execute("PRAGMA user_version=99")
        with self.assertRaises(RuntimeError):
            Store(self.database)

    def test_invalid_token(self):
        with self.assertRaises(ValueError):
            make_server("127.0.0.1", 0, self.database, "short")


if __name__ == "__main__":
    unittest.main()
