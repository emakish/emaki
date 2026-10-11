#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real directory/rename fixtures; ownership is simulated unless run as root in a VM.

Temporary ancestors are deliberately accepted by these fixtures. Separate tests
check their production trust rule. No real account or system directory is changed.
"""

import datetime
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location(
    "users_home", Path(__file__).resolve().parents[1] / "scripts/users_home.py")
home_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(home_module)


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "dee"
        self.home.mkdir(mode=0o700)
        (self.home / "private").write_text("retained data")
        self.uid = os.getuid() or 60001
        self.sealed = set()
        real_stat = os.fstat

        def fstat(fd):
            value = list(real_stat(fd))
            value[4:6] = [0, 0] if value[1] in self.sealed else [self.uid, self.uid]
            return os.stat_result(value)

        def chown(fd, uid, gid):
            self.assertEqual((uid, gid), (0, 0))
            self.sealed.add(real_stat(fd).st_ino)

        for patch in (mock.patch.object(home_module, "_check_parent"),
                      mock.patch.object(home_module.os, "fchown", side_effect=chown),
                      mock.patch.object(home_module.os, "fstat", side_effect=fstat)):
            patch.start()
            self.addCleanup(patch.stop)

    def retain(self):
        return home_module.retain(str(self.home), self.uid)

    def test_retains_content_and_frees_original_home(self):
        archive = Path(self.retain())
        self.assertEqual(archive.name, "dee.removed-" + datetime.date.today().strftime("%Y%m%d"))
        self.assertEqual((archive / "private").read_text(), "retained data")
        self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o700)
        self.assertFalse(self.home.exists())
        self.home.mkdir()
        self.assertFalse((self.home / "private").exists())

    def test_collision_never_overwrites_directory_or_symlink(self):
        target = self.base / ("dee.removed-" + datetime.date.today().strftime("%Y%m%d"))
        target.mkdir()
        (target / "sentinel").write_text("original")
        Path(str(target) + "-1").symlink_to(target)
        self.assertEqual(self.retain(), str(target) + "-2")
        self.assertEqual((target / "sentinel").read_text(), "original")
        self.assertTrue(Path(str(target) + "-1").is_symlink())

    def test_absent_home_has_no_archive(self):
        self.assertIsNone(home_module.retain(str(self.base / "absent"), self.uid))

    def test_symlink_leaf_is_refused(self):
        link = self.base / "link"
        link.symlink_to(self.home)
        with self.assertRaises(OSError):
            home_module.retain(str(link), self.uid)
        self.assertEqual((self.home / "private").read_text(), "retained data")
        self.assertFalse(self.sealed)

    def test_symlink_ancestor_is_refused(self):
        link = self.base / "link"
        link.symlink_to(self.base)
        with self.assertRaises(OSError):
            home_module.retain(str(link / "dee"), self.uid)
        self.assertFalse(self.sealed)

    def test_other_owner_is_refused_before_chown(self):
        with self.assertRaises(ValueError):
            home_module.retain(str(self.home), self.uid + 1)
        self.assertFalse(self.sealed)

    def test_non_directory_is_refused(self):
        with self.assertRaises(OSError):
            home_module.retain(str(self.home / "private"), self.uid)

    def test_unsafe_paths_are_refused(self):
        for path in ("/", "/home", "/home/..", "relative/home", "//home/dee",
                     "/home/./dee", "/home/dee/", "/home/dee\0"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                home_module.retain(path, self.uid)
        with self.assertRaises(ValueError):
            home_module.retain(str(self.home), 0)

    def test_failed_protection_does_not_rename(self):
        with mock.patch.object(home_module.os, "fchmod", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                self.retain()
        self.assertTrue((self.home / "private").exists())

    def test_failed_rename_leaves_original_sealed(self):
        with mock.patch.object(home_module, "_rename_noreplace", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                self.retain()
        self.assertTrue((self.home / "private").exists())
        self.assertIn(self.home.stat().st_ino, self.sealed)
        self.assertEqual(stat.S_IMODE(self.home.stat().st_mode), 0o700)

    def test_inode_replacement_is_refused(self):
        real_chmod = os.fchmod

        def replace(fd, mode):
            real_chmod(fd, mode)
            self.home.rename(self.base / "original")
            self.home.mkdir()

        with mock.patch.object(home_module.os, "fchmod", side_effect=replace):
            with self.assertRaisesRegex(OSError, "Home changed"):
                self.retain()
        self.assertTrue((self.base / "original" / "private").exists())
        self.assertTrue(self.home.is_dir())


class ParentTrustTests(unittest.TestCase):
    def test_only_root_owned_non_writable_ancestors(self):
        for uid, mode, accepted in ((0, 0o755, True), (0, 0o700, True),
                                    (0, 0o775, False), (0, 0o1777, False),
                                    (1000, 0o700, False)):
            with self.subTest(uid=uid, mode=mode):
                info = types.SimpleNamespace(st_uid=uid, st_mode=stat.S_IFDIR | mode)
                with mock.patch.object(home_module.os, "fstat", return_value=info):
                    if accepted:
                        home_module._check_parent(1)
                    else:
                        with self.assertRaises(ValueError):
                            home_module._check_parent(1)


@unittest.skipUnless(os.geteuid() == 0, "real ownership and UID reuse require the disposable root VM")
class RootAccessTests(unittest.TestCase):
    def test_reused_uid_cannot_read_retained_files(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            base.chmod(0o755)
            home = base / "dee"
            home.mkdir(mode=0o700)
            private = home / "private"
            private.write_text("previous user's secret")
            uid = 60001
            os.chown(home, uid, uid)
            os.chown(private, uid, uid)
            # Only ancestor validation is mocked: temporary roots need not be trusted.
            with mock.patch.object(home_module, "_check_parent"):
                retained = Path(home_module.retain(str(home), uid))
            self.assertEqual((retained.stat().st_uid, retained.stat().st_gid), (0, 0))
            self.assertEqual(stat.S_IMODE(retained.stat().st_mode), 0o700)
            home.mkdir(mode=0o700)
            os.chown(home, uid, uid)
            # Relative access avoids an inaccessible outer test directory confounding
            # the result. This child's real and effective IDs reproduce account UID reuse.
            fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
            child = os.fork()
            if child == 0:
                os.fchdir(fd)
                os.setgroups([])
                os.setgid(uid)
                os.setuid(uid)
                try:
                    Path("dee/fresh").write_text("new user's data")
                    (Path(retained.name) / "private").read_text()
                except PermissionError:
                    os._exit(0 if Path("dee/fresh").exists() else 2)
                except BaseException:
                    os._exit(3)
                os._exit(1)
            os.close(fd)
            _, result = os.waitpid(child, 0)
            self.assertEqual(os.waitstatus_to_exitcode(result), 0)
            self.assertEqual((retained / "private").read_text(), "previous user's secret")


if __name__ == "__main__":
    unittest.main()
