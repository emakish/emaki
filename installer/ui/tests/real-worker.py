#!/usr/bin/env python3
"""Serve the window over stdio with the real installer controller and worker.

Only the machine is faked: the inventory is installer/tests/support.py's 40 GiB
disk, the target is a temporary directory, and `pacman` on PATH is a stub that
refuses the repository sync. So the real Worker.run fails in its repository
preflight, before any disk write, and the window receives the events the worker
itself emits (log lines, then the error with its code and retryable flag).
Requests and passwords are never printed or saved.
"""
import json
from functools import partial
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

# The tests copy the window to a temporary directory; the caller names the checkout.
CHECKOUT = Path(os.environ.get('EMAKI_INSTALLER_CHECKOUT') or Path(__file__).resolve().parents[3])
sys.path[:0] = [str(CHECKOUT / 'installer'), str(CHECKOUT / 'installer/tests')]

from emaki_installer.protocol import Controller  # noqa: E402
from emaki_installer.worker import Worker  # noqa: E402
from emaki_installer.constants import OFFLINE_CONF, OFFLINE_REPO  # noqa: E402
from emaki_installer.media import package_source  # noqa: E402
from support import FakeInventory  # noqa: E402

PACMAN = '''#!/bin/sh
echo "pacman stub: the repository sync is refused for this test" >&2
exit 1
'''


def main():
    with tempfile.TemporaryDirectory(prefix='emaki-real-worker-') as temporary:
        root = Path(temporary)
        (root / 'bin').mkdir()
        (root / 'bin/pacman').write_text(PACMAN)
        (root / 'bin/pacman').chmod(0o755)
        os.environ['PATH'] = str(root / 'bin') + os.pathsep + os.environ['PATH']
        repo = root / 'run/archiso/bootmnt/emaki/repo'
        repo.mkdir(parents=True)
        config = root / str(OFFLINE_CONF).lstrip('/')
        config.parent.mkdir(parents=True)
        shipped = CHECKOUT / 'iso/profile/airootfs' / str(OFFLINE_CONF).lstrip('/')
        config.write_text(shipped.read_text().replace(OFFLINE_REPO, repo.as_uri()))
        out, lock = sys.stdout.buffer, threading.Lock()

        def send(message):
            with lock:
                out.write((json.dumps(message) + '\n').encode())
                out.flush()

        def factory(broker):
            return Worker(None, broker.inventory, broker.redactor, broker.emit, broker.log,
                          target=root / 'target', skip_update=broker.job.skip_update)

        inventory = FakeInventory()
        inventory.root = root
        controller = Controller(inventory, factory)
        threads = []
        with patch('emaki_installer.worker.package_source',
                   partial(package_source, work_parent=root / 'work')):
            for frame in sys.stdin.buffer:
                request = json.loads(frame)
                if request['type'] == 'confirm' and send not in controller.listeners:
                    controller.listeners.add(send)
                for response in controller.handle(request):
                    if callable(response):
                        threads.append(response())
                    else:
                        send(response)
            for thread in threads:
                thread.join(30)


if __name__ == '__main__':
    main()
