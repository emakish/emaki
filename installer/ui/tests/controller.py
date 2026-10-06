#!/usr/bin/env python3
"""Run every recorded scenario through the production QML controller.

--unix additionally exercises real Quickshell Socket and backoff using the same
mock worker; run it outside sandboxes that deny Unix socket binds.
"""
import argparse
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile

UI = Path(__file__).resolve().parents[1]
SCENARIOS = ['erase-happy', 'alongside', 'manual-reprobe', 'gparted-stale', 'plan-errors', 'error-retry', 'disconnect-resume', 'token-expiry', 'cancel', 'choices', 'real-preflight', 'real-erase-btrfs', 'no-boot-medium', 'preflight-progress', 'update-progress']
# real-preflight is served by real-worker.py (the real controller and worker over stdio); the
# recorded install has hundreds of frames, too many for the socket mock's per-frame delay.
STDIO_ONLY = {'real-preflight', 'real-erase-btrfs'}
# No worker at all (its unit was skipped): over --unix the window's socket path does not exist.
NO_WORKER = {'no-boot-medium'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unix', action='store_true')
    parser.add_argument('--output', type=Path, default=Path('/tmp/emaki-installer-controller'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='emaki-controller-') as temporary:
        root = Path(temporary)
        if args.unix:
            try:
                with socket.socket(socket.AF_UNIX) as probe:
                    probe.bind(str(root / 'probe.sock'))
            except PermissionError:
                print('SKIP: this sandbox denies Unix socket binds; rerun --unix in the VM.')
                return 77
        shutil.copytree(UI, root / 'ui', ignore=shutil.ignore_patterns('__pycache__', 'artifacts'))
        entry = (UI / 'tests/ControllerTest.qml').read_text().replace('".." as UI', '"." as UI').replace('"mock-worker.py"', '"tests/mock-worker.py"').replace('"real-worker.py"', '"tests/real-worker.py"')
        (root / 'ui/controller-test.qml').write_text(entry)
        # Scenarios that render the page use this checkout's shell, not an installed one.
        for filename in (root / 'ui').glob('*.qml'):
            filename.write_text(filename.read_text().replace('"file:///usr/share/emaki/shell"', '"file://' + str(UI.parents[1] / 'shell') + '"'))
        for name in ['runtime', 'cache', 'config', 'state', 'data']:
            (root / name).mkdir(mode=0o700)
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1',
                   QS_DISABLE_CRASH_HANDLER='1', XDG_RUNTIME_DIR=str(root / 'runtime'), XDG_CACHE_HOME=str(root / 'cache'),
                   XDG_CONFIG_HOME=str(root / 'config'), XDG_STATE_HOME=str(root / 'state'), XDG_DATA_HOME=str(root / 'data'),
                   EMAKI_INSTALLER_TEST_UNIX='1' if args.unix else '0', EMAKI_INSTALLER_CHECKOUT=str(UI.parents[1]))
        for key in ['WAYLAND_DISPLAY', 'DISPLAY', 'NIRI_SOCKET', 'DBUS_SESSION_BUS_ADDRESS']:
            env.pop(key, None)
        for scenario in SCENARIOS:
            worker = None
            if args.unix and scenario in STDIO_ONLY:
                print('SKIP QML controller ' + scenario + ': served over stdio only', flush=True)
                continue
            env['EMAKI_INSTALLER_SCENARIO'] = scenario
            try:
                if args.unix:
                    env['EMAKI_INSTALLER_SOCKET'] = str(root / (scenario + '.sock'))
                if args.unix and scenario not in NO_WORKER:
                    worker = subprocess.Popen([sys.executable, '-B', str(UI / 'tests/mock-worker.py'), '--scenario', scenario,
                                               '--socket', env['EMAKI_INSTALLER_SOCKET'], '--delay', '0.05'],
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    assert worker.stdout.readline().strip() == env['EMAKI_INSTALLER_SOCKET']
                result = subprocess.run(['qs', '-p', str(root / 'ui/controller-test.qml'), '--no-color'], env=env,
                                        text=True, capture_output=True, timeout=15)
                log = result.stdout + result.stderr
                (args.output / (scenario + '.log')).write_text(log)
                assert result.returncode == 0 and 'SCENARIO_OK ' + scenario in log and 'ASSERTION_FAILED' not in log, log
                for diagnostic in ['ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign']:
                    assert diagnostic not in log, log
                print('PASS QML controller ' + scenario, flush=True)
            finally:
                if worker:
                    worker.terminate()
                    worker.communicate(timeout=3)
    return 0


if __name__ == '__main__': raise SystemExit(main())
