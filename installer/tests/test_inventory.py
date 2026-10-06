"""Inventory tests use lsblk-shaped fixtures and fake runners; no disk access."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from emaki_installer.planner import fingerprint, make_plan
from support import alongside_on, config
from emaki_installer.errors import Code, InstallError
from emaki_installer.inventory import (
    BUSY_REASON, GIB, Inventory, MIB, _has_windows, _root_hint, _timezone, _walk,
    parse_lsblk, public_inventory, unconfirmed_identity_warning,
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

    def test_disk_without_a_model_is_named_after_its_bus(self):
        for change in ("missing", "", "  ", None):
            with self.subTest(model=change):
                data = fixture("blank")
                if change == "missing":
                    del data["blockdevices"][0]["model"]
                else:
                    data["blockdevices"][0]["model"] = change
                self.assertEqual(parse_lsblk(data)[0]["model"], "Virtio disk")
        data = fixture("blank")
        data["blockdevices"][0].update(model=None, tran="usb")
        self.assertEqual(parse_lsblk(data)[0]["model"], "USB disk")
        data["blockdevices"][0].update(tran=None, path="/dev/sdz", name="/dev/sdz", kname="/dev/sdz")
        self.assertEqual(parse_lsblk(data)[0]["model"], "Disk")
        self.assertEqual(parse_lsblk(fixture("blank"))[0]["model"], "Virtio Block Device")

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

    def test_public_inventory_names_the_partition_table(self):
        # The window greys out Manual on a disk the planner would refuse for it (not GPT).
        for pttype in ("gpt", "dos", None):
            data = fixture("windows")
            data["blockdevices"][0]["pttype"] = pttype
            public = public_inventory({"disks": parse_lsblk(data)})
            self.assertEqual(public["disks"][0]["partition_table"], pttype)
        self.assertIsNone(public_inventory({"disks": parse_lsblk(fixture("blank"))})["disks"][0]["partition_table"])

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

    def busy_probe(self, edit):
        data = fixture("windows")
        edit(data["blockdevices"][0])
        runner = Mock()
        runner.run.side_effect = lambda argv: json.dumps(data) if argv[0] == "lsblk" else ""
        inventory = Inventory(runner)
        with patch.object(inventory, "_boot_sources", return_value=({"/dev/sr0"}, True)), \
                patch.object(inventory, "_stable_ids", return_value={}), \
                patch.object(inventory, "_holders", return_value={}), \
                patch.object(inventory, "_inspect"), \
                patch("emaki_installer.inventory._online", return_value=False):
            return public_inventory(inventory.probe())["disks"][0]

    def test_busy_disk_carries_a_public_reason(self):
        def lvm(disk):
            disk["children"][2]["children"] = [{"path": "/dev/dm-0", "type": "lvm"}]
        public = self.busy_probe(lvm)
        self.assertEqual(public["reason"], BUSY_REASON)
        self.assertFalse([key for key in public if key.startswith("_")])

    def test_mounted_and_free_disks_carry_no_busy_reason(self):
        def mounted(disk):
            disk["children"][2]["mountpoints"] = ["/mnt"]
        # The interface has its own sentence for a mounted partition.
        self.assertNotIn("reason", self.busy_probe(mounted))
        self.assertNotIn("reason", self.busy_probe(lambda disk: None))

    def test_windows_disk_carries_no_alongside_offer_by_default(self):
        # The shape the interface receives for a Windows disk in 0.2.
        public = self.busy_probe(lambda disk: None)
        self.assertIsNone(public["shrink"])
        self.assertFalse([part["path"] for part in public["partitions"] if "shrink" in part])
        with alongside_on(), patch("emaki_installer.inventory.bitlocker_signature", return_value=True):
            self.assertIn("BitLocker", self.busy_probe(lambda disk: None)["shrink"]["reason"])

    def firmware_probe(self, size):
        """probe() on a machine whose firmware reports this fw_platform_size."""
        data = fixture("blank")
        runner = Mock()
        runner.run.side_effect = lambda argv: json.dumps(data) if argv[0] == "lsblk" else ""
        inventory = Inventory(runner)
        with tempfile.TemporaryDirectory() as temp:
            efi = Path(temp) / "efi"
            if size != "bios":
                efi.mkdir()
            if size not in (None, "bios"):
                (efi / "fw_platform_size").write_text(size)
            with patch("emaki_installer.inventory.EFI", efi), \
                    patch.object(inventory, "_boot_sources", return_value=({"/dev/sr0"}, True)), \
                    patch.object(inventory, "_stable_ids", return_value={}), \
                    patch.object(inventory, "_holders", return_value={}), \
                    patch("emaki_installer.inventory._online", return_value=False):
                return inventory.probe()

    def test_32_bit_uefi_is_reported_as_not_installable(self):
        for size, uefi, bits in (("32\n", False, 32), ("64\n", True, 64), (None, True, None),
                                 ("bios", False, None)):
            with self.subTest(size=size):
                result = self.firmware_probe(size)
                self.assertIs(result["uefi"], uefi)
                self.assertEqual(public_inventory(result)["uefi_bits"], bits)
                c = config()
                if uefi:
                    make_plan(c, result)
                    continue
                with self.assertRaises(InstallError) as raised:
                    make_plan(c, result)
                self.assertEqual(raised.exception.code, Code.UEFI_REQUIRED)
                self.assertEqual(raised.exception.message,
                                 "64-bit UEFI is required." if bits else "UEFI boot is required.")

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


def failed(status, output=""):
    return InstallError(Code.COMMAND_FAILED, f"exited with status {status}.", output=output, returncode=status)


class MembershipTests(unittest.TestCase):
    """A device that is one of several in a set must never be offered for erasing."""

    READ_ONLY = (["lsblk"], ["blkid", "--probe"], ["btrfs", "filesystem", "show"],
                 ["pvs", "--readonly"], ["mdadm", "--examine"], ["lspci"], ["mount"])

    def probe(self, name, answers=None, counts=2, edit=None, holders=None):
        """Probe a fixture. blkid repeats the fixture's FSTYPE unless overridden.

        `answers` maps (tool, device) to output or to an exception; `counts`
        is how many devices every multi-device tool reports.
        """
        data = fixture(name)
        if edit:
            edit(data)
        types = {node["path"]: node.get("fstype") for node in _walk(data["blockdevices"])}
        answers = answers or {}
        self.commands = []

        def run(argv):
            self.commands.append(argv)
            tool, device = argv[0], argv[-1]
            answer = answers.get((tool, device))
            if isinstance(answer, Exception):
                raise answer
            if answer is not None:
                return answer
            if tool == "lsblk":
                return json.dumps(data)
            if tool == "blkid":
                if not types[device]:
                    raise failed(2)
                return f"DEVNAME={device}\nTYPE={types[device]}\nUUID=fresh-{device}\nUSAGE=filesystem\n"
            if tool == "btrfs":
                return f"Label: none  uuid: fixture\n\tTotal devices {counts} FS bytes used 147456\n"
            if tool == "pvs":
                return f"  {counts}\n"
            if tool == "mdadm":
                return f"MD_LEVEL=raid0\nMD_DEVICES={counts}\nMD_METADATA=1.2\nMD_EVENTS=0\n"
            if tool == "mount":
                raise failed(32)
            return ""

        runner = Mock()
        runner.run.side_effect = run
        inventory = Inventory(runner)
        mkdtemp = tempfile.mkdtemp
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(inventory, "_boot_sources", return_value=({"/dev/sr0"}, True)), \
                patch.object(inventory, "_stable_ids", return_value={}), \
                patch.object(inventory, "_holders", return_value=holders or {}), \
                patch("emaki_installer.inventory.tempfile.mkdtemp",
                      side_effect=lambda **kw: mkdtemp(dir=temp)), \
                patch("emaki_installer.inventory._online", return_value=False):
            result = inventory.probe()
        result["uefi"] = True
        for argv in self.commands:
            self.assertTrue(any(argv[:len(head)] == head for head in self.READ_ONLY), argv)
        return result

    def refused(self, result, path, *words):
        disk = next(d for d in result["disks"] if d["path"] == path)
        public = next(d for d in public_inventory(result)["disks"] if d["path"] == path)
        self.assertTrue(disk["_busy"], path)
        for word in words:
            self.assertIn(word, public["reason"])
        self.assertFalse([key for key in public if key.startswith("_")])
        for mode in ("erase", "manual"):
            c = config(mode=mode)
            c["disk_id"] = path
            with self.assertRaises(InstallError) as raised:
                make_plan(c, result)
            self.assertEqual(raised.exception.code, Code.DISK_BUSY)
            self.assertEqual(raised.exception.message, public["reason"])

    def installable(self, result, path):
        disk = next(d for d in result["disks"] if d["path"] == path)
        self.assertFalse(disk["_busy"], path)
        self.assertNotIn("reason", disk)
        for fs in ("btrfs", "ext4"):
            c = config(fs=fs)
            c["disk_id"] = path
            self.assertEqual(make_plan(c, result).disk["path"], path)

    def test_whole_disk_members_of_a_set_are_refused_with_a_public_reason(self):
        result = self.probe("member-disks")
        for path in ("/dev/vdb", "/dev/vdc"):
            self.refused(result, path, path, "one of 2 devices", "Btrfs")
        for path in ("/dev/vdd", "/dev/vde"):
            self.refused(result, path, path, "one of 2 devices", "LVM")
        for path in ("/dev/vdf", "/dev/vdg"):
            self.refused(result, path, path, "one of 2 devices", "RAID")
        self.refused(result, "/dev/vdi", "/dev/vdi", "zfs_member")

    def test_single_device_and_blank_whole_disks_stay_installable(self):
        result = self.probe("member-disks")
        for path in ("/dev/vdk", "/dev/vdl", "/dev/vdm"):  # ext4, NTFS, blank
            self.installable(result, path)
        result = self.probe("member-disks", counts=1)
        for path in ("/dev/vdb", "/dev/vdd", "/dev/vdf", "/dev/vdj"):
            self.installable(result, path)
        self.refused(result, "/dev/vdi", "zfs_member")

    def test_whole_disk_probe_commands_are_the_read_only_ones(self):
        self.probe("member-disks")
        for argv in (["blkid", "--probe", "--output", "export", "/dev/vdm"],
                     ["btrfs", "filesystem", "show", "--raw", "/dev/vdb"],
                     ["pvs", "--readonly", "--noheadings", "--options", "pv_count", "/dev/vdd"],
                     ["mdadm", "--examine", "--export", "/dev/vdf"]):
            self.assertIn(argv, self.commands)

    def test_unreadable_whole_disk_signature_refuses(self):
        for error in (failed(1), failed(8), failed(2, "blkid: error: /dev/vdm: Input/output error\n"),
                      RuntimeError("blkid is missing")):
            with self.subTest(error=error):
                result = self.probe("member-disks", {("blkid", "/dev/vdm"): error,
                                                     ("blkid", "/dev/vdk"): error})
                self.refused(result, "/dev/vdm", "Cannot read the signature of /dev/vdm")
                self.refused(result, "/dev/vdk", "Cannot read the signature of /dev/vdk")
                self.installable(result, "/dev/vdl")

    def test_failing_or_silent_membership_tool_refuses(self):
        for tool, path in (("btrfs", "/dev/vdj"), ("pvs", "/dev/vdd"), ("mdadm", "/dev/vdf")):
            for answer in (failed(1), RuntimeError("missing tool"), "", "no count here\n",
                           "  WARNING: something\n  1\n  2\n" if tool == "pvs" else "Total devices 0\nMD_DEVICES=0\n"):
                with self.subTest(tool=tool, answer=answer):
                    result = self.probe("member-disks", {(tool, path): answer}, counts=1)
                    self.refused(result, path, "Cannot verify", path)

    def test_lvm_warning_lines_do_not_hide_the_count(self):
        warning = "  WARNING: Couldn't find device with uuid fixture.\n  WARNING: VG vg0 is missing PV fixture.\n"
        result = self.probe("member-disks", {("pvs", "/dev/vdd"): warning + "  2\n"})
        self.refused(result, "/dev/vdd", "one of 2 devices")
        result = self.probe("member-disks", {("pvs", "/dev/vdd"): warning + "  1\n"}, counts=1)
        self.installable(result, "/dev/vdd")

    def test_fresh_signature_wins_over_stale_lsblk_and_the_reverse_still_refuses(self):
        # udev has not seen the signature yet: lsblk says blank, the disk is a member.
        result = self.probe("member-disks", {("blkid", "/dev/vdm"): "DEVNAME=/dev/vdm\nTYPE=LVM2_member\n"})
        self.refused(result, "/dev/vdm", "LVM")
        # udev still remembers a member that blkid no longer reports: stay closed.
        result = self.probe("member-disks", {("blkid", "/dev/vdf"): failed(2)})
        self.refused(result, "/dev/vdf", "RAID")

    def test_whole_disk_signature_is_part_of_the_plan_fingerprint(self):
        before = self.probe("member-disks")
        after = self.probe("member-disks", {("blkid", "/dev/vdm"): "TYPE=ext4\n"})
        blank = lambda result: next(d for d in result["disks"] if d["path"] == "/dev/vdm")
        self.assertEqual(blank(after)["_fs"], "ext4")
        self.assertNotEqual(fingerprint(blank(before)), fingerprint(blank(after)))
        self.assertEqual(fingerprint(blank(before)), fingerprint(blank(self.probe("member-disks"))))

    def test_boot_medium_and_busy_disks_are_not_probed_for_membership(self):
        def mounted(data):
            data["blockdevices"][0]["mountpoints"] = ["/mnt"]
        self.probe("member-disks", edit=mounted)
        self.assertNotIn("/dev/vdb", [argv[-1] for argv in self.commands])

    def test_partition_members_of_a_set_are_refused_with_a_public_reason(self):
        result = self.probe("member-partitions")
        self.refused(result, "/dev/sda", "/dev/sda2", "one of 2 devices", "Btrfs")
        self.refused(result, "/dev/sdb", "/dev/sdb2", "one of 2 devices", "LVM")
        self.refused(result, "/dev/sdc", "/dev/sdc2", "one of 2 devices", "RAID")
        self.refused(result, "/dev/sde", "/dev/sde2", "zfs_member")
        for argv in (["pvs", "--readonly", "--noheadings", "--options", "pv_count", "/dev/sdb2"],
                     ["mdadm", "--examine", "--export", "/dev/sdc2"]):
            self.assertIn(argv, self.commands)

    def test_open_luks_partition_stays_busy(self):
        result = self.probe("member-partitions")
        disk = next(d for d in result["disks"] if d["path"] == "/dev/sdf")
        self.assertTrue(disk["_busy"])
        self.assertNotIn("/dev/sdf2", [argv[-1] for argv in self.commands])
        self.assertEqual(disk["reason"], BUSY_REASON)

    def encrypted(self, result, path, device, kind='crypto_LUKS'):
        disk = next(d for d in result['disks'] if d['path'] == path)
        self.assertFalse(disk['_busy'])
        volume = next(v for v in disk['closed_encrypted'] if v['path'] == device)
        self.assertEqual(volume['type'], kind)
        self.assertIn(volume, next(d for d in public_inventory(result)['disks'] if d['path'] == path)['closed_encrypted'])
        c = dict(config(), disk_id=path)
        with self.assertRaises(InstallError) as raised:
            make_plan(c, result)
        self.assertEqual(raised.exception.code, Code.ENCRYPTED_CONFIRMATION)
        self.assertIn(volume['warning'], raised.exception.message)
        if volume['uuid']:
            c['confirmed_encrypted'] = [{k: v[k] for k in ('path', 'type', 'uuid')} for v in disk['closed_encrypted']]
            make_plan(c, result)
        return volume

    def test_windows_offer_survives_an_unrelated_locked_volume(self):
        def mixed(data):
            disk = data['blockdevices'][0]
            extra = copy.deepcopy(disk['children'][-1])
            extra.update(path='/dev/nvme0n1p9', name='/dev/nvme0n1p9', partn=9,
                         fstype='crypto_LUKS', uuid='locked-ubuntu', children=[])
            disk['children'].append(extra)
        def shrink(disk, part):
            self.assertFalse(disk['_busy'])
            return {'min_bytes': GIB, 'max_free_bytes': 40 * GIB}
        with alongside_on(), patch.object(Inventory, '_ntfs_shrink', side_effect=shrink):
            disk = self.probe('windows', edit=mixed)['disks'][0]
        self.assertNotIn('reason', disk['shrink'])
        self.assertEqual(disk['closed_encrypted'][0]['path'], '/dev/nvme0n1p9')

    def test_closed_encrypted_partitions_name_the_encryption_type(self):
        for kind in ('BitLocker', 'cs_fvault2', 'apfs'):
            result = self.probe('member-partitions', {('blkid', '/dev/sdd2'): f'TYPE={kind}\nUUID=encrypted-uuid\n'})
            volume = self.encrypted(result, '/dev/sdd', '/dev/sdd2', kind)
            self.assertIn('Windows' if kind == 'BitLocker' else 'macOS', volume['warning'])

    def test_closed_encrypted_whole_disk_requires_confirmation(self):
        for kind in ('crypto_LUKS', 'BitLocker', 'cs_fvault2', 'apfs'):
            with self.subTest(kind=kind):
                result = self.probe('member-disks', {('blkid', '/dev/vdh'): f'TYPE={kind}\nUUID=encrypted-uuid\n'})
                self.encrypted(result, '/dev/vdh', '/dev/vdh', kind)

    def test_apfs_container_requires_confirmation_without_claiming_it_is_encrypted(self):
        result = self.probe('macos')
        volume = self.encrypted(result, '/dev/sda', '/dev/sda2', 'apfs')
        self.assertEqual(volume['warning'],
                         '/dev/sda2 contains a macOS disk (APFS). '
                         'The installer cannot check its contents; they may belong to a set of several disks. '
                         'Type ERASE to confirm that this volume and any connected data can be erased.')
        self.assertEqual(len(result['disks'][0]['closed_encrypted']), 1)

    def test_apfs_without_a_fresh_volume_id_has_one_actionable_refusal(self):
        for name, disk, device in (('member-disks', '/dev/vdh', '/dev/vdh'),
                                   ('macos', '/dev/sda', '/dev/sda2')):
            with self.subTest(device=device):
                result = self.probe(name, {('blkid', device): 'TYPE=apfs\n'})
                volume = self.encrypted(result, disk, device, 'apfs')
                self.assertIsNone(volume['uuid'])
                self.assertEqual(volume['warning'], unconfirmed_identity_warning(device))
                self.assertEqual(volume['warning'],
                                 f'{device} has no readable volume ID, so the installer cannot erase this device safely. '
                                 'Choose another target or wipe this device in the partition editor. '
                                 'If this is a partition, you can also leave it unformatted in Manual mode.')
                self.assertNotIn('Type ERASE', volume['warning'])

    def test_held_apfs_containers_stay_busy(self):
        for name, disk, device in (('member-disks', '/dev/vdh', '/dev/vdh'),
                                   ('macos', '/dev/sda', '/dev/sda2')):
            with self.subTest(device=device):
                def apfs(data):
                    next(n for n in _walk(data['blockdevices']) if n['path'] == device)['fstype'] = 'apfs'
                result = self.probe(name, edit=apfs, holders={device: True})
                self.refused(result, disk, BUSY_REASON)
                self.assertNotIn(device, [argv[-1] for argv in self.commands if argv[0] == 'blkid'])

    def test_hfs_and_hfsplus_stay_installable_without_confirmation(self):
        for kind in ('hfs', 'hfsplus'):
            with self.subTest(kind=kind):
                result = self.probe('macos', {('blkid', '/dev/sda2'): f'TYPE={kind}\nUUID=mac-uuid\n'})
                self.installable(result, '/dev/sda')
                self.assertEqual(result['disks'][0]['closed_encrypted'], [])

    @staticmethod
    def luks_layout(data, layout):
        disk = next(d for d in data["blockdevices"] if d["path"] == "/dev/sdd")
        esp, root = disk["children"]
        esp.update(label="EMAKI_EFI", partlabel="EFI")
        root.update(label="emaki-root", partlabel="Emaki", size=disk["size"] - GIB - 2 * MIB)
        if layout == "emaki":
            return
        # An EFI + /boot + LUKS layout, or LUKS beside an unrelated data partition.
        extra = copy.deepcopy(root)
        extra.update(name="/dev/sdd3", path="/dev/sdd3", partn=3, **{
            "maj:min": "254:259", "partuuid": "10000000-0000-4000-8000-000016000003"})
        if layout == "ubuntu":
            root.update(fstype="ext4", label="boot", partlabel="boot", uuid="boot-uuid", size=GIB)
            extra.update(label=None, partlabel=None, start=root["start"] + GIB // 512,
                         size=extra["size"] - GIB)
        else:
            root["size"] -= GIB
            extra.update(fstype="ntfs", label="Data", partlabel="Data", uuid="data-uuid",
                         start=root["start"] + root["size"] // 512, size=GIB)
        disk["children"].append(extra)

    def test_closed_luks_partitions_require_confirmation_regardless_of_layout_or_labels(self):
        for layout, encrypted in (("emaki", "/dev/sdd2"), ("ubuntu", "/dev/sdd3"),
                                  ("mixed", "/dev/sdd2")):
            with self.subTest(layout=layout):
                result = self.probe("member-partitions", edit=lambda data: self.luks_layout(data, layout))
                self.encrypted(result, "/dev/sdd", encrypted)
                self.assertNotIn(encrypted, [a[-2] for a in self.commands if a[0] == "mount"])

    def test_fresh_luks_signature_overrides_cached_plain_filesystem(self):
        for fixture_name, disk, device in (("member-disks", "/dev/vdk", "/dev/vdk"),
                                           ("member-partitions", "/dev/sdg", "/dev/sdg2")):
            with self.subTest(device=device):
                result = self.probe(fixture_name, {("blkid", device): "TYPE=crypto_LUKS\nVERSION=2\nUUID=fresh-luks\n"})
                self.encrypted(result, disk, device)

    def test_stale_luks_signature_cannot_reuse_cached_uuid(self):
        for fixture_name, disk, device in (("member-disks", "/dev/vdh", "/dev/vdh"),
                                           ("member-partitions", "/dev/sdd", "/dev/sdd2")):
            with self.subTest(device=device):
                result = self.probe(fixture_name, {("blkid", device): failed(2)})
                self.encrypted(result, disk, device)

    def test_disks_with_single_device_partitions_stay_installable(self):
        result = self.probe("member-partitions")
        for path in ("/dev/sdg", "/dev/sdh", "/dev/sdi"):  # ext4, NTFS, swap
            self.installable(result, path)
        result = self.probe("member-partitions", counts=1)
        for path in ("/dev/sda", "/dev/sdb", "/dev/sdc"):
            self.installable(result, path)
        for name in ("windows", "blank"):
            with self.subTest(fixture=name):
                result = self.probe(name)
                self.installable(result, result["disks"][0]["path"])

    def test_unreadable_partition_signature_refuses(self):
        for error in (failed(1), failed(2, "blkid: error: /dev/sdg2: Input/output error\n"),
                      RuntimeError("blkid is missing")):
            with self.subTest(error=error):
                result = self.probe("member-partitions", {("blkid", "/dev/sdg2"): error})
                self.refused(result, "/dev/sdg", "Cannot read the signature of /dev/sdg2")
                self.installable(result, "/dev/sdi")

    def test_failing_membership_tool_refuses_partition(self):
        for tool, path in (("pvs", "/dev/sdb"), ("mdadm", "/dev/sdc")):
            for answer in (failed(5), RuntimeError("missing tool"), ""):
                with self.subTest(tool=tool, answer=answer):
                    result = self.probe("member-partitions", {(tool, path + "2"): answer}, counts=1)
                    self.refused(result, path, "Cannot verify", path + "2")

    def test_fresh_partition_signature_wins_over_stale_lsblk(self):
        result = self.probe("member-partitions",
                            {("blkid", "/dev/sdg2"): "DEVNAME=/dev/sdg2\nTYPE=linux_raid_member\n"})
        self.refused(result, "/dev/sdg", "/dev/sdg2", "RAID")


if __name__ == "__main__":
    unittest.main()
