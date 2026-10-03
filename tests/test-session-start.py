#!/usr/bin/env python3
"""Fresh PNG handoff, one-shot claims, private state and compositor readiness."""
import importlib.util
import importlib.machinery
import json
import os
from pathlib import Path
import pwd
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent

def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module

helper = load('startup', ROOT / 'shell/helpers/session-start.py')
publisher = load('publish', ROOT / 'shell/helpers/greeter-handoff.py')

class StartupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / '.cache')
        self.base = Path(self.tmp.name)
        self.runtime = self.base / 'runtime'
        self.runtime.mkdir(mode=0o700)
        self.images = self.base / 'handoff'
        self.images.mkdir(mode=0o711)
        self.marker = dict(v=1, token='a'*32, createdMs=time.time()*1000,
                           idleMode='lake', clock=4, introStart=1)
        self.env = dict(XDG_RUNTIME_DIR=str(self.runtime), EMAKI_LOGIN_HANDOFF=json.dumps(self.marker))
        self.patches = [patch.object(helper, 'HANDOFF_ROOT', self.images),
                        patch.object(publisher, 'ROOT', self.images),
                        patch.object(pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=os.geteuid()))]
        for context in self.patches:
            context.start()
        publisher.handoff('prepare', self.marker['token'])
        for suffix in ('', '-plate'):
            Image.new('RGBA', (64, 48), (40, 80, 120, 255)).save(self.images / ('a'*32 + suffix + '.png'))
        publisher.handoff('publish', self.marker['token'])

    def tearDown(self):
        for context in reversed(self.patches):
            context.stop()
        self.tmp.cleanup()

    def test_fresh_once_only_and_restart(self):
        self.assertTrue(helper.prepare(self.env).endswith('a'*32 + '.png'))
        self.assertEqual(helper.prepare(self.env), '')
        self.assertEqual(helper.claim_shell(self.env), 'a'*32)
        self.assertEqual(helper.claim_shell(self.env), '')
        value = helper.status('a'*32, self.env)
        self.assertEqual(value['visual'], self.marker)
        self.assertEqual((self.runtime / 'emaki-session-start').stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.runtime / 'emaki-session-start' / ('a'*32 + '.json')).stat().st_mode & 0o777, 0o600)

    def test_no_token_stale_invalid_or_missing_picture(self):
        for marker in ('', '{}', 'x', json.dumps(dict(self.marker, createdMs=1)),
                       json.dumps(dict(self.marker, clock=float('nan')))):
            env = dict(self.env, EMAKI_LOGIN_HANDOFF=marker)
            self.assertEqual(helper.prepare(env), '')
            self.assertEqual(helper.claim_shell(env), '')
        (self.images / ('a'*32 + '.png')).unlink()
        with self.assertRaises(FileNotFoundError): helper.prepare(self.env)

    def test_picture_symlink_and_permissions_refused(self):
        path = self.images / ('a'*32 + '.png')
        path.chmod(0o666)
        with self.assertRaises(ValueError): helper.prepare(self.env)
        path.unlink()
        path.symlink_to(self.images / ('a'*32 + '-plate.png'))
        with self.assertRaises(OSError): helper.prepare(self.env)

    def test_missing_corrupt_or_wrong_size_plate_rejected(self):
        path = self.images / ('a'*32 + '-plate.png')
        valid = path.read_bytes()
        for data in (None, valid[:40], b'not a png'):
            if data is None:
                path.unlink()
            else:
                path.write_bytes(data)
                path.chmod(0o644)
            with self.assertRaises((OSError, ValueError)):
                helper.prepare(self.env)
        Image.new('RGB', (1, 1)).save(path)
        with self.assertRaises(ValueError):
            helper.prepare(self.env)
        self.assertFalse((self.runtime / 'emaki-session-start').exists())

    def test_context_timeout(self):
        helper.prepare(self.env)
        def hung(_command, **kwargs):
            self.assertEqual(kwargs['timeout'], 2)
            raise subprocess.TimeoutExpired(_command, kwargs['timeout'])
        with patch.object(helper.subprocess, 'run', side_effect=hung):
            with self.assertRaises(subprocess.TimeoutExpired):
                helper.cover_context('a'*32, self.env)

    def test_state_symlink_refused(self):
        helper.prepare(self.env)
        path = self.runtime / 'emaki-session-start' / ('a'*32 + '.json')
        path.unlink()
        path.symlink_to(self.images / ('a'*32 + '.png'))
        with self.assertRaises(OSError): helper.claim_shell(self.env)

    def test_deadline_invalid_and_lock_status_prevent_late_cover(self):
        helper.prepare(self.env)
        for state in (dict(active=False, remaining_ms=0, outputs=[]),
                      dict(active=True, remaining_ms=200, outputs=['A'])):
            with patch.object(helper.subprocess, 'run', return_value=SimpleNamespace(stdout=json.dumps(state))):
                self.assertFalse(helper.cover_context('a'*32, self.env)['active'])
        with patch.object(helper.subprocess, 'run', return_value=SimpleNamespace(
                stdout=json.dumps(dict(active=True, remaining_ms=2500, outputs=['A'])))) as run:
            result = helper.cover_context('a'*32, self.env)
            self.assertTrue(result['active'])
            self.assertGreater(result['remainingMs'], 2240)
            self.assertLessEqual(result['remainingMs'], 2250)
            self.assertEqual(result['outputs'], ['A'])
            self.assertEqual(run.call_args.args[0][-1], 'emaki-startup-cover')

    def test_windows_require_one_second_since_last_change(self):
        r = helper.Readiness(0)
        snap = dict(windowIds=[1], coverMapped=True, barMapped=True, dockMapped=True, wallpaperMapped=True)
        self.assertFalse(r.sample(snap, True, 0)['ready'])
        self.assertFalse(r.sample(snap, False, 2)['ready'])
        self.assertTrue(r.sample(snap, True, 2)['ready'])
        self.assertFalse(r.sample(dict(snap, windowIds=[1,2]), True, 2.1)['ready'])
        self.assertTrue(r.sample(dict(snap, windowIds=[1,2]), True, 3.11)['ready'])
        self.assertFalse(r.sample(dict(snap, windowIds=[]), True, 5)['ready'])
        self.assertTrue(r.sample(dict(snap, windowIds=[]), True, 6.01)['ready'])
        self.assertFalse(r.sample(dict(snap, windowIds=[], wallpaperMapped=False), True, 8)['ready'])
        empty = helper.Readiness(0)
        self.assertFalse(empty.sample(dict(snap, windowIds=[]), True, 0)['ready'])
        self.assertFalse(empty.sample(dict(snap, windowIds=[]), True, .76)['ready'])
        # An app maps after the old 750 ms settle. Its resize also restarts quiet.
        late = dict(snap, windowIds=[42], windowSizes=[(42, [640, 480])])
        self.assertFalse(empty.sample(late, True, .9)['ready'])
        self.assertFalse(empty.sample(late, True, 1.8)['ready'])
        late['windowSizes'] = [(42, [800, 600])]
        self.assertFalse(empty.sample(late, True, 1.85)['ready'])
        self.assertFalse(empty.sample(late, True, 2.8)['ready'])
        self.assertTrue(empty.sample(late, True, 2.86)['ready'])
        self.assertFalse(empty.sample(None, True, 3)['ready'])
        self.assertFalse(empty.sample(late, True, 3.1)['ready'])

    def test_layers_on_each_output_and_real_window_ids(self):
        layers = [dict(output='A', namespace=n) for n in ('emaki-session-cover','emaki-test-bar','emaki-test-dock','wpaperd-A')]
        windows = [dict(id=42, layout=dict(window_size=[640,480]), title='must not be retained')]
        with patch.object(helper.subprocess, 'run', side_effect=[SimpleNamespace(returncode=0,stdout=json.dumps(v).encode()) for v in (layers,windows)]):
            result = helper.niri_snapshot(dict(NIRI_SOCKET='/fixture/socket'))
        self.assertTrue(result['wallpaperMapped'])
        self.assertEqual(result['windowIds'], [42])
        self.assertEqual(result['windowSizes'], [(42, [640,480])])
        self.assertNotIn('title', str(result))
        layers += [dict(output='B', namespace='emaki-session-cover')]
        with patch.object(helper.subprocess, 'run', side_effect=[SimpleNamespace(returncode=0,stdout=json.dumps(v).encode()) for v in (layers,windows)]):
            result = helper.niri_snapshot(dict(NIRI_SOCKET='/fixture/socket'))
        self.assertFalse(result['barMapped'])
        self.assertFalse(result['wallpaperMapped'])

    def test_wallpaper_starts_without_cover_wait_and_once(self):
        binary = self.base / 'bin'; binary.mkdir()
        stub = binary / 'wpaperd'
        stub.write_text('#!/bin/sh\nprintf started >> "$RESULT"\n')
        stub.chmod(0o700)
        env = dict(self.env, PATH=str(binary), RESULT=str(self.base / 'result'),
                   NIRI_SOCKET='/fixture/socket', WAYLAND_DISPLAY='fixture')
        for _ in range(2):
            subprocess.run([str(ROOT / 'scripts/emaki-session-wallpaper')], env=env, check=True, timeout=1)
        self.assertEqual((self.base / 'result').read_text(), 'started')

if __name__ == '__main__': unittest.main()
