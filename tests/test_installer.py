import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless((ROOT / 'install.sh').exists(), 'Installer checks only run in toolkit repository')
class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        archive = self.root / 'release.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            for item in ('outpost', 'worker.py', 'api', 'control', 'tests', 'Dockerfile', 'compose.yaml', '.gitignore', '.dockerignore'):
                tar.add(ROOT / item, arcname='outpost/' + item)
        mock = self.root / 'mock'
        mock.mkdir()
        (mock / 'curl').write_text('#!/bin/sh\nfor last do :; done\ncp "$ARCHIVE" "$last"\n')
        (mock / 'curl').chmod(0o755)
        self.env = {**os.environ, 'PATH': str(mock) + ':' + os.environ['PATH'], 'ARCHIVE': str(archive),
                    'OUTPOST_BIN_DIR': str(self.root / 'bin'), 'OUTPOST_INSTALL_DIR': str(self.root / 'runtime')}

    def tearDown(self):
        self.tmp.cleanup()

    def install(self):
        return subprocess.run(['sh', str(ROOT / 'install.sh')], env=self.env, text=True, capture_output=True)

    def test_install_update_and_scaffold_include_worker_and_management(self):
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual(self.install().returncode, 0)
        app = self.root / 'app'
        app.mkdir()
        subprocess.run([str(self.root / 'bin/outpost'), 'init', '--domain', 'api.example.com', '--host', 'example', '--name', 'smoke'],
                       cwd=app, env=self.env, check=True, capture_output=True)
        self.assertTrue((self.root / 'runtime/worker.py').is_file())
        self.assertTrue((app / 'control/server.py').is_file())
        tokens = [(app / 'secrets' / name).read_text() for name in ('api_token', 'change_token', 'worker_token')]
        self.assertEqual(len(set(tokens)), 3)

    def test_unrelated_command_never_overwritten(self):
        (self.root / 'bin').mkdir()
        executable = self.root / 'bin/outpost'
        executable.write_text('do not touch')
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual(executable.read_text(), 'do not touch')

    def test_previous_version_launcher_can_update(self):
        (self.root / 'bin').mkdir()
        (self.root / 'bin/outpost').write_text('#!/bin/sh\nexec python3 "' + str(self.root / 'runtime/outpost') + '" "$@"\n')
        self.assertEqual(self.install().returncode, 0)
