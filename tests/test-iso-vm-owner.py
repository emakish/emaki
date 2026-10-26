#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ISO harness talks only to the VM its directory started (GOTCHAS 2026-10-10).

run-iso.sh, night/vm.sh, iso-ssh.sh, iso-wait-ssh.sh, iso-install.sh and iso-stop.sh run for real
against a fake QEMU (tests/vm/fixtures/fake-qemu.py) that binds the forwarded port, writes its
pidfile and answers the monitor. No guest and no host device: run-iso.sh's firmware, KVM and
render-node checks are pointed at fixture files, and one test proves the check itself refuses.
The fixtures listen on loopback ports; where socket creation is denied the run says BLOCKED
(exit 0 only with EMAKI_TEST_SANDBOX=1), because a skip would prove nothing.
"""
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
VM = ROOT / 'tests/vm'
requires_night_launcher = unittest.skipUnless(
    (VM / 'night/vm.sh').is_file(),
    'private night launcher is absent (excluded from public exports)')
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)
# Records the command; on call STUB_KILL_AT it first ends the QEMU named by STUB_KILL_PIDFILE (the
# VM going away between two contacts) and leaves STUB_LOG.killed behind. STUB_KILL_GATE holds
# that call until the test writes "retry" to stdin, after the replacement listener is ready.
# Exits STUB_SSH_EXIT.
STUB_SSH = r'''#!/usr/bin/env bash
printf "%s\n" "${!#}" >>"$STUB_LOG"
if [[ -n ${STUB_KILL_AT:-} && $(wc -l <"$STUB_LOG") == "$STUB_KILL_AT" ]]; then
    pid=$(<"$STUB_KILL_PIDFILE")
    kill "$pid"
    for _ in $(seq 200); do
        state=$(sed 's/.*) //' "/proc/$pid/stat" 2>/dev/null) || break
        [[ ${state%% *} != Z ]] || break
        sleep 0.05
    done
    : >"$STUB_LOG.killed"
    if [[ -n ${STUB_KILL_GATE:-} ]]; then
        IFS= read -r -t 120 release && [[ $release == retry ]] || exit 124
    fi
fi
exit "${STUB_SSH_EXIT:-0}"
'''
STUB_QEMU_IMG = '#!/usr/bin/env bash\nsleep "${STUB_IMG_DELAY:-0}"\n: >"$4"\n'  # qemu-img create -f qcow2 FILE SIZE
# python3 on PATH; holds a launcher in its port check (after the lock, before any PID file): two
# seconds with STUB_PORT_CHECK_READY, until GATE.go exists with STUB_PORT_CHECK_GATE=GATE. With
# STUB_PAUSE_SCAN, iso-owner.py find runs through paused-scan.py.
STUB_PYTHON = f'''#!/usr/bin/env bash
if [[ $1 == */iso-owner.py && $2 == port-free && -n ${{STUB_PORT_CHECK_READY:-}} ]]; then
    : >"$STUB_PORT_CHECK_READY"
    sleep 2
fi
if [[ $1 == */iso-owner.py && $2 == port-free && -n ${{STUB_PORT_CHECK_GATE:-}} ]]; then
    : >"$STUB_PORT_CHECK_GATE.ready"
    while [[ ! -e $STUB_PORT_CHECK_GATE.go ]]; do sleep 0.01; done
fi
if [[ $1 == */iso-owner.py && $2 == find && -n ${{STUB_PAUSE_SCAN:-}} ]]; then
    exec {sys.executable} "$(dirname -- "$0")/paused-scan.py" "$@"
fi
exec {sys.executable} "$@"
'''
# Runs iso-owner.py with its first /proc listing held between the enumeration and the descriptor
# reads: STUB_PAUSE_SCAN.ready appears, and the scan goes on once STUB_PAUSE_SCAN.go exists.
PAUSED_SCAN = '''import os
from pathlib import Path
import runpy
import sys
import time

gate = os.environ['STUB_PAUSE_SCAN']
listdir = os.listdir


def paused(path):
    entries = listdir(path)
    if path == '/proc' and not Path(f'{gate}.go').exists():
        Path(f'{gate}.ready').touch()
        while not Path(f'{gate}.go').exists():
            time.sleep(0.01)
    return entries


os.listdir = paused
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
'''


def sockets_allowed():
    try:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
    except PermissionError:
        return False
    return True


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def running(pid):
    try:
        stat = Path(f'/proc/{pid}/stat').read_text()
    except OSError:
        return False
    return stat.rsplit(')', 1)[-1].split()[0] != 'Z'


def wait_for(condition, seconds=20):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.02)
    return condition()


class VmOwnerTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='iso-owner-', dir=CACHE)
        self.base = Path(self.work.name)
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        for name, text in (('ssh', STUB_SSH), ('qemu-img', STUB_QEMU_IMG), ('python3', STUB_PYTHON),
                           ('paused-scan.py', PAUSED_SCAN),
                           ('qemu-system-x86_64', (VM / 'fixtures/fake-qemu.py').read_text())):
            (self.bin / name).write_text(text)
            (self.bin / name).chmod(0o755)
        # Stand-ins for OVMF, /dev/kvm and the render node; the fake QEMU opens none of them.
        self.host = self.base / 'host'
        self.host.mkdir()
        for name in ('OVMF_CODE.4m.fd', 'OVMF_VARS.4m.fd', 'kvm', 'renderD128'):
            (self.host / name).touch()
        self.key = self.base / 'key'
        self.key.write_text('not a key\n')
        self.ssh_log = self.base / 'ssh.log'
        self.ssh_log.touch()
        self.qemu_log = self.base / 'qemu.log'
        self.qemu_log.touch()
        self.port = free_port()
        self.launched = []

    def tearDown(self):
        for process in self.launched:
            if process.poll() is None:
                process.terminate()
                process.wait(10)
        for directory in self.base.iterdir():
            subprocess.run(['python3', str(VM / 'socket_runtime.py'), 'cleanup', str(directory)],
                           capture_output=True)
        self.work.cleanup()

    def env(self, **extra):
        return dict(os.environ, PATH=f'{self.bin}{os.pathsep}{os.environ["PATH"]}', STUB_LOG=str(self.ssh_log),
                    FAKE_QEMU_LOG=str(self.qemu_log), EMAKI_ISO_SSH_KEY=str(self.key),
                    EMAKI_ISO_OVMF_DIR=str(self.host), EMAKI_ISO_KVM_DEVICE=str(self.host / 'kvm'),
                    EMAKI_ISO_RENDER_NODE=str(self.host / 'renderD128'), HOME=str(self.base / 'home')) | extra

    def vm_dir(self, name='vm', disk=True):
        directory = self.base / name
        directory.mkdir(exist_ok=True)
        if disk:
            (directory / 'target.qcow2').touch()
        return directory

    def launch(self, directory, port=None, media=('--no-cd',), **extra):
        log = open(directory / 'qemu-run.log', 'w')
        process = subprocess.Popen(['bash', str(VM / 'run-iso.sh'), *media, '--dir', str(directory),
                                    '--ssh-port', str(port or self.port)],
                                   env=self.env(**extra), stdout=log, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, start_new_session=True)
        log.close()
        self.launched.append(process)
        return process

    def script(self, name, *args, timeout=120, **extra):
        return subprocess.run(['bash', str(VM / name), *args], env=self.env(**extra), text=True,
                              capture_output=True, timeout=timeout)

    def owner(self, *args):
        return subprocess.run(['python3', str(VM / 'iso-owner.py'), *args], text=True, capture_output=True,
                              timeout=60)

    def nothing_runs(self, directory):
        """True when no process holds the directory's lock: no launcher, no QEMU."""
        return self.owner('find', '--dir', str(directory)).returncode == 1

    def wait_owned(self, directory):
        result = self.owner('prove', '--dir', str(directory), '--port', str(self.port), '--wait', '20')
        log = next((directory / name for name in ('qemu-run.log', 'qemu.log') if (directory / name).exists()), None)
        self.assertEqual(result.returncode, 0, result.stderr + (log.read_text() if log else ''))

    def ssh_calls(self):
        return self.ssh_log.read_text().splitlines()

    def test_preflight_refuses_without_host_devices(self):
        directory = self.vm_dir()
        process = self.launch(directory, EMAKI_ISO_KVM_DEVICE=str(self.host / 'missing'))
        self.assertNotEqual(process.wait(30), 0)
        self.assertIn('OVMF or KVM unavailable', (directory / 'qemu-run.log').read_text())
        process = self.launch(directory, EMAKI_ISO_RENDER_NODE=str(self.host / 'missing'))
        self.assertNotEqual(process.wait(30), 0)
        self.assertIn('unavailable', (directory / 'qemu-run.log').read_text())
        self.assertEqual(self.qemu_log.read_text(), '', 'QEMU must not be started')

    def test_refuses_to_start_on_a_taken_port(self):
        with socket.socket() as holder:
            holder.bind(('127.0.0.1', self.port))
            holder.listen()
            directory = self.vm_dir()
            process = self.launch(directory)
            self.assertNotEqual(process.wait(30), 0)
        self.assertIn(f'127.0.0.1:{self.port} is already in use', (directory / 'qemu-run.log').read_text())
        self.assertEqual(self.qemu_log.read_text(), '', 'QEMU must not be started')

    def test_launched_vm_proves_ownership_and_stops(self):
        directory = self.vm_dir()
        monitor_log = self.base / 'monitor.log'
        process = self.launch(directory, FAKE_QEMU_MONITOR_LOG=str(monitor_log))
        self.wait_owned(directory)
        result = self.owner('prove', '--dir', str(directory), '--port', str(free_port()))
        self.assertEqual(result.returncode, 1)
        self.assertIn('does not forward', result.stderr)
        stop = self.script('iso-stop.sh', '--dir', str(directory))
        self.assertEqual(stop.returncode, 0, stop.stderr)
        self.assertIn('OK: QEMU stopped', stop.stdout)
        self.assertEqual(process.wait(10), 0)
        self.assertNotIn('SIGTERM', (directory / 'stop-vm.log').read_text())
        self.assertEqual(monitor_log.read_text().splitlines(), ['system_powerdown'])
        self.assertTrue(self.nothing_runs(directory))

    def test_old_vm_on_the_port_is_never_reached(self):
        # The incident: a renamed directory's live QEMU keeps the port; the new directory's run
        # fails, and the harness goes on. Nothing may reach the old guest.
        old = self.vm_dir('base')
        old_process = self.launch(old)
        self.wait_owned(old)
        renamed = self.base / 'base-failed'
        old.rename(renamed)
        new = self.vm_dir('base')
        new_process = self.launch(new)
        self.assertNotEqual(new_process.wait(30), 0)
        self.assertIn('already in use', (new / 'qemu-run.log').read_text())
        wait = self.script('iso-wait-ssh.sh', '--dir', str(new), '--ssh-port', str(self.port))
        self.assertNotEqual(wait.returncode, 0)
        self.assertIn('held by PID', wait.stderr)
        self.assertIn('refusing to talk to a VM', wait.stderr)
        ssh = self.script('iso-ssh.sh', '--dir', str(new), '--ssh-port', str(self.port), '--', 'true')
        self.assertNotEqual(ssh.returncode, 0)
        self.assertIn('refusing to talk to a VM', ssh.stderr)
        install = self.script('iso-install.sh', 'erase-btrfs', '--dir', str(new), '--ssh-port', str(self.port))
        self.assertNotEqual(install.returncode, 0)
        self.assertIn('refusing to talk to a VM', install.stderr)
        self.assertEqual(self.ssh_calls(), [], 'no SSH command may reach the old guest')
        self.assertFalse((new / 'runs').exists())
        # The new name's stop leaves the old VM alone; the renamed directory's stop ends it.
        stop = self.script('iso-stop.sh', '--dir', str(new))
        self.assertEqual(stop.returncode, 0, stop.stderr)
        self.assertIn('no VM of this directory', stop.stdout)
        self.assertIsNone(old_process.poll())
        stop = self.script('iso-stop.sh', '--dir', str(renamed))
        self.assertEqual(stop.returncode, 0, stop.stderr)
        self.assertEqual(old_process.wait(10), 0)

    def test_stop_right_after_launch_before_the_pidfile(self):
        directory = self.vm_dir()
        process = self.launch(directory, FAKE_QEMU_DELAY='30')
        self.assertTrue(wait_for(lambda: self.qemu_log.read_text()))
        self.assertFalse((directory / 'qemu.pid').exists())
        stop = self.script('iso-stop.sh', '--dir', str(directory), '--force')
        self.assertEqual(stop.returncode, 0, stop.stderr)
        self.assertIn('OK: QEMU stopped', stop.stdout)
        self.assertIn('SIGTERM', (directory / 'stop-vm.log').read_text())
        self.assertIsNotNone(process.poll())

    def test_stop_during_the_port_check_before_any_pid_file(self):
        # RD1 must 1: run-iso.sh holds its lock but has written nothing else yet. The old stop
        # reported "no VM" here and QEMU started a moment later.
        directory = self.vm_dir()
        ready = self.base / 'port-check'
        process = self.launch(directory, STUB_PORT_CHECK_READY=str(ready))
        self.assertTrue(wait_for(ready.exists))
        self.assertEqual(sorted(path.name for path in directory.iterdir()), ['qemu-run.log', 'run.lock', 'target.qcow2'])
        stop = self.script('iso-stop.sh', '--dir', str(directory))
        self.assertEqual(stop.returncode, 0, stop.stderr)
        self.assertIn('OK: QEMU stopped', stop.stdout, (directory / 'qemu-run.log').read_text())
        self.assertEqual(len(self.qemu_log.read_text().splitlines()), 1, 'the stop waited for QEMU and ended it')
        self.assertIsNotNone(process.poll(), 'the VM survived its stop')
        time.sleep(1)
        self.assertEqual(len(self.qemu_log.read_text().splitlines()), 1, 'no QEMU may start after the stop')
        self.assertTrue(self.nothing_runs(directory))

    def test_stop_while_run_iso_is_still_preparing(self):
        # A live boot with a new disk: the slow qemu-img keeps run-iso.sh before its exec.
        directory = self.vm_dir(disk=False)
        iso = self.base / 'live.iso'
        iso.touch()
        process = self.launch(directory, media=('--iso', str(iso)), STUB_IMG_DELAY='2')
        self.assertTrue(wait_for(lambda: not self.nothing_runs(directory)))
        self.assertEqual(self.qemu_log.read_text(), '')
        self.assertFalse((directory / 'qemu.pid').exists())
        stop = self.script('iso-stop.sh', '--dir', str(directory))
        self.assertEqual(stop.returncode, 0, stop.stderr)
        self.assertIn('OK: QEMU stopped', stop.stdout, (directory / 'qemu-run.log').read_text())
        self.assertNotEqual(self.qemu_log.read_text(), '', 'the stop waited for the exec')
        self.assertIsNotNone(process.wait(10))
        self.assertTrue(self.nothing_runs(directory))

    def night_vm(self, directory, timeout=60, **extra):
        return subprocess.run(['bash', str(VM / 'night/vm.sh'), '--dir', str(directory), '--port', str(self.port),
                               '--bios'], env=self.env(**extra), text=True, capture_output=True, timeout=timeout)

    @requires_night_launcher
    def test_night_vm_follows_the_same_rules(self):
        # tests/vm/night/vm.sh starts its own QEMU (setsid -f); the ISO scripts must accept it.
        directory = self.vm_dir('night')
        with socket.socket() as holder:
            holder.bind(('127.0.0.1', self.port))
            holder.listen()
            refused = self.night_vm(directory)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('already in use', refused.stderr)
        self.assertEqual(self.qemu_log.read_text(), '')
        started = self.night_vm(directory)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        pid = int((directory / 'qemu.pid').read_text())
        self.assertIn(f'OK: QEMU {pid} in', started.stdout)
        try:
            self.wait_owned(directory)
            again = self.night_vm(directory)
            self.assertNotEqual(again.returncode, 0)
            self.assertIn('is running', again.stderr)
            ssh = self.script('iso-ssh.sh', '--dir', str(directory), '--', 'true')
            self.assertEqual(ssh.returncode, 0, ssh.stderr)
            self.assertEqual(self.ssh_calls(), ['true'])
            stop = self.script('iso-stop.sh', '--dir', str(directory))
            self.assertEqual(stop.returncode, 0, stop.stderr)
            self.assertIn('OK: QEMU stopped', stop.stdout)
            self.assertFalse(running(pid))
        finally:
            if running(pid):
                os.kill(pid, 15)

    @requires_night_launcher
    def test_failed_night_launch_leaves_no_qemu(self):
        # RD1 must 1: QEMU writes no pidfile within vm.sh's 10 seconds. vm.sh used to report the
        # failure and leave that QEMU running with the lock, so a stop then said "no VM".
        directory = self.vm_dir('night')
        failed = self.night_vm(directory, timeout=90, FAKE_QEMU_DELAY='60')
        self.assertNotEqual(failed.returncode, 0)
        found = re.search(r'BAD: QEMU (\d+) did not write its pidfile', failed.stderr)
        self.assertIsNotNone(found, failed.stderr)
        pid = int(found.group(1))
        try:
            self.assertFalse(running(pid), 'the failed launch left its QEMU running')
            self.assertTrue(self.nothing_runs(directory))
            stop = self.script('iso-stop.sh', '--dir', str(directory))
            self.assertEqual(stop.returncode, 0, stop.stderr)
            self.assertIn('no VM of this directory', stop.stdout)
        finally:
            if running(pid):
                os.kill(pid, 15)

    @requires_night_launcher
    def test_stop_whose_scan_misses_the_handoff_to_qemu(self):
        # RD1b must 1: the stop lists /proc while vm.sh holds the lock; vm.sh starts QEMU, which
        # inherits the lock, and exits before the stop reads the descriptors. No PID of that
        # listing holds run.lock any more, yet it stayed held throughout: the old stop said
        # "no VM" and left QEMU running.
        directory = self.vm_dir('night')
        launch_gate, scan_gate = self.base / 'launch', self.base / 'scan'
        launch = subprocess.Popen(['bash', str(VM / 'night/vm.sh'), '--dir', str(directory), '--port', str(self.port),
                                   '--bios'], env=self.env(STUB_PORT_CHECK_GATE=str(launch_gate)), text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        self.launched.append(launch)
        self.assertTrue(wait_for(Path(f'{launch_gate}.ready').exists))
        stop = subprocess.Popen(['bash', str(VM / 'iso-stop.sh'), '--dir', str(directory)],
                                env=self.env(STUB_PAUSE_SCAN=str(scan_gate)), text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.launched.append(stop)
        self.assertTrue(wait_for(Path(f'{scan_gate}.ready').exists))
        Path(f'{launch_gate}.go').touch()
        out, err = launch.communicate(timeout=60)
        self.assertEqual(launch.returncode, 0, out + err)
        pid = int((directory / 'qemu.pid').read_text())
        try:
            self.assertTrue(running(pid))
            Path(f'{scan_gate}.go').touch()
            out, err = stop.communicate(timeout=120)
            self.assertEqual(stop.returncode, 0, out + err)
            self.assertIn('OK: QEMU stopped', out)
            self.assertFalse(running(pid), 'the stop reported success with QEMU running')
            self.assertTrue(self.nothing_runs(directory))
        finally:
            if running(pid):
                os.kill(pid, 15)

    def test_ssh_without_a_vm_is_refused(self):
        # RD1 must 2: iso-ssh.sh (night/install-cli.sh runs `emaki-install-cli --yes` through it)
        # used to run SSH with no proof at all.
        directory = self.vm_dir()
        ssh = self.script('iso-ssh.sh', '--dir', str(directory), '--ssh-port', str(self.port), '--',
                          'emaki-install-cli --plan /tmp/emaki-plan.json --yes')
        self.assertNotEqual(ssh.returncode, 0)
        self.assertIn('refusing to talk to a VM', ssh.stderr)
        self.assertEqual(self.ssh_calls(), [])

    def test_wait_ssh_retry_never_reaches_the_vm_that_took_the_port(self):
        # RD1 must 2: the first attempt fails, the VM goes away and another process takes the
        # port before the retry. Hold that attempt until the foreign listener is ready instead
        # of racing iso-wait-ssh.sh's three-second sleep. The retry must refuse before connecting.
        directory = self.vm_dir()
        qemu = self.launch(directory)
        self.wait_owned(directory)
        # The release gate exports a default SSH port; the fixture's explicit port must win.
        wait = subprocess.Popen(['bash', str(VM / 'iso-wait-ssh.sh'), '--dir', str(directory),
                                 '--ssh-port', str(self.port)],
                                env=self.env(STUB_SSH_EXIT='255', STUB_KILL_AT='1',
                                             STUB_KILL_GATE='1',
                                             EMAKI_ISO_SSH_PORT=str(self.port % 65535 + 1),
                                             STUB_KILL_PIDFILE=str(directory / 'qemu.pid')),
                                text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            killed = Path(f'{self.ssh_log}.killed')
            # Startup and ownership scans can be slow under load; this is a watchdog, not the
            # window in which the port must change hands. Report an early exit immediately.
            wait_for(lambda: killed.exists() or wait.poll() is not None, seconds=120)
            if not killed.exists():
                if wait.poll() is None:
                    wait.kill()
                stdout, stderr = wait.communicate(timeout=30)
                self.fail(f'first SSH attempt did not stop QEMU: {stdout}{stderr}')
            self.assertEqual(qemu.wait(timeout=120), 0, 'the original QEMU must exit before port takeover')
            with socket.socket() as foreign:
                foreign.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                foreign.bind(('127.0.0.1', self.port))
                foreign.listen()
                _, stderr = wait.communicate(input='retry\n', timeout=120)
        finally:
            if wait.poll() is None:
                wait.kill()
            wait.communicate(timeout=30)
        self.assertNotEqual(wait.returncode, 0)
        self.assertIn('held by PID', stderr)
        self.assertIn('refusing to talk to a VM', stderr)
        self.assertEqual(self.ssh_calls(), ['true'], 'the retry reached the foreign VM')

    def test_install_never_reaches_a_guest_after_its_vm_went_away(self):
        # RD1 must 2: iso-install.sh's SSH steps between its first and last proof were unchecked.
        # The VM ends during the guest check (call 2); call 3 must not happen.
        directory = self.vm_dir()
        self.launch(directory)
        self.wait_owned(directory)
        install = self.script('iso-install.sh', 'erase-btrfs', '--dir', str(directory), '--ssh-port', str(self.port),
                              STUB_KILL_AT='2', STUB_KILL_PIDFILE=str(directory / 'qemu.pid'))
        self.assertNotEqual(install.returncode, 0)
        self.assertIn('refusing to talk to a VM', install.stderr)
        calls = self.ssh_calls()
        self.assertEqual(len(calls), 2, calls)
        self.assertFalse(any('--yes' in call for call in calls))

    def test_install_proves_the_vm_before_any_ssh(self):
        directory = self.vm_dir()
        self.launch(directory)
        self.wait_owned(directory)
        install = self.script('iso-install.sh', 'erase-btrfs', '--dir', str(directory), '--ssh-port', str(self.port))
        # What matters is that the ownership proof came first and passed for every step.
        self.assertIn(f'owns 127.0.0.1:{self.port}', install.stdout)
        self.assertNotIn('refusing', install.stderr)
        self.assertTrue(any('--yes' in call for call in self.ssh_calls()))
        self.assertEqual(self.script('iso-stop.sh', '--dir', str(directory)).returncode, 0)


if __name__ == '__main__':
    if not sockets_allowed():
        print('BLOCKED: socket creation is denied here; the fake-QEMU ownership tests need loopback sockets')
        sys.exit(0 if os.environ.get('EMAKI_TEST_SANDBOX') == '1' else 1)
    unittest.main(verbosity=2)
