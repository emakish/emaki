#!/usr/bin/env python3
"""tests/vm/iso-install.sh against a stub ssh: no QEMU, no guest. The alongside fixture on an
image whose installer refuses the mode ends NOT APPLICABLE (exit 77), never a failure or a pass."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from unittest import mock
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
VM = ROOT / 'tests/vm'
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)

# Answers by the remote command (the last argument). STUB_OFFER=refuse makes the worker refuse
# the alongside mode as the current installer core does; accept makes it take the plan.
STUB_SSH = r'''#!/usr/bin/env bash
command=${!#}
printf '%s\n' "$command" >>"$STUB_LOG"
case $command in
    *'cat > '*) cat >/dev/null ;;
    *'emaki-install-cli --plan'*)
        if [[ $STUB_OFFER == refuse ]]; then
            echo '{"type": "plan_ack", "plan_id": "p1", "errors": [{"code": "unsupported_mode", "message": "Installing alongside Windows is not available in this version."}]}'
            exit 3
        fi
        echo '{"type": "plan_ack", "plan_id": "p1", "errors": []}'
        [[ $command != *--yes* ]] || echo '{"type": "done", "ok": true}' ;;
esac
exit 0
'''


class IsoInstallAlongsideTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='iso-install-', dir=CACHE)
        self.base = Path(self.work.name)
        self.vm = self.base / 'repo/tests/vm'
        self.vm.mkdir(parents=True)
        for name in ('iso-install.sh', 'iso-common.sh', 'iso-wait-ssh.sh', 'iso-owner.py'):
            shutil.copy(VM / name, self.vm / name)
        shutil.copytree(VM / 'fixtures', self.vm / 'fixtures')
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        (self.bin / 'ssh').write_text(STUB_SSH)
        (self.bin / 'ssh').chmod(0o755)
        self.key = self.base / 'key'
        self.key.write_text('not a key\n')
        self.log = self.base / 'calls.log'
        self.log.touch()
        self.dir = self.base / 'vm'
        self.dir.mkdir()
        # iso-install.sh talks only to the QEMU of its directory: a fake one holds the directory's
        # run.lock (as run-iso.sh's QEMU does) and listens on the forwarded port.
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.port = probe.getsockname()[1]
        with open(self.dir / 'run.lock', 'w') as lock:
            self.qemu = subprocess.Popen(
                ['qemu-system-x86_64', str(VM / 'fixtures/fake-qemu.py'), '-nic', f'user,hostfwd=tcp:127.0.0.1:{self.port}-:22',
                 '-pidfile', str(self.dir / 'qemu.pid')], executable=shutil.which('python3'),
                pass_fds=(lock.fileno(),))
        deadline = time.monotonic() + 20
        while not (self.dir / 'qemu.pid').exists() and time.monotonic() < deadline:
            time.sleep(0.02)

    def tearDown(self):
        self.qemu.terminate()
        self.qemu.wait(10)
        self.work.cleanup()

    def install(self, fixture, offer):
        env = dict(os.environ, PATH=f'{self.bin}{os.pathsep}{os.environ["PATH"]}', STUB_LOG=str(self.log),
                   STUB_OFFER=offer, EMAKI_ISO_SSH_KEY=str(self.key), HOME=str(self.base / 'home'))
        result = subprocess.run(['bash', str(self.vm / 'iso-install.sh'), fixture, '--dir', str(self.dir),
                                 '--ssh-port', str(self.port)], env=env, text=True, capture_output=True, timeout=60)
        return result, self.log.read_text().splitlines()

    def run_dir(self):
        return Path((self.dir / 'last-install-run').read_text().strip())

    def test_refused_alongside_is_not_applicable(self):
        # A prepared Windows baseline: the old script went on to the install and exited 3.
        (self.dir / 'windows-before.json').write_text('{}\n')
        result, calls = self.install('alongside', 'refuse')
        self.assertEqual(result.returncode, 77, result.stdout + result.stderr)
        self.assertIn('NOT APPLICABLE', result.stdout)
        run = self.run_dir()
        self.assertTrue((run / 'NOT-APPLICABLE').is_file())
        self.assertEqual((run / 'exit-code').read_text().strip(), '77')
        self.assertIn('unsupported_mode', (run / 'offer.ndjson').read_text())
        self.assertFalse(any('--yes' in call for call in calls), 'nothing may be installed')
        self.assertFalse(any('windows-fixture' in call for call in calls))

    def test_offered_alongside_goes_on_to_the_real_install_path(self):
        result, calls = self.install('alongside', 'accept')
        self.assertNotIn(result.returncode, (0, 77))
        self.assertIn('prepare the Windows fixture and baseline first', result.stderr)
        self.assertFalse((self.run_dir() / 'NOT-APPLICABLE').exists())

    def test_erase_fixture_is_not_probed(self):
        result, calls = self.install('erase-btrfs', 'accept')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Command-line installation evidence [CLI]:', result.stdout)
        cli = [call for call in calls if 'emaki-install-cli' in call]
        self.assertEqual(len(cli), 1)
        self.assertIn('--yes', cli[0])
        lines = (self.run_dir() / 'install.ndjson').read_text().splitlines()
        self.assertEqual(json.loads(lines[-1])['type'], 'done')



class AlongsideScenarioSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('alongside_scenario', VM / 'iso-alongside-check.py')
        cls.scenario = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.scenario)

    def test_candidate_run_checks_both_fallback_variants(self):
        with mock.patch.object(self.scenario.sys, 'argv', ['check', 'btrfs', 'candidate']):
            with mock.patch.object(self.scenario, 'run_scenario', return_value=None) as run:
                self.assertEqual(self.scenario.main(), 0)
                self.assertEqual(run.call_args_list, [
                    mock.call('btrfs', 'candidate', no_fallback=False),
                    mock.call('btrfs', 'candidate-no-fallback', no_fallback=True)])

    def test_disabled_candidate_does_not_start_second_variant(self):
        with mock.patch.object(self.scenario.sys, 'argv', ['check', 'ext4', 'candidate']):
            with mock.patch.object(self.scenario, 'run_scenario', return_value=77) as run:
                self.assertEqual(self.scenario.main(), 77)
                run.assert_called_once_with('ext4', 'candidate', no_fallback=False)

    def test_failed_candidate_does_not_start_second_variant(self):
        with mock.patch.object(self.scenario.sys, 'argv', ['check', 'ext4', 'candidate']):
            with mock.patch.object(self.scenario, 'run_scenario', side_effect=RuntimeError('failed')) as run:
                with self.assertRaisesRegex(RuntimeError, 'failed'):
                    self.scenario.main()
                run.assert_called_once()

    def test_private_monitor_must_match_both_port_and_exact_disk(self):
        vm = Path('/tmp/alongside-owned')
        network = '  TCP[HOST_FORWARD] 12 127.0.0.1 2299 10.0.2.15 22 0 0\n'
        blocks = 'target: /tmp/alongside-owned/target.qcow2 (qcow2)\n'
        with mock.patch.object(self.scenario.monitor, 'command', side_effect=[network, blocks]) as command:
            self.scenario.check_owner(vm, 2299)
            self.assertEqual(command.call_args_list,
                             [mock.call(vm, 'info usernet'), mock.call(vm, 'info block')])
        for wrong_network, wrong_blocks in (
                (network.replace('2299', '22299'), blocks),
                (network.replace('127.0.0.1', '0.0.0.0'), blocks),
                (network.replace(' 22 ', ' 222 '), blocks),
                (network, blocks.replace('target.qcow2', 'target.qcow2.other')),
                (network, blocks.replace('target:', 'other:'))):
            with self.subTest(network=wrong_network, blocks=wrong_blocks):
                with mock.patch.object(self.scenario.monitor, 'command',
                                       side_effect=[wrong_network, wrong_blocks]):
                    with self.assertRaises(RuntimeError):
                        self.scenario.check_owner(vm, 2299)

    def test_current_qemu_named_block_output(self):
        vm = Path('/tmp/alongside-fixture/vm')
        network = '  TCP[HOST_FORWARD] 12 127.0.0.1 2299 10.0.2.15 22 0 0\n'
        blocks = 'target (#block551): ' + str(vm / 'target.qcow2') + ' (qcow2)\n'
        with mock.patch.object(self.scenario.monitor, 'command', side_effect=[network, blocks]):
            self.scenario.check_owner(vm, 2299)

    def test_missing_base_never_creates_a_disk(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(self.scenario.subprocess, 'run') as run:
                with self.assertRaisesRegex(RuntimeError, 'BASE_DISK'):
                    self.scenario.clone_disk(Path('/tmp/unused-target.qcow2'))
                run.assert_not_called()

    def test_clone_requires_reflink_without_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / 'base.qcow2'
            base.touch()
            target = Path(directory) / 'target.qcow2'
            with mock.patch.dict(os.environ, EMAKI_ALONGSIDE_BASE_DISK=str(base)):
                with mock.patch.object(self.scenario.subprocess, 'run') as run:
                    run.side_effect = subprocess.CalledProcessError(1, 'cp')
                    with self.assertRaises(subprocess.CalledProcessError):
                        self.scenario.clone_disk(target)
                    run.assert_called_once_with(
                        ['cp', '--reflink=always', '--', str(base.resolve()), str(target)], check=True)

    def test_only_empty_standalone_clones_are_accepted_and_small_clones_grow(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / 'base.qcow2'
            base.touch()
            target = Path(directory) / 'target.qcow2'
            for size, extra, zero, accepted in (
                    (40, {}, True, True), (96, {}, True, True),
                    (40, {}, False, False), (40, {}, None, False),
                    (40, {'backing-filename': '/tmp/other.qcow2'}, True, False),
                    (40, {'encrypted': True}, True, False),
                    (40, {'format': 'raw'}, True, False)):
                info = {'format': 'qcow2', 'virtual-size': size * 1024**3, **extra}
                replies = [mock.Mock(), mock.Mock(stdout=json.dumps(info)),
                           mock.Mock(stdout=json.dumps([{'zero': zero}])), mock.Mock()]
                with self.subTest(size=size, extra=extra, zero=zero):
                    with mock.patch.dict(os.environ, EMAKI_ALONGSIDE_BASE_DISK=str(base)):
                        with mock.patch.object(self.scenario.subprocess, 'run', side_effect=replies) as run:
                            if accepted:
                                self.scenario.clone_disk(target)
                            else:
                                with self.assertRaises(RuntimeError):
                                    self.scenario.clone_disk(target)
                            resize = [call for call in run.call_args_list if call.args[0][:2] == ['qemu-img', 'resize']]
                            self.assertEqual(bool(resize), accepted and size < 80)
                            if resize:
                                self.assertEqual(resize[0], mock.call(['qemu-img', 'resize', str(target), '96G'], check=True))

    def test_windows_target_requires_firmware_reboot_after_selection(self):
        marker = 'EMAKI_SYNTHETIC_WINDOWS_EFI_OK'
        started = 'BdsDxe: starting Boot00A2 "Windows Boot Manager" from HD(...)\r\n'
        self.assertTrue(self.scenario.firmware_windows_boot(started + marker, '00A2'))
        self.assertFalse(self.scenario.firmware_windows_boot(marker, '00A2'))
        self.assertFalse(self.scenario.firmware_windows_boot(marker + started, '00A2'))
        self.assertFalse(self.scenario.firmware_windows_boot(started + marker, '00A3'))
        self.assertFalse(self.scenario.firmware_windows_boot(
            started.replace('starting', 'loading') + marker, '00A2'))
        with self.assertRaises(ValueError):
            self.scenario.firmware_windows_boot(started + marker, '.*')

    def test_evidence_directories_are_confined(self):
        root = (Path.home() / 'VMs').resolve()
        self.assertFalse(self.scenario.allowed_vm_dir(root))
        self.assertTrue(self.scenario.allowed_vm_dir(root / 'alongside'))
        self.assertTrue(self.scenario.allowed_vm_dir(root / 'release-gate/run/alongside'))
        self.assertFalse(self.scenario.allowed_vm_dir(CACHE.resolve()))
        self.assertFalse(self.scenario.allowed_vm_dir(CACHE.resolve() / 'alongside'))
        self.assertFalse(self.scenario.allowed_vm_dir(Path('/tmp/alongside')))
        self.assertFalse(self.scenario.allowed_vm_dir(CACHE.parent / '.cache-elsewhere'))

    def test_luks_evidence_follows_active_mapping_with_recovery_partition(self):
        stub = r'''cryptsetup() {
    case "$1" in
        status) test "$2" = emaki-root; printf '  device:  /dev/vda5\n' ;;
        luksDump) test "$2" = /dev/vda5; printf 'verified actual root\n' ;;
        *) return 1 ;;
    esac
}
'''
        result = subprocess.run(['bash', '-euc', stub + self.scenario.ROOT_LUKS_CHECK],
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'verified actual root\n')
        script = (VM / 'iso-alongside-check.py').read_text()
        self.assertNotIn('cryptsetup luksDump /dev/vda', script)

    def test_busy_port_refuses_before_start(self):
        with mock.patch.object(self.scenario.socket, 'socket') as socket:
            socket.return_value.__enter__.return_value.bind.side_effect = OSError('busy')
            with self.assertRaises(OSError):
                self.scenario.check_free_port(2299)

    def test_unsupported_mode_retains_skip_status(self):
        reply = {'type': 'plan_ack', 'errors': [{'code': 'unsupported_mode'}]}
        self.assertTrue(self.scenario.mode_refused(json.dumps(reply).encode()))
        self.assertEqual(self.scenario.NOT_APPLICABLE, 77)
        self.assertFalse(self.scenario.mode_refused(b'{"type":"plan_ack","errors":[]}'))

if __name__ == '__main__':
    unittest.main()
