import argparse
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless((ROOT / 'worker.py').exists(), 'Agent adapter tests run in toolkit repository')
class AgentCommandTest(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT))
        import worker
        self.worker = worker

    def args(self, **kwargs):
        return argparse.Namespace(agent=None, agent_command=None, model=None, **kwargs)

    def test_presets_and_model_flags(self):
        for agent in ('pi', 'claude', 'codex'):
            args = self.args()
            args.agent = agent
            args.model = 'local-model'
            with patch.object(self.worker.shutil, 'which', return_value='/bin/' + agent):
                command = self.worker.agent_command(args)
            self.assertEqual(command[0], '/bin/' + agent)
            self.assertIn('local-model', command)
            self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', command)
            self.assertNotIn('--dangerously-skip-permissions', command)
            if agent == 'codex':
                self.assertEqual(command[-1], '-')

    def test_custom_argv_is_not_shell_evaluated(self):
        args = self.args()
        args.agent_command = 'my-agent --mode "a b" --input {prompt_file} "$(touch /tmp/nope)"'
        with patch.object(self.worker.shutil, 'which', return_value='/bin/my-agent'):
            command = self.worker.agent_command(args)
        self.assertEqual(command, ['/bin/my-agent', '--mode', 'a b', '--input', '{prompt_file}', '$(touch /tmp/nope)'])
        result, file_input = self.worker.invocation({'agent_command': command}, Path('/private/prompt file'))
        self.assertTrue(file_input)
        self.assertIn('/private/prompt file', result)
        self.assertNotIn('{prompt_file}', result)

    def test_bad_commands_fail_before_worker_starts(self):
        for command, model in (('', None), ('missing', None), ('pi --input={prompt_file}', None), ('pi', 'model')):
            args = self.args()
            args.agent_command = command or ' '
            args.model = model
            with patch.object(self.worker.shutil, 'which', return_value=None):
                with self.assertRaises(ValueError):
                    self.worker.agent_command(args)

    def test_old_pi_configuration_still_works(self):
        command, file_input = self.worker.invocation({'pi': '/bin/pi', 'model': 'local'}, Path('/private/prompt'))
        self.assertEqual(command[0], '/bin/pi')
        self.assertIn('local', command)
        self.assertFalse(file_input)

    def test_custom_file_agent_implements_change_and_leaves_original_untouched(self):
        import subprocess
        import threading
        spec = importlib.util.spec_from_file_location('adapter_control', ROOT / 'control/server.py')
        control = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(control)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / 'repo'
            repo.mkdir()
            subprocess.run(['git', 'init', '-b', 'main', str(repo)], check=True, capture_output=True)
            self.worker.git(repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '--allow-empty', '-m', 'init')
            fake = root / 'custom agent.py'
            fake.write_text('import sys\nfrom pathlib import Path\nassert sys.stdin.read()==""\nassert "Do not deploy" in Path(sys.argv[1]).read_text()\nPath("endpoint.py").write_text("# new endpoint\\n")\n')
            server = control.make_server('127.0.0.1', 0, root / 'queue.sqlite3', 'c' * 64, 'w' * 64)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            token = root / 'token'
            token.write_text('w' * 64)
            state = root / 'state'
            state.mkdir()
            server.queue.submit('Add an endpoint; $(touch /tmp/nope)', 'file-agent')
            job = server.queue.claim()
            config = {'repo': str(repo), 'agent_command': [sys.executable, str(fake), '{prompt_file}'],
                      'url': f'http://127.0.0.1:{server.server_port}', 'token_file': str(token), 'timeout': 15,
                      'test_command': [sys.executable, '-c', 'from pathlib import Path; assert Path("endpoint.py").exists()']}
            try:
                result = self.worker.run_job(config, job, state)
                self.assertEqual(result['status'], 'review_ready')
                self.assertFalse((repo / 'endpoint.py').exists())
                self.assertFalse((state / (job['id'] + '.prompt')).exists())
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
