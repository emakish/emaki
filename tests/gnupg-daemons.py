#!/usr/bin/env python3
"""GnuPG daemons left running with a --homedir under a test's own temporary root.

gpg starts its key daemon (and scdaemon, dirmngr) on demand and detaches it; nothing stops it
when the home directory is left behind. Each one holds inotify instances, and a few hundred
left by repeated test runs exhaust the user's limit (fs.inotify.max_user_instances), after
which every program that watches files fails. A suite that makes temporary GnuPG homes
counts what runs under its own root after it has cleaned up: the count must be zero.

  tests/gnupg-daemons.py ROOT

prints the count under ROOT and every process left there, stops them (the suite started
them: their home lies under the suite's own root) and exits 1 when there were any.
"""
import os
from pathlib import Path
import signal
import sys
import time


def daemons(root):
    """(pid, program, homedir) of this user's processes whose --homedir lies under ROOT."""
    root = os.path.abspath(root)
    if root == os.sep:
        raise ValueError('the root must be a test directory, not /')
    found = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal() or int(entry.name) == os.getpid():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            argv = [a.decode(errors='replace') for a in (entry / 'cmdline').read_bytes().split(b'\0') if a]
        except OSError:
            continue  # exited during the scan
        home = None
        for index, arg in enumerate(argv):
            if arg == '--homedir' and index + 1 < len(argv):
                home = argv[index + 1]
            elif arg.startswith('--homedir='):
                home = arg.split('=', 1)[1]
        if home and (os.path.abspath(home) + os.sep).startswith(root + os.sep):
            found.append((int(entry.name), os.path.basename(argv[0]), home))
    return sorted(found)


def left(root, wait=5.0):
    """What still runs under ROOT once exiting daemons had WAIT seconds to go: `gpgconf --kill`
    and a removed home directory both end a daemon asynchronously."""
    deadline = time.monotonic() + wait
    found = daemons(root)
    while found and time.monotonic() < deadline:
        time.sleep(0.1)
        found = daemons(root)
    return found


def stop(root):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid, _, _ in daemons(root):
            try:
                fd = os.pidfd_open(pid)
            except OSError:
                continue
            try:
                # Pinned, then looked up again: a recycled pid never gets the signal.
                if pid in {p for p, _, _ in daemons(root)}:
                    signal.pidfd_send_signal(fd, sig)
            except ProcessLookupError:
                pass
            finally:
                os.close(fd)
        if left(root, 2.0) == []:
            return


def check(root, wait=5.0):
    """What was left under ROOT (empty when clean). Anything left is stopped before this
    returns, so a failing run does not add to the pile it reports."""
    found = left(root, wait)
    if found:
        stop(root)
    return found


def describe(found):
    return '\n'.join(f'  {program} pid {pid} --homedir {home}' for pid, program, home in found)


def main(argv):
    if len(argv) != 2:
        print('usage: gnupg-daemons.py ROOT', file=sys.stderr)
        return 2
    found = check(argv[1])
    print(f'GnuPG daemons left under {argv[1]}: {len(found)}')
    if found:
        print(describe(found))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
