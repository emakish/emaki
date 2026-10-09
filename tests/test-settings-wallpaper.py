#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Wallpaper backend contract with an isolated fake daemon command."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'scripts/emaki-settings-wallpaper'


def load(path):
    loader = importlib.machinery.SourceFileLoader(path.name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class Wallpaper(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root / 'chosen image.png'
        self.image.write_bytes(b'fake image')
        self.old = {'DP-1': '/old/one.png', 'DP-2': '/old/two.png'}
        self.state = self.root / 'state.json'
        self.state.write_text(json.dumps(self.old))
        fake = self.root / 'wpaperctl'
        fake.write_text('''#!/usr/bin/python3
import json, os, pathlib, sys
p = pathlib.Path(os.environ['FAKE_STATE'])
s = json.loads(p.read_text())
if sys.argv[1] == 'get-all':
 print(json.dumps([{'display': k, 'path': v} for k,v in s.items()]))
elif os.environ.get('FAKE_IGNORE') != '1':
 for k in sys.argv[3:] or s:
  s[k] = sys.argv[2]
 p.write_text(json.dumps(s))
''')
        fake.chmod(0o700)
        self.env = dict(os.environ, PATH=str(self.root), FAKE_STATE=str(self.state),
                        XDG_CONFIG_HOME=str(self.root / 'config'))

    def run_helper(self, *args, **env):
        return subprocess.run([str(HELPER), *args], env=dict(self.env, **env),
                              text=True, capture_output=True, timeout=10)

    def test_snapshot_apply_restore(self):
        before = self.run_helper('snapshot')
        self.assertEqual(json.loads(before.stdout), self.old)
        self.assertEqual(self.run_helper('apply', str(self.image)).returncode, 0)
        self.assertEqual(json.loads(self.state.read_text()), dict.fromkeys(self.old, str(self.image)))
        self.assertEqual(self.run_helper('restore', before.stdout).returncode, 0)
        self.assertEqual(json.loads(self.state.read_text()), self.old)

    def test_acknowledgement_without_change_fails(self):
        result = self.run_helper('apply', str(self.image), FAKE_IGNORE='1')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(self.state.read_text()), self.old)

    def test_reset_reads_personal_static_configuration(self):
        config = self.root / 'config/wpaperd/config.toml'
        config.parent.mkdir(parents=True)
        config.write_text('[default]\npath = ' + json.dumps(str(self.image)))
        self.assertEqual(self.run_helper('apply', '').returncode, 0)
        self.assertEqual(json.loads(self.state.read_text()), dict.fromkeys(self.old, str(self.image)))
        config.write_text('[default]\npath = ' + json.dumps(str(self.root)))
        self.assertEqual(self.run_helper('apply', '').returncode, 1)

    def test_unavailable_daemon_fails(self):
        (self.root / 'wpaperctl').unlink()
        self.assertEqual(self.run_helper('snapshot').returncode, 1)

    def test_texture_uses_managed_image_but_published_reader_does_not(self):
        from PIL import Image
        module = load(ROOT / 'shell/helpers/wallpaper.py')
        Image.new('RGB', (16, 16), 'red').save(self.image)
        config = self.root / 'config/emaki/settings.toml'
        config.parent.mkdir(parents=True)
        config.write_text('schema_version = 1\n[appearance]\nwallpaper = ' + json.dumps(str(self.image)))
        with patch.dict(os.environ, dict(self.env, XDG_CACHE_HOME=str(self.root / 'cache')), clear=True):
            self.assertEqual(module.texture(16, 16, 1, 'DP-1')['state'], 'ready')
            with patch.dict(os.environ, EMAKI_GREETER_WALLPAPER_ROOT=str(self.root / 'published')):
                self.assertEqual(module.texture(16, 16, 1, 'DP-1')['state'], 'config_missing')

    def test_bad_settings_start_plain_wallpaper(self):
        module = load(ROOT / 'scripts/emaki-session-wallpaper')
        config = self.root / 'config/emaki/settings.toml'
        config.parent.mkdir(parents=True)
        for content in ('broken = [', 'schema_version = 1\n[appearance]\nwallpaper = "/missing"',
                        'schema_version = 1\nappearance = 7'):
            with self.subTest(content=content):
                config.write_text(content)
                claim = self.root / 'claim'
                claim.write_bytes(b'')
                fd = os.open(claim, os.O_RDWR)
                with patch.dict(os.environ, self.env, clear=True), patch.object(module, 'claim_file', return_value=fd), patch('os.execvpe') as execute:
                    self.assertEqual(module.main(), 0)
                    execute.assert_called_once_with('wpaperd', ['wpaperd'], self.env)

    def test_startup_selects_generation_and_keeps_personal_file(self):
        module = load(ROOT / 'scripts/emaki-session-wallpaper')
        config = self.root / 'config/emaki/settings.toml'
        config.parent.mkdir(parents=True)
        state = self.root / 'managed-state'
        generated = state / 'emaki/generations/g-1-2-3/wpaperd.toml'
        generated.parent.mkdir(parents=True)
        generated.write_text('[default]\npath = ' + json.dumps(str(self.image)))
        config.write_text('schema_version = 1\ngeneration = "g-1-2-3"\n[appearance]\nwallpaper = ' + json.dumps(str(self.image)))
        env = dict(self.env, XDG_STATE_HOME=str(state))
        self.assertEqual(module.wallpaper_arguments(env), ['wpaperd', '--config', str(generated)])
        config.write_text('schema_version = 1\n')
        self.assertEqual(module.wallpaper_arguments(env), ['wpaperd'])
        config.write_text('schema_version = 1\ngeneration = "../escape"\n[appearance]\nwallpaper = ' + json.dumps(str(self.image)))
        with self.assertRaises(ValueError):
            module.wallpaper_arguments(env)


if __name__ == '__main__':
    unittest.main()
