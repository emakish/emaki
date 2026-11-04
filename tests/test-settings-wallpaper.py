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
import sys
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

    def test_bad_settings_start_default_wallpaper(self):
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
                    execute.assert_called_once_with('wpaperd', module.default_arguments(self.env), self.env)

    def test_ring_startup_skips_daemon_without_claiming_it(self):
        module = load(ROOT / 'scripts/emaki-session-wallpaper')
        env = dict(self.env, NIRI_SOCKET='/fixture/socket')
        from types import SimpleNamespace
        def status(mode, state):
            return SimpleNamespace(returncode=0, stdout=json.dumps(dict(mode=mode, load_state=state)))
        for mode in ('ring', 'strip'):
            with patch.dict(os.environ, env, clear=True), patch.object(module, 'claim_file') as claim, patch.object(module.subprocess, 'run', side_effect=[status(mode, 'loading'), status(mode, 'ready')]), patch.object(module.time, 'sleep'):
                self.assertEqual(module.main(), 0)
                claim.assert_not_called()
        for mode, state in (('image', 'image'), ('ring', 'failed'), ('strip', 'failed')):
            with patch.object(module.subprocess, 'run', return_value=status(mode, state)):
                self.assertFalse(module.compositor_draws_wallpaper(env))
        with patch.object(module.subprocess, 'run', side_effect=FileNotFoundError()):
            self.assertFalse(module.compositor_draws_wallpaper(env))
        self.assertFalse(module.compositor_draws_wallpaper({}))
        claim_path = self.root / 'forced-claim'
        claim_path.write_bytes(b'')
        descriptor = os.open(claim_path, os.O_RDWR)
        with patch.dict(os.environ, env, clear=True), patch.object(module, 'claim_file', return_value=descriptor), patch.object(module, 'compositor_draws_wallpaper') as query, patch.object(module.os, 'execvpe') as execute:
            self.assertEqual(module.main(force=True), 0)
            query.assert_not_called()
            execute.assert_called_once_with('wpaperd', module.default_arguments(env), env)

    def test_settings_start_image_daemon_on_demand(self):
        module = load(HELPER)
        response = json.dumps([dict(display=key, path=value) for key, value in self.old.items()])
        with patch.object(module, 'command', side_effect=[ValueError('wallpaper_command_failed'), ValueError('wallpaper_command_failed'), response]), patch.object(module.subprocess, 'run') as start, patch.object(module.time, 'sleep'):
            self.assertEqual(module.snapshot(), self.old)
            self.assertEqual(start.call_count, 1)
            # The compositor, not the asking shell service, owns the started daemon.
            self.assertEqual(start.call_args.args[0], ['emaki-session-wallpaper', '--spawn'])
        with patch.object(module, 'command', return_value=response), patch.object(module.subprocess, 'run') as start:
            self.assertEqual(module.snapshot(), self.old)
            start.assert_not_called()

    def test_snapshot_retries_empty_paths_until_all_outputs_are_ready(self):
        module = load(HELPER)
        ready = json.dumps([dict(display=key, path=value) for key, value in self.old.items()])
        empty = json.dumps([dict(display=key, path='') for key in self.old])
        partial = json.dumps([dict(display='DP-1', path=self.old['DP-1']),
                              dict(display='DP-2', path='')])
        for first, starts in ((ValueError('wallpaper_command_failed'), 1), (empty, 0)):
            with self.subTest(starts=starts), patch.object(module, 'command', side_effect=[first, empty, partial, ready]) as query, patch.object(module.subprocess, 'run') as start, patch.object(module.time, 'sleep') as sleep:
                self.assertEqual(module.snapshot(), self.old)
                self.assertEqual(query.call_count, 4)
                self.assertEqual(start.call_count, starts)
                self.assertEqual(sleep.call_count, 3)

    def test_snapshot_empty_paths_exhaust_bounded_retry(self):
        module = load(HELPER)
        empty = json.dumps([dict(display='DP-1', path='')])
        for first, starts in ((ValueError('wallpaper_command_failed'), 1), (empty, 0)):
            with self.subTest(starts=starts), patch.object(module, 'command', side_effect=[first] + [empty] * 30) as query, patch.object(module.subprocess, 'run') as start, patch.object(module.time, 'sleep') as sleep:
                with self.assertRaisesRegex(ValueError, '^wallpaper_not_ready$'):
                    module.snapshot()
                self.assertEqual(query.call_count, 31)
                self.assertEqual(start.call_count, starts)
                self.assertEqual(sleep.call_count, 30)
                self.assertTrue(all(call.args == (.1,) for call in sleep.call_args_list))

    def test_snapshot_invalid_payload_is_not_retried(self):
        module = load(HELPER)
        invalid = [None, {}, [], [dict(display='DP-1', path='relative')],
                   [dict(display='DP-1', path=None)], [dict(display='DP-1', path=[])],
                   [dict(display='DP-1', path='/ok', extra=True)],
                   [dict(display='-option', path='')],
                   [dict(display='DP-1', path=''), dict(display='DP-1', path='/ok')],
                   [dict(display='DP-1', path=''), dict(display='DP-2', path='relative')],
                   [dict(display='DP-1', path=''), dict(display='DP-2', path='/bad\0path')]]
        for payload in ['{'] + [json.dumps(value) for value in invalid]:
            for spawned in (False, True):
                replies = ([ValueError('wallpaper_command_failed')] if spawned else []) + [payload]
                with self.subTest(payload=payload, spawned=spawned), patch.object(module, 'command', side_effect=replies) as query, patch.object(module.subprocess, 'run') as start, patch.object(module.time, 'sleep') as sleep:
                    with self.assertRaises(ValueError):
                        module.snapshot()
                    self.assertEqual(query.call_count, 1 + int(spawned))
                    self.assertEqual(start.call_count, int(spawned))
                    self.assertEqual(sleep.call_count, int(spawned))

    def test_spawn_asks_the_compositor_and_falls_back_to_a_detached_start(self):
        from types import SimpleNamespace
        module = load(ROOT / 'scripts/emaki-session-wallpaper')
        env = dict(self.env, NIRI_SOCKET='/fixture/socket')
        with patch.object(module.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as ask, patch.object(module.subprocess, 'Popen') as start:
            self.assertEqual(module.spawn(env), 0)
            self.assertEqual(ask.call_args.args[0], ['niri-emaki', 'msg', 'action', 'spawn', '--', 'emaki-session-wallpaper', '--force'])
            start.assert_not_called()
        for answer in (SimpleNamespace(returncode=1), FileNotFoundError(), subprocess.TimeoutExpired('niri-emaki', 1)):
            with patch.object(module.subprocess, 'run', side_effect=[answer]), patch.object(module.subprocess, 'Popen') as start:
                self.assertEqual(module.spawn(env), 0)
                self.assertEqual(start.call_args.args[0], ['emaki-session-wallpaper', '--force'])
                self.assertTrue(start.call_args.kwargs['start_new_session'])
        with patch.object(module.subprocess, 'run') as ask, patch.object(module.subprocess, 'Popen', side_effect=FileNotFoundError()):
            self.assertEqual(module.spawn(self.env), 1)
            ask.assert_not_called()

    def test_forced_start_replaces_a_dead_claim_only(self):
        module = load(ROOT / 'scripts/emaki-session-wallpaper')
        claim = self.root / 'stale-claim'
        def run(force, answers):
            claim.write_bytes(b'1')  # Claimed earlier; the lock is free, so that daemon is gone.
            descriptor = os.open(claim, os.O_RDWR)
            with patch.dict(os.environ, self.env, clear=True), patch.object(module, 'claim_file', return_value=descriptor), patch.object(module, 'compositor_draws_wallpaper', return_value=False), patch.object(module, 'daemon_answers', return_value=answers) as asked, patch.object(module.os, 'execvpe') as execute:
                self.assertEqual(module.main(force=force), 0)
            return execute.called, asked.called
        self.assertEqual(run(False, False), (False, False))
        self.assertEqual(run(True, True), (False, True))
        self.assertEqual(run(True, False), (True, True))
        from types import SimpleNamespace
        for answer, expected in ((SimpleNamespace(returncode=0), True), (SimpleNamespace(returncode=1), False),
                                 (FileNotFoundError(), False), (subprocess.TimeoutExpired('wpaperctl', 1), True)):
            with patch.object(module.subprocess, 'run', side_effect=[answer]):
                self.assertEqual(module.daemon_answers(self.env), expected)

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
        self.assertEqual(module.wallpaper_arguments(env), module.default_arguments(env))
        config.write_text('schema_version = 1\ngeneration = "../escape"\n[appearance]\nwallpaper = ' + json.dumps(str(self.image)))
        with self.assertRaises(ValueError):
            module.wallpaper_arguments(env)

    def test_first_picture_without_personal_config_starts_with_explicit_default(self):
        # Exercise the real snapshot/start/apply chain with an isolated image daemon.
        data = self.root / 'data'
        packaged = data / 'wpaperd/config.toml'
        packaged.parent.mkdir(parents=True)
        fallback = data / 'wallpaper/fallback.png'
        fallback.parent.mkdir()
        fallback.write_bytes(b'fallback')
        rendered = subprocess.run(
            [sys.executable, str(ROOT / 'scripts/render-paths'), '--datadir', str(data),
             '--text', str(ROOT / 'etc-skel/.config/wpaperd/config.toml')],
            text=True, capture_output=True, check=True)
        packaged.write_text(rendered.stdout)
        runtime = self.root / 'runtime'
        runtime.mkdir(mode=0o700)
        starter = self.root / 'emaki-session-wallpaper'
        starter.write_text('#!/usr/bin/python3\nimport runpy\n'
                           + 'module = runpy.run_path(' + repr(str(ROOT / 'scripts/emaki-session-wallpaper')) + ')\n'
                           + 'module["default_arguments"].__globals__["DATADIR"] = ' + repr(str(data)) + '\n'
                           + 'raise SystemExit(module["main"](force=True))\n')
        starter.chmod(0o700)
        daemon = self.root / 'wpaperd'
        daemon.write_text('''#!/usr/bin/python3
import json, os, pathlib, sys, tomllib
assert sys.argv[1] == '--config'
config = tomllib.loads(pathlib.Path(sys.argv[2]).read_text())
path = config['any']['path']
assert pathlib.Path(path).is_file()
pathlib.Path(os.environ['FAKE_STATE']).write_text(json.dumps({'DP-1': path}))
''')
        daemon.chmod(0o700)
        personal = self.root / 'config/wpaperd/config.toml'
        source = self.root / 'config/emaki/settings.toml'
        source.parent.mkdir(parents=True)
        for settings in (None, 'schema_version = 1\n[appearance]\nstyle = "07-blue-light"\n'):
            with self.subTest(settings=settings):
                if settings is not None:
                    source.write_text(settings)
                self.state.unlink()
                before = self.run_helper('snapshot', XDG_RUNTIME_DIR=str(runtime))
                self.assertEqual(before.returncode, 0, before.stderr)
                self.assertEqual(json.loads(before.stdout), {'DP-1': str(fallback)})
                applied = self.run_helper('apply', str(self.image), XDG_RUNTIME_DIR=str(runtime))
                self.assertEqual(applied.returncode, 0, applied.stderr)
                self.assertEqual(json.loads(self.state.read_text()), {'DP-1': str(self.image)})
                self.assertEqual(self.run_helper('restore', before.stdout).returncode, 0)
                self.assertEqual(json.loads(self.state.read_text()), {'DP-1': str(fallback)})
                self.assertFalse(personal.exists())
                if settings is None:
                    self.assertFalse(source.exists())
                else:
                    self.assertEqual(source.read_text(), settings)

    def test_personal_configuration_keeps_precedence_over_packaged_default(self):
        module = load(ROOT / 'scripts/emaki-session-wallpaper')
        personal = self.root / 'config/wpaperd/config.toml'
        personal.parent.mkdir(parents=True)
        personal.write_text('[default]\npath = ' + json.dumps(str(self.image)))
        before = personal.read_bytes()
        self.assertEqual(module.wallpaper_arguments(self.env), ['wpaperd'])
        self.assertEqual(personal.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
