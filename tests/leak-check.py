#!/usr/bin/env python3
"""Fails when a test leaves a process running after it ends.

Each test runs with EMAKI_TEST_RUN=<random marker> in its environment, and this process is a
child subreaper, so whatever the test leaves without a parent is re-parented here. Once the
test has exited (plus a short settle), every process still below this one or carrying the
marker in /proc/<pid>/environ is a leak: it is listed, stopped and the check fails. Only this
user's processes from this run are looked at and stopped.

Besides plain runs, test-system.py is also ended the two ways that leaked before:
  interrupted  SIGTERM to the test once its offscreen qs has started udevadm;
  timeout      its own run limit fires (EMAKI_TEST_WAIT_SCALE=0.05, so 4.5 s) while its inner
               run is frozen with SIGSTOP, as a hung run would be.

Usage: leak-check.py [test [args...] ...]   each test one argument, e.g.
       'tests/test-dock.py .cache/target/debug/emaki'; default: the representative set below.
"""
import os
from pathlib import Path
import re
import secrets
import shlex
import signal
import subprocess
import sys
import time

import reaper

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / '.cache/leak-check'
# Each starts long-lived processes: offscreen qs (with udevadm under SystemService),
# dbus-run-session and its dbus-daemon, a fake greetd, NetworkManager, tray and locker, the
# greeter wrapper's compositor and journal stubs, swayidle and niri fakes. test-system.py and
# test-greeter-compositor.py left processes behind even when they passed.
DEFAULT = ['tests/test-system.py', 'tests/test-greeter-compositor.py', 'tests/test-notifications.py',
           'tests/test-launcher-tools.py', 'tests/test-app-scope-dbus.py', 'tests/test-greeter-entry.py',
           'tests/test-lock-session.py', 'tests/test-sleep-guard.py', 'tests/test-idle.sh']


def marked(marker):
    """{pid: start time} of this user's live processes whose environment carries the marker."""
    entry, uid, found = ('EMAKI_TEST_RUN=' + marker).encode(), os.getuid(), {}
    for name in os.listdir('/proc'):
        if not name.isdigit() or int(name) == os.getpid():
            continue
        try:
            if os.stat('/proc/' + name).st_uid != uid:
                continue
            with open(f'/proc/{name}/environ', 'rb') as f:
                if entry not in f.read().split(b'\0'):
                    continue
            _, state, start = reaper._stat(name)
        except (OSError, ValueError, IndexError):
            continue
        if state != 'Z':
            found[int(name)] = start
    return found


def command(test):
    argv = shlex.split(test)
    if argv[0].endswith('.py'):
        return [sys.executable] + argv
    if argv[0].endswith('.sh'):
        return ['sh'] + argv
    return argv


class Run:
    """One test process; reaps adopted orphans while it runs and keeps the test's own status."""

    def __init__(self, name, argv, env):
        self.name = name
        self.marker = secrets.token_hex(8)
        LOGS.mkdir(parents=True, exist_ok=True)
        self.log = LOGS / (re.sub(r'[^A-Za-z0-9.-]+', '_', name) + '.log')
        with self.log.open('w') as log:
            self.process = subprocess.Popen(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                            env=dict(os.environ, EMAKI_TEST_RUN=self.marker, **env))
        self.status = None
        self.started = time.monotonic()

    def poll(self):
        while self.status is None:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if pid == 0:
                break
            if pid == self.process.pid:
                self.status = status
                self.process.returncode = os.waitstatus_to_exitcode(status)
        return self.status

    def wait(self, limit):
        end = time.monotonic() + limit
        while self.poll() is None:
            if time.monotonic() > end:
                os.kill(self.process.pid, signal.SIGKILL)
                end = float('inf')
            time.sleep(.05)
        return self.process.returncode

    def processes(self):
        return {**marked(self.marker), **reaper.descendants()}


def leftovers(run):
    """What is still running 3 s after the test ended; then stop it."""
    settle = time.monotonic() + 3
    while True:
        reaper._reap()
        left = run.processes()
        if not left or time.monotonic() > settle:
            break
        time.sleep(.1)
    rows = [f'  {pid} {reaper.describe(pid)}' for pid in sorted(left)]
    for pid, start in marked(run.marker).items():
        reaper._signal(pid, start, signal.SIGKILL)
    reaper.stop_all()
    return rows


def interrupt(run):
    """SIGTERM to the test once its qs has a udevadm child."""
    end = time.monotonic() + 90
    while run.poll() is None and time.monotonic() < end:
        if any(reaper.describe(pid).startswith('udevadm ') or '/udevadm ' in reaper.describe(pid)
               for pid in run.processes()):
            os.kill(run.process.pid, signal.SIGTERM)
            return 'sent SIGTERM with udevadm running'
        time.sleep(.05)
    return 'udevadm never started'


def freeze_inner(run):
    """SIGSTOP test-system.py's inner run (every process of it) until the test exits."""
    frozen = set()
    while run.poll() is None:
        for pid, start in run.processes().items():
            args = reaper.describe(pid).split()
            # The python process only: dbus-run-session carries --inside in its argv too.
            if pid not in frozen and args and 'python' in Path(args[0]).name and '--inside' in args:
                reaper._signal(pid, start, signal.SIGSTOP)
                frozen.add(pid)
        time.sleep(.01)
    return f'froze {len(frozen)} inner process(es)' if frozen else 'inner run never seen'


def main():
    if not reaper.become_subreaper():
        raise SystemExit('leak-check: PR_SET_CHILD_SUBREAPER failed')
    cases = [(test, command(test), {}, None, True) for test in (sys.argv[1:] or DEFAULT)]
    if not sys.argv[1:]:
        system = command('tests/test-system.py')
        cases += [('test-system.py interrupted', system, {}, interrupt, False),
                  ('test-system.py timeout', system, {'EMAKI_TEST_WAIT_SCALE': '0.05'}, freeze_inner, False)]
    failed = []
    for name, argv, env, action, must_pass in cases:
        run = Run(name, argv, env)
        note = action(run) if action else ''
        code = run.wait(600)
        seconds = time.monotonic() - run.started
        rows = leftovers(run)
        text = run.log.read_text(errors='replace')
        if name.endswith('timeout') and 'TimeoutExpired' not in text:
            rows.append('  (the run limit did not fire: ' + (note or 'no note') + ')')
        if name.endswith('interrupted') and not note.startswith('sent'):
            rows.append('  (' + note + ')')
        result = 'LEAK' if rows else 'clean'
        print(f'{result}: {name} (exit {code}, {seconds:.0f} s{", " + note if note else ""})', flush=True)
        for row in rows:
            print(row, flush=True)
        if must_pass and code != 0:
            print(f'  test failed (exit {code}): {run.log.relative_to(ROOT)}', flush=True)
        if rows or (must_pass and code != 0):
            failed.append(name)
    if failed:
        raise SystemExit('leak-check: FAIL: ' + ', '.join(failed))
    print(f'leak-check: PASS: {len(cases)} run(s), nothing left running')


if __name__ == '__main__':
    main()
