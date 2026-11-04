#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic publication recovery when compositor state is unavailable."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'shell/helpers'))
import wallpaper
import emaki_paths

spec = importlib.util.spec_from_file_location('pending_publisher', ROOT / 'shell/helpers/publish-wallpaper.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class WatchFinished(Exception):
    pass


class PendingWallpaper(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config_root = self.root / 'config'
        self.config = self.config_root / 'wpaperd/config.toml'
        self.settings = self.config_root / 'emaki/settings.toml'
        self.settings.parent.mkdir(parents=True)
        self.destination = self.root / 'published'
        self.destination.mkdir()
        self.destination.chmod(0o2750)
        self.old = self.root / 'old.png'
        self.new = self.root / 'new.png'
        Image.new('RGB', (32, 16), 'red').save(self.old)
        Image.new('RGB', (32, 16), 'blue').save(self.new)
        environment = dict(HOME=str(self.root), XDG_CONFIG_HOME=str(self.config_root),
                           NIRI_SOCKET=str(self.root / 'fake-socket'))
        self.environment = patch.dict(os.environ, environment, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.geometry = patch.object(publisher, 'publication_geometry', return_value=(1920, 1080))
        self.geometry.start()
        self.addCleanup(self.geometry.stop)

    def status(self, path, state='ready'):
        return dict(mode='strip', load_state=state, files=dict(ring=str(path)))

    def published_bytes(self):
        return {str(path.relative_to(self.destination)): path.read_bytes()
                for path in self.destination.rglob('*') if path.is_file()}

    def published_image(self):
        data = tomllib.loads((self.destination / 'wpaperd/config.toml').read_text())
        return Path(data['default']['path']).read_bytes()

    def seed(self):
        with patch.object(wallpaper, 'compositor_wallpaper', return_value=self.status(self.old)):
            self.assertEqual(publisher.attempt(self.config, self.destination)['state'], 'published')
        self.assertEqual(self.published_image(), self.old.read_bytes())

    def unset(self, present):
        if present:
            self.settings.write_text('schema_version = 1\n[appearance]\n')
        else:
            self.settings.unlink(missing_ok=True)
        self.assertFalse(self.config.exists())

    def test_watcher_keeps_copy_past_timeout_and_replaces_it_after_recovery(self):
        for present in (False, True):
            with self.subTest(settings_present=present):
                self.unset(present)
                self.seed()
                before = self.published_bytes()
                clock = [0.0]
                recovered = [False]
                fallbacks = []
                closed = []
                real_attempt = publisher.attempt

                def query(endpoint):
                    if not recovered[0]:
                        raise TimeoutError('fixture compositor is not responding')
                    return self.status(self.new)

                def attempt(config, destination, wait=True):
                    result = real_attempt(config, destination, wait=wait)
                    if not wait:
                        fallbacks.append(clock[0])
                        self.assertGreaterEqual(clock[0], publisher.PENDING_FALLBACK)
                        self.assertEqual(result['state'], 'pending')
                        self.assertEqual(self.published_bytes(), before)
                    return result

                test = self

                class Notifier:
                    def update(self, candidates):
                        pass

                    def changed(self, timeout):
                        if recovered[0]:
                            test.assertEqual(test.published_image(), test.new.read_bytes())
                            raise WatchFinished()
                        test.assertEqual(test.published_bytes(), before)
                        clock[0] += timeout
                        if fallbacks:
                            recovered[0] = True
                        if clock[0] > 100:
                            test.fail('watcher failed to use its bounded fallback')

                    def close(self):
                        closed.append(True)

                with patch.object(wallpaper, 'compositor_wallpaper', side_effect=query), patch.object(publisher, 'compositor_wallpaper', side_effect=query), patch.object(publisher, 'Inotify', Notifier), patch.object(publisher, 'attempt', side_effect=attempt), patch.object(publisher.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(publisher.time, 'sleep', side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)):
                    with self.assertRaises(WatchFinished):
                        publisher.watch(self.config, self.destination)
                self.assertEqual(len(fallbacks), 1)
                self.assertEqual(closed, [True])

    def test_unset_style_loading_strip_publishes_reported_asset_on_fallback(self):
        for present in (False, True):
            with self.subTest(settings_present=present):
                self.unset(present)
                self.seed()
                with patch.object(wallpaper, 'compositor_wallpaper', return_value=self.status(self.new, 'loading')):
                    self.assertEqual(publisher.attempt(self.config, self.destination)['state'], 'pending')
                    self.assertEqual(publisher.attempt(self.config, self.destination, wait=False)['state'], 'published')
                self.assertEqual(self.published_image(), self.new.read_bytes())

    def test_committed_image_is_available_during_compositor_timeout(self):
        self.seed()
        self.settings.write_text('schema_version = 1\n[appearance]\nwallpaper = ' + json.dumps(str(self.new)))
        with patch.object(wallpaper, 'compositor_wallpaper', side_effect=TimeoutError()):
            self.assertEqual(publisher.attempt(self.config, self.destination)['state'], 'pending')
            self.assertEqual(publisher.attempt(self.config, self.destination, wait=False)['state'], 'published')
        self.assertEqual(self.published_image(), self.new.read_bytes())

    def test_committed_legacy_landscape_keeps_ring_during_compositor_timeout(self):
        data = self.root / 'data'
        ring = data / 'wallpaper/ring.png'
        ring.parent.mkdir(parents=True)
        ring.write_bytes(self.new.read_bytes())
        self.settings.write_text('schema_version = 1\n[appearance]\nwallpaper = ' +
                                 json.dumps(str(data / 'wallpaper/fallback.png')))
        with patch.object(emaki_paths, 'DATADIR', str(data)), patch.object(wallpaper, 'compositor_wallpaper', side_effect=TimeoutError()):
            self.assertEqual(publisher.attempt(self.config, self.destination, wait=False)['state'], 'published')
        self.assertEqual(self.published_image(), ring.read_bytes())

    def test_malformed_compositor_reply_preserves_copy_without_selection(self):
        for present in (False, True):
            self.unset(present)
            self.seed()
            before = self.published_bytes()
            for reply in (None, {}, {'mode': 'strip'}, {'mode': 'strip', 'load_state': 'ready', 'files': None}):
                with self.subTest(settings_present=present, reply=reply), patch.object(wallpaper, 'compositor_wallpaper', return_value=reply):
                    self.assertEqual(publisher.attempt(self.config, self.destination, wait=False)['state'], 'pending')
                    self.assertEqual(self.published_bytes(), before)


if __name__ == '__main__':
    unittest.main()
