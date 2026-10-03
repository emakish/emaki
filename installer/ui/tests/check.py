#!/usr/bin/env python3
"""Installer-specific lint, syntax and packaging checks; no system installation."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

UI = Path(__file__).resolve().parents[1]
ROOT = UI.parents[1]
# `qs ipc` subcommands in Quickshell 0.3.1 (src/launch/parsecommand.cpp). An IpcHandler function
# with one of these names cannot be called: `qs ipc call installer show` runs `qs ipc show`.
QS_IPC_SUBCOMMANDS = {'show', 'call', 'wait', 'listen', 'prop'}


def check_reopen_contract():
    shell = (UI / 'shell.qml').read_text()
    handler = shell[shell.index('IpcHandler {'):]
    functions = set(re.findall(r'function (\w+)\(', handler))
    assert functions and not functions & QS_IPC_SUBCOMMANDS, functions
    launcher = (UI / 'emaki-install').read_text()
    called = re.findall(r'qs ipc -p "\$config" call installer (\w+)', launcher)
    assert called and set(called) <= functions, called
    # The launcher trusts only the function's own reply, never the exit status of `qs ipc`.
    reply = re.search(r'return root\.present\(\) \? "(\w+)"', shell)[1]
    assert f'== {reply} ]]' in launcher, reply
    assert 'qs -p "$config" 9>&-' in launcher and 'exec qs' not in launcher, 'the lock must stay out of qs'
    print('PASS reopen contract: IPC names, reply token, lock not inherited by qs')


def check_icon():
    checked([sys.executable, str(ROOT / 'art/icons/emaki-install.py'), '--check'])
    svg = ET.parse(UI / 'emaki-install.svg').getroot()
    assert svg.get('viewBox') == '0 0 16 16' and svg.get('shape-rendering') == 'crispEdges', svg.attrib
    rects = list(svg)
    assert rects and all(r.tag == '{http://www.w3.org/2000/svg}rect' for r in rects)
    for rect in rects:
        x, y, width, height = (int(rect.get(k)) for k in ('x', 'y', 'width', 'height'))
        assert 0 <= x < x + width <= 16 and 0 <= y < y + height <= 16, rect.attrib
    desktop = (UI / 'emaki-install.desktop').read_text().splitlines()
    assert 'Icon=emaki-install' in desktop, desktop
    print(f'PASS icon: generated, 16×16 whole-pixel rects ({len(rects)}), crispEdges, desktop Icon=emaki-install')


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
    check_reopen_contract()
    check_icon()
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
        icon = Path(temp) / 'usr/share/icons/hicolor/scalable/apps/emaki-install.svg'
        assert icon.read_bytes() == (UI / 'emaki-install.svg').read_bytes()
    print('PASS isolated staging of only the installer-window package block')


if __name__ == '__main__': main()
