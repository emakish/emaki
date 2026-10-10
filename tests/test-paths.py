#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Install paths across the renderer, language modules and staged text payload."""
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest

import reaper
from runtime_fixture import short_runtime

ROOT = Path(__file__).resolve().parent.parent
RENDER = runpy.run_path(str(ROOT / 'scripts/render-paths'))
HEADER = '# Copyright (C) 2026 Artur Yakymenko\n# SPDX-License-Identifier: GPL-3.0-or-later\n'


class Paths(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='emaki-paths-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.values = RENDER['paths']('/tmp/x', '/opt/emaki-data', '/opt/emaki-tools')

    def command(self, args, **kwargs):
        result = subprocess.run(args, text=True, capture_output=True, timeout=60, **kwargs)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def test_defaults_and_independent_overrides(self):
        default = RENDER['paths']()
        self.assertEqual(default['DATADIR'], '/usr/share/emaki')
        self.assertEqual(default['LIBEXECDIR'], '/usr/libexec/emaki')
        self.assertEqual(self.values['PREFIX'], '/tmp/x')
        self.assertEqual(self.values['DATADIR'], '/opt/emaki-data')
        self.assertEqual(self.values['LIBEXECDIR'], '/opt/emaki-tools')
        self.assertEqual(self.values['BINDIR'], '/tmp/x/bin')
        self.assertEqual(self.values['LIBDIR'], '/tmp/x/lib/emaki')
        self.assertEqual(RENDER['paths']('/tmp/x')['DATADIR'], '/tmp/x/share/emaki')
        # Other packages' files and system defaults do not move with Emaki's prefix.
        for key in RENDER['SYSTEM']:
            with self.subTest(key=key):
                self.assertEqual(self.values[key], default[key])
                self.assertTrue(default[key].startswith('/usr/'), default[key])
        self.assertEqual(self.values['TRUSTED_PATH'], '/usr/bin')
        self.assertEqual(self.values['XDG_DATA_DIRS_DEFAULT'], '/usr/local/share:/usr/share')
        self.assertEqual(RENDER['paths']('/tmp/x', system={'TRUSTED_PATH': '/run/sw/bin'})['TRUSTED_PATH'],
                         '/run/sw/bin')

    def test_installed_modules_are_readable_under_a_strict_umask(self):
        root = self.base / 'strict'
        inventory = self.base / 'empty.list'
        inventory.write_text('')
        for attempt in ('new', 'unchanged'):
            with self.subTest(attempt=attempt):
                self.command([sys.executable, str(ROOT / 'scripts/render-paths'), '--prefix', '/tmp/x',
                              '--install', str(root), '--inventory', str(inventory)], umask=0o077)
                for name in ('tmp/x/libexec/emaki/paths', 'tmp/x/libexec/emaki/emaki_paths.py',
                             'tmp/x/share/emaki/shell/Platform.qml',
                             'tmp/x/share/emaki/shell/helpers/emaki_paths.py'):
                    path = root / name
                    self.assertEqual(path.stat().st_mode & 0o777, 0o644, name)
                    for directory in path.relative_to(root).parents[:-1]:
                        self.assertEqual((root / directory).stat().st_mode & 0o777, 0o755, directory)
                if attempt == 'new':
                    (root / 'tmp/x/libexec/emaki/paths').chmod(0o600)
                    (root / 'tmp/x/libexec/emaki/emaki_paths.py').chmod(0o600)

    def test_rejects_relative_and_unsafe_paths_before_writing(self):
        unsafe = ['relative', '/tmp/two words', '/tmp/new\nline', '/tmp/a"b',
                  "/tmp/a'b", '/tmp/$HOME', '/tmp/`id`', '/tmp/a;b',
                  '/tmp/a|b', '/tmp/a&b', '/tmp/a>b', '/tmp/a<b', '/tmp/a\\b']
        for argument in ('--prefix', '--datadir', '--libexecdir', '--trusted-path'):
            for value in unsafe:
                with self.subTest(argument=argument, value=value):
                    target = self.base / 'rejected'
                    result = subprocess.run(
                        [sys.executable, str(ROOT / 'scripts/render-paths'),
                         '--source', str(target), argument, value],
                        text=True, capture_output=True, timeout=10)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('must be an absolute path', result.stderr)
                    self.assertFalse(target.exists())

    def test_one_pass_substitution_and_repeated_install(self):
        text = ('/usr/share/emaki/shell /usr/share/emaki-update-manager/ui '
                '/usr/libexec/emaki/helper /usr/bin/emaki-shell @EMAKI_DATADIR@ '
                '/usr/share/unrelated')
        expected = ('/opt/emaki-data/shell /tmp/x/share/emaki-update-manager/ui '
                    '/opt/emaki-tools/helper /tmp/x/bin/emaki-shell /opt/emaki-data '
                    '/usr/share/unrelated')
        rendered = RENDER['substitute'](text, self.values)
        self.assertEqual(rendered, expected)
        self.assertEqual(RENDER['substitute'](rendered, self.values), expected)
        nested = RENDER['paths']('/tmp/x', '/usr/share/emaki/nested')
        self.assertEqual(RENDER['substitute']('@EMAKI_DATADIR@/shell', nested),
                         '/usr/share/emaki/nested/shell')
        self.assertEqual(RENDER['substitute']('/usr/share/emaki/nested/shell', nested),
                         '/usr/share/emaki/nested/shell')

    def test_install_preserves_unowned_files_binary_symlinks_and_modes(self):
        root = self.base / 'stage'
        directory = root / 'opt/emaki-data'
        directory.mkdir(parents=True)
        owned = directory / 'owned'
        owned.write_text('/usr/share/emaki/shell @EMAKI_LIBEXECDIR@\n')
        owned.chmod(0o751)
        binary = directory / 'binary'
        binary.write_bytes(b'\0/usr/share/emaki\xff')
        nul = directory / 'nul'
        nul.write_bytes(b'\0/usr/share/emaki')
        unowned = directory / 'personal'
        unowned.write_text('/usr/share/emaki/personal')
        linked = directory / 'linked'
        linked.symlink_to(unowned)
        inventory = self.base / 'inventory'
        inventory.write_text('\n'.join('/usr/share/emaki/' + name
                                       for name in ('owned', 'binary', 'nul', 'linked', 'missing')))
        for _ in range(2):
            RENDER['render_install'](root, self.values, inventory)
        self.assertEqual(owned.read_text(), '/opt/emaki-data/shell /opt/emaki-tools\n')
        self.assertEqual(owned.stat().st_mode & 0o777, 0o751)
        self.assertEqual(binary.read_bytes(), b'\0/usr/share/emaki\xff')
        self.assertEqual(nul.read_bytes(), b'\0/usr/share/emaki')
        self.assertEqual(unowned.read_text(), '/usr/share/emaki/personal')
        self.assertTrue(linked.is_symlink())
        self.assertFalse((directory / 'missing').exists())

    def check_python(self, path, values):
        code = ('import json, sys; sys.path.insert(0, sys.argv[1]); '
                'import emaki_paths; print(json.dumps({key: getattr(emaki_paths, key) '
                'for key in ("PREFIX", "DATADIR", "LIBEXECDIR", "BINDIR")}))')
        output = self.command([sys.executable, '-IB', '-c', code, str(path.parent)], cwd='/')
        self.assertEqual(json.loads(output), {key: values[key] for key in
                                              ('PREFIX', 'DATADIR', 'LIBEXECDIR', 'BINDIR')})

    def check_shell(self, path, values):
        output = self.command(['sh', '-eu', '-c',
                               '. "$1"; printf "%s\\n" "$EMAKI_PREFIX" "$EMAKI_DATADIR" '
                               '"$EMAKI_LIBEXECDIR" "$EMAKI_BINDIR"', 'paths-test', str(path)])
        self.assertEqual(output.splitlines(), [values[key] for key in
                                               ('PREFIX', 'DATADIR', 'LIBEXECDIR', 'BINDIR')])

    def check_qml(self, module, values):
        self.assertTrue(shutil.which('qs'), 'qs is required for the Platform singleton test')
        fixture = self.base / 'qml'
        fixture.mkdir(exist_ok=True)
        shutil.copyfile(module, fixture / 'Platform.qml')
        (fixture / 'qmldir').write_text('singleton Platform 1.0 Platform.qml\n')
        (fixture / 'shell.qml').write_text(
            '// Copyright (C) 2026 Artur Yakymenko\n'
            '// SPDX-License-Identifier: GPL-3.0-or-later\n'
            'import QtQuick\nimport Quickshell\nimport "."\n'
            'Timer { interval: 1; running: true; onTriggered: {\n'
            'console.log("PATHS_RESULT " + JSON.stringify([Platform.prefix, Platform.dataDir, '
            'Platform.libexecDir, Platform.binDir, Platform.python])); Qt.quit();\n} }\n')
        with short_runtime() as runtime:
            env = dict(os.environ, XDG_RUNTIME_DIR=runtime, QT_QPA_PLATFORM='offscreen',
                       QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
                       XDG_CACHE_HOME=str(self.base / 'cache'),
                       DBUS_SESSION_BUS_ADDRESS='unix:path=' + runtime + '/no-bus')
            for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'EMAKI_PYTHON'):
                env.pop(key, None)
            for interpreter in ('python3', 'fixture-python'):
                if interpreter != 'python3':
                    env['EMAKI_PYTHON'] = interpreter
                output = self.command(['qs', '-p', str(fixture / 'shell.qml'), '--no-color'], env=env)
                match = re.search(r'PATHS_RESULT (\[[^\n]+\])', output)
                self.assertIsNotNone(match, output)
                self.assertEqual(json.loads(match[1]),
                                 [values[key] for key in ('PREFIX', 'DATADIR', 'LIBEXECDIR', 'BINDIR')]
                                 + [interpreter])

    def test_source_modules_and_isolated_helper(self):
        RENDER['render_source'](self.base, self.values)
        self.check_python(self.base / 'scripts/emaki_paths.py', self.values)
        self.check_python(self.base / 'shell/helpers/emaki_paths.py', self.values)
        self.check_shell(self.base / 'scripts/paths', self.values)
        self.check_qml(self.base / 'shell/Platform.qml', self.values)
        helper = self.base / 'shell/helpers/shortcuts.py'
        shutil.copyfile(ROOT / 'shell/helpers/shortcuts.py', helper)
        config = self.base / 'shortcuts.kdl'
        config.write_text('binds { Mod+Return { spawn "terminal"; }; }\n')
        output = self.command([sys.executable, '-IB', str(helper), '--config', str(config)], cwd='/')
        self.assertEqual(json.loads(output)['state'], 'ready')

    def test_staged_install_text_and_modules(self):
        # Only the text payload is under test here; delivery checks build the real
        # core and shaders. A fresh executable avoids rebuilding unrelated code.
        core = self.base / 'core'
        core.write_text('#!/bin/sh\n' + HEADER + 'exit 0\n')
        core.chmod(0o755)
        legacy = re.compile(r'/usr/(?:share/emaki|libexec/emaki|lib/emaki|bin/emaki|bin/niri-emaki-session)')
        for custom in (False, True):
            with self.subTest(custom_directories=custom):
                values = self.values if custom else RENDER['paths']('/tmp/x')
                stage = self.base / ('custom' if custom else 'prefix')
                args = ['make', '--no-print-directory', 'install', 'install-niri-emaki',
                        'PREFIX=/tmp/x', 'DESTDIR=' + str(stage),
                        'CORE_BIN=' + str(core), 'SHADERS=']
                if custom:
                    args += ['DATADIR=' + values['DATADIR'], 'LIBEXECDIR=' + values['LIBEXECDIR']]
                self.command(args, cwd=ROOT)
                failures = []
                count = 0
                for path in stage.rglob('*'):
                    if not path.is_file() or path.is_symlink():
                        continue
                    try:
                        text = path.read_text()
                    except UnicodeError:
                        continue
                    if '\0' in text:
                        continue
                    count += 1
                    if legacy.search(text) or '@EMAKI_' in text:
                        failures.append(str(path.relative_to(stage)))
                self.assertGreater(count, 100, 'a real package text payload must be installed')
                self.assertEqual(failures, [], 'unrendered installed files')
                libexec = stage / values['LIBEXECDIR'].lstrip('/')
                data = stage / values['DATADIR'].lstrip('/')
                self.check_python(libexec / 'emaki_paths.py', values)
                self.check_python(data / 'shell/helpers/emaki_paths.py', values)
                self.check_shell(libexec / 'paths', values)
                self.check_qml(data / 'shell/Platform.qml', values)
                output = self.command([sys.executable, '-IB', str(data / 'shell/helpers/shortcuts.py')], cwd='/')
                self.assertEqual(json.loads(output)['state'], 'ready')
                self.assertIn(values['DATADIR'], (stage / 'etc/niri/config.kdl').read_text())
                self.assertTrue((stage / 'tmp/x/bin/niri-emaki-session').is_file())


if __name__ == '__main__':
    reaper.guard()
    unittest.main()
