#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise real clipboard storage, migration boundaries, and both recorder routes."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
HELPERS = ROOT / 'shell/helpers'
spec = importlib.util.spec_from_file_location('clipboard_store', HELPERS / 'clipboard_store.py')
store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)
sys.path.insert(0, str(HELPERS))
launcher_spec = importlib.util.spec_from_file_location("launcher_tools", HELPERS / "launcher-tools.py")
launcher = importlib.util.module_from_spec(launcher_spec)
launcher_spec.loader.exec_module(launcher)
sys.path.pop(0)


class ClipboardRuntime(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='clipboard-runtime-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = dict(os.environ, HOME=str(self.root / 'home'),
                        XDG_RUNTIME_DIR=str(self.root / 'runtime'),
                        XDG_CACHE_HOME=str(self.root / 'cache'),
                        XDG_STATE_HOME=str(self.root / 'state'),
                        XDG_CONFIG_HOME=str(self.root / 'config'))
        for name in ('home', 'runtime', 'cache', 'state', 'config'):
            (self.root / name).mkdir(mode=0o700)
        self.patch = patch.dict(os.environ, self.env, clear=True)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.old = self.root / 'cache/cliphist'
        self.old.mkdir()
        self.db = self.root / 'runtime/emaki-cliphist.db'

    def command(self, args, data=None):
        return subprocess.run(args, input=data, capture_output=True, check=True,
                              env=self.env, timeout=5).stdout

    def helper(self, request):
        return json.loads(self.command([sys.executable, '-B', str(HELPERS / 'launcher-tools.py')],
                                       json.dumps(request).encode()))

    def test_legacy_database_only_once(self):
        (self.old / 'db').write_bytes(b'old private clipboard')
        (self.old / 'keep').write_bytes(b'other data')
        store.prepare()
        self.assertFalse((self.old / 'db').exists())
        self.assertEqual((self.old / 'keep').read_bytes(), b'other data')
        (self.old / 'db').write_bytes(b'created independently after migration')
        store.prepare()
        self.assertEqual((self.old / 'db').read_bytes(), b'created independently after migration')

    def test_legacy_symlink_and_hardlink_are_not_removed(self):
        target = self.root / 'personal'
        target.write_bytes(b'keep me')
        for kind in ('symlink', 'hardlink'):
            with self.subTest(kind=kind):
                marker = self.root / 'state/emaki/clipboard-runtime-v1'
                marker.unlink(missing_ok=True)
                old = self.old / 'db'
                if kind == 'symlink':
                    old.symlink_to(target)
                else:
                    os.link(target, old)
                store.prepare()
                self.assertEqual(target.read_bytes(), b'keep me')
                self.assertTrue(old.exists())
                old.unlink()
                self.assertNotEqual(marker.read_bytes(), b'1')

    def test_legacy_parent_symlink_is_not_followed(self):
        self.old.rmdir()
        outside = self.root / 'personal'
        outside.mkdir()
        (outside / 'db').write_bytes(b'keep me')
        self.old.symlink_to(outside)
        store.prepare()
        self.assertEqual((outside / 'db').read_bytes(), b'keep me')

    def test_cleanup_oserror_is_retryable(self):
        (self.old / 'db').write_bytes(b'old clipboard')
        marker = self.root / 'state/emaki/clipboard-runtime-v1'
        with patch.object(store.os, 'unlink', side_effect=OSError('cleanup unavailable')):
            store.prepare()
        self.assertNotEqual(marker.read_bytes(), b'1')
        self.assertTrue((self.old / 'db').exists())
        store.prepare()
        self.assertFalse((self.old / 'db').exists())
        self.assertEqual(marker.read_bytes(), b'1')

    def test_cache_symlink_does_not_mark_cleanup_done(self):
        cache = self.root / 'cache'
        moved = self.root / 'moved-cache'
        (self.old / 'db').write_bytes(b'old clipboard')
        cache.rename(moved)
        cache.symlink_to(moved, target_is_directory=True)
        store.prepare()
        marker = self.root / 'state/emaki/clipboard-runtime-v1'
        self.assertNotEqual(marker.read_bytes(), b'1')
        self.assertTrue((moved / 'cliphist/db').exists())
        cache.unlink()
        moved.rename(cache)
        store.prepare()
        self.assertFalse((self.old / 'db').exists())
        self.assertEqual(marker.read_bytes(), b'1')

    def test_state_symlink_does_not_stop_both_recorders(self):
        state = self.root / 'state'
        moved = self.root / 'moved-state'
        state.rename(moved)
        state.symlink_to(moved, target_is_directory=True)
        self.test_both_recorders_use_same_runtime_database()
        self.assertFalse((moved / 'emaki/clipboard-runtime-v1').exists())

    def test_missing_or_unsafe_runtime_refuses_persistent_fallback(self):
        for value in ('', 'relative', str(self.root / 'missing')):
            with self.subTest(value=value), patch.dict(os.environ, XDG_RUNTIME_DIR=value):
                with self.assertRaises((PermissionError, FileNotFoundError)):
                    store.prepare()
        (self.root / 'runtime').chmod(0o755)
        with self.assertRaises(PermissionError):
            store.runtime_db()

    def test_real_database_read_decode_delete_and_clear(self):
        prefix = ['cliphist', '-config-path', '/dev/null', '-db-path', str(self.db)]
        self.assertEqual(self.helper({'op': 'clip-list'})['entries'], [])
        self.assertFalse(self.db.exists())
        for text in (b'first clipboard value', b'second clipboard value'):
            self.command(prefix + ['store'], text)
        entries = self.helper({'op': 'clip-list'})['entries']
        self.assertEqual(len(entries), 2)
        decoded = self.command(prefix + ['decode'], (entries[0]['id'] + '\t\n').encode())
        self.assertIn(decoded, (b'first clipboard value', b'second clipboard value'))
        with patch.object(launcher.app_scope, 'copy', return_value=True) as copy:
            self.assertEqual(launcher.clip({'op': 'clip-copy', 'id': entries[0]['id']})['state'], 'copied')
            self.assertEqual(copy.call_args.args[1], decoded)
        self.assertEqual(self.helper({'op': 'clip-delete', 'id': entries[0]['id']})['state'], 'deleted')
        self.assertEqual(len(self.helper({'op': 'clip-list'})['entries']), 1)
        self.assertEqual(self.helper({'op': 'clip-clear'})['state'], 'deleted')
        self.assertEqual(self.helper({'op': 'clip-list'})['entries'], [])
        self.assertFalse((self.old / 'db').exists())

    def test_both_recorders_use_same_runtime_database(self):
        bindir = self.root / 'bin'
        bindir.mkdir()
        watcher = bindir / 'wl-paste'
        watcher.write_text('#!/usr/bin/python3\nimport json,os,sys\n'
                           'open(os.environ["WATCH_ARGS"],"w").write(json.dumps(sys.argv[1:]))\n')
        watcher.chmod(0o700)
        self.env.update(PATH=str(bindir) + ':' + self.env['PATH'],
                        WATCH_ARGS=str(self.root / 'args'), EMAKI_WL_PASTE=str(watcher))
        line = next(line for line in (ROOT / 'niri/default.kdl').read_text().splitlines()
                    if line.startswith('spawn-at-startup "emaki-autostart" "clipboard" ')
                    and 'clipboard_store.py' in line)
        # The login switch wrapper execs the rest unchanged; run that command directly.
        argv = [str(HELPERS / 'clipboard_store.py')
                if word == '/usr/share/emaki/shell/helpers/clipboard_store.py' else word
                for word in shlex.split(line)[3:]]
        if argv[0] == 'python3':
            argv[0] = sys.executable
        expected = ['--watch', 'cliphist', '-config-path', '/dev/null', '-db-path', str(self.db), 'store']
        with self.subTest(recorder='niri'):
            self.command(argv)
            self.assertEqual(json.loads((self.root / 'args').read_text()), expected)
        (self.root / 'args').unlink(missing_ok=True)
        # Keep the recorder's stdin alive until its short-lived watcher has finished.
        with self.subTest(recorder='shell'):
            proc = subprocess.Popen([sys.executable, '-B', str(HELPERS / 'clip-recorder.py')],
                                    stdin=subprocess.PIPE, env=self.env)
            try:
                self.assertEqual(proc.wait(timeout=5), 0)
            finally:
                proc.stdin.close()
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
            self.assertEqual(json.loads((self.root / 'args').read_text()), expected)


if __name__ == '__main__':
    unittest.main()
