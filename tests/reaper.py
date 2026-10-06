#!/usr/bin/env python3
"""Nothing a test starts outlives the test.

A test script calls `reaper.guard()` before it starts anything. The process forks: the child
runs the test; the parent stays behind as the test's child subreaper (PR_SET_CHILD_SUBREAPER),
so a process that loses its parent below it -- the `udevadm monitor` of a terminated qs
(Quickshell does not stop its children when it exits), the dbus-daemon of a dbus-run-session
killed by a harness timeout, Quickshell's crash reporter -- is re-parented to it rather than to
the user manager. When the test ends, however it ends (pass, failure, its own timeout, a
signal), the parent stops every process still below it (SIGTERM, then SIGKILL) and exits with
the test's status. SIGTERM, SIGINT and SIGHUP sent to the parent are passed on to the test,
except those the kernel sent to the test as well (Ctrl+C in a terminal); if the parent is
killed outright, the test is killed too (PR_SET_PDEATHSIG).

Processes are found by parentage in /proc, not by process group or environment, so children
that call setsid or build their own environment are covered as well. Run a non-Python test
under it as `python3 tests/reaper.py <command...>`.
"""
import ctypes
import os
import resource
import signal
import sys
import time

PR_SET_PDEATHSIG = 1
PR_SET_DUMPABLE = 4
PR_SET_CHILD_SUBREAPER = 36
SI_KERNEL = 0x80
# Passed on to the test. The parent holds these and SIGCHLD blocked from before the fork and
# takes them with sigtimedwait(), which also says who sent each one.
FORWARDED = {signal.SIGTERM, signal.SIGINT, signal.SIGHUP}
WAITED = FORWARDED | {signal.SIGCHLD}
_guarded = False


def _prctl(option, value):
    libc = ctypes.CDLL(None, use_errno=True)
    return libc.prctl(option, ctypes.c_ulong(value), ctypes.c_ulong(0), ctypes.c_ulong(0), ctypes.c_ulong(0)) == 0


def _stat(pid):
    """(ppid, state, start time) of a process; OSError when it is gone."""
    with open(f'/proc/{pid}/stat', 'rb') as f:
        data = f.read()
    fields = data[data.rindex(b')') + 2:].split()
    if not fields:
        raise ProcessLookupError(pid)
    return int(fields[1]), fields[0].decode(), int(fields[19])


def descendants(root=None):
    """Live (non-zombie) processes below `root` (default: this process) as {pid: start time}."""
    root = os.getpid() if root is None else root
    table = {}
    for name in os.listdir('/proc'):
        if name.isdigit():
            try:
                table[int(name)] = _stat(name)
            except (OSError, ValueError, IndexError):
                pass
    children = {}
    for pid, (ppid, _, _) in table.items():
        children.setdefault(ppid, []).append(pid)
    found, todo = {}, [root]
    while todo:
        for pid in children.get(todo.pop(), ()):
            todo.append(pid)
            if table[pid][1] != 'Z':
                found[pid] = table[pid][2]
    return found


def describe(pid):
    try:
        with open(f'/proc/{pid}/cmdline', 'rb') as f:
            return f.read().replace(b'\0', b' ').decode(errors='replace').strip()[:160]
    except OSError:
        return '?'


def _signal(pid, start, sig):
    # A pidfd pins the process: after the start-time check the signal cannot reach a new
    # process that reused the number.
    try:
        fd = os.pidfd_open(pid)
    except OSError:
        return
    try:
        if _stat(pid)[2] == start:
            signal.pidfd_send_signal(fd, sig)
    except OSError:
        pass
    finally:
        os.close(fd)


def _alive(pid, start):
    try:
        _, state, started = _stat(pid)
    except (OSError, ValueError, IndexError):
        return False
    return started == start and state != 'Z'


def _reap():
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def stop_all(limit=10.0):
    """Stop every process below this one; returns those still running after `limit` seconds."""
    end = time.monotonic() + limit
    signals = (signal.SIGTERM, signal.SIGCONT)  # SIGCONT: a stopped process handles SIGTERM too
    while True:
        _reap()
        left = descendants()
        if not left or time.monotonic() > end:
            return left
        for pid, start in left.items():
            for sig in signals:
                _signal(pid, start, sig)
        settle = time.monotonic() + (1.0 if signals[0] == signal.SIGTERM else .5)
        while time.monotonic() < min(settle, end):
            time.sleep(.02)
            _reap()
            if not any(_alive(pid, start) for pid, start in left.items()):
                break
        signals = (signal.SIGKILL,)


def become_subreaper():
    """Orphans below this process are re-parented to it (until it exits)."""
    return _prctl(PR_SET_CHILD_SUBREAPER, 1)


def _sent_to_the_test_too(info, child):
    """Whether the kernel sent this signal to the test as well: Ctrl+C, or the hangup a
    terminal's foreground process group gets when its session leader exits, go to the whole
    group (si_code SI_KERNEL), the test included while it stays in this process's group. A
    second copy from here would interrupt the test again, e.g. inside the `finally` that
    cleans up after the first. A hangup of the terminal also reaches its session leader
    alone: when that is this process, the test has not had it."""
    if info.si_code != SI_KERNEL or info.si_signo not in (signal.SIGINT, signal.SIGHUP):
        return False
    if info.si_signo == signal.SIGHUP and os.getsid(0) == os.getpid():
        return False
    try:
        return os.getpgid(child) == os.getpgrp()
    except OSError:
        return False


def _supervise(child):
    child_fd = os.pidfd_open(child)
    status = None
    while status is None:
        info = signal.sigtimedwait(WAITED, 1)
        if info is not None and info.si_signo in FORWARDED and not _sent_to_the_test_too(info, child):
            try:
                signal.pidfd_send_signal(child_fd, info.si_signo)
            except OSError:
                pass
        # After SIGCHLD, or a second without any signal: collect the test's status and every
        # adopted orphan that has ended.
        while True:
            try:
                pid, value = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                if status is None:
                    status = 1 << 8
                break
            if pid == 0:
                break
            if pid == child:
                status = value
    left = stop_all()
    if left:
        print(f'reaper: {len(left)} process(es) left by the test did not stop:', file=sys.stderr)
        for pid in left:
            print(f'  {pid} {describe(pid)}', file=sys.stderr)
        sys.stderr.flush()
        os._exit(1)
    if os.WIFSIGNALED(status):
        sig = os.WTERMSIG(status)
        if sig == signal.SIGKILL:
            # No action can be set for SIGKILL (signal() fails with EINVAL): end with the
            # status a shell reports for it.
            os._exit(128 + sig)
        # The test's crash is the one to look at: this process dumps no core of its own.
        # RLIMIT_CORE does not stop a core_pattern pipe (systemd-coredump records the crash
        # anyway); the kernel skips a process that is not dumpable.
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        _prctl(PR_SET_DUMPABLE, 0)
        signal.signal(sig, signal.SIG_DFL)
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {sig})
        os.kill(os.getpid(), sig)
        os._exit(128 + sig)
    os._exit(os.waitstatus_to_exitcode(status))


def guard():
    """Run the rest of the calling script as a child whose leftovers are stopped at its end.

    Only the script being run (`__main__`) is guarded: a test script loaded as a module by
    another test leaves this to the running one.
    """
    global _guarded
    if _guarded or sys._getframe(1).f_globals.get('__name__') != '__main__':
        return
    _guarded = True
    sys.stdout.flush()
    sys.stderr.flush()
    parent = os.getpid()
    # Set before the fork so no orphan of the test's first moments can miss it; the test
    # itself must not adopt orphans (zombies it never reaps would look alive to it).
    become_subreaper()
    # Blocked before the fork, so none of them can end the parent before it waits for them.
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, WAITED)
    child = os.fork()
    if child == 0:
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        _prctl(PR_SET_CHILD_SUBREAPER, 0)
        _prctl(PR_SET_PDEATHSIG, signal.SIGKILL)
        if os.getppid() != parent:
            os._exit(1)
        return
    _supervise(child)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit('usage: reaper.py <command...>')
    guard()
    os.execvp(sys.argv[1], sys.argv[1:])
