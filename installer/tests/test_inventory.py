"""Inventory tests use lsblk-shaped fixtures and fake runners; no disk access."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from emaki_installer.inventory import (
    Inventory, MIB, _has_windows, _root_hint, _timezone, _walk,
    parse_lsblk, public_inventory,
)


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / f"lsblk-{name}.json").read_text())


class ParseInventoryTests(unittest.TestCase):
    def test_blank_virtio_has_documented_path_fallback(self):
        disks = parse_lsblk(fixture("blank"), boot_sources={"/dev/sr0"})
        self.assertEqual(len(disks), 1)
        self.assertEqual(disks[0]["id"], "/dev/vda")
        self.assertTrue(disks[0]["_id_fallback"])
        self.assertFalse(disks[0]["is_boot_medium"])
        self.assertEqual(disks[0]["bus"], "virtio")
        self.assertFalse(disks[0]["_busy"])
        self.assertEqual(disks[0]["free_extents"], [{"start_bytes": MIB, "size_bytes": 42949672960 - 2 * MIB}])

    def test_windows_esp_and_4kn_offset(self):
        disk = parse_lsblk(fixture("windows"), stable_ids={"/dev/nvme0n1": "/dev/disk/by-id/nvme-fixture"})[0]
        self.assertFalse(disk["_id_fallback"])
        self.assertEqual(disk["_sector_size"], 4096)
        self.assertEqual(disk["partitions"][0]["start_bytes"], MIB)
        self.assertTrue(disk["partitions"][0]["esp"])
        self.assertIsNone(disk["partitions"][0]["_esp_free_bytes"])
        self.assertEqual(disk["partitions"][0]["id"], "/dev/disk/by-id/nvme-fixture-part1")
        self.assertEqual(disk["partitions"][2]["fs"], "ntfs")
        self.assertIsNone(disk["shrink"])
        self.assertIn("partuuid", disk["_identity"]["partitions"][0])

    def test_macos_fs_hints(self):
        disk = parse_lsblk(fixture("macos"))[0]
        self.assertEqual([p["os_hint"] for p in disk["partitions"]], [None, "macos", "macos"])
        self.assertEqual(disk["bus"], "sata")

    def test_boot_partition_marks_ancestor(self):
        disk = parse_lsblk(fixture("windows"), boot_sources={"/dev/nvme0n1p1"})[0]
        self.assertTrue(disk["is_boot_medium"])

    def test_mount_anywhere_or_holder_makes_disk_busy(self):
        data = fixture("windows")
        data["blockdevices"][0]["children"][2]["mountpoints"] = [None, "/run/media/live/Windows"]
        self.assertTrue(parse_lsblk(data)[0]["_busy"])
        self.assertTrue(parse_lsblk(fixture("windows"), holders={"/dev/nvme0n1p3": True})[0]["_busy"])

    def test_swap_and_device_mapper_children_are_busy(self):
        data = fixture("windows")
        part = data["blockdevices"][0]["children"][2]
        part["mountpoints"] = ["[SWAP]"]
        self.assertTrue(parse_lsblk(data)[0]["_busy"])
        part["mountpoints"] = [None]
        part["children"] = [{"path": "/dev/dm-0", "type": "crypt", "mountpoints": [None]}]
        disk = parse_lsblk(data, boot_sources={"/dev/dm-0"})[0]
        self.assertTrue(disk["_busy"])
        self.assertTrue(disk["is_boot_medium"])

    def test_identity_changes_on_repartition(self):
        before = fixture("windows")
        after = copy.deepcopy(before)
        after["blockdevices"][0]["children"][0]["partuuid"] = "different"
        self.assertNotEqual(parse_lsblk(before)[0]["_identity"], parse_lsblk(after)[0]["_identity"])

    def test_identity_changes_on_same_geometry_vm_hotplug(self):
        before = fixture("blank")
        after = copy.deepcopy(before)
        after["blockdevices"][0]["disk-seq"] += 1
        self.assertNotEqual(parse_lsblk(before)[0]["_identity"], parse_lsblk(after)[0]["_identity"])

    def test_missing_geometry_never_produces_free_extents(self):
        data = fixture("windows")
        del data["blockdevices"][0]["children"][0]["start"]
        self.assertEqual(parse_lsblk(data)[0]["free_extents"], [])

    def test_readonly_partition_propagates_to_disk(self):
        data = fixture("windows")
        data["blockdevices"][0]["children"][0]["ro"] = True
        disk = parse_lsblk(data)[0]
        self.assertTrue(disk["_read_only"])
        self.assertTrue(disk["partitions"][0]["_read_only"])

    def test_public_shape_hides_every_internal_field(self):
        data = {"disks": parse_lsblk(fixture("windows")), "_boot_medium_known": True}
        public = public_inventory(data)
        self.assertNotIn("_boot_medium_known", public)
        self.assertNotIn("_identity", public["disks"][0])
        self.assertNotIn("_esp_free_bytes", public["disks"][0]["partitions"][0])
        self.assertIn("_identity", data["disks"][0])


class ProbeSafetyTests(unittest.TestCase):
    def test_unknown_boot_source_fails_closed(self):
        runner = Mock()
        runner.run.return_value = "{}"
        inventory = Inventory(runner)
        self.assertEqual(inventory._boot_sources(list(_walk(fixture("blank")["blockdevices"]))), (set(), False))

    def test_optical_boot_source_is_known_without_selectable_cd_disk(self):
        runner = Mock()
        runner.run.return_value = json.dumps({"filesystems": [{"source": "/dev/sr0", "maj:min": "11:0"}]})
        inventory = Inventory(runner)
        self.assertEqual(inventory._boot_sources(list(_walk(fixture("blank")["blockdevices"]))), ({"/dev/sr0"}, True))

    def test_probe_skips_boot_and_busy_and_unknown(self):
        for known, boot, busy in [(False, False, False), (True, True, False), (True, False, True)]:
            with self.subTest(known=known, boot=boot, busy=busy):
                data = fixture("windows")
                if busy:
                    data["blockdevices"][0]["mountpoints"] = ["/mnt"]
                runner = Mock()
                runner.run.side_effect = lambda argv: json.dumps(data) if argv[0] == "lsblk" else ""
                inventory = Inventory(runner)
                with patch.object(inventory, "_boot_sources", return_value=({"/dev/nvme0n1"} if boot else set(), known)), \
                        patch.object(inventory, "_stable_ids", return_value={}), \
                        patch.object(inventory, "_holders", return_value={}), \
                        patch.object(inventory, "_inspect") as inspect, \
                        patch("emaki_installer.inventory._online", return_value=False):
                    result = inventory.probe()
                inspect.assert_not_called()
                self.assertEqual(result["_boot_medium_known"], known)
                self.assertFalse(any(call.args[0][0] == "blkid" for call in runner.run.call_args_list))

    def test_linux_and_incomplete_markers_and_windows_case(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "etc").mkdir()
            (root / "etc/os-release").write_text("NAME=Linux\nID=arch\n")
            self.assertEqual(_root_hint(root), "linux")
            (root / ".emaki-install-incomplete").write_text("prepare_disk\n")
            self.assertEqual(_root_hint(root), "emaki-incomplete")
            (root / "efi/microsoft").mkdir(parents=True)
            self.assertTrue(_has_windows(root))

    def test_release_symlink_does_not_escape_probe(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "etc").symlink_to("/etc")
            self.assertIsNone(_root_hint(root))

    def test_ro_ext4_mount_disables_journal_replay_and_cleanup_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            probe = Path(temp) / "probe"
            probe.mkdir()
            disk = parse_lsblk(fixture("windows"))[0]
            part = disk["partitions"][2]
            part["fs"] = "ext4"
            runner = Mock()
            runner.run.return_value = ""
            with patch("emaki_installer.inventory.tempfile.mkdtemp", return_value=str(probe)):
                Inventory(runner)._inspect(disk, part)
            commands = [call.args[0] for call in runner.run.call_args_list]
            self.assertIn("ro,noload,nodev,nosuid,noexec", commands[0])
            self.assertEqual(commands[-1], ["umount", "--", str(probe)])
            self.assertFalse(probe.exists())

    def test_failed_unmount_leaves_directory_and_aborts(self):
        with tempfile.TemporaryDirectory() as temp:
            probe = Path(temp) / "probe"
            probe.mkdir()
            disk = parse_lsblk(fixture("windows"))[0]
            part = disk["partitions"][2]
            part["fs"] = "ext4"
            runner = Mock()
            runner.run.side_effect = lambda argv: (_ for _ in ()).throw(RuntimeError("busy")) if argv[0] == "umount" else ""
            with patch("emaki_installer.inventory.tempfile.mkdtemp", return_value=str(probe)):
                with self.assertRaises(RuntimeError):
                    Inventory(runner)._inspect(disk, part)
            self.assertTrue(probe.exists())

    def test_btrfs_custom_root_hint_and_replay_disabled(self):
        with tempfile.TemporaryDirectory() as temp:
            probe = Path(temp) / "probe"
            (probe / "custom-root").mkdir(parents=True)
            (probe / "custom-root/.emaki-install-incomplete").write_text("unfinished")
            disk = parse_lsblk(fixture("windows"))[0]
            part = disk["partitions"][2]
            part["fs"] = "btrfs"

            def command(argv):
                if argv[:3] == ["btrfs", "filesystem", "show"]:
                    return "Total devices 1 FS bytes used 65536\n"
                if argv[:3] == ["btrfs", "subvolume", "list"]:
                    return "ID 256 gen 1 top level 5 path custom-root\n"
                if argv[0] == "umount":
                    (probe / "custom-root/.emaki-install-incomplete").unlink()
                    (probe / "custom-root").rmdir()
                return ""

            runner = Mock()
            runner.run.side_effect = command
            with patch("emaki_installer.inventory.tempfile.mkdtemp", return_value=str(probe)):
                Inventory(runner)._inspect(disk, part)
            self.assertEqual(part["os_hint"], "emaki-incomplete")
            self.assertEqual(part["_subvolumes"], ["custom-root"])
            mount = next(call.args[0] for call in runner.run.call_args_list if call.args[0][0] == "mount")
            self.assertIn("ro,nologreplay,subvolid=5,nodev,nosuid,noexec", mount)

    def test_multi_device_btrfs_is_never_mounted(self):
        disk = parse_lsblk(fixture("windows"))[0]
        part = disk["partitions"][2]
        part["fs"] = "btrfs"
        runner = Mock()
        runner.run.return_value = "Label: none\n\tTotal devices 2 FS bytes used 65536\n"
        Inventory(runner)._inspect(disk, part)
        self.assertTrue(disk["_busy"])
        self.assertEqual(part["_btrfs_devices"], 2)
        self.assertFalse(any(call.args[0][0] == "mount" for call in runner.run.call_args_list))

    def test_unknown_btrfs_device_count_refuses_disk(self):
        disk = parse_lsblk(fixture("windows"))[0]
        part = disk["partitions"][2]
        part["fs"] = "btrfs"
        runner = Mock()
        runner.run.side_effect = RuntimeError("probe failed")
        Inventory(runner)._inspect(disk, part)
        self.assertTrue(disk["_busy"])
        self.assertIsNone(part["_btrfs_devices"])

    def test_timezone_offline_never_contacts_service(self):
        with patch("emaki_installer.inventory.urlopen") as request:
            self.assertIsNone(_timezone(False))
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
