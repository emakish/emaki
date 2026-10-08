# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cached sysfs graphics inventory and deterministic target driver selection."""
from pathlib import Path
import re
import tarfile


CONSOLE_FONT = 'ter-124b'
NVIDIA = 0x10de
HEADERS = ('linux-headers', 'linux-lts-headers')
PREBUILT = ('nvidia-open', 'nvidia-open-lts')
DEFAULT_AVAILABLE = frozenset(PREBUILT)


def _number(path):
    try:
        return int(path.read_text().strip(), 16)
    except (OSError, ValueError):
        return None


def detect(sysfs_root=Path('/sys')):
    """Read cached PCI attributes only; never open config or invoke GPU probes."""
    root = Path(sysfs_root)
    cards = {}
    for card in sorted((root / 'class/drm').glob('card*')):
        if re.fullmatch(r'card[0-9]+', card.name):
            device = (card / 'device').resolve()
            cards.setdefault(device, []).append(card.name)
    paths = set((root / 'bus/pci/devices').glob('*'))
    paths = {path.resolve() for path in paths} | set(cards)
    devices = []
    for path in sorted(paths):
        vendor, device, pci_class = (_number(path / name) for name in ('vendor', 'device', 'class'))
        if pci_class is None or pci_class >> 16 != 3 or vendor is None:
            continue
        devices.append(dict(slot=path.name, vendor=vendor, device=device, **{'class': pci_class},
                            driver=(path / 'driver').resolve().name if (path / 'driver').is_symlink() else '',
                            cards=tuple(cards.get(path, ()))))
    return tuple(devices)


def available_packages(database=None, *, root=Path('/')):
    """Read the discovered offline index, or select official prebuilts online."""
    if database is None:
        # Session policy also imports this module without the installer package.
        from .media import discover_repo
        repo = discover_repo(root)
        if repo is None:
            return DEFAULT_AVAILABLE
        database = repo / 'emaki-offline.db'
    names = set()
    try:
        with tarfile.open(database, 'r:*') as archive:
            for member in archive:
                if member.isfile() and member.name.endswith('/desc') and member.size <= 1024 * 1024:
                    stream = archive.extractfile(member)
                    lines = stream.read().decode('utf-8').splitlines()
                    if '%NAME%' in lines:
                        index = lines.index('%NAME%') + 1
                        if index < len(lines):
                            names.add(lines[index])
    except (OSError, tarfile.TarError, UnicodeError):
        return frozenset()
    return frozenset(names)


def family(device):
    """Use open modules for Turing and newer; keep other cards on nouveau."""
    return 'open' if device is not None and 0x1e00 <= device <= 0xffff else 'nouveau'


def package_selection(devices, available=DEFAULT_AVAILABLE):
    families = {family(gpu['device']) for gpu in devices if gpu['vendor'] == NVIDIA}
    # nvidia-utils blacklists nouveau: preserve it when any card still needs it.
    if families != {'open'}:
        return ()
    modules = PREBUILT if set(PREBUILT) <= set(available) else ('nvidia-open-dkms', 'dkms', *HEADERS)
    return ('emaki-nvidia', *modules, 'nvidia-utils', 'libva-nvidia-driver')


def inventory(sysfs_root=Path('/sys'), database=None, *, root=Path('/')):
    devices = detect(sysfs_root)
    if not any(gpu['vendor'] == NVIDIA for gpu in devices):
        return {}
    return {'_graphics': {'devices': devices, 'available': sorted(available_packages(database, root=root))}}


def planned_packages(inventory):
    graphics = inventory.get('_graphics')
    if not graphics:
        return ()
    return package_selection(graphics['devices'], graphics['available'])


def console_font(plan, sysfs_root=Path('/sys')):
    """Choose from native panel modes without shrinking the kernel's HiDPI font.

    Internal panels take precedence over detachable displays. With no internal panel,
    every connected display must be known and below the kernel's 16x32 threshold.
    NVIDIA with resume may keep the GRUB/GOP framebuffer during initramfs; a
    hybrid system may instead use its integrated GPU. Preserve automatic selection
    because the live mode cannot establish the installed early framebuffer.
    """
    if 'emaki-nvidia' in plan.graphics_packages and plan.config.get('hibernation'):
        return None
    panels, external = [], []
    for connector in sorted((Path(sysfs_root) / 'class/drm').glob('card*-*')):
        try:
            if (connector / 'status').read_text().strip() != 'connected':
                continue
        except (OSError, UnicodeError):
            continue
        try:
            first = (connector / 'modes').read_text().splitlines()[0]
            match = re.fullmatch(r'([1-9][0-9]*)x([1-9][0-9]*)', first)
            mode = tuple(map(int, match.groups())) if match else None
        except (OSError, IndexError, UnicodeError):
            mode = None
        output = connector.name.split('-', 1)[1]
        # Firmware framebuffers report the boot loader's mode, not panel geometry.
        driver = connector.parent / connector.name.split('-', 1)[0] / 'device/driver'
        firmware_drivers = ('simple-framebuffer', 'simpledrm', 'efi-framebuffer', 'vesa-framebuffer')
        if output.startswith('Unknown-') or driver.resolve().name in firmware_drivers:
            mode = None
        (panels if output.startswith(('eDP-', 'LVDS-', 'DSI-')) else external).append(mode)
    modes = panels or external
    # Linux get_default_font uses integer division at each step. Unknown geometry
    # must not force a smaller font onto a high-resolution framebuffer.
    if not modes or any(mode is None or (mode[0] // 8) * (mode[1] // 16) // 1000 >= 22
                        for mode in modes):
        return None
    return CONSOLE_FONT
