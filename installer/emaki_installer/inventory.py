"""Read-only device inventory. No probe command partitions or repairs a filesystem.

Device IDs prefer udev's /dev/disk/by-id links. Machines without a disk serial
(notably QEMU's default virtio disk) use /dev paths; confirmation must compare
the private identity and geometry again before permitting a write.
"""

import json
import hashlib
import os
from pathlib import Path
import re
import shlex
import tempfile
import threading
from urllib.request import urlopen

from . import constants
from .media import discover_repo, MISSING_NOTICE
from .hardware import disk_behind_vmd, secure_boot, vmd_present, VMD_MESSAGE
from .constants import GIB, MIB
from .errors import Code, InstallError, require


ESP_GUID = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
MIN_ROOT = 32 * GIB
WINDOWS_RESERVE = 2 * GIB


def require_verified():
    # Read at call time: constants.ALONGSIDE is the only switch.
    require(constants.ALONGSIDE, Code.UNSUPPORTED_MODE,
            'Installing alongside Windows is not available in this version.')


def ntfs_error(output):
    text = output.lower()
    if 'bitlocker' in text or '-fve-fs-' in text:
        return 'BitLocker encryption is not supported; Windows was not changed.'
    if any(word in text for word in ('hibernat', 'fast startup', 'windows cache')):
        return 'Windows is hibernated or Fast Startup is active. Fully shut down Windows and disable Fast Startup first.'
    if any(word in text for word in ('dirty', 'scheduled for check', 'unclean', 'inconsistent', 'chkdsk /f')):
        return 'NTFS is dirty or needs a Windows consistency check. Run chkdsk in Windows and fully shut down first.'
    if any(word in text for word in ('mounted', 'busy', 'already opened')):
        return 'The Windows partition is mounted, held, or in use. Unmount it before trying again.'
    if re.search(r'\b(error|failed|refused|bad sectors?)\b', text):
        return 'ntfsresize reported an error; shrinking Windows is refused.'
    return None


def parse_ntfs_minimum(output):
    reason = ntfs_error(output)
    require(not reason, Code.SHRINK_BOUNDS, reason or '')
    matches = re.findall(r'^You might resize at ([0-9]+) bytes\b', output, re.M)
    require(len(matches) == 1 and int(matches[0]) > 0, Code.SHRINK_BOUNDS,
            'ntfsresize did not report one unambiguous minimum size.')
    return int(matches[0])


def shrink_offer(minimum, size, start):
    require(type(minimum) is int and 0 < minimum < size, Code.SHRINK_BOUNDS,
            'NTFS minimum size is invalid.')
    require(start > 0 and start % MIB == 0 and size % MIB == 0, Code.SHRINK_BOUNDS,
            'Windows partition boundaries must be aligned to 1 MiB; this layout is unsupported.')
    keep = (minimum + WINDOWS_RESERVE + MIB - 1) // MIB * MIB
    free = max(0, size - keep)
    require(free >= MIN_ROOT, Code.SHRINK_BOUNDS,
            'Less than 32 GiB can be freed while keeping Windows its minimum size plus 2 GiB.')
    return {'min_bytes': minimum, 'max_free_bytes': free}


def hibernation_reason(root):
    """RO ntfs-3g mount only. Absent file or a zero 4 KiB header is safe.

    Follow ntfs-3g's header-size check; also reject all unknown nonzero headers.
    A read-only mount alone does NOT establish that Windows was shut down.
    """
    matches = [p for p in root.iterdir() if p.name.casefold() == 'hiberfil.sys']
    if not matches:
        return None
    if len(matches) != 1 or matches[0].is_symlink() or not matches[0].is_file():
        return 'Cannot safely inspect the Windows hibernation file.'
    with matches[0].open('rb') as stream:
        header = stream.read(4096)
    if len(header) != 4096 or any(header):
        return 'Windows is hibernated or Fast Startup is active (nonzero or incomplete hibernation header). Fully shut down Windows first.'
    return None


def bitlocker_signature(path):
    with Path(path).open('rb', buffering=0) as stream:
        stream.seek(3)
        signature = stream.read(8)
    require(len(signature) == 8, Code.SHRINK_BOUNDS, 'Cannot read the Windows volume signature.')
    return signature == b'-FVE-FS-'


def windows_system(root):
    """Do not offer a data volume whose hibernation file belongs elsewhere."""
    path = root
    for component in ('Windows', 'System32', 'config', 'SYSTEM'):
        matches = [p for p in path.iterdir() if p.name.casefold() == component.casefold()]
        if len(matches) != 1 or matches[0].is_symlink():
            return False
        path = matches[0]
        if component != 'SYSTEM' and not path.is_dir():
            return False
    return path.is_file() and path.stat().st_size > 0


def verify_ntfs_size(path, size):
    """Read primary/backup boot records after resize, without clearing dirty.

    ntfsresize reserves one sector, then rounds down to whole clusters. With
    an exact byte size it writes the backup record at the partition's end.
    This verifies geometry, not Windows consistency or every file's contents.
    """
    with Path(path).open('rb', buffering=0) as stream:
        boot = stream.read(512)
        require(len(boot) == 512 and boot[3:11] == b'NTFS    ' and boot[510:512] == b'\x55\xaa',
                Code.UNSAFE_DISK, 'Unreadable NTFS boot record after resize.')
        sector = int.from_bytes(boot[11:13], 'little')
        encoded = boot[13]
        clusters = 1 << (256 - encoded) if encoded > 128 else encoded
        require(sector in (512, 1024, 2048, 4096) and clusters > 0
                and clusters & (clusters - 1) == 0 and clusters * sector < size,
                Code.UNSAFE_DISK, 'Unsupported NTFS sector or cluster geometry.')
        cluster = sector * clusters
        sectors = int.from_bytes(boot[40:48], 'little')
        require(sectors * sector == (size - sector) // cluster * cluster,
                Code.UNSAFE_DISK, 'NTFS size does not match the approved shrink.')
        for offset in (48, 56):
            location = int.from_bytes(boot[offset:offset + 8], 'little')
            require(0 < location < sectors // clusters, Code.UNSAFE_DISK,
                    'NTFS metadata lies outside the resized filesystem.')
        stream.seek(size - sector)
        require(stream.read(512) == boot, Code.UNSAFE_DISK,
                'NTFS backup boot record does not match after resize.')


def verify_resized_ntfs(runner, path, size):
    require(runner.run(['blockdev', '--getsize64', path]).strip() == str(size),
            Code.UNSAFE_DISK, 'Kernel partition size differs after resize.')
    verify_ntfs_size(path, size)
    root = Path(tempfile.mkdtemp(prefix='ntfs-check-', dir='/run/emaki-installer'))
    mounted = False
    try:
        runner.run(['ntfs-3g', '-o', 'ro,norecover,nodev,nosuid,noexec,show_sys_files', path, str(root)])
        mounted = True
        require(windows_system(root), Code.UNSAFE_DISK, 'Windows system metadata is unreadable after resize.')
        # Opening the directory and volume bitmap also exercises NTFS metadata.
        require(os.statvfs(root).f_blocks > 0, Code.UNSAFE_DISK, 'NTFS allocation metadata is unreadable.')
    finally:
        if mounted:
            runner.run(['umount', '--', str(root)])
        root.rmdir()


def parse_gpt_info(text):
    """sgdisk --info is read-only; require all preservation metadata."""
    patterns = {
        'guid': r'^Partition unique GUID: ([0-9A-Fa-f-]{36})$',
        'type': r'^Partition GUID code: ([0-9A-Fa-f-]{36})(?: .*)?$',
        'start': r'^First sector: ([0-9]+)(?: .*)?$',
        'end': r'^Last sector: ([0-9]+)(?: .*)?$',
        'attributes': r'^Attribute flags: ([0-9A-Fa-f]{16})$',
        'name': r"^Partition name: '(.*)'$",
    }
    result = {}
    for key, pattern in patterns.items():
        values = re.findall(pattern, text, re.M)
        require(len(values) == 1, Code.UNSAFE_DISK, 'Cannot verify GPT partition ' + key + '.')
        result[key] = values[0]
    for key in ('guid', 'type'):
        require(re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', result[key]),
                Code.UNSAFE_DISK, 'Invalid GPT GUID.')
        result[key] = result[key].lower()
    result['start'], result['end'] = int(result['start']), int(result['end'])
    require(result['end'] >= result['start'] > 0 and
            not any(ord(c) < 32 for c in result['name']), Code.UNSAFE_DISK, 'Invalid GPT geometry or name.')
    return result


def read_gpt(runner, disk, number):
    return parse_gpt_info(runner.run(['sgdisk', f'--info={number}', disk]))


def verify_gpt(runner, disk):
    output = runner.run(['sgdisk', '--verify', disk])
    require('No problems found.' in output and not re.search(r'warning|error|caution', output, re.I),
            Code.UNSAFE_DISK, 'GPT validation failed; Windows will not be resized.')


def efi_loaders(root):
    """Only known EFI paths, ordinary bounded files; FAT lookup ignores case."""
    result = {}
    for name in ('EFI/Microsoft/Boot/bootmgfw.efi', 'EFI/BOOT/BOOTX64.EFI'):
        path = root
        for component in name.split('/'):
            path = path / component
            require(not path.is_symlink(), Code.UNSAFE_DISK, 'EFI loader path contains a symlink.')
        if path.exists():
            require(path.is_file() and 0 < path.stat().st_size <= 64 * MIB,
                    Code.UNSAFE_DISK, 'EFI loader is not a valid bounded file.')
            result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


BOOT_MOUNT = "/run/archiso/bootmnt"
EFI = Path("/sys/firmware/efi")
BUSY_REASON = "This disk is busy [LVM, RAID or an open encrypted volume]. Close its volumes, then try again."
ENCRYPTED_TYPES = {
    "crypto_LUKS": "encrypted Linux system (LUKS)",
    "BitLocker": "encrypted Windows drive (BitLocker)",
    "cs_fvault2": "encrypted macOS disk (FileVault 2)",
    "apfs": "macOS disk (APFS)",
}


def encrypted_warning(path, kind):
    description = ENCRYPTED_TYPES[kind] if kind == "apfs" else f"locked {ENCRYPTED_TYPES[kind]}"
    return (f"{path} contains a {description}. "
            "The installer cannot check its contents; they may belong to a set of several disks. "
            "Type ERASE to confirm that this volume and any connected data can be erased.")


def unconfirmed_identity_warning(path):
    return (f"{path} has no readable volume ID, so the installer cannot erase this device safely. "
            "Choose another target or wipe this device in the partition editor. "
            "If this is a partition, you can also leave it unformatted in Manual mode.")


# Signatures that live on one device only: erasing such a device cannot damage
# another disk. Every other signature must prove that it stands alone, and one
# this code cannot answer for (f2fs, bcachefs, zfs_member, ...) is refused.
SINGLE_DEVICE = frozenset((
    "ReFS", "befs", "bfs", "cramfs",
    "erofs", "exfat", "exfs", "ext2", "ext3", "ext4", "ext4dev", "gfs", "gfs2", "hfs",
    "hfsplus", "hpfs", "iso9660", "jfs", "minix", "nilfs2", "ntfs", "ntfs3", "ocfs",
    "ocfs2", "reiser4", "reiserfs", "romfs", "squashfs", "squashfs3", "swap",
    "swsuspend", "sysv", "ubifs", "udf", "ufs", "vfat", "xenix", "xfs", "zonefs",
))
LSBLK_COLUMNS = (
    "NAME,KNAME,PATH,TYPE,SIZE,LOG-SEC,START,PARTN,PARTTYPE,PARTUUID,PTTYPE,"
    "PTUUID,PARTLABEL,FSTYPE,UUID,LABEL,MOUNTPOINTS,MODEL,SERIAL,WWN,TRAN,RM,RO,MAJ:MIN,PKNAME,DISK-SEQ"
)


def memory_size(table=Path('/sys/firmware/dmi/tables/DMI')):
    """Installed RAM from SMBIOS type 17, without rounding usable MemTotal up."""
    total = 0
    try:
        table_data = table.read_bytes()
        offset = 0
        while offset + 4 <= len(table_data):
            kind, length = table_data[offset:offset + 2]
            if length < 4 or offset + length > len(table_data):
                return 0
            data = table_data[offset:offset + length]
            end = table_data.find(b'\0\0', offset + length)
            if end < 0:
                return 0
            offset = end + 2
            if kind != 17:
                continue
            if len(data) < 14:
                return 0
            size = int.from_bytes(data[12:14], 'little')
            if size == 0xffff:
                return 0
            if size == 0x7fff:
                if len(data) < 32:
                    return 0
                total += (int.from_bytes(data[28:32], 'little') & 0x7fffffff) * MIB
            else:
                total += (size & 0x7fff) * (1024 if size & 0x8000 else MIB)
    except OSError:
        return 0
    return total


def firmware():
    """(installable UEFI, firmware word size). GRUB is installed for x86_64-efi only.

    A firmware that does not expose fw_platform_size is taken as 64-bit.
    """
    if not EFI.is_dir():
        return False, None
    try:
        size = (EFI / "fw_platform_size").read_text().strip()
    except OSError:
        return True, None
    return size == "64", int(size) if size.isdigit() else None


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


def _model(node):
    """lsblk has no MODEL for some buses (virtio-blk); name such a disk after its bus."""
    names = {"nvme": "NVMe", "sata": "SATA", "usb": "USB", "virtio": "Virtio", "mmc": "MMC"}
    bus = names.get(_bus(node))
    return (node.get("model") or "").strip() or (f"{bus} disk" if bus else "Disk")


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


def busy_reason(nodes, holders):
    details = []
    for node in nodes:
        path = _path(node)
        mounts = _mountpoints(node)
        if mounts:
            details.append(f"{path} is open at {', '.join(mounts)} [mount]. Close files there, then unmount it [umount].")
        held = holders.get(path)
        if isinstance(held, list) and held:
            details.append(f"{path} is held by {', '.join('/dev/' + item for item in held)} [device mapper or RAID]. Deactivate that volume or stop that array before trying again.")
        elif node.get('type') not in {'disk', 'part'}:
            kind = node.get('type')
            action = 'Close it [cryptsetup close]' if kind == 'crypt' else ('Deactivate it [lvchange -an]' if kind == 'lvm' else 'Stop it [mdadm --stop]')
            details.append(f"{path} is active [{kind}]. {action}, then try again.")
    return ' '.join(details) or BUSY_REASON


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
                        ("partuuid", "uuid", "parttype", "partlabel", "start", "size", "maj:min")}
            part = {
                "id": stable_ids.get(child_path, f"{disk_id}-part{number}" if disk_id != path else child_path),
                "path": child_path, "number": number, "uuid": child.get("uuid") or None,
                "fs": fs, "size_bytes": _number(child.get("size")),
                "start_bytes": _number(child.get("start")) * 512,
                "mountpoint": mounts[0] if mounts else None, "label": child.get("label") or None,
                "os_hint": "macos" if fs in {"apfs", "hfs", "hfsplus"} else None,
                "esp": parttype in {ESP_GUID, "0xef", "ef"},
                "_identity": identity, "_partuuid": child.get("partuuid"),
                "_partlabel": child.get("partlabel") or '',
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
            "id": disk_id, "path": path, "model": _model(node),
            "size_bytes": _number(node.get("size")), "bus": _bus(node),
            "removable": _truth(node.get("rm")),
            "is_boot_medium": any(_path(item) in boot_sources for item in descendants),
            "partitions": parts, "closed_encrypted": [],
            "free_extents": _extents(_number(node.get("size")), parts)
            if all(p["_geometry_known"] for p in parts) else [],
            "shrink": None,
            "_busy_reason": busy_reason(descendants, holders),
            "_busy": any(_mountpoints(item) or holders.get(_path(item))
                         or item.get("type") not in {"disk", "part"} for item in descendants),
            "_read_only": any(_truth(item.get("ro")) for item in descendants), "_identity": identity,
            "_sector_size": _number(node.get("log-sec"), 512),
            "_pttype": node.get("pttype"), "_id_fallback": disk_id == path,
            # Public twin of _pttype: the window greys out Manual where the planner refuses it.
            "partition_table": node.get("pttype"),
            # A signature on the disk node itself: a whole-disk filesystem or member.
            "_fs": node.get("fstype") or None,
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
    try:
        with urlopen("https://ipapi.co/timezone", timeout=2.5) as response:
            value = response.read(256).decode("ascii").strip()
        if (re.fullmatch(r"[A-Za-z0-9_+\-/]+", value) and ".." not in value
                and not value.startswith("/") and Path("/usr/share/zoneinfo", value).is_file()):
            return value
    except Exception:
        pass
    return None


class Inventory:
    def __init__(self, runner, *, root=Path("/"), sysfs_root=None):
        self.sysfs_root = Path(sysfs_root) if sysfs_root is not None else Path(root) / "sys"
        self.runner = runner
        self.root = Path(root)
        self.tz_guess = None
        self._timezone_thread = None
        self._timezone_lock = threading.Lock()
        self._timezone_done = threading.Event()

    def start_timezone_lookup(self):
        """Start the session's one best-effort lookup, outside every disk probe."""
        with self._timezone_lock:
            if self._timezone_thread is not None or not _online():
                return

            def lookup():
                try:
                    self.tz_guess = _timezone(True)
                finally:
                    self._timezone_done.set()

            # DNS and redirects can outlive the socket timeout. Never join this
            # daemon thread: it must not hold up inventory or service shutdown.
            self._timezone_thread = threading.Thread(target=lookup, daemon=True)
            self._timezone_thread.start()

    def timezone_guess(self):
        self.start_timezone_lookup()
        # Offline, no lookup has started yet: still pending (a later network starts it),
        # but the window polls slowly instead of every half second.
        return dict(tz_guess=self.tz_guess, pending=not self._timezone_done.is_set(),
                    offline=self._timezone_thread is None)

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
                holders[path] = [entry.name for entry in (Path("/sys/class/block") / Path(path).name / "holders").iterdir()]
            except OSError:
                holders[path] = True  # Missing/unreadable safety data fails closed.
        return holders

    def _boot_marker(self, nodes):
        """Retain boot-device exclusion when copytoram has unmounted bootmnt."""
        try:
            tokens = shlex.split(_read(self.root / 'proc/cmdline'))
        except ValueError:
            return set(), False
        markers = [token.split('=', 1)[1] for token in tokens if token.startswith('archisodevice=')]
        labels = [token.split('=', 1)[1] for token in tokens if token.startswith('archisolabel=')]
        matches = set()
        for node in nodes:
            path = _path(node)
            for marker in markers:
                if marker.startswith('/dev/'):
                    resolved = os.path.realpath(self.root / marker.lstrip('/'))
                    expected = str(self.root / path.lstrip('/'))
                    if resolved == expected:
                        matches.add(path)
            if labels and node.get('label') in labels:
                matches.add(path)
        # Ambiguous labels or contradictory boot markers prove nothing.
        return (matches, True) if len(matches) == 1 else (set(), False)

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
                    return self._boot_marker(nodes)
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
                return self._boot_marker(nodes)
        return set(), False

    def _signature(self, path):
        """Fresh low-level blkid tags; None when the device cannot be read.

        lsblk's FSTYPE comes from the udev database, which can be stale.
        """
        try:
            output = self.runner.run(["blkid", "--probe", "--output", "export", path])
        except InstallError as exc:
            # blkid exits 2 without a word when it finds no signature, and
            # with an error message when it could not open the device.
            return {} if exc.returncode == 2 and not exc.output.strip() else None
        except (OSError, RuntimeError):
            return None
        return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)

    def _count(self, argv, pattern):
        """The one device count a read-only tool reports, or None."""
        matches = re.findall(pattern, self._optional(argv), re.M)
        return int(matches[0]) if len(matches) == 1 else None

    def _btrfs_devices(self, path):
        return self._count(["btrfs", "filesystem", "show", "--raw", path], r"Total devices\s+(\d+)")

    @staticmethod
    def _set_reason(path, kind, count):
        if count == 1:
            return None
        if not count:
            return f"Cannot verify that {path} is the only device of {kind}."
        return f"{path} is one of {count} devices of {kind}; erasing it would damage the other devices."

    def _member_reason(self, path, fs):
        """Why erasing this device could damage another disk; None when it cannot.

        Every command here only reads. A missing or failing tool refuses.
        """
        if not fs or fs in SINGLE_DEVICE or fs in ENCRYPTED_TYPES:
            return None
        if fs == "btrfs":
            return self._set_reason(path, "a Btrfs filesystem", self._btrfs_devices(path))
        if fs == "LVM2_member":
            return self._set_reason(path, "an LVM volume group", self._count(
                ["pvs", "--readonly", "--noheadings", "--options", "pv_count", path], r"^\s*(\d+)\s*$"))
        if fs == "linux_raid_member":
            return self._set_reason(path, "a RAID array", self._count(
                ["mdadm", "--examine", "--export", path], r"^MD_DEVICES=(\d+)$"))
        return f"{path} carries a {fs} signature and may be part of a multi-device set."

    @staticmethod
    def _refuse(disk, reason):
        disk["_busy"] = True
        disk.setdefault("reason", reason)

    @staticmethod
    def _record_encrypted(disk, path, kind, tags):
        if kind in ENCRYPTED_TYPES:
            # Only fresh on-device evidence can authorize destruction; never
            # reuse a UUID from the udev cache after a signature disappears.
            uuid = tags.get("UUID") if tags.get("TYPE") == kind else None
            disk["closed_encrypted"].append({
                "path": path, "type": kind, "uuid": uuid,
                "warning": encrypted_warning(path, kind) if uuid else unconfirmed_identity_warning(path),
            })

    def _inspect(self, disk, part):
        fs = part["fs"]
        if fs not in {"vfat", "ext4", "btrfs", "ntfs", "ntfs3"}:
            return
        if fs == "btrfs":
            part["_btrfs_devices"] = self._btrfs_devices(part["path"])
            if part["_btrfs_devices"] != 1:
                # An unverified volume could be a member of a multi-device
                # filesystem. Erasing one member would also damage other disks.
                self._refuse(disk, self._set_reason(part["path"], "a Btrfs filesystem", part["_btrfs_devices"]))
                return
        options = {"vfat": "ro", "ext4": "ro,noload",
                   "btrfs": "ro,nologreplay,subvolid=5",
                   "ntfs": "ro,norecover", "ntfs3": "ro,norecover"}[fs] + ",nodev,nosuid,noexec"
        root = Path(tempfile.mkdtemp(prefix="probe-", dir="/run/emaki-installer"))
        mounted = False
        try:
            try:
                if fs in {"ntfs", "ntfs3"}:
                    self.runner.run(["ntfs-3g", "-o", options, part["path"], str(root)])
                else:
                    self.runner.run(["mount", "--types", fs, "--options", options, "--", part["path"], str(root)])
                mounted = True
            except (OSError, RuntimeError):
                return
            if part["esp"]:
                usage = os.statvfs(root)
                part["_esp_free_bytes"] = usage.f_bavail * usage.f_frsize
                if _has_windows(root):
                    part["os_hint"] = "windows"
                part['_efi_loaders'] = efi_loaders(root)
            if fs in {"ntfs", "ntfs3"} and windows_system(root):
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

    def _ntfs_shrink(self, disk, part):
        """Every command in this probe opens NTFS read-only; never recover it."""
        refusal = {'min_bytes': 0, 'max_free_bytes': 0}
        if disk['_busy'] or part.get('_busy') or part.get('mountpoint'):
            return dict(refusal, reason='The Windows partition or its disk is mounted, held, or in use.')
        if disk['_read_only'] or disk['is_boot_medium']:
            return dict(refusal, reason='The disk is read-only or is the live boot medium.')
        if disk['_pttype'] != 'gpt' or not any(p['esp'] for p in disk['partitions']):
            return dict(refusal, reason='Alongside Windows requires a GPT disk with an existing ESP.')
        root, mounted = None, False
        try:
            if bitlocker_signature(part['path']):
                return dict(refusal, reason='BitLocker encryption is not supported; Windows was not changed.')
            esps = [p for p in disk['partitions'] if p['esp']]
            require(len(esps) == 1 and esps[0]['fs'] == 'vfat', Code.ESP_SPACE,
                    'Alongside requires exactly one FAT32 ESP.')
            esp = esps[0]
            require((esp.get('_esp_free_bytes') or 0) >= 32 * MIB, Code.ESP_SPACE,
                    'ESP needs at least 32 MiB of verified free space.')
            require('EFI/Microsoft/Boot/bootmgfw.efi' in esp.get('_efi_loaders', {}), Code.SHRINK_BOUNDS,
                    'No verified Microsoft EFI loader exists on this disk.')
            verify_gpt(self.runner, disk['path'])
            for original in disk['partitions']:
                metadata = read_gpt(self.runner, disk['path'], original['number'])
                sector = disk['_sector_size']
                require(metadata['start'] * sector == original['start_bytes']
                        and (metadata['end'] + 1) * sector == original['start_bytes'] + original['size_bytes']
                        and metadata['guid'] == str(original.get('_partuuid')).lower()
                        and metadata['type'] == original.get('_parttype')
                        and metadata['name'] == original.get('_partlabel', ''), Code.UNSAFE_DISK,
                        'GPT identity or geometry could not be verified.')
                original['_gpt'] = metadata
            output = self.runner.run(['ntfsresize', '--info', '--no-action', part['path']])
            minimum = parse_ntfs_minimum(output)
            root = Path(tempfile.mkdtemp(prefix='ntfs-probe-', dir='/run/emaki-installer'))
            # --readwrite ntfs-3g.probe can change NTFS metadata. Do not use it.
            self.runner.run(['ntfs-3g', '-o', 'ro,norecover,nodev,nosuid,noexec,show_sys_files',
                             part['path'], str(root)])
            mounted = True
            require(windows_system(root), Code.SHRINK_BOUNDS,
                    'This NTFS partition is not a verified Windows system volume.')
            reason = hibernation_reason(root)
            if reason:
                return dict(refusal, reason=reason)
            return shrink_offer(minimum, part['size_bytes'], part['start_bytes'])
        except (OSError, RuntimeError) as exc:
            reason = ntfs_error(getattr(exc, 'output', '') or str(exc))
            return dict(refusal, reason=reason or 'Cannot verify NTFS, hibernation state or GPT metadata: ' + str(exc))
        finally:
            if mounted:
                self.runner.run(['umount', '--', str(root)])
            if root is not None:
                root.rmdir()

    def _alongside(self, disk, boot_known):
        if not constants.ALONGSIDE:
            return
        candidates = [p for p in disk['partitions'] if p['fs'] in ('ntfs', 'ntfs3', 'BitLocker')]
        if not candidates:
            return
        largest = max(p['size_bytes'] for p in candidates)
        tied = sum(p['size_bytes'] == largest for p in candidates) > 1
        for part in candidates:
            part['os_hint'] = 'windows'
            offer = (self._ntfs_shrink(disk, part) if boot_known else
                     {'min_bytes': 0, 'max_free_bytes': 0, 'reason': 'Cannot identify the live boot medium.'})
            part['_shrink_probe'] = dict(offer)
            if part['size_bytes'] != largest or tied:
                offer['reason'] = ('Windows candidates have equal sizes; selection is ambiguous.' if tied else
                                   'Only the largest Windows/NTFS partition is considered; this partition is not offered.')
            part['shrink'] = offer
        selected = next(p for p in candidates if p['size_bytes'] == largest)
        disk['shrink'] = dict(selected['shrink'], partition_id=selected['id'])

    def probe(self):
        payload = json.loads(self.runner.run(["lsblk", "--json", "--bytes", "--paths", "--tree",
                                             "--output", LSBLK_COLUMNS]))
        nodes = list(_walk(payload.get("blockdevices", [])))
        boot_sources, boot_known = self._boot_sources(nodes)
        disks = parse_lsblk(payload, stable_ids=self._stable_ids(), boot_sources=boot_sources,
                            holders=self._holders(nodes))
        for disk in disks:
            if disk_behind_vmd(disk["path"], self.root):
                disk["_vmd"] = True
            if not boot_known or disk["_busy"] or disk["is_boot_medium"] or disk["_read_only"]:
                self._alongside(disk, boot_known)
                continue
            # parse_lsblk keeps a filesystem type for partitions only, so a
            # whole-disk member of a set spanning other disks is caught here.
            tags = self._signature(disk["path"])
            if tags is None:
                self._refuse(disk, f"Cannot read the signature of {disk['path']}; "
                                   "it may be part of a multi-device set.")
            else:
                disk["_fs"] = tags.get("TYPE") or disk["_fs"]
                self._record_encrypted(disk, disk["path"], disk["_fs"], tags)
                reason = self._member_reason(disk["path"], disk["_fs"])
                if reason:
                    self._refuse(disk, reason)
            for part in disk["partitions"]:
                if disk["_busy"]:
                    break
                # Bypass blkid's cache; metadata is used only for this probe.
                tags = self._signature(part["path"])
                if tags is None:
                    self._refuse(disk, f"Cannot read the signature of {part['path']}; "
                                       "it may be part of a multi-device set.")
                    break
                if tags.get("TYPE"):
                    part["fs"] = tags["TYPE"]
                if tags.get("UUID"):
                    part["uuid"] = tags["UUID"]
                    part["_identity"]["uuid"] = tags["UUID"]
                if tags.get("LABEL"):
                    part["label"] = tags["LABEL"]
                if part["fs"] in {"apfs", "hfs", "hfsplus"}:
                    part["os_hint"] = "macos"
                self._record_encrypted(disk, part["path"], part["fs"], tags)
                # An inactive LVM or md member has no child device in lsblk.
                # _inspect counts the devices of a Btrfs partition itself.
                reason = part["fs"] != "btrfs" and self._member_reason(part["path"], part["fs"])
                if reason:
                    self._refuse(disk, reason)
                    break
                self._inspect(disk, part)
            self._alongside(disk, boot_known)
        for disk in disks:
            # _inspect can still raise _busy above, so this runs last. The
            # interface words the boot medium and mounted partitions itself.
            if (disk["_busy"] and not disk["is_boot_medium"]
                    and not any(part["mountpoint"] for part in disk["partitions"])):
                disk.setdefault("reason", disk.get("_busy_reason", BUSY_REASON))
        online = _online()
        from .graphics import inventory as graphics_inventory
        graphics = graphics_inventory(self.sysfs_root, root=self.root)
        pci = ""
        if graphics:
            # Keep hybrid discrete GPUs asleep: even the display label uses
            # cached attributes once NVIDIA hardware has been identified.
            gpu = "; ".join(f"Graphics controller (PCI {row['vendor']:04x}:"
                            + (f"{row['device']:04x}" if row['device'] is not None else "unknown") + ")"
                            for row in graphics['_graphics']['devices'])
        else:
            pci = self._optional(["lspci"])
            gpu = "; ".join(line for line in pci.splitlines()
                            if any(kind in line for kind in ("VGA compatible controller", "3D controller", "Display controller")))
        cpu = next((line.split(":", 1)[1].strip() for line in _read("/proc/cpuinfo").splitlines()
                    if line.startswith(("model name", "Hardware")) and ":" in line), "Unknown CPU")
        uefi, uefi_bits = firmware()
        hardware = {}
        state = secure_boot(self.root)
        if state is not False:
            hardware["secure_boot"] = state
        if vmd_present(self.root, pci) and not any(not d["is_boot_medium"] for d in disks):
            hardware["disk_notice"] = VMD_MESSAGE
        if discover_repo(self.root) is None:
            hardware["media_notice"] = MISSING_NOTICE if online else (
                "The installation files are missing. Connect to the internet before installing (Ventoy or copytoram).")
        return {**graphics, "hardware": hardware, "disks": disks, "gpu": gpu or "Unknown GPU", "cpu": cpu,
                "memory_bytes": memory_size(),
                "uefi": uefi, "uefi_bits": uefi_bits, "network": {"online": online},
                "tz_guess": self.tz_guess, "scale_guess": None,
                "_boot_medium_known": boot_known}
