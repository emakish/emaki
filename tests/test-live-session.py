#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""ISO policy with an isolated marker and command fakes; never touch the host session."""
from runtime_fixture import runtime_path
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MARKER = '/etc/emaki-live/greetd.toml'
sys.path.insert(0, str(ROOT / 'shell/helpers'))


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class LiveSession(unittest.TestCase):
    def test_lock_refuses_before_any_runtime_or_process_access(self):
        lock = load('live_lock', 'scripts/emaki-lock')
        with patch.object(lock.Path, 'is_file', return_value=True), patch.object(lock, 'runtime_directory') as runtime:
            self.assertEqual(lock.main(), 1)
            runtime.assert_not_called()

    def test_guard_does_not_acquire_inhibitors(self):
        guard = load('live_guard', 'scripts/emaki-sleep-guard')
        with patch.object(guard.Path, 'is_file', return_value=True), patch.object(guard.GLib, 'MainLoop') as loop:
            self.assertEqual(guard.main(), 0)
            loop.assert_not_called()
        self.assertIn('ConditionPathExists=!' + MARKER,
                      (ROOT / 'systemd/emaki-sleep-guard.service').read_text())

    def test_panel_helper_skips_live_lock_but_preserves_installed_lock(self):
        helper = load('live_system', 'shell/helpers/system-tools.py')
        with patch.object(helper.Path, 'is_file', return_value=True), patch.object(helper, 'run') as run:
            self.assertEqual(helper.lock()['state'], 'unavailable')
            self.assertEqual(helper.operation(dict(op='session', value='suspend', confirmed=True))['state'], 'requested')
            run.assert_called_once_with('systemctl', ['suspend'])
        with patch.object(helper.Path, 'is_file', return_value=False), patch.object(helper, 'lock', return_value=dict(state='lock_failed')) as lock, patch.object(helper, 'run') as run:
            self.assertEqual(helper.operation(dict(op='session', value='suspend', confirmed=True))['state'], 'lock_failed')
            lock.assert_called_once_with(prepare_sleep=True)
            run.assert_not_called()

    def test_live_shell_power_and_idle(self):
        with tempfile.TemporaryDirectory(prefix='emaki-live-') as temp:
            base = Path(temp)
            marker = base / 'live-marker'
            log = base / 'calls'
            shell = base / 'shell'
            shell.mkdir()
            binary = base / 'emaki'
            binary.write_text('fixture core\n')
            env = dict(os.environ, PATH=str(base) + ':' + os.environ['PATH'],
                       XDG_RUNTIME_DIR=str(runtime_path(temp)), EMAKI_LIVE_SESSION='1',
                       EMAKI_SHELL_DIR=str(shell), EMAKI_BIN=str(binary))
            commands = {
                'emaki-config-path': 'echo /unused',
                'emaki-qt-check': 'exit 0',
                'busctl': 'echo no',
                'fuzzel': f'cat > "{base}/menu"; echo Sleep',
                'systemctl': f'echo "$*" >> "{log}"',
                'emaki-lock': f'echo lock >> "{log}"',
                'swayidle': f'printf "%s\\n" "$@" > "{base}/idle"',
                'qs': f'echo "$EMAKI_LIVE_SESSION" > "{base}/flag"',
                'emaki-shell-health': '[ "$1" = record ] || exit 1; printf "%s\\n" "$2"',
                # Generation copying has its own suite; preserve its launch
                # contract here without reading the host package database.
                'emaki-session-files': f'''[ "$#" = 4 ] && [ "$1" = prepare ] || exit 1
[ "$2" = "$EMAKI_SHELL_DIR" ] && [ "$3" = "$EMAKI_BIN" ] || exit 1
kill -0 "$4" || exit 1
echo prepare > "{base}/generation"
printf "%s\\n" "$2"''',
            }
            for name, content in commands.items():
                target = base / name
                target.write_text('#!/bin/sh\n' + content + '\n')
                target.chmod(0o700)
            (base / 'paths').write_text((ROOT / 'scripts/paths').read_text())
            for name in ('emaki-power', 'emaki-idle', 'emaki-shell'):
                (base / ('test-' + name)).write_text((ROOT / 'scripts' / name).read_text().replace(MARKER, str(marker)))
            for live in (False, True):
                marker.touch() if live else marker.unlink(missing_ok=True)
                log.unlink(missing_ok=True)
                (base / 'generation').unlink(missing_ok=True)
                for name in ('emaki-power', 'emaki-idle', 'emaki-shell'):
                    subprocess.run(['sh', str(base / ('test-' + name))], env=env, check=True, capture_output=True)
                self.assertEqual('Lock' in (base / 'menu').read_text().splitlines(), not live)
                self.assertEqual('emaki-lock' in (base / 'idle').read_text(), not live)
                self.assertEqual(log.read_text().splitlines(), ['suspend'] if live else ['lock', 'suspend'])
                self.assertEqual((base / 'flag').read_text().strip(), '1' if live else '0')
                self.assertEqual((base / 'generation').read_text().strip(), 'prepare')
            # A full or unwritable runtime directory must not keep the panel from starting.
            (base / 'emaki-shell-health').write_text('#!/bin/sh\nexit 1\n')
            (base / 'flag').unlink()
            subprocess.run(['sh', str(base / 'test-emaki-shell')], env=env, check=True, capture_output=True)
            self.assertTrue((base / 'flag').exists())

    def test_live_welcome_blocks_startup_and_explicit_open(self):
        with tempfile.TemporaryDirectory(prefix='emaki-live-welcome-') as temp:
            base = Path(temp)
            fixture = base / 'welcome.qml'
            fixture.write_text('''import QtQuick
import Quickshell
import "''' + (ROOT / 'shell').as_uri() + '''" as Shell
ShellRoot {
    Shell.WelcomeController { id: welcome; ready: true; loadWallpaper: false }
    Timer {
        interval: 800; running: true
        onTriggered: {
            const passed = welcome.store.restored && !welcome.opened && welcome.present() === "unavailable" && !welcome.store.seen;
            console.log(passed ? "LIVE_WELCOME_PASS" : "LIVE_WELCOME_FAIL");
            Qt.exit(passed ? 0 : 1);
        }
    }
}
''')
            env = dict(os.environ, HOME=temp, XDG_STATE_HOME=temp,
                       XDG_RUNTIME_DIR=str(runtime_path(temp)), EMAKI_LIVE_SESSION='1',
                       QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                       QS_DISABLE_CRASH_HANDLER='1')
            result = subprocess.run(['qs', '-p', str(fixture), '--no-color'], env=env,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('LIVE_WELCOME_PASS', result.stdout + result.stderr)
            self.assertFalse((base / 'emaki/welcome.json').exists())

    def test_panel_hides_live_lock(self):
        qml = (ROOT / 'shell/SystemBody.qml').read_text()
        self.assertIn('visible: Quickshell.env("EMAKI_LIVE_SESSION") !== "1"\n                        key: "act-lock"', qml)


if __name__ == '__main__':
    unittest.main()
