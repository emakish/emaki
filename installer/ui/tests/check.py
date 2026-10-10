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


def check_crash_handler_disabled():
    # Run the real launcher against a stub qs that records the environment it receives.
    with tempfile.TemporaryDirectory(prefix='installer-qs-') as temporary:
        root = Path(temporary)
        (root / 'bin').mkdir()
        stub = root / 'bin/qs'
        stub.write_text('#!/bin/sh\nprintf %s "${QS_DISABLE_CRASH_HANDLER-unset}" > "$QS_STUB_OUT"\n')
        stub.chmod(0o755)
        env = {key: value for key, value in os.environ.items() if key != 'QS_DISABLE_CRASH_HANDLER'}
        env.update(PATH=f'{root / "bin"}:/usr/bin', XDG_RUNTIME_DIR=str(root), QS_STUB_OUT=str(root / 'out'))
        checked(['bash', str(UI / 'emaki-install')], env=env)
        assert (root / 'out').read_text() == '1', (root / 'out').read_text()
    print('PASS crash handler: emaki-install starts qs with QS_DISABLE_CRASH_HANDLER=1')


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


def check_window_corners():
    import tomllib
    tokens = tomllib.loads((ROOT / 'tokens.toml').read_text())
    theme = (ROOT / 'niri/theme.kdl').read_text()
    pane = (UI / 'GlassPane.qml').read_text()
    radius = int(re.search(r'geometry-corner-radius (\d+)', theme)[1])
    assert radius == tokens['geometry']['radius']
    assert 'clip-to-geometry true' in theme
    assert f'readonly property real cornerRadius: {radius}' in pane
    assert 'radius: root.cornerRadius' in pane
    assert 'uRadius: root.cornerRadius' in pane
    print('PASS installer glass matches compositor corner geometry')


def required_tool(name, fallback=None):
    found = shutil.which(name)
    if not found and fallback and Path(fallback).is_file() and os.access(fallback, os.X_OK):
        found = fallback
    if not found:
        raise RuntimeError(f'Required check tool is unavailable [{name}]')
    return found


def main():
    lint = required_tool('qmllint', '/usr/lib/qt6/bin/qmllint')
    formatter = required_tool('qmlformat', '/usr/lib/qt6/bin/qmlformat')
    shellcheck = required_tool('shellcheck')
    desktop_validate = required_tool('desktop-file-validate')
    check_window_corners()
    qmlfiles = sorted(UI.glob('*.qml')) + sorted((UI / 'tests').glob('*.qml'))
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
    for filename in qmlfiles:
        assert checked([formatter, '--ignore-settings', str(filename)]).stdout == filename.read_text(), filename
    print('PASS qmlformat')
    checked(['bash', '-n', str(UI / 'emaki-install'), str(ROOT / 'packaging/emaki-installer/PKGBUILD')])
    print('PASS bash -n launcher and PKGBUILD')
    check_reopen_contract()
    check_crash_handler_disabled()
    check_icon()
    checked([shellcheck, str(UI / 'emaki-install')])
    print('PASS shellcheck launcher')
    checked([desktop_validate, str(UI / 'emaki-install.desktop')])
    print('PASS desktop-file-validate')
    with tempfile.TemporaryDirectory(prefix='emaki-installer-package-') as temp:
        block = (ROOT / 'packaging/emaki-installer/PKGBUILD').read_text().split('# --- installer window ---')[1].split('# --- end installer window ---')[0]
        checked(['bash', '-c', 'set -eu\nstage() {\n' + block + '\n}\nstage\n'], cwd=ROOT, env=dict(os.environ, pkgdir=temp))
        destination = Path(temp) / 'usr/share/emaki-installer/ui'
        for name in ['shell.qml', 'InstallerController.qml', 'InstallerView.qml', 'GlassPane.qml', 'TimezoneMap.qml', 'Protocol.js', 'Timezones.js', 'ui-helper.py']:
            assert (destination / name).read_bytes() == (UI / name).read_bytes()
        for name in ['ZoneData.js', 'timezone-map.png', 'README.md', 'ODbL-1.0.txt', 'generate-timezones.py']:
            assert (destination / 'assets' / name).read_bytes() == (UI / 'assets' / name).read_bytes()
        assert (destination / 'shaders').is_symlink()
        assert os.access(Path(temp) / 'usr/bin/emaki-install', os.X_OK)
        assert (Path(temp) / 'usr/share/applications/emaki-install.desktop').exists()
        icon = Path(temp) / 'usr/share/icons/hicolor/scalable/apps/emaki-install.svg'
        assert icon.read_bytes() == (UI / 'emaki-install.svg').read_bytes()
    print('PASS isolated staging of only the installer-window package block')


if __name__ == '__main__': main()
