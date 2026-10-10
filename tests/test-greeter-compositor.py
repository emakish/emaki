#!/usr/bin/python3
"""emaki-greeter-compositor failure path and emaki-text-session, without a VM.

The wrapper runs on a pty that stands for the login VT greetd hands it, with stubs for
dbus-run-session, the compositor, systemd-cat and agreety. The stubs record their argv, where
their standard fds point and the signals they receive. For the keys typed at the login prompt
the wrapper runs under a stand-in for greetd's session worker, which owns the pty as its
controlling terminal.
"""
import os
import pty
import shlex
import signal
import subprocess
import sys
import tempfile
import termios
import threading
import time
import unittest
from pathlib import Path
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / 'scripts/emaki-greeter-compositor'
TEXT_SESSION = ROOT / 'scripts/emaki-text-session'
MESSAGE = ROOT / 'greetd/graphics-failed.txt'
CLEAR = '\x1b[H\x1b[2J'
CAUSE = 'Cause: no hardware 3D renderer was found.'
LIVE = 'User: live, no password.'
# Arch's /etc/issue is `\S{PRETTY_NAME} \r (\l)`. agreety 0.10.3 prints it on its standard output
# expanding \S but not agetty's {VARIABLE} (VM pass 2, P2-2b); agetty --show-issue prints it as
# the getty on tty2 does.
AGREETY_ISSUE = 'Emaki{PRETTY_NAME} 7.2.8-arch1-2 (tty1)\n\n'
BANNER = 'Emaki 7.2.8-arch1-2 (tty1)\n\n'
AGETTY_STUB = '''#!/bin/bash
/usr/bin/printf '%s|%s|%s\\n' "$*" "$(/usr/bin/readlink /proc/$$/fd/0)" \\
    "$(/usr/bin/readlink /proc/$$/fd/1)" >> "$AGETTY_LOG"
/usr/bin/printf 'Emaki 7.2.8-arch1-2 (tty1)\\n\\n'
'''

COMPOSITOR_STUB = '''#!/bin/bash
# Records how it was started, then sleeps and exits as the test asks. The trap comes first: the
# tests send SIGTERM as soon as $RESULT exists.
trap '/usr/bin/printf TERM >> "$RESULT"; exit 143' TERM
/usr/bin/printf '%s\\n' "$*" "$(/usr/bin/readlink /proc/$$/fd/0)" \\
    "$(/usr/bin/grep SigIgn /proc/self/status)" > "$RESULT"
/usr/bin/sleep "${STUB_SLEEP:-0}" &
wait $!
# Moves the fake boot clock (EMAKI_UPTIME_FILE) to STUB_UPTIME seconds before exiting.
if [[ -n $STUB_UPTIME ]]; then
    /usr/bin/printf '%s 1.00\\n' "$STUB_UPTIME" > "$EMAKI_UPTIME_FILE"
fi
exit "${STUB_EXIT:-0}"
'''
AGREETY_STUB = '''#!/bin/bash
# Records argv, fds 0/1/2 and the VT's settings (stty -g), then exits with the next code from
# AGREETY_CODES; text after the code goes to stderr first, as agreety's "error: ..." does. Like
# agreety 0.10.3 it starts by printing Arch's issue on its standard output.
/usr/bin/printf '%s|%s|%s|%s|%s\\n' "$*" "$(/usr/bin/readlink /proc/$$/fd/0)" \\
    "$(/usr/bin/readlink /proc/$$/fd/1)" "$(/usr/bin/readlink /proc/$$/fd/2)" \\
    "$(/usr/bin/stty -g)" >> "$AGREETY_LOG"
/usr/bin/printf 'Emaki{PRETTY_NAME} 7.2.8-arch1-2 (tty1)\\n\\n'
# Its process group, the VT's foreground group and the signals it ignores.
read -r -a stat < /proc/$$/stat
while read -r key value; do [[ $key == SigIgn: ]] && sigign=$value; done < /proc/$$/status
/usr/bin/printf 'group %s %s %s %s\\n' "$$" "${stat[4]}" "${stat[7]}" "$sigign" >> "$AGREETY_LOG"
line=$(/usr/bin/head -n1 "$AGREETY_CODES")
/usr/bin/sed -i 1d "$AGREETY_CODES"
code=${line%% *}
if [[ $line == *' '* ]]; then
    /usr/bin/printf '%s\\n' "${line#* }" >&2
fi
if [[ $code == sleep || $code == read ]]; then
    trap '/usr/bin/printf TERM >> "$AGREETY_LOG"; exit 143' TERM
    /usr/bin/printf 'waiting\\n' >> "$AGREETY_LOG"
    # "read": the login name as agreety reads it from the VT, one line.
    if [[ $code == read ]]; then
        IFS= read -r name
        /usr/bin/printf 'name %q\\n' "$name" >> "$AGREETY_LOG"
    fi
    /usr/bin/sleep 30 &
    wait $!
fi
exit "${code:-1}"
'''
# greetd 0.10.3's session worker (greetd/src/session/worker.rs): a fresh process with default
# signal actions calls setsid, takes the VT as its controlling terminal, forks the greeter with
# PDEATHSIG=SIGTERM through /bin/sh -c "exec CMD" and waits for it. The greeter therefore shares
# the VT's foreground process group with the worker. greetd stops the greeter with SIGTERM to
# the child, and exits ("greeter exited without creating a session") when the worker dies.
# This stand-in writes the greeter's pid to argv[2] and exits with its status.
WORKER = '''
import ctypes, fcntl, os, shlex, signal, sys, termios
signal.signal(signal.SIGINT, signal.SIG_DFL)
fcntl.ioctl(0, termios.TIOCSCTTY, 0)
child = os.fork()
if child == 0:
    ctypes.CDLL(None).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    os.execv('/bin/sh', ['/bin/sh', '-c', 'exec ' + shlex.quote(sys.argv[1])])
with open(sys.argv[2], 'w') as pid:
    pid.write(str(child))
_, status = os.waitpid(child, 0)
sys.exit(os.waitstatus_to_exitcode(status) & 255)
'''
# A python3 on PATH that cannot run the wrapper's foreground step. 'failing' stands for a broken
# interpreter and records its arguments; 'no termios' is the real interpreter with the termios
# module missing (the wrapper calls `python3 -I -c CODE [ARGS...]`).
BROKEN_PYTHON = {
    'failing': '#!/bin/bash\n/usr/bin/printf "%s\\n" "$*" >> "$PYTHON_LOG"\necho "python3: broken" >&2\nexit 1\n',
    'no termios': '#!/bin/bash\nexec /usr/bin/python3 -I -c \'import sys; sys.modules["termios"] = None; '
                  'code = sys.argv.pop(1); exec(compile(code, "<string>", "exec"))\' "$3" "${@:4}"\n',
}


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.02)
    return predicate()


class Wrapper(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix='gc-'))
        self.addCleanup(subprocess.run, ['rm', '-rf', str(self.base)])
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        self.stub('dbus-run-session', '#!/bin/bash\nexec "$@"\n')
        self.stub('niri-emaki', COMPOSITOR_STUB)
        self.stub('systemd-cat', '#!/bin/bash\n/usr/bin/cat >> "$JOURNAL"\n')
        self.stub('agreety', AGREETY_STUB)
        self.stub('agetty', AGETTY_STUB)
        self.agetty_log = self.base / 'agetty'
        for name in ('sleep', 'cat', 'stty', 'python3', 'readlink'):
            (self.bin / name).symlink_to('/usr/bin/' + name)
        self.journal = self.base / 'journal'
        self.result = self.base / 'result'
        self.agreety_log = self.base / 'agreety'
        self.agreety_codes = self.base / 'codes'
        self.live_marker = self.base / 'archiso'

    def stub(self, name, text):
        path = self.bin / name
        path.write_text(text)
        path.chmod(0o700)

    def start(self, codes=(), live=False, worker=False, **env):
        """Starts the wrapper on a new pty; with worker=True under the greetd worker stand-in."""
        self.agreety_codes.write_text(''.join(f'{code}\n' for code in codes))
        if live:
            self.live_marker.touch()
        master, slave = pty.openpty()
        self.slave_path = os.ttyname(slave)
        self.output = bytearray()

        def pump():
            while True:
                try:
                    data = os.read(master, 4096)
                except OSError:
                    return
                if not data:
                    return
                self.output += data

        self.pump = threading.Thread(target=pump, daemon=True)
        self.pump.start()
        command = [str(WRAPPER)]
        if worker:
            command = [sys.executable, '-c', WORKER, str(WRAPPER), str(self.base / 'pid')]
        process = subprocess.Popen(
            command, stdin=slave, stdout=slave, stderr=slave, start_new_session=worker,
            env=dict(PATH=str(self.bin), JOURNAL=str(self.journal), RESULT=str(self.result),
                     AGREETY_LOG=str(self.agreety_log), AGETTY_LOG=str(self.agetty_log),
                     AGREETY_CODES=str(self.agreety_codes),
                     EMAKI_GREETD_DIR=str(ROOT / 'greetd'), EMAKI_LIVE_MARKER=str(self.live_marker),
                     **env))
        os.close(slave)
        self.master = master
        self.addCleanup(self.finish, process)
        return process

    def finish(self, process):
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(self.master)

    def wrapper_pid(self):
        """The wrapper's pid under the worker stand-in."""
        path = self.base / 'pid'
        self.assertTrue(wait_for(lambda: path.exists() and path.read_text()))
        return int(path.read_text())

    def signal_keys_on(self, settings=None):
        """Whether the VT turns Ctrl+C, Ctrl+\\ and Ctrl+Z into signals (ISIG), now or in a
        recorded `stty -g`."""
        if settings is None:
            lflag = termios.tcgetattr(self.master)[3]
        else:
            lflag = int(settings.split(':')[3], 16)
        return bool(lflag & termios.ISIG)

    def screen(self, process):
        """Everything the wrapper wrote to the VT, after it exited."""
        self.assertIsNotNone(process.returncode)
        wait_for(lambda not_alive=self.pump.is_alive: not not_alive(), 2)
        return self.output.decode().replace('\r\n', '\n')

    def agreety_runs(self):
        if not self.agreety_log.exists():
            return []
        return [line.split('|') for line in self.agreety_log.read_text().splitlines() if '|' in line]

    def expected_message(self, cause, live):
        lines = MESSAGE.read_text().splitlines()
        body = [lines[0]] + ([CAUSE] if cause else []) + lines[1:-1] + ([LIVE] if live else []) + [lines[-1]]
        return CLEAR + '\n'.join(body) + '\n'

    def test_message_file_fits_a_console(self):
        lines = MESSAGE.read_text().splitlines()
        self.assertEqual(lines[0], 'Emaki could not start its graphical desktop.')
        self.assertLessEqual(len(lines), 20)
        self.assertTrue(all(len(line) <= 78 for line in lines), lines)
        text = MESSAGE.read_text()
        self.assertIn('journalctl -b -t emaki-greeter-compositor -t niri-emaki-session', text)
        self.assertNotIn('NVIDIA', text)
        # The other way in (getty on tty2) is named on one line; the last line stays the
        # instruction for the prompt right below it.
        self.assertEqual(len([line for line in lines[1:-1] if 'Ctrl+Alt+F2' in line]), 1)
        self.assertTrue(lines[-1].startswith('Log in below'), lines[-1])

    def test_status_3_shows_cause_and_runs_agreety_on_the_vt(self):
        # Three failed agreety runs, then a login: four runs, wrapper exits 0.
        process = self.start(codes=(1, 1, 1, 0), live=True, STUB_EXIT='3')
        self.assertEqual(process.wait(timeout=10), 0)
        runs = self.agreety_runs()
        self.assertEqual(len(runs), 4, runs)
        for argv, stdin, stdout, stderr, settings in runs:
            self.assertEqual(argv, '--max-failures 1000000 --cmd emaki-text-session')
            # agreety's standard output carries only its own copy of the issue.
            self.assertEqual((stdin, stdout, stderr), (self.slave_path, '/dev/null', self.slave_path))
            # The VT's signal keys are off while agreety runs, and on again after the login.
            self.assertFalse(self.signal_keys_on(settings), settings)
        self.assertTrue(self.signal_keys_on())
        # The issue before every prompt comes from agetty, read from and written to the VT.
        self.assertEqual(self.agetty_log.read_text().splitlines(),
                         [f'--show-issue|{self.slave_path}|{self.slave_path}'] * 4)
        # The message once: a new agreety run does not clear the screen.
        self.assertEqual(self.screen(process), self.expected_message(cause=True, live=True) + BANNER * 4)
        self.assertIn('showing graphics-failed message (status 3)\n', self.journal.read_text())
        # The compositor started as before: VT on stdin, no inherited signal ignores.
        argv, stdin, sigign = self.result.read_text().splitlines()[:3]
        self.assertEqual(argv, '--config /usr/share/emaki/greetd/niri.kdl')
        self.assertEqual(stdin, self.slave_path)
        self.assertEqual(sigign.split()[-1], '0' * 16)

    def test_agreety_error_stays_visible_under_the_message(self):
        # agreety ends with "error: ..." on its stderr (Ctrl+D at "login:" gives "error: no
        # input"); the next run must not clear that line away.
        process = self.start(codes=('1 error: no input', '1 error: login error: "x"', 0),
                             STUB_EXIT='3')
        self.assertEqual(process.wait(timeout=10), 0)
        self.assertEqual(len(self.agreety_runs()), 3)
        screen = self.screen(process)
        self.assertEqual(screen.count(CLEAR), 1, screen)
        self.assertEqual(screen, self.expected_message(cause=True, live=False) + BANNER
                         + 'error: no input\n' + BANNER + 'error: login error: "x"\n' + BANNER)
        # VM pass 2 (P2-2b): agreety's own issue printed "Emaki{PRETTY_NAME} ..." here.
        self.assertNotIn('{PRETTY_NAME}', screen)

    def test_early_other_failure_shows_message_without_cause(self):
        process = self.start(codes=(0,), STUB_EXIT='1', STUB_SLEEP='1')
        self.assertEqual(process.wait(timeout=10), 0)
        self.assertEqual(len(self.agreety_runs()), 1)
        self.assertEqual(self.screen(process), self.expected_message(cause=False, live=False) + BANNER)
        self.assertIn('(status 1)\n', self.journal.read_text())

    @unittest.skipUnless(Path('/usr/bin/agetty').exists(), 'agetty (util-linux) is not installed')
    def test_agetty_expands_the_issue_like_the_getty(self):
        # The real tool the wrapper relies on: \S{PRETTY_NAME} from os-release (agreety left
        # "{PRETTY_NAME}"), \r the kernel release; \l is always tty1 with --show-issue, the VT
        # greetd gives the greeter (greetd/config.toml). The VT's settings stay as they were.
        issue = self.base / 'issue'
        issue.write_text('\\S{PRETTY_NAME} \\r (\\l)\n\n')
        release = next(path for path in (Path('/etc/os-release'), Path('/usr/lib/os-release')) if path.exists())
        fields = dict(line.split('=', 1) for line in release.read_text().splitlines() if '=' in line)
        name = shlex.split(fields['PRETTY_NAME'])[0]
        master, slave = pty.openpty()
        try:
            before = termios.tcgetattr(slave)
            result = subprocess.run(['/usr/bin/agetty', '--show-issue', '--issue-file', str(issue)],
                                    stdin=slave, stdout=slave, stderr=subprocess.PIPE, timeout=10)
            after = termios.tcgetattr(slave)
        finally:
            os.close(slave)
        output = b''
        while True:
            try:
                data = os.read(master, 4096)
            except OSError:
                break
            if not data:
                break
            output += data
        os.close(master)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.decode().replace('\r\n', '\n'), f'{name} {os.uname().release} (tty1)\n\n')
        self.assertEqual(before, after)

    def test_late_failure_keeps_the_status_and_the_vt_silent(self):
        process = self.start(codes=(0,), STUB_EXIT='1', STUB_SLEEP='16')
        self.assertEqual(process.wait(timeout=25), 1)
        self.assertEqual(self.agreety_runs(), [])
        self.assertEqual(self.screen(process), '')

    def fake_boot_clock(self, seconds):
        uptime = self.base / 'uptime'
        uptime.write_text(f'{seconds} 1.00\n')
        return dict(EMAKI_UPTIME_FILE=str(uptime))

    # The wrapper measures the compositor's run on the boot clock (/proc/uptime, faked here),
    # never on the wall clock, which NTP may step at boot in either direction.
    def test_late_failure_is_measured_on_the_boot_clock(self):
        # 0 s by the wall clock (as after a backward step), 15 s by the boot clock: late.
        process = self.start(codes=(0,), STUB_EXIT='1', STUB_UPTIME='115.00',
                             **self.fake_boot_clock('100.00'))
        self.assertEqual(process.wait(timeout=10), 1)
        self.assertEqual(self.agreety_runs(), [])
        self.assertEqual(self.screen(process), '')

    def test_early_failure_is_measured_on_the_boot_clock(self):
        # 15 s by the wall clock (standing in for a forward step), 14 s by the boot clock:
        # early, so the text login appears instead of greetd restarting into the same failure.
        process = self.start(codes=(0,), STUB_EXIT='1', STUB_SLEEP='15', STUB_UPTIME='114.99',
                             **self.fake_boot_clock('100.00'))
        self.assertEqual(process.wait(timeout=25), 0)
        self.assertEqual(len(self.agreety_runs()), 1)
        self.assertIn('(status 1)\n', self.journal.read_text())

    def test_clean_exit_is_unchanged(self):
        process = self.start(codes=(0,), STUB_EXIT='0')
        self.assertEqual(process.wait(timeout=10), 0)
        self.assertEqual(self.agreety_runs(), [])
        self.assertEqual(self.screen(process), '')
        self.assertNotIn('showing', self.journal.read_text() if self.journal.exists() else '')

    def test_sigterm_reaches_the_compositor(self):
        process = self.start(codes=(0,), STUB_EXIT='3', STUB_SLEEP='30')
        self.assertTrue(wait_for(self.result.exists))
        started = time.monotonic()
        process.send_signal(signal.SIGTERM)
        status = process.wait(timeout=5)
        self.assertLess(time.monotonic() - started, 2)
        self.assertNotEqual(status, 0)
        self.assertTrue(self.result.read_text().endswith('TERM'), self.result.read_text())
        self.assertEqual(self.agreety_runs(), [])
        self.assertEqual(self.screen(process), '')

    def agreety_waiting(self):
        return wait_for(lambda: self.agreety_log.exists()
                        and 'waiting\n' in self.agreety_log.read_text())

    def test_sigterm_during_login_prompt_ends_agreety_and_the_wrapper(self):
        process = self.start(codes=('sleep',), STUB_EXIT='3')
        self.assertTrue(self.agreety_waiting())
        self.assertFalse(self.signal_keys_on())
        started = time.monotonic()
        process.send_signal(signal.SIGTERM)
        self.assertEqual(process.wait(timeout=5), 0)
        self.assertLess(time.monotonic() - started, 2)
        self.assertTrue(wait_for(lambda: self.agreety_log.read_text().endswith('TERM')))
        self.assertTrue(self.signal_keys_on())

    def agreety_group(self):
        """agreety's pid, process group, the VT's foreground group and its ignored signals."""
        line = next(line for line in self.agreety_log.read_text().splitlines() if line.startswith('group '))
        pid, group, front, ignored = line.split()[1:]
        return int(pid), int(group), int(front), int(ignored, 16)

    def use_python(self, python):
        """python3 on the wrapper's PATH: 'working', 'missing' or a key of BROKEN_PYTHON."""
        if python != 'working':
            (self.bin / 'python3').unlink()
        if python in BROKEN_PYTHON:
            self.stub('python3', BROKEN_PYTHON[python])
        self.python_log = self.base / 'python'
        return {'PYTHON_LOG': str(self.python_log)}

    def press_at_the_login_prompt(self, key, python='working'):
        """Types a name with `key` in it on the VT while agreety reads it; greetd's worker must
        outlive the key, and with Python the key must not reach agreety (VM pass 2, P2-2c: the
        name "^C^C..." got greetd's "protocol error")."""
        worker = self.start(codes=('read',), worker=True, STUB_EXIT='3', **self.use_python(python))
        self.assertTrue(self.agreety_waiting())
        os.write(self.master, b'ab' + key + b'cd\n')
        self.assertTrue(wait_for(lambda: 'name ' in self.agreety_log.read_text()))
        time.sleep(.3)
        # A dead worker ends greetd; five of those in 30 s leave greetd.service failed.
        self.assertIsNone(worker.poll(), f'greetd worker ended with {worker.returncode}')
        state = Path(f'/proc/{worker.pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
        self.assertNotEqual(state, 'T', 'greetd worker stopped')
        self.assertEqual(len(self.agreety_runs()), 1)
        self.assertFalse(self.agreety_log.read_text().endswith('TERM'))
        pid, group, front, ignored = self.agreety_group()
        settings = self.agreety_runs()[0][4]
        if python == 'working':
            # The key never reaches agreety; it discards the line typed before it, as at any
            # terminal prompt.
            name = next(line for line in self.agreety_log.read_text().splitlines() if line.startswith('name '))
            self.assertEqual(name, 'name cd')
            # agreety leads its own process group in front of the VT, with the signal keys on:
            # the key signals only that group, which ignores it.
            self.assertEqual((group, front), (pid, pid))
            self.assertTrue(self.signal_keys_on(settings), settings)
            mask = 1 << signal.SIGINT - 1 | 1 << signal.SIGQUIT - 1 | 1 << signal.SIGTSTP - 1
            self.assertEqual(ignored & mask, mask, hex(ignored))
        else:
            # Without a Python that can run the step agreety runs in the worker's group with the
            # signal keys off.
            self.assertEqual(group, worker.pid)
            self.assertFalse(self.signal_keys_on(settings), settings)
        # greetd stops the greeter with SIGTERM; the VT gets its signal keys back.
        os.kill(self.wrapper_pid(), signal.SIGTERM)
        self.assertEqual(worker.wait(timeout=5), 0)
        self.assertTrue(self.signal_keys_on())

    def test_ctrl_c_at_the_login_prompt_keeps_greetd_running(self):
        self.press_at_the_login_prompt(b'\x03')

    def test_ctrl_backslash_at_the_login_prompt_keeps_greetd_running(self):
        self.press_at_the_login_prompt(b'\x1c')

    def test_ctrl_z_at_the_login_prompt_keeps_greetd_running(self):
        self.press_at_the_login_prompt(b'\x1a')

    def test_without_python_ctrl_c_still_keeps_greetd_running(self):
        self.press_at_the_login_prompt(b'\x03', python='missing')

    # A python3 on PATH that fails, or one without termios, ended every attempt before agreety
    # started: the VT showed nothing but the issue, once a second, and no prompt.
    def test_with_a_failing_python_the_prompt_comes_and_ctrl_c_keeps_greetd_running(self):
        self.press_at_the_login_prompt(b'\x03', python='failing')

    def test_without_termios_the_prompt_comes_and_ctrl_c_keeps_greetd_running(self):
        self.press_at_the_login_prompt(b'\x03', python='no termios')

    def test_python_is_probed_once(self):
        # One probe before the first prompt, none per attempt; its error goes to the journal.
        process = self.start(codes=(1, 1, 0), STUB_EXIT='3', **self.use_python('failing'))
        self.assertEqual(process.wait(timeout=10), 0)
        self.assertEqual(len(self.agreety_runs()), 3)
        self.assertEqual(len(self.python_log.read_text().splitlines()), 1)
        self.assertIn('python3: broken\n', self.journal.read_text())


class TextSession(unittest.TestCase):
    # The shell reads this from stdin: a bash login shell (exec -l) reports shell=-bash login.
    PROBE = 'echo "shell=$0 $(shopt -q login_shell && echo login)"\nexit 0\n'

    def run_session(self, shell):
        result = subprocess.run([str(TEXT_SESSION)], input=self.PROBE,
                                env=dict(PATH='/usr/bin', SHELL=shell), capture_output=True,
                                text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.splitlines()

    def test_prints_three_lines_and_execs_the_login_shell(self):
        lines = self.run_session('/bin/bash')
        self.assertEqual(lines[:3], [
            'Text session - the graphical desktop did not start.',
            'Type "exit" to try the graphical login again.',
            'Details: journalctl -b -t emaki-greeter-compositor -t niri-emaki-session'])
        self.assertEqual(lines[3], 'shell=-/bin/bash login')

    def test_missing_shell_falls_back_to_bash(self):
        self.assertEqual(self.run_session('/bin/false-missing')[3], 'shell=-/bin/bash login')

    def test_user_shell_is_used_when_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            shell = Path(directory) / 'myshell'
            shell.write_text('#!/bin/bash\necho "myshell ran as $0"\n')
            shell.chmod(0o700)
            self.assertEqual(self.run_session(str(shell))[3], f'myshell ran as {shell}')

    def test_the_shell_gets_the_signal_keys_back_on_a_vt(self):
        # A text login whose wrapper died without restoring the VT leaves ISIG and ECHO off;
        # the session resets the VT and keeps its UTF-8 input flag (stty sane clears it).
        for utf8 in (True, False):
            with self.subTest(utf8=utf8), tempfile.TemporaryDirectory() as directory:
                record = Path(directory) / 'stty'
                shell = Path(directory) / 'shell'
                shell.write_text(f'#!/bin/bash\n/usr/bin/stty -g > {shlex.quote(str(record))}\n')
                shell.chmod(0o700)
                master, slave = pty.openpty()
                try:
                    attrs = termios.tcgetattr(slave)
                    attrs[0] = attrs[0] | termios.IUTF8 if utf8 else attrs[0] & ~termios.IUTF8
                    attrs[3] &= ~(termios.ISIG | termios.ECHO)
                    termios.tcsetattr(slave, termios.TCSANOW, attrs)
                    result = subprocess.run([str(TEXT_SESSION)], stdin=slave, stdout=slave,
                                            stderr=slave, env=dict(PATH='/usr/bin', SHELL=str(shell)),
                                            timeout=5)
                finally:
                    os.close(slave)
                    os.close(master)
                self.assertEqual(result.returncode, 0)
                iflag, _, _, lflag = (int(field, 16) for field in record.read_text().split(':')[:4])
                self.assertTrue(lflag & termios.ISIG)
                self.assertTrue(lflag & termios.ECHO)
                self.assertEqual(bool(iflag & termios.IUTF8), utf8)

    def test_never_starts_a_compositor(self):
        commands = [line for line in TEXT_SESSION.read_text().splitlines()
                    if line and not line.lstrip().startswith('#') and 'journalctl' not in line]
        for word in ('niri-emaki-session', 'niri-session', 'systemctl', 'dbus-run-session', 'exec niri'):
            self.assertFalse(any(word in line for line in commands), word)
        self.assertEqual([line for line in commands if line.startswith('exec')], ['exec -l "$shell"'])


if __name__ == '__main__':
    unittest.main(argv=sys.argv[:1] + ['-v'])
