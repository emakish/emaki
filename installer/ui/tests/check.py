#!/usr/bin/env python3
"""Installer-specific lint, syntax and packaging checks; no system installation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

UI = Path(__file__).resolve().parents[1]
ROOT = UI.parents[1]


def checked(argv, **kwargs):
    return subprocess.run(argv, check=True, text=True, capture_output=True, timeout=30, **kwargs)


def main():
    qmlfiles = sorted(UI.glob('*.qml')) + sorted((UI / 'tests').glob('*.qml'))
    lint = shutil.which('qmllint') or '/usr/lib/qt6/bin/qmllint'
    if Path(lint).exists():
        result = subprocess.run([lint, '--ignore-settings', '-W', '0', '--json', '-', '-I', '/usr/lib/qt6/qml', *map(str, qmlfiles)], text=True, capture_output=True, timeout=30)
        report = json.loads(result.stdout)
        known = []
        # Exactly the two upstream metadata omissions already allowed by
        # tests/test-shell.py, at one Socket and two Process handlers.
        allowed = {
            'Type QLocalSocket::LocalSocketError of parameter error in signal called error was not found, but is required to compile onError. Did you add all imports and dependencies?': 1,
            'Type QProcess::ExitStatus of parameter exitStatus in signal called exited was not found, but is required to compile onExited. Did you add all imports and dependencies?': 2,
        }
        for file in report['files']:
            for warning in file['warnings']:
                assert Path(file['filename']).name == 'InstallerController.qml', warning
                assert warning['id'] == 'signal-handler-parameters' and warning['message'] in allowed, warning
                known.append(warning['message'])
        for message, count in allowed.items(): assert known.count(message) <= count
        assert result.returncode == 0 or known, result.stderr
        print(f'PASS qmllint: {len(known)} known Quickshell 0.3.1 metadata diagnostics; no new diagnostics')
    else:
        print('SKIP qmllint: unavailable')
    formatter = Path('/usr/lib/qt6/bin/qmlformat')
    if formatter.exists():
        for filename in qmlfiles:
            assert checked([str(formatter), '--ignore-settings', str(filename)]).stdout == filename.read_text(), filename
        print('PASS qmlformat')
    checked(['bash', '-n', str(UI / 'emaki-install'), str(ROOT / 'packaging/emaki-installer/PKGBUILD')])
    print('PASS bash -n launcher and PKGBUILD')
    if shutil.which('shellcheck'):
        checked(['shellcheck', str(UI / 'emaki-install')])
        print('PASS shellcheck launcher')
    else: print('SKIP shellcheck: unavailable')
    if shutil.which('desktop-file-validate'):
        checked(['desktop-file-validate', str(UI / 'emaki-install.desktop')])
        print('PASS desktop-file-validate')
    with tempfile.TemporaryDirectory(prefix='emaki-package-s4-') as temp:
        block = (ROOT / 'packaging/emaki-installer/PKGBUILD').read_text().split('# --- installer window ---')[1].split('# --- end installer window ---')[0]
        checked(['bash', '-c', 'set -eu\nstage() {\n' + block + '\n}\nstage\n'], cwd=ROOT, env=dict(os.environ, pkgdir=temp))
        destination = Path(temp) / 'usr/share/emaki-installer/ui'
        for name in ['shell.qml', 'InstallerController.qml', 'InstallerView.qml', 'GlassPane.qml', 'Protocol.js', 'ui-helper.py']:
            assert (destination / name).read_bytes() == (UI / name).read_bytes()
        assert (destination / 'shaders').is_symlink()
        assert os.access(Path(temp) / 'usr/bin/emaki-install', os.X_OK)
        assert (Path(temp) / 'usr/share/applications/emaki-install.desktop').exists()
    print('PASS isolated staging of only the installer-window package block')


if __name__ == '__main__': main()
