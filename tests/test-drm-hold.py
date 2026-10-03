#!/usr/bin/env python3
"""DRM fd ownership/error/hotplug boundaries; no actual GPU node is opened."""
import errno
import importlib.machinery
import importlib.util
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
loader = importlib.machinery.SourceFileLoader('drm_hold', str(ROOT / 'scripts/emaki-drm-hold'))
spec = importlib.util.spec_from_loader(loader.name, loader)
h = importlib.util.module_from_spec(spec)
loader.exec_module(h)
INFO = SimpleNamespace(st_mode=stat.S_IFCHR, st_rdev=226 << 8, st_ino=1)

class HoldTests(unittest.TestCase):
    def test_master_is_dropped_and_verified(self):
        with patch.object(h.os, 'open', return_value=9), patch.object(h.os, 'fstat', return_value=INFO), \
             patch.object(h.os, 'close') as close, patch.object(h.fcntl, 'ioctl', side_effect=[
                 OSError(errno.EINVAL, 'master'), 0, OSError(errno.EACCES, 'not master')]) as ioctl:
            self.assertEqual(h.open_nonmaster('/fixture/card0'), (9, (1, 226 << 8)))
            self.assertEqual([c.args[1] for c in ioctl.call_args_list],
                             [h.DRM_IOCTL_AUTH_MAGIC, h.DRM_IOCTL_DROP_MASTER, h.DRM_IOCTL_AUTH_MAGIC])
            close.assert_not_called()

    def test_uncertain_or_retained_master_closes_fd(self):
        for effects in ([OSError(errno.EINVAL, 'master'), OSError(errno.EPERM, 'drop failed')],
                        [OSError(errno.EINVAL, 'master'), 0, OSError(errno.EINVAL, 'still master')],
                        [OSError(errno.ENOTTY, 'not DRM')]):
            with patch.object(h.os, 'open', return_value=9), patch.object(h.os, 'fstat', return_value=INFO), \
                 patch.object(h.os, 'close') as close, patch.object(h.fcntl, 'ioctl', side_effect=effects):
                with self.assertRaises((OSError, RuntimeError)):
                    h.open_nonmaster('/fixture/card0')
                close.assert_called_once_with(9)

    def test_nonmaster_never_requests_master(self):
        with patch.object(h.os, 'open', return_value=9), patch.object(h.os, 'fstat', return_value=INFO), \
             patch.object(h.fcntl, 'ioctl', side_effect=OSError(errno.EACCES, 'not master')) as ioctl:
            h.open_nonmaster('/fixture/card0')
            self.assertEqual([c.args[1] for c in ioctl.call_args_list], [h.DRM_IOCTL_AUTH_MAGIC]*2)

    def test_hotplug_retry_and_render_nodes_excluded(self):
        holder = h.Holder(Path('/fixture'))
        card0, card1 = Path('/fixture/card0'), Path('/fixture/card1')
        with patch.object(Path, 'glob', return_value=[card0, Path('/fixture/renderD128')]), \
             patch.object(h, 'open_nonmaster', return_value=(9, (1, 226 << 8))) as opening:
            holder.scan()
            opening.assert_called_once_with(card0)
        with patch.object(Path, 'glob', return_value=[card0, card1]), patch.object(Path, 'lstat', return_value=INFO), \
             patch.object(h, 'open_nonmaster', side_effect=OSError(errno.EACCES, 'unavailable')):
            holder.scan()
            self.assertEqual(set(holder.held), {card0})
        with patch.object(Path, 'glob', return_value=[card1]), \
             patch.object(h, 'open_nonmaster', return_value=(10, (1, 226 << 8))), patch.object(h.os, 'close') as close, \
             patch.object(h.time, 'monotonic', return_value=h.time.monotonic()+6):
            holder.scan()
            close.assert_called_once_with(9)
            self.assertEqual(set(holder.held), {card1})
            holder.close()
            self.assertEqual(close.call_args.args, (10,))

    def test_backoff_and_held_card_never_reopened(self):
        holder = h.Holder(Path('/fixture'))
        card = Path('/fixture/card0')
        with patch.object(Path, 'glob', return_value=[card]), patch.object(Path, 'lstat', return_value=INFO), \
             patch.object(h, 'open_nonmaster', side_effect=OSError(errno.EACCES, 'busy')) as opening, \
             patch.object(h.time, 'monotonic') as clock:
            for now, attempts in ((0,1), (1,1), (4.9,1), (5,2), (14,2), (15,3), (34,3)):
                clock.return_value = now
                holder.scan()
                self.assertEqual(opening.call_count, attempts)
            opening.side_effect = None
            opening.return_value = (9, (1, 226 << 8))
            clock.return_value = 35
            holder.scan()
            self.assertIsNone(holder.retry_timeout())
            for now in (36, 100, 10000):
                clock.return_value = now
                holder.scan()
            self.assertEqual(opening.call_count, 4)

    def test_real_inotify_directory_and_card_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'dri'
            watch = h.CardWatch(directory)
            try:
                self.assertFalse(watch.changed(0))
                directory.mkdir()
                self.assertTrue(watch.changed(.1))
                card = directory / 'card0'
                card.touch()
                self.assertTrue(watch.changed(.1))
                self.assertFalse(watch.changed(0))
                card.unlink()
                self.assertTrue(watch.changed(.1))
                directory.rmdir()
                self.assertTrue(watch.changed(.1))
                directory.mkdir()
                self.assertTrue(watch.changed(.1))
                card.touch()
                self.assertTrue(watch.changed(.1))
            finally:
                watch.close()

    def test_idle_main_does_not_rescan(self):
        with patch.object(h, 'Holder') as holder, patch.object(h, 'CardWatch') as watcher, \
             patch.object(h, 'notify_ready') as notify, patch.object(h.signal, 'signal') as signal:
            holder.return_value.retry_timeout.return_value = None
            calls = 0
            def change(_timeout):
                nonlocal calls
                calls += 1
                if calls == 4:
                    signal.call_args.args[1](None, None)
                return calls == 3  # Only the third wait reports a hotplug event.
            watcher.return_value.changed.side_effect = change
            h.main()
            self.assertEqual(holder.return_value.scan.call_count, 2)
            notify.assert_called_once()
            watcher.return_value.close.assert_called_once()
            holder.return_value.close.assert_called_once()

    def test_restart_is_failure_only_and_slow(self):
        service = (ROOT / 'systemd/emaki-drm-hold.service').read_text()
        self.assertIn('Restart=on-failure', service)
        self.assertIn('RestartSec=5s', service)

if __name__ == '__main__':
    unittest.main()
