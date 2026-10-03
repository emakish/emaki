"""Read-only device inventory. No probe command partitions or repairs a filesystem.

Device IDs prefer udev's /dev/disk/by-id links. Machines without a disk serial
(notably QEMU's default virtio disk) use /dev paths; confirmation must compare
the private identity and geometry again before permitting a write.
"""

import json
import os
from pathlib import Path
import re
import tempfile
import threading
from urllib.request import urlopen


ESP_GUID = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
MIB = 1024**2
BOOT_MOUNT = "/run/archiso/bootmnt"
LSBLK_COLUMNS = (
    "NAME,KNAME,PATH,TYPE,SIZE,LOG-SEC,START,PARTN,PARTTYPE,PARTUUID,PTTYPE,"
    "PTUUID,FSTYPE,UUID,LABEL,MOUNTPOINTS,MODEL,SERIAL,WWN,TRAN,RM,RO,MAJ:MIN,PKNAME,DISK-SEQ"
)


def public_inventory(value):
    """Keep internal safety data out of the frozen public protocol."""
    if isinstance(value, dict):
        return {key: public_inventory(item) for key, item in value.items()
                if not key.startswith("_")}
    if isinstance(value, list):
        return [public_inventory(item) for item in value]
    return value


def _number(value, default=0):
    try:
        return int(value) if value is not None else default
    except (ValueError, TypeError):
        return default


def _truth(value):
    return value is True or value in (1, "1", "true")


def _path(node):
    name = node.get("path") or node.get("kname") or node.get("name") or ""
    return name if name.startswith("/dev/") else "/dev/" + name


def _walk(nodes):
    for node in nodes:
        yield node
        yield from _walk(node.get("children", []))


def _mountpoints(node):
    mounts = node.get("mountpoints", node.get("mountpoint")) or []
    return [item for item in (mounts if isinstance(mounts, list) else [mounts]) if item]


def _bus(node):
    transport = (node.get("tran") or "").lower()
    name = Path(_path(node)).name
    if transport in {"nvme", "sata", "usb", "virtio", "mmc"}:
        return transport
    if name.startswith("vd"):
        return "virtio"
    if name.startswith("mmcblk"):
        return "mmc"
    if name.startswith("nvme"):
        return "nvme"
    return "unknown"


def _extents(size, partitions):
    """Conservative MiB-aligned gaps, reserving both GPT metadata regions."""
    end = max(MIB, size // MIB * MIB - MIB)
    cursor = MIB
    gaps = []
    for part in sorted(partitions, key=lambda row: row["start_bytes"]):
        start = part["start_bytes"] // MIB * MIB
        if start > cursor:
            gaps.append({"start_bytes": cursor, "size_bytes": start - cursor})
        cursor = max(cursor, (part["start_bytes"] + part["size_bytes"] + MIB - 1) // MIB * MIB)
    if end > cursor:
        gaps.append({"start_bytes": cursor, "size_bytes": end - cursor})
    return gaps


def parse_lsblk(payload, *, stable_ids=None, boot_sources=(), holders=None):
    """Parse recorded lsblk JSON without reading files or running commands.

    START is always in 512-byte sectors, including with --bytes and a 4Kn disk
    (util-linux `lsblk --list-columns` documents this explicitly). SIZE is bytes.
    `holders` maps device paths to bool; true includes unreadable holder state.
    Boot sources can be any node in the lsblk tree, including a dm descendant.
    """
    if isinstance(payload, str):
        payload = json.loads(payload)
    stable_ids = stable_ids or {}
    holders = holders or {}
    boot_sources = set(boot_sources)
    disks = []
    seen = set()
    for node in _walk(payload.get("blockdevices", [])):
        path = _path(node)
        if node.get("type") != "disk" or path in seen:
            continue
        seen.add(path)
        descendants = list(_walk([node]))
        disk_id = stable_ids.get(path, path)
        parts = []
        for child in descendants[1:]:
            if child.get("type") != "part" or _path(child) in {p["path"] for p in parts}:
                continue
            child_path = _path(child)
            fs = child.get("fstype") or None
            parttype = (child.get("parttype") or "").lower()
            number = _number(child.get("partn"))
            if not number:
                match = re.search(r"(?:p)?(\d+)$", Path(child_path).name)
                number = int(match[1]) if match else 0
            mounts = _mountpoints(child)
            identity = {key: child.get(key) for key in
                        ("partuuid", "uuid", "parttype", "start", "size", "maj:min")}
            part = {
                "id": stable_ids.get(child_path, f"{disk_id}-part{number}" if disk_id != path else child_path),
                "path": child_path, "number": number, "uuid": child.get("uuid") or None,
                "fs": fs, "size_bytes": _number(child.get("size")),
                "start_bytes": _number(child.get("start")) * 512,
                "mountpoint": mounts[0] if mounts else None, "label": child.get("label") or None,
                "os_hint": "macos" if fs in {"apfs", "hfs", "hfsplus"} else None,
                "esp": parttype in {ESP_GUID, "0xef", "ef"},
                "_identity": identity, "_partuuid": child.get("partuuid"),
                "_parttype": parttype, "_esp_free_bytes": None, "_subvolumes": None,
                "_btrfs_devices": None, "_mountpoints": mounts,
                "_busy": bool(mounts or holders.get(child_path) or child.get("children")),
                "_read_only": _truth(child.get("ro")),
                "_geometry_known": child.get("start") is not None and number > 0,
            }
            parts.append(part)
        identity = {key: node.get(key) for key in ("serial", "wwn", "maj:min", "size", "ptuuid", "disk-seq")}
        identity["partitions"] = [p["_identity"] for p in parts]
        disks.append({
            "id": disk_id, "path": path, "model": (node.get("model") or "Unknown disk").strip(),
            "size_bytes": _number(node.get("size")), "bus": _bus(node),
            "removable": _truth(node.get("rm")),
            "is_boot_medium": any(_path(item) in boot_sources for item in descendants),
            "partitions": parts,
            "free_extents": _extents(_number(node.get("size")), parts)
            if all(p["_geometry_known"] for p in parts) else [],
            # Alongside is deferred. Never advertise unverified NTFS shrink bounds.
            "shrink": None,
            "_busy": any(_mountpoints(item) or holders.get(_path(item))
                         or item.get("type") not in {"disk", "part"} for item in descendants),
            "_read_only": any(_truth(item.get("ro")) for item in descendants), "_identity": identity,
            "_sector_size": _number(node.get("log-sec"), 512),
            "_pttype": node.get("pttype"), "_id_fallback": disk_id == path,
        })
    return disks


def _read(path, limit=131072):
    try:
        with Path(path).open(encoding="utf-8", errors="replace") as stream:
            return stream.read(limit)
    except OSError:
        return ""


def _safe_file(root, relative):
    """Read only ordinary files below a probe root, without following symlinks."""
    current = root
    try:
        for part in Path(relative).parts:
            current = current / part
            if current.is_symlink():
                return ""
        return _read(current, 16384) if current.is_file() else ""
    except OSError:
        return ""


def _root_hint(root):
    if (root / ".emaki-install-incomplete").is_file():
        return "emaki-incomplete"
    release = _safe_file(root, "etc/os-release") or _safe_file(root, "usr/lib/os-release")
    return "linux" if re.search(r"^ID=", release, re.MULTILINE) else None


def _has_windows(root):
    try:
        for efi in root.iterdir():
            if efi.name.casefold() == "efi" and efi.is_dir() and not efi.is_symlink():
                return any(child.name.casefold() == "microsoft" and child.is_dir()
                           and not child.is_symlink() for child in efi.iterdir())
    except OSError:
        pass
    return False


def _online():
    # A default route is a deliberately modest availability heuristic, not a
    # connectivity promise. The later online update can fail non-fatally.
    for line in _read("/proc/net/route").splitlines()[1:]:
        fields = line.split()
        if len(fields) > 3 and fields[1] == "00000000":
            try:
                if int(fields[3], 16) & 1:
                    return True
            except ValueError:
                pass
    for line in _read("/proc/net/ipv6_route").splitlines():
        fields = line.split()
        if len(fields) >= 10 and fields[0] == "0" * 32 and fields[1] == "00" and fields[-1] != "lo":
            return True
    return False


def _timezone(online):
    if not online:
        return None
    result = []

    def lookup():
        try:
            with urlopen("https://ipapi.co/timezone", timeout=2.5) as response:
                value = response.read(256).decode("ascii").strip()
            if (re.fullmatch(r"[A-Za-z0-9_+\-/]+", value) and ".." not in value
                    and not value.startswith("/") and Path("/usr/share/zoneinfo", value).is_file()):
                result.append(value)
        except Exception:
            pass

    # A socket timeout alone does not bound DNS lookup or a chain of redirects.
    # A daemon lookup gives inventory an actual three-second wall-clock limit.
    thread = threading.Thread(target=lookup, daemon=True)
    thread.start()
    thread.join(3)
    return result[0] if result and not thread.is_alive() else None


class Inventory:
    def __init__(self, runner):
        self.runner = runner

    def _optional(self, argv):
        try:
            return self.runner.run(argv)
        except (OSError, RuntimeError):
            return ""

    def _stable_ids(self):
        ids = {}
        # WWN IDs come before transport/serial links; partition links share the
        # same preference. Sorting also makes aliases stable across probes.
        paths = sorted(Path("/dev/disk/by-id").glob("*"),
                       key=lambda p: (not p.name.startswith(("wwn-", "nvme-eui.")), p.name))
        for path in paths:
            if path.is_symlink():
                ids.setdefault(os.path.realpath(path), str(path))
        return ids

    def _holders(self, nodes):
        holders = {}
        for node in nodes:
            path = _path(node)
            try:
                holders[path] = any((Path("/sys/class/block") / Path(path).name / "holders").iterdir())
            except OSError:
                holders[path] = True  # Missing/unreadable safety data fails closed.
        return holders

    def _boot_sources(self, nodes):
        """Resolve the exact live-medium mount and, for loop ISOs, its backing disk."""
        known = {_path(node): node for node in nodes}
        numbers = {node.get("maj:min"): _path(node) for node in nodes if node.get("maj:min")}
        query = ["findmnt", "--json", "--mountpoint", BOOT_MOUNT, "--output", "SOURCE,MAJ:MIN"]
        visited = set()
        for _ in range(8):
            try:
                data = json.loads(self._optional(query))
                mounts = data.get("filesystems", [])
                if len(mounts) != 1:
                    return set(), False
                source = mounts[0].get("source", "").split("[", 1)[0]
                path = numbers.get(mounts[0].get("maj:min")) or os.path.realpath(source)
                if path not in known or path in visited:
                    return set(), False
                visited.add(path)
                if known[path].get("type") != "loop":
                    # Optical drives need not appear in selectable disks, but
                    # their positively resolved source still makes safety known.
                    return {path}, True
                backing = _read(Path("/sys/class/block") / Path(path).name / "loop/backing_file").strip()
                if not backing.startswith("/"):
                    return set(), False
                query = ["findmnt", "--json", "--target", backing, "--output", "SOURCE,MAJ:MIN"]
            except (ValueError, TypeError, KeyError):
                return set(), False
        return set(), False

    def _inspect(self, disk, part):
        fs = part["fs"]
        if fs not in {"vfat", "ext4", "btrfs"}:
            return
        if fs == "btrfs":
            info = self._optional(["btrfs", "filesystem", "show", "--raw", part["path"]])
            match = re.search(r"Total devices\s+(\d+)", info)
            part["_btrfs_devices"] = int(match[1]) if match else None
            if part["_btrfs_devices"] != 1:
                # An unverified volume could be a member of a multi-device
                # filesystem. Erasing one member would also damage other disks.
                disk["_busy"] = True
                return
        options = {"vfat": "ro", "ext4": "ro,noload",
                   "btrfs": "ro,nologreplay,subvolid=5"}[fs] + ",nodev,nosuid,noexec"
        root = Path(tempfile.mkdtemp(prefix="probe-", dir="/run/emaki-installer"))
        mounted = False
        try:
            try:
                self.runner.run(["mount", "--types", fs, "--options", options, "--", part["path"], str(root)])
                mounted = True
            except (OSError, RuntimeError):
                return
            if part["esp"]:
                usage = os.statvfs(root)
                part["_esp_free_bytes"] = usage.f_bavail * usage.f_frsize
                if _has_windows(root):
                    part["os_hint"] = "windows"
            if fs in {"ext4", "btrfs"}:
                part["os_hint"] = _root_hint(root)
            if fs == "btrfs":
                output = self.runner.run(["btrfs", "subvolume", "list", str(root)])
                names = [line.split(" path ", 1)[1] for line in output.splitlines() if " path " in line]
                part["_subvolumes"] = names
                # Manual layouts can name root something other than @. Inspect
                # only top-level subvolumes, never nested historical snapshots.
                candidates = [name for name in names if "/" not in name and name not in (".", "..")]
                for name in sorted(candidates, key=lambda name: name not in ("@", "root"))[:256]:
                    if (root / name).is_symlink():
                        continue
                    hint = _root_hint(root / name)
                    if hint == "emaki-incomplete":
                        part["os_hint"] = hint
                        break
                    part["os_hint"] = part["os_hint"] or hint
        except OSError:
            # Unknown free space remains None and cannot authorize ESP reuse.
            part["_esp_free_bytes"] = None
        finally:
            if mounted:
                # A cleanup failure must abort inventory. Never recursively
                # delete a mount directory: it might still be a real filesystem.
                self.runner.run(["umount", "--", str(root)])
            root.rmdir()

    def probe(self):
        payload = json.loads(self.runner.run(["lsblk", "--json", "--bytes", "--paths", "--tree",
                                             "--output", LSBLK_COLUMNS]))
        nodes = list(_walk(payload.get("blockdevices", [])))
        boot_sources, boot_known = self._boot_sources(nodes)
        disks = parse_lsblk(payload, stable_ids=self._stable_ids(), boot_sources=boot_sources,
                            holders=self._holders(nodes))
        for disk in disks:
            if not boot_known or disk["_busy"] or disk["is_boot_medium"] or disk["_read_only"]:
                continue
            for part in disk["partitions"]:
                if disk["_busy"]:
                    break
                # Bypass blkid's cache; metadata is used only for this probe.
                output = self._optional(["blkid", "--probe", "--output", "export", part["path"]])
                tags = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
                if tags.get("TYPE"):
                    part["fs"] = tags["TYPE"]
                if tags.get("UUID"):
                    part["uuid"] = tags["UUID"]
                    part["_identity"]["uuid"] = tags["UUID"]
                if tags.get("LABEL"):
                    part["label"] = tags["LABEL"]
                if part["fs"] in {"apfs", "hfs", "hfsplus"}:
                    part["os_hint"] = "macos"
                self._inspect(disk, part)
        online = _online()
        pci = self._optional(["lspci"])
        gpu = "; ".join(line for line in pci.splitlines()
                        if any(kind in line for kind in ("VGA compatible controller", "3D controller", "Display controller")))
        cpu = next((line.split(":", 1)[1].strip() for line in _read("/proc/cpuinfo").splitlines()
                    if line.startswith(("model name", "Hardware")) and ":" in line), "Unknown CPU")
        memory = re.search(r"^MemTotal:\s+(\d+)\s+kB", _read("/proc/meminfo"), re.MULTILINE)
        return {"disks": disks, "gpu": gpu or "Unknown GPU", "cpu": cpu,
                "memory_bytes": int(memory[1]) * 1024 if memory else 0,
                "uefi": Path("/sys/firmware/efi").is_dir(), "network": {"online": online},
                "tz_guess": _timezone(online), "scale_guess": None,
                "_boot_medium_known": boot_known}
