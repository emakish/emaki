import importlib.util
import json
from pathlib import Path
import secrets
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

UI = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ui_helper', UI / 'ui-helper.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class Helpers(unittest.TestCase):
    def test_catalog(self):
        rows = helper.layouts()
        self.assertTrue(any(x['layout'] == 'us' and not x['variant'] for x in rows))
        self.assertTrue(any(x['layout'] == 'us' and x['variant'] == 'dvorak' for x in rows))
        self.assertIn('Europe/Lisbon', helper.zones())

    def test_nmcli_escape_and_credentials(self):
        self.assertEqual(helper.terse(r'Cafe\: A\\B:72'), ['Cafe: A\\B', '72'])
        secret = secrets.token_hex(16)
        with patch.object(helper, 'run', return_value=subprocess.CompletedProcess([], 0, b'', b'')) as run:
            result = helper.join(dict(bssid='AA:BB:CC:DD:EE:FF', device='wlan0', password=secret))
            self.assertTrue(result['ok'])
            argv = run.call_args.args[0]
            self.assertNotIn(secret, ' '.join(argv))
            self.assertIn('--ask', argv)
            self.assertEqual(run.call_args.kwargs['input'], (secret + '\n').encode())
            self.assertNotIn(secret, json.dumps(result))

    def test_trial_refuses_nonlive(self):
        with patch.object(helper, 'trial_available', return_value=False), patch.object(helper.os, 'open') as op:
            self.assertFalse(helper.trial(['us'])['ok'])
            op.assert_not_called()

    def test_media_filters_unmounted_and_nonremovable(self):
        mounts = dict(filesystems=[dict(target='/home/live', source='/dev/vda2'),
                                   dict(target='/run/media/live/USB', source='/dev/sdb1'),
                                   dict(target='/run/media/live/INTERNAL', source='/dev/vda3')])
        def run(argv, **_):
            data = mounts if argv[0] == 'findmnt' else dict(blockdevices=[dict(rm=argv[-1] == '/dev/sdb1', tran='sata')])
            return subprocess.CompletedProcess(argv, 0, json.dumps(data).encode(), b'')
        with patch.object(helper, 'run', side_effect=run):
            self.assertEqual(helper.media()['media'], ['/run/media/live/USB'])

    def test_mock_worker_stdio(self):
        secret = secrets.token_hex(16)
        requests = [dict(type='hello', id='h', proto=1), dict(type='probe', id='i'),
                    dict(type='plan', id='p', config=dict(user=dict(password=secret))),
                    dict(type='confirm', id='c', plan_id='fixture-plan', token='mock-capability')]
        result = subprocess.run([sys.executable, '-B', str(UI / 'tests/mock-worker.py'), '--stdio', '--delay', '0'],
                                input=''.join(json.dumps(x) + '\n' for x in requests), text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout.splitlines()[-1])['type'], 'done')

    def test_launcher_single_instance_and_reopen(self):
        with tempfile.TemporaryDirectory(prefix='emaki-launcher-') as temporary:
            root = Path(temporary)
            (root / 'runtime').mkdir(mode=0o700)
            (root / 'bin').mkdir()
            fake = root / 'bin/qs'
            fake.write_text('#!/usr/bin/python3\nimport os,sys,time\n'
                            'with open(os.environ["EMAKI_LAUNCH_TEST_LOG"], "a") as f: f.write("ipc\\n" if "ipc" in sys.argv else "launch\\n")\n'
                            'if "ipc" not in sys.argv: time.sleep(1.5)\n')
            fake.chmod(0o700)
            log = root / 'calls'
            env = dict(os.environ, XDG_RUNTIME_DIR=str(root / 'runtime'), EMAKI_LAUNCH_TEST_LOG=str(log),
                       PATH=str(root / 'bin') + os.pathsep + os.environ['PATH'])
            first = subprocess.Popen([str(UI / 'emaki-install')], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 2
                while not log.exists() and time.monotonic() < deadline: time.sleep(.01)
                self.assertTrue(log.exists())
                second = subprocess.run([str(UI / 'emaki-install')], env=env, capture_output=True, timeout=3)
                self.assertEqual(second.returncode, 0)
                self.assertEqual(log.read_text().splitlines(), ['launch', 'ipc'])
            finally:
                first.terminate()
                first.communicate(timeout=3)


if __name__ == '__main__': unittest.main()
