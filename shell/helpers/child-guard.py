#!/usr/bin/env python3
"""Run a long-lived child that must not outlive the shell: child-guard.py <argv...>.

Quickshell 0.3.1 does not end child processes when qs exits, so the child lives exactly
as long as this process's stdin: the shell keeps the pipe open, and when the shell exits
(even by SIGKILL) the pipe closes, the child gets SIGTERM and nothing keeps running.
SIGTERM to this guard (a stop from the shell) is forwarded. The child's output goes
to /dev/null; the exit code is the child's.
"""
import os
import select
import signal
import subprocess
import sys


def main():
    argv = sys.argv[1:]
    if not argv:
        sys.exit(2)
    with open(os.devnull, 'wb') as null:
        try:
            child = subprocess.Popen(argv, stdin=null, stdout=null, stderr=null)
        except OSError:
            sys.exit(127)
    def stop(*_):
        if child.poll() is None:
            child.terminate()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while child.poll() is None:
            # EOF on stdin means the shell is gone; a byte is never expected.
            # The timeout also notices a child that died on its own.
            if select.select([0], [], [], .5)[0] and not os.read(0, 1):
                stop()
                break
    except OSError:
        stop()
    try:
        child.wait(timeout=3)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
    sys.exit(child.returncode if child.returncode is not None else 1)


if __name__ == '__main__':
    main()
