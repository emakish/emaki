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
# Install alongside Windows. Off: the inventory does not probe Windows, the
# interface never offers the mode, and planner, worker and backend refuse it.
# It returns only after a check on a real Windows (real shrink, BitLocker).
# Only a source change turns it on; there is no runtime override.
ALONGSIDE = False
PHASES = {'prepare_disk': 5, 'copy_packages': 60, 'bootloader': 10,
          'account': 5, 'settings': 10, 'snapshot': 3, 'update': 5, 'finish': 2}
SUBVOLUMES = {'@': '/', '@home': '/home', '@log': '/var/log',
              '@pkg': '/var/cache/pacman/pkg', '@snapshots': '/.snapshots'}
BTRFS_OPTIONS = ['compress=zstd:3', 'noatime', 'space_cache=v2', 'discard=async']
TEST_PACKAGES = ['openssh', 'grim']
PACKAGES = '''base linux linux-lts linux-firmware intel-ucode amd-ucode
btrfs-progs e2fsprogs dosfstools cryptsetup grub efibootmgr snapper snap-pac grub-btrfs
inotify-tools zram-generator networkmanager wpa_supplicant wireless-regdb sudo emaki emaki-config
emaki-desktop emaki-keyring emaki-mirrorlist niri-emaki quickshell-emaki'''.split()
