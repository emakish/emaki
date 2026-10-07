# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only firmware and storage probes, rooted for fixture tests."""
from pathlib import Path


SECURE_BOOT_MESSAGE = (
    'Emaki cannot start with firmware signature checking enabled (Secure Boot). '
    'Restart into your firmware settings, turn Secure Boot off, then start the USB again. '
    'No disk has been changed.')
SECURE_BOOT_UNKNOWN = (
    'The firmware signature setting could not be read (Secure Boot). '
    'Restart into your firmware settings, turn Secure Boot off, then start the USB again. '
    'No disk has been changed.')
VMD_MESSAGE = (
    'No installation disk is visible behind the Intel storage controller (VMD/RST). '
    'In your firmware settings, disable VMD or Intel RST and select AHCI storage mode, '
    'then restart the USB. If another operating system is installed, prepare it for '
    'AHCI before changing this setting; otherwise it may stop booting.')


def secure_boot(root=Path('/')):
    """False if firmware lacks Secure Boot; None when its state is unreadable."""
    efi = Path(root) / 'sys/firmware/efi'
    if not efi.is_dir():
        return False
    variable = efi / 'efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c'
    try:
        data = variable.read_bytes()
    except FileNotFoundError:
        return False if variable.parent.is_dir() else None
    except OSError:
        return None
    return bool(data[4]) if len(data) == 5 and data[4] in (0, 1) else None


def disk_behind_vmd(device, root=Path('/')):
    """Follow the block device's PCI ancestors, not unrelated loaded drivers."""
    node = Path(root) / 'sys/class/block' / Path(device).name
    try:
        node = node.resolve(strict=True)
        boundary = (Path(root) / 'sys').resolve()
        if not node.is_relative_to(boundary):
            return False
        while node != boundary:
            driver = node / 'driver'
            if driver.is_symlink() and driver.resolve().name == 'vmd':
                return True
            node = node.parent
    except (OSError, RuntimeError):
        pass
    return False


def vmd_present(root=Path('/'), pci=''):
    sys = Path(root) / 'sys'
    if (sys / 'module/vmd').is_dir():
        return True
    for device in (sys / 'bus/pci/devices').glob('*'):
        if (device / 'driver').is_symlink() and (device / 'driver').resolve().name == 'vmd':
            return True
    return any('intel' in line.lower() and
               ('volume management device' in line.lower() or 'vmd' in line.lower())
               for line in pci.splitlines())
