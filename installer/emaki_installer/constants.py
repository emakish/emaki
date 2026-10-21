# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

MIB = 1024 ** 2
GIB = 1024 ** 3
MIN_DISK = 24 * GIB
TARGET = Path('/mnt/emaki-target')
WORK = Path('/run/emaki-installer')
SOCKET = WORK / 'sock'
LOG = Path('/var/log/emaki-install.log')
# The live boot medium; the worker's unit starts only while it is mounted (copytoram removes it).
BOOT_MOUNT = Path('/run/archiso/bootmnt')
OFFLINE_CONF = Path('/etc/emaki-installer/pacman-offline.conf')
OFFLINE_REPO = 'file:///run/archiso/bootmnt/emaki/repo'
MARKER = '/.emaki-install-incomplete'
# Retained memdisk font; the visible unlock text is pre-rendered artwork.
GRUB_VISIBLE_FONT = Path('/usr/share/emaki-installer/grub/unlock-24.pf2')
MAX_FRAME = 64 * 1024
# Install alongside Windows only when the read-only inventory verifies a safe
# shrink offer. The source switch also gates planner, worker and backend;
# there is no runtime override. 0.8.0 ships it on: the release-input gate passes only by a recorded
# H-W-ALONGSIDE PASS or an owner-signed exception, and the stable channel waits for the real-Windows
# hardware acceptance.
ALONGSIDE = True
# Source-tree test builds only. Packaging fixes this to False; no runtime flag enables it.
TEST_REPO_OVERRIDE = True
PHASES = {'prepare_disk': 5, 'copy_packages': 60, 'bootloader': 10,
          'account': 5, 'settings': 10, 'snapshot': 3, 'update': 5, 'finish': 2}
SUBVOLUMES = {'@': '/', '@home': '/home', '@log': '/var/log',
              '@pkg': '/var/cache/pacman/pkg', '@snapshots': '/.snapshots'}
BTRFS_OPTIONS = ['compress=zstd:3', 'noatime', 'space_cache=v2', 'discard=async']
TEST_PACKAGES = ['openssh', 'grim']
SNAPSHOT_PACKAGES = ['snapper', 'snap-pac', 'grub-btrfs']
# The Rich programs, installed explicitly next to emaki-apps so a person can remove
# any of them and keep it removed. The same names are emaki-apps' optdepends.
RICH_APPS = '''gwenview ark okular thunderbird qbittorrent haruna elisa kate libreoffice-fresh
spectacle obs-studio plasma-systemmonitor partitionmanager filelight
isoimagewriter skanlite kcharselect keepassxc'''.split()
# Installed in both editions, separately from the vital base.
DESKTOP_APPS = 'firefox dolphin kdegraphics-thumbnailers ffmpegthumbs'.split()
PACKAGES = '''base linux linux-lts linux-firmware intel-ucode amd-ucode
btrfs-progs e2fsprogs dosfstools cryptsetup grub efibootmgr terminus-font
inotify-tools zram-generator networkmanager wpa_supplicant wireless-regdb sudo emaki emaki-config
emaki-desktop emaki-keyring emaki-mirrorlist niri-emaki quickshell-emaki'''.split()
