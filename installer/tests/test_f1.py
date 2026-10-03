import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from emaki_installer import cli
from emaki_installer.arch_backend import load_archinstall
from emaki_installer.constants import GIB, PACKAGES
from emaki_installer.errors import Code, InstallError
from emaki_installer.planner import make_plan
from emaki_installer.protocol import Controller
from emaki_installer.runtime import Redactor, Runner
from emaki_installer.worker import Worker, preflight_repo
from support import FakeInventory, config, inventory, manual


class FixTests(unittest.TestCase):
    def test_distribution_version_and_hello_without_module_version(self):
        parted = SimpleNamespace(getAllDevices=Mock(), getDevice=Mock(), IOException=RuntimeError)
        with patch.dict('sys.modules', parted=parted), \
                patch('emaki_installer.arch_backend.dist_version', return_value='4.5') as version, \
                patch('emaki_installer.arch_backend.importlib.import_module', return_value=SimpleNamespace()):
            api = load_archinstall()
            version.assert_called_once_with('archinstall')
            hello = Controller(FakeInventory(), None, version=api.version).handle(
                {'type': 'hello', 'id': 'hello', 'proto': 1})[0]
            self.assertEqual(hello['archinstall_version'], '4.5')
            version.return_value = '4.4'
            with self.assertRaises(InstallError) as raised:
                load_archinstall()
            self.assertEqual(raised.exception.code, Code.UNSUPPORTED_VERSION)

    def test_cli_follow_plan_and_reattach(self):
        with tempfile.TemporaryDirectory() as temporary:
            plan = Path(temporary) / 'plan.json'
            plan.write_text(json.dumps(config()))
            for args, replies, sent in (
                (['--plan', str(plan), '--yes', '--follow'],
                 [{'type': 'hello'}, {'type': 'plan_ack', 'errors': [], 'plan_id': 'p', 'token': 'secret'},
                  {'type': 'done'}], ['hello', 'plan', 'confirm']),
                (['--follow'], [{'type': 'hello', 'busy_job': 'job'}, {'type': 'done', 'job_id': 'job'}],
                 ['hello', 'resume']),
            ):
                with patch('emaki_installer.cli.socket.socket') as socket, contextlib.redirect_stdout(io.StringIO()):
                    client = socket.return_value.__enter__.return_value
                    client.makefile.return_value = io.BytesIO(b''.join(json.dumps(r).encode() + b'\n' for r in replies))
                    self.assertEqual(cli.main(args), 0)
                    self.assertEqual([json.loads(c.args[0])['type'] for c in client.sendall.call_args_list], sent)

    def test_cli_exclusive_actions(self):
        for args in ([], ['--follow', '--probe'], ['--follow', '--save-log', '/tmp/log'],
                     ['--plan', 'x', '--follow'], ['--probe', '--plan', 'x']):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                cli.main(args)
            self.assertEqual(raised.exception.code, 2)

    def test_manual_root_partition_size_boundary(self):
        for size in (4 * GIB, 20 * GIB - 1, 20 * GIB):
            for fmt in (True, False):
                inv = inventory(partitions=True)
                inv['disks'][0]['partitions'][1]['size_bytes'] = size
                if size < 20 * GIB:
                    with self.assertRaises(InstallError) as raised:
                        make_plan(manual(format=fmt), inv)
                    self.assertEqual(raised.exception.code, Code.ROOT_TOO_SMALL)
                else:
                    make_plan(manual(format=fmt), inv)

    def test_all_vm_fixtures_match_serial_ids(self):
        root = Path(__file__).resolve().parents[2]
        for directory in ('installer/fixtures', 'tests/vm/fixtures'):
            for path in (root / directory).glob('plan-*.json'):
                data = json.loads(path.read_text())
                self.assertEqual(data['disk_id'], '/dev/disk/by-id/virtio-emaki-target')
                inv = inventory(partitions=True)
                inv['disks'][0]['id'] = data['disk_id']
                for part in inv['disks'][0]['partitions']:
                    part['id'] = data['disk_id'] + '-part' + str(part['number'])
                make_plan(data, inv)

    def test_finish_rejects_each_bad_directory_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            worker = Worker(None, None, Redactor(), Mock(), Mock(), target=root)
            for name in ('/', '/etc', '/usr', '/var', '/boot'):
                worker.files.mkdir(name).chmod(0o755)
            with patch('emaki_installer.worker.LOG') as log:
                log.read_bytes.return_value = b'log\n'
                for name in ('/', '/etc', '/usr', '/var', '/boot'):
                    path = worker.files.path(name)
                    path.chmod(0o700)
                    with self.assertRaises(InstallError) as raised:
                        worker.finish()
                    self.assertIn(name, str(raised.exception))
                    self.assertEqual(raised.exception.code, Code.BOOT_VERIFY)
                    path.chmod(0o755)
                worker.finish()
                # Arch's root directory is 0555 on real pacstrap targets (seen in the VM).
                worker.files.path('/').chmod(0o555)
                worker.finish()
                worker.files.path('/').chmod(0o755)


class RepoPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        package = self.root / 'example.pkg.tar.zst'
        package.write_bytes(b'package')
        Path(str(package) + '.sig').write_bytes(b'signature')
        self.package = package
        self.calls = self.root / 'calls.jsonl'
        pacman = self.root / 'pacman'
        pacman.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ['PREFLIGHT_CALLS'], 'a') as stream:
    stream.write(json.dumps(args) + '\\n')
db = pathlib.Path(args[args.index('--dbpath') + 1])
if args[0] == '-Sy':
    assert not list(db.iterdir())
    (db / 'synced').touch()
assert (db / 'synced').exists()
if os.environ.get('PREFLIGHT_FAIL') == args[0]:
    sys.exit(1)
if args[0] == '-Sp':
    print(os.environ['PREFLIGHT_PACKAGE'])
''')
        pacman.chmod(0o755)
        env = patch.dict(os.environ, PATH=str(self.root) + ':' + os.environ['PATH'],
                         PREFLIGHT_CALLS=str(self.calls), PREFLIGHT_PACKAGE=package.as_uri())
        env.start()
        self.addCleanup(env.stop)
        conf = patch('emaki_installer.worker.offline_config', return_value=self.root / 'offline.conf')
        conf.start()
        self.addCleanup(conf.stop)

    def test_fake_pacman_resolves_full_set_in_private_db(self):
        preflight_repo(Runner(lambda line: None), True)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual([c[0] for c in calls], ['-Sy', '-Sp'])
        self.assertTrue(set(PACKAGES + ['mkinitcpio', 'openssh', 'grim']) <= set(calls[1]))
        self.assertIn('wireless-regdb', PACKAGES)
        self.assertEqual(calls[0][calls[0].index('--dbpath') + 1], calls[1][calls[1].index('--dbpath') + 1])
        self.assertFalse(Path(calls[0][calls[0].index('--dbpath') + 1]).exists())

    def test_release_preflight_excludes_test_extras(self):
        preflight_repo(Runner(lambda line: None), False)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertNotIn('openssh', calls[1])
        self.assertNotIn('grim', calls[1])

    def test_failure_never_constructs_backend_or_cleans_target(self):
        for failure in ('-Sy', '-Sp', 'missing-package', 'missing-signature'):
            with self.subTest(failure=failure), patch.dict(os.environ, PREFLIGHT_FAIL=failure):
                if failure == 'missing-package':
                    self.package.unlink()
                if failure == 'missing-signature':
                    self.package.write_bytes(b'package')
                    Path(str(self.package) + '.sig').unlink()
                backend, events = Mock(), []
                worker = Worker(None, FakeInventory(), Redactor(), lambda kind, **kw: events.append((kind, kw)),
                                lambda line: None, target=self.root / 'target', backend_factory=backend)
                with patch('emaki_installer.worker.cleanup') as cleanup:
                    worker.run(make_plan(config(), inventory()), threading.Event())
                backend.assert_not_called()
                cleanup.assert_not_called()
                self.assertFalse(worker.disk_changed)
                self.assertFalse(worker.target.exists())
                self.assertEqual(events[-1][0], 'error')
                self.assertEqual(events[-1][1]['code'], 'offline_repo_incomplete')
