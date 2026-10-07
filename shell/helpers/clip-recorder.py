#!/usr/bin/env python3
"""Own a clipboard watcher for the shell: `wl-paste --watch cliphist store`.

The watcher lives exactly as long as this process's stdin: the shell keeps the pipe
open, and when the shell exits (even by SIGKILL) the pipe closes, the watcher gets
SIGTERM and nothing keeps recording. SIGTERM to this wrapper (Pause) is forwarded.
No clipboard contents pass through here: wl-paste hands them to cliphist directly.
"""
import os
import select
import signal
import subprocess
import sys
import clipboard_store


def main():
    clipboard_store.prepare()
    argv = [os.environ.get('EMAKI_WL_PASTE') or 'wl-paste', '--watch',
            os.environ.get('EMAKI_CLIPHIST') or 'cliphist', '-config-path', '/dev/null',
            '-db-path', str(clipboard_store.runtime_db()), 'store']
    with open(os.devnull, 'wb') as null:
        child = subprocess.Popen(argv, stdin=null, stdout=null, stderr=null)
    def stop(*_):
        if child.poll() is None:
            child.terminate()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while child.poll() is None:
            # EOF on stdin means the shell is gone; a byte is never expected.
            # The timeout also notices a watcher that died on its own.
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
