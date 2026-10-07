import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
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
from emaki_installer.worker import Worker, preflight_repo, verify_packages, verify_snapshot_hook
from support import FakeInventory, alongside_on, config, inventory, manual

# The packaged snapshot boot hook, as named inside an emaki-config archive.
SNAPSHOT_HOOK = ('usr/lib/initcpio/hooks/emaki-snapshot-fstab', 'usr/lib/initcpio/install/emaki-snapshot-fstab')


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

    def test_cli_without_a_worker_says_why(self):
        # copytoram: the worker's unit is skipped (ConditionPathExists) and no socket exists.
        with tempfile.TemporaryDirectory(prefix='emi-', dir='/tmp') as temporary:
            missing = Path(temporary) / 'sock'
            for mounted in (False, True):
                if mounted:
                    (Path(temporary) / 'bootmnt').mkdir()
                stderr = io.StringIO()
                with self.subTest(mounted=mounted):
                    with patch('emaki_installer.cli.BOOT_MOUNT', Path(temporary) / 'bootmnt'), \
                            contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(cli.main(['--probe', '--socket', str(missing)]), 2)
                    text = stderr.getvalue()
                    self.assertNotIn('FileNotFoundError', text)
                    self.assertNotIn('Traceback', text)
                    if mounted:
                        self.assertIn('the installer service is not running', text)
                    else:
                        self.assertIn("Restart from the USB stick without the 'copy to RAM' option to install.", text)
                        self.assertIn('boot medium is not mounted', text)

    def test_one_boot_mount_for_the_unit_the_cli_and_the_window(self):
        root = Path(__file__).resolve().parents[1]
        unit = (root / 'systemd/emaki-installerd.service').read_text()
        self.assertIn(f'ConditionPathExists={cli.BOOT_MOUNT}\n', unit)
        helper = (root / 'ui/ui-helper.py').read_text()
        self.assertIn(f"BOOT_MOUNT = Path('{cli.BOOT_MOUNT}')\n", helper)

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
                if path.name.startswith('plan-upgrade'):
                    # Fed to the old 0.1.x installers by tests/vm/upgrade-check.py, in their format.
                    continue
                data = json.loads(path.read_text())
                self.assertEqual(data['disk_id'], '/dev/disk/by-id/virtio-emaki-target')
                inv = inventory(partitions=True)
                inv['disks'][0]['id'] = data['disk_id']
                for part in inv['disks'][0]['partitions']:
                    part['id'] = data['disk_id'] + '-part' + str(part['number'])
                if data['mode'] == 'alongside':
                    self.assertEqual(data['partition_id'], data['disk_id'] + '-part3')
                    with self.assertRaises(InstallError) as raised:
                        make_plan(data, inv)
                    self.assertEqual(raised.exception.code, Code.UNSUPPORTED_MODE)
                    with alongside_on(), self.assertRaises(InstallError) as raised:
                        make_plan(data, inv)
                    self.assertEqual(raised.exception.code, Code.SHRINK_BOUNDS)
                else:
                    make_plan(data, inv)

    def test_finish_rejects_each_bad_directory_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            worker = Worker(None, None, Redactor(), Mock(), Mock(), target=root)
            worker.plan = make_plan(config(), inventory())
            for name in ('/', '/etc', '/usr', '/var', '/boot'):
                worker.files.mkdir(name).chmod(0o755)
            # Offline at the end: finish() leaves the package lists to the person (NO-SYNC-DB).
            with patch('emaki_installer.worker.LOG') as log, \
                    patch('emaki_installer.worker.default_route', return_value=False):
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
        # The fake pacman-key below accepts a signature that is the archive's SHA-256.
        Path(str(package) + '.sig').write_text(hashlib.sha256(b'package').hexdigest())
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
        self.key_calls = self.root / 'key-calls.jsonl'
        pacman_key = self.root / 'pacman-key'
        pacman_key.write_text('''#!/usr/bin/env python3
import hashlib, json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ['PREFLIGHT_KEY_CALLS'], 'a') as stream:
    stream.write(json.dumps(args) + '\\n')
assert args[0] == '--verify' and len(args) == 3
signature, package = (pathlib.Path(name).read_bytes() for name in args[1:])
sys.exit(0 if signature.decode() == hashlib.sha256(package).hexdigest() else 1)
''')
        pacman_key.chmod(0o755)
        env = patch.dict(os.environ, PATH=str(self.root) + ':' + os.environ['PATH'],
                         PREFLIGHT_CALLS=str(self.calls), PREFLIGHT_KEY_CALLS=str(self.key_calls),
                         PREFLIGHT_PACKAGE=package.as_uri())
        env.start()
        self.addCleanup(env.stop)
        conf = patch('emaki_installer.worker.offline_config', return_value=self.root / 'offline.conf')
        conf.start()
        self.addCleanup(conf.stop)
        source = patch('emaki_installer.worker.package_source',
                       side_effect=lambda *a, **kw: contextlib.nullcontext(SimpleNamespace(
                           validate=lambda: self.root / 'offline.conf')))
        source.start()
        self.addCleanup(source.stop)
        clock = patch('emaki_installer.worker.ensure_clock', return_value=False)
        clock.start()
        self.addCleanup(clock.stop)

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
        for failure in ('-Sy', '-Sp', 'missing-package', 'missing-signature', 'bad-signature'):
            with self.subTest(failure=failure), patch.dict(os.environ, PREFLIGHT_FAIL=failure):
                if failure == 'missing-package':
                    self.package.unlink()
                if failure == 'missing-signature':
                    self.package.write_bytes(b'package')
                    Path(str(self.package) + '.sig').unlink()
                if failure == 'bad-signature':
                    # One changed byte: the archive no longer matches its signature.
                    self.package.write_bytes(b'packagf')
                    Path(str(self.package) + '.sig').write_text(hashlib.sha256(b'package').hexdigest())
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
                self.assertIs(events[-1][1]['retryable'], True)
                if failure == 'bad-signature':
                    self.assertIn('disk untouched', events[-1][1]['message'])
                    self.assertIn(self.package.name, events[-1][1]['message'])
                    self.assertEqual(json.loads(self.key_calls.read_text()),
                                     ['--verify', str(self.package) + '.sig', str(self.package)])

    def emaki_config(self, *members, name='emaki-config-0.1.2-1-x86_64.pkg.tar.zst'):
        """A real zstd package archive in the offline repository, signed for the fake pacman-key."""
        archive = self.root / name
        with tarfile.open(archive, 'w:zst') as output:
            for member in ('.PKGINFO', 'etc/niri/config.kdl', *members):
                entry = tarfile.TarInfo(member)
                entry.size = len(member)
                output.addfile(entry, io.BytesIO(member.encode()))
        Path(str(archive) + '.sig').write_text(hashlib.sha256(archive.read_bytes()).hexdigest())
        return archive

    def test_an_emaki_config_without_the_snapshot_hook_is_refused_before_the_disk(self):
        # A mis-assembled ISO: an emaki-config of the same version (0.1.2-1) built before the
        # hook moved into the package. The bootloader phase found it only after the disk was erased.
        for members in ((), (SNAPSHOT_HOOK[0],), (SNAPSHOT_HOOK[1],)):
            with self.subTest(members=members):
                for path in self.root.glob('emaki-config-*'):
                    path.unlink()
                self.key_calls.unlink(missing_ok=True)
                archive = self.emaki_config(*members)
                backend, events = Mock(), []
                worker = Worker(None, FakeInventory(), Redactor(), lambda kind, **kw: events.append((kind, kw)),
                                lambda line: None, target=self.root / 'target', backend_factory=backend)
                with patch.dict(os.environ, PREFLIGHT_PACKAGE=self.package.as_uri() + '\n' + archive.as_uri()), \
                        patch('emaki_installer.worker.cleanup') as cleanup:
                    worker.run(make_plan(config(fs='btrfs'), inventory()), threading.Event())
                backend.assert_not_called()
                cleanup.assert_not_called()
                self.assertFalse(worker.disk_changed)
                self.assertFalse(worker.target.exists())
                self.assertEqual(events[-1][0], 'error')
                self.assertEqual(events[-1][1]['code'], 'offline_repo_incomplete')
                self.assertIs(events[-1][1]['retryable'], True)
                self.assertIn('disk untouched', events[-1][1]['message'])
                self.assertIn(archive.name, events[-1][1]['message'])
                # The archive that is read is the one the signature check has proven.
                verified = [json.loads(line)[-1] for line in self.key_calls.read_text().splitlines()]
                self.assertEqual(verified, [str(self.package), str(archive)])

    def test_the_snapshot_hook_check_reads_the_emaki_config_archive(self):
        logs = []
        runner = Runner(logs.append)
        archive = self.emaki_config(*SNAPSHOT_HOOK)
        verify_snapshot_hook(runner, [self.package, archive])
        self.assertIn('$ bsdtar -tf ' + str(archive) + ' ' + ' '.join(SNAPSHOT_HOOK), logs)
        # Only emaki-config itself counts: another package whose name starts the same does not.
        decoy = self.emaki_config(*SNAPSHOT_HOOK, name='emaki-config-extra-0.1.2-1-x86_64.pkg.tar.zst')
        for packages in ([self.package], [self.package, decoy]):
            with self.subTest(packages=[p.name for p in packages]), self.assertRaises(InstallError) as raised:
                verify_snapshot_hook(runner, packages)
            self.assertEqual(raised.exception.code, Code.OFFLINE_REPO_INCOMPLETE)
            self.assertIn('disk untouched: no single emaki-config archive', raised.exception.message)
        # An archive bsdtar cannot read is not reported as one without the hook.
        archive.write_bytes(b'not an archive')
        with self.assertRaises(InstallError) as raised:
            verify_snapshot_hook(runner, [archive])
        self.assertIn(f'disk untouched: cannot list {archive.name}: bsdtar exited with status 1.',
                      raised.exception.message)

    def test_every_resolved_archive_is_verified_against_its_signature(self):
        packages = preflight_repo(Runner(lambda line: None), False)
        self.assertEqual(packages, [self.package])
        self.assertFalse(self.key_calls.exists())
        verify_packages(Runner(lambda line: None), packages)
        self.assertEqual(json.loads(self.key_calls.read_text()),
                         ['--verify', str(self.package) + '.sig', str(self.package)])
