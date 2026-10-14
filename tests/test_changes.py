import importlib.util
import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('outpost_control', ROOT / 'control/server.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class ChangesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'queue.sqlite3'
        self.server = control.make_server('127.0.0.1', 0, self.db, 'c' * 64, 'w' * 64)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def call(self, path, body=None, token='c' * 64):
        request = urllib.request.Request(self.url + path, data=json.dumps(body).encode() if body is not None else None,
                                        headers={'Authorization': 'Bearer ' + token})
        try:
            response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def submit(self, key='one'):
        status, job = self.call('/_outpost/changes', {'request': 'Add a tasks endpoint', 'idempotencyKey': key})
        self.assertEqual(status, 202)
        return job

    def test_auth_roles_and_body_validation(self):
        self.assertEqual(self.call('/_outpost/changes', {}, token='a' * 64)[0], 401)
        self.assertEqual(self.call('/_outpost/worker/claim', {})[0], 401)
        self.assertEqual(self.call('/_outpost/changes', {}, token='w' * 64)[0], 401)
        for body in ({}, [], {'request': 'x', 'idempotencyKey': '../bad'}, {'request': '', 'idempotencyKey': 'x'},
                     {'request': 'x', 'idempotencyKey': 'x', 'repo': '/tmp'}):
            self.assertEqual(self.call('/_outpost/changes', body)[0], 400)

    def test_durable_queue_idempotency_and_completion(self):
        first = self.submit()
        self.assertEqual(first, self.submit())
        self.assertEqual(control.Queue(self.db).get(first['id'])['status'], 'queued')
        self.assertEqual(self.call('/_outpost/changes', {'request': 'different', 'idempotencyKey': 'one'})[0], 400)
        _, claimed = self.call('/_outpost/worker/claim', {}, 'w' * 64)
        job = claimed['job']
        self.assertEqual(job['id'], first['id'])
        self.assertIsNone(self.call('/_outpost/worker/claim', {}, 'w' * 64)[1]['job'])
        path = '/_outpost/worker/' + job['id']
        self.assertEqual(self.call(path + '/heartbeat', {'lease': 'wrong'}, 'w' * 64)[0], 409)
        self.assertEqual(self.call(path + '/heartbeat', {'lease': job['lease']}, 'w' * 64)[0], 200)
        self.assertEqual(self.call(path + '/complete', {'lease': job['lease'], 'result': {'status': 'review_ready'}}, 'w' * 64)[0], 200)
        result = self.call('/_outpost/changes/' + job['id'])[1]
        self.assertEqual(result['status'], 'review_ready')
        self.assertNotIn('lease', result)

    def test_concurrent_claims_only_one_wins(self):
        self.submit()
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.server.queue.claim())) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len([job for job in results if job]), 1)

    def test_expired_jobs_not_replayed_and_queue_bound(self):
        self.submit()
        job = self.server.queue.claim()
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('UPDATE jobs SET expires=?', (time.time() - 10,))
        self.assertIsNone(self.server.queue.claim())
        self.assertEqual(self.server.queue.get(job['id'])['status'], 'failed')
        for index in range(20):
            self.submit(str(index))
        self.assertEqual(self.call('/_outpost/changes', {'request': 'more', 'idempotencyKey': 'full'})[0], 429)

    @unittest.skipUnless((ROOT / 'worker.py').exists(), 'Worker integration runs in toolkit repository')
    def test_worker_executes_fake_pi_in_worktree_and_commits_after_tests(self):
        sys.path.insert(0, str(ROOT))
        import worker
        import subprocess
        repo = Path(self.tmp.name) / 'repo'
        repo.mkdir()
        subprocess.run(['git', 'init', '-b', 'main', str(repo)], check=True, capture_output=True)
        worker.git(repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '--allow-empty', '-m', 'init')
        fake = Path(self.tmp.name) / 'pi'
        fake.write_text('#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\nprompt=sys.stdin.read()\nassert "Add a tasks endpoint" in prompt\nPath("new_endpoint.py").write_text("# generated\\n")\n')
        fake.chmod(0o755)
        token = Path(self.tmp.name) / 'token'
        token.write_text('w' * 64)
        self.submit()
        job = self.server.queue.claim()
        state = Path(self.tmp.name) / 'state'
        state.mkdir()
        config = {'repo': str(repo), 'pi': str(fake), 'test_command': [sys.executable, '-c', 'from pathlib import Path; assert Path("new_endpoint.py").exists()'],
                  'url': self.url, 'token_file': str(token), 'timeout': 20}
        result = worker.run_job(config, job, state)
        self.assertEqual(result['status'], 'review_ready')
        self.assertEqual(worker.git(repo, 'rev-parse', '--abbrev-ref', 'HEAD'), 'main')
        self.assertFalse((repo / 'new_endpoint.py').exists())
        self.assertIn('new_endpoint.py', worker.git(repo, 'show', '--stat', result['commit']))
        self.assertFalse((state / (job['id'] + '.prompt')).exists())

    @unittest.skipUnless((ROOT / 'worker.py').exists(), 'Worker integration runs in toolkit repository')
    def test_background_worker_claims_completes_and_stops(self):
        import os
        import subprocess
        import hashlib
        sys.path.insert(0, str(ROOT))
        import worker
        repo = Path(self.tmp.name) / 'background'
        repo.mkdir()
        subprocess.run(['git', 'init', '-b', 'main', str(repo)], check=True, capture_output=True)
        worker.git(repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '--allow-empty', '-m', 'init')
        fake = Path(self.tmp.name) / 'fake-pi'
        fake.write_text('#!/usr/bin/env python3\nfrom pathlib import Path\nPath("result.py").write_text("# generated\\n")\n')
        fake.chmod(0o755)
        token = Path(self.tmp.name) / 'token'
        token.write_text('w' * 64)
        config = {'repo': str(repo), 'pi': str(fake), 'test_command': [sys.executable, '-c', 'pass'],
                  'url': self.url, 'token_file': str(token), 'timeout': 20}
        (repo / '.gitignore').write_text('.outpost-worker.json\n')
        worker.git(repo, 'add', '.gitignore')
        worker.git(repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-m', 'ignore config')
        (repo / '.outpost-worker.json').write_text(json.dumps(config))
        job = self.submit()
        env = {**os.environ, 'HOME': self.tmp.name}
        process = subprocess.Popen([sys.executable, str(ROOT / 'worker.py'), str(repo)], env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        tag = hashlib.sha256(str(repo.resolve()).encode()).hexdigest()[:16]
        state = Path(self.tmp.name) / '.local/state/outpost/workers' / tag
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                result = self.server.queue.get(job['id'])
                if result['status'] == 'review_ready':
                    break
                time.sleep(0.1)
            self.assertEqual(result['status'], 'review_ready')
            self.assertFalse((state / 'pending.json').exists())
            (state / 'stop').touch()
            self.assertEqual(process.wait(timeout=10), 0)
            self.assertFalse((state / 'pid').exists())
        finally:
            worker.terminate(process)

    @unittest.skipUnless((ROOT / 'worker.py').exists(), 'Worker integration runs in toolkit repository')
    def test_worker_timeout_stops_process(self):
        sys.path.insert(0, str(ROOT))
        import worker
        import subprocess
        repo = Path(self.tmp.name) / 'repo'
        repo.mkdir()
        subprocess.run(['git', 'init', '-b', 'main', str(repo)], check=True, capture_output=True)
        worker.git(repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '--allow-empty', '-m', 'init')
        fake = Path(self.tmp.name) / 'pi'
        fake.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n')
        fake.chmod(0o755)
        token = Path(self.tmp.name) / 'token'
        token.write_text('w' * 64)
        self.submit()
        job = self.server.queue.claim()
        state = Path(self.tmp.name) / 'state'
        state.mkdir()
        config = {'repo': str(repo), 'pi': str(fake), 'test_command': ['false'], 'url': self.url,
                  'token_file': str(token), 'timeout': 0.5}
        with self.assertRaises(RuntimeError):
            worker.run_job(config, job, state)
        self.assertFalse((state / (job['id'] + '.prompt')).exists())

    @unittest.skipUnless((ROOT / 'worker.py').exists(), 'Worker integration runs in toolkit repository')
    def test_worker_tests_failure_never_marks_review_ready(self):
        sys.path.insert(0, str(ROOT))
        import worker
        import subprocess
        repo = Path(self.tmp.name) / 'repo'
        repo.mkdir()
        subprocess.run(['git', 'init', '-b', 'main', str(repo)], check=True, capture_output=True)
        worker.git(repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '--allow-empty', '-m', 'init')
        self.submit()
        job = self.server.queue.claim()
        token = Path(self.tmp.name) / 'token'
        token.write_text('w' * 64)
        state = Path(self.tmp.name) / 'state'
        state.mkdir()
        config = {'repo': str(repo), 'pi': '/usr/bin/false', 'test_command': ['false'], 'url': self.url,
                  'token_file': str(token), 'timeout': 5}
        with self.assertRaises(RuntimeError):
            worker.run_job(config, job, state)
