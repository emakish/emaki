#!/usr/bin/env python3
"""tests/reaper.py seen from outside: what reaches a guarded test, and how its run ends.

Every case runs a small guarded script as a child process, never this process. Terminal keys
and hangups go to private ptys owned by sessions of their own, never to a terminal of the
running session.
"""
import os
import pty
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
import reaper
reaper.guard()  # nothing this test starts outlives it

TESTS = Path(__file__).resolve().parent
# argv: mode, log file. The reaper logs every signal it passes on; the guarded part logs
# 'ready <reaper pid>', every signal it gets and the end of its cleanup, and ends by the first
# signal it got once the cleanup is over. Whether a second copy of a signal reaches the test
# depends on timing (a signal that arrives while the first is still pending merges with it),
# so what the reaper sends is logged where it sends it.
TARGET = '''
import os, signal, sys, time
sys.path.insert(0, %r)
import reaper
mode, log = sys.argv[1:3]


def note(text):
    with open(log, 'a') as f:
        f.write(text + '\\n')


send = signal.pidfd_send_signal


def passed_on(fd, sig, *args):
    note('passed on ' + signal.Signals(sig).name)
    return send(fd, sig, *args)


signal.pidfd_send_signal = passed_on
reaper.guard()
if mode == 'own-group':
    os.setpgid(0, 0)


class Stop(BaseException):
    pass


def stop(sig, _frame):
    note('got ' + signal.Signals(sig).name)
    raise Stop(sig)


for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
    signal.signal(sig, stop)
note(f'ready {os.getppid()}')
try:
    try:
        time.sleep(30)
    finally:
        # Cleanup that must run to its end: busy, then a short sleep.
        end = time.monotonic() + .5
        while time.monotonic() < end:
            pass
        time.sleep(.2)
        note('cleanup done')
except Stop as stopped:
    signal.signal(stopped.args[0], signal.SIG_DFL)
    os.kill(os.getpid(), stopped.args[0])
''' % str(TESTS)
# Takes the pty on its stdin as the controlling terminal of a new session. 'exec': becomes the
# guarded script, which then leads the session; 'fork': stays the session leader (as a shell
# does) and runs the script as its child, in the foreground process group.
SESSION = '''
import fcntl, os, sys, termios
os.setsid()
fcntl.ioctl(0, termios.TIOCSCTTY, 0)
if sys.argv[1] == 'fork':
    child = os.fork()
    if child:
        _, status = os.waitpid(child, 0)
        os._exit(os.waitstatus_to_exitcode(status) & 255)
os.execvp(sys.argv[2], sys.argv[2:])
'''
# argv: mode. The guarded part ends at once: 'kill' by SIGKILL (as the OOM killer ends a
# process), 'abort' and 'segv' by those signals, without a core of its own (it makes itself
# non-dumpable first). The reaper's re-raise of a core-dumping signal is caught at its
# os.kill() call and turned into the exit status 100 + PR_GET_DUMPABLE, so no core of the
# reaper is written either, whatever the reaper does before.
ENDING = '''
import ctypes, os, resource, signal, sys
sys.path.insert(0, %r)
import reaper
PR_GET_DUMPABLE, PR_SET_DUMPABLE = 3, 4
libc = ctypes.CDLL(None)


def prctl(option, value=0):
    zero = ctypes.c_ulong(0)
    return libc.prctl(option, ctypes.c_ulong(value), zero, zero, zero)


kill = os.kill


def reraise(pid, sig):
    if pid == os.getpid() and sig in (signal.SIGABRT, signal.SIGSEGV):
        os._exit(100 + prctl(PR_GET_DUMPABLE))
    kill(pid, sig)


os.kill = reraise
reaper.guard()
if sys.argv[1] == 'kill':
    kill(os.getpid(), signal.SIGKILL)
prctl(PR_SET_DUMPABLE, 0)
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
signal.raise_signal(signal.SIGABRT if sys.argv[1] == 'abort' else signal.SIGSEGV)
''' % str(TESTS)


def wait_for(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.02)
    return predicate()


def ended(pid):
    try:
        return reaper._stat(pid)[1] == 'Z'
    except (OSError, ValueError, IndexError):
        return True


def ctrl_c(master, _reaper):
    os.write(master, b'\x03')


def hang_up(_master, _reaper):
    return True  # the master is closed


class Signals(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix='reaper-', dir=TESTS.parent / '.cache'))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.target = self.base / 'target.py'
        self.target.write_text(TARGET)
        self.log = self.base / 'log'

    def lines(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def on_a_terminal(self, act, mode='wait', session='exec'):
        """The guarded script in the foreground of a private pty; act(master, reaper pid) once
        it is ready. What the reaper passed on, what the test logged, and the status of the
        session's first process, once the reaper has ended."""
        self.log.unlink(missing_ok=True)
        master, slave = pty.openpty()
        process = subprocess.Popen([sys.executable, '-c', SESSION, session, sys.executable, '-I',
                                    str(self.target), mode, str(self.log)],
                                   stdin=slave, stdout=slave, stderr=slave)
        os.close(slave)
        closed = False
        try:
            self.assertTrue(wait_for(lambda: any(line.startswith('ready ') for line in self.lines())),
                            self.lines())
            pid = int(next(line for line in self.lines() if line.startswith('ready ')).split()[1])
            time.sleep(.2)
            closed = act(master, pid)
            if closed:
                os.close(master)
            # The pty's buffer holds the little the scripts print; nothing has to read it.
            self.assertTrue(wait_for(lambda: ended(pid)), f'the reaper did not end: {self.lines()}')
            process.wait(timeout=15)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            if not closed:
                os.close(master)
        lines = [line for line in self.lines() if not line.startswith('ready ')]
        sent = [line for line in lines if line.startswith('passed on ')]
        return sent, [line for line in lines if line not in sent], process.returncode

    def test_ctrl_c_reaches_the_test_once(self):
        # The terminal sends SIGINT to its whole foreground process group, the test included.
        # The copy the reaper passed on could land in the test's cleanup and cut it short.
        sent, test, status = self.on_a_terminal(ctrl_c)
        self.assertEqual(sent, [])
        self.assertEqual(test, ['got SIGINT', 'cleanup done'])
        self.assertEqual(status, -signal.SIGINT)

    def test_ctrl_c_reaches_a_test_that_left_the_foreground_group(self):
        # Then only the reaper gets the key, and passes it on.
        sent, test, status = self.on_a_terminal(ctrl_c, mode='own-group')
        self.assertEqual(sent, ['passed on SIGINT'])
        self.assertEqual(test, ['got SIGINT', 'cleanup done'])
        self.assertEqual(status, -signal.SIGINT)

    def test_a_hangup_reaches_the_test_once(self):
        # A closed terminal: its session leader gets SIGHUP, dies of it, and the kernel sends
        # SIGHUP to the foreground process group, the test included. (The status is the
        # session leader's.)
        sent, test, _status = self.on_a_terminal(hang_up, session='fork')
        self.assertEqual(sent, [])
        self.assertEqual(test, ['got SIGHUP', 'cleanup done'])

    def test_a_hangup_is_passed_on_by_a_reaper_that_leads_the_session(self):
        # Then the hangup reaches the reaper alone (a test run as a terminal's command).
        sent, test, status = self.on_a_terminal(hang_up)
        self.assertEqual(sent, ['passed on SIGHUP'])
        self.assertEqual(test, ['got SIGHUP', 'cleanup done'])
        self.assertEqual(status, -signal.SIGHUP)

    def test_a_signal_sent_to_the_reaper_is_passed_on(self):
        # kill(1) aimed at the reaper reaches the test, in front of a terminal too.
        for sig in (signal.SIGINT, signal.SIGHUP, signal.SIGTERM):
            with self.subTest(signal=sig.name):
                sent, test, status = self.on_a_terminal(lambda _master, pid: os.kill(pid, sig))
                self.assertEqual(sent, ['passed on ' + sig.name])
                self.assertEqual(test, ['got ' + sig.name, 'cleanup done'])
                self.assertEqual(status, -sig)


class Endings(unittest.TestCase):
    def run_target(self, mode):
        with tempfile.TemporaryDirectory(prefix='reaper-', dir=TESTS.parent / '.cache') as base:
            target = Path(base, 'ending.py')
            target.write_text(ENDING)
            return subprocess.run([sys.executable, '-I', str(target), mode], capture_output=True, text=True,
                                  timeout=30, start_new_session=True)

    def test_a_test_killed_by_sigkill_ends_with_137(self):
        # No action can be set for SIGKILL, so it cannot be raised again the way the other
        # signals are; a killed test ended with "OSError: [Errno 22]" and status 1.
        done = self.run_target('kill')
        self.assertEqual(done.returncode, 128 + signal.SIGKILL, done.stderr)
        self.assertEqual(done.stderr, '')

    def test_the_reaper_dumps_no_core_of_its_own(self):
        # Raising the test's SIGABRT or SIGSEGV again added a second crash, the reaper's, to
        # coredumpctl: RLIMIT_CORE does not stop a core_pattern pipe, a non-dumpable process is
        # skipped (Linux 7.2 fs/coredump.c coredump_skip).
        for mode in ('abort', 'segv'):
            with self.subTest(mode):
                done = self.run_target(mode)
                self.assertEqual(done.returncode, 100,
                                 f'PR_GET_DUMPABLE at the re-raise: {done.returncode - 100}; {done.stderr}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
