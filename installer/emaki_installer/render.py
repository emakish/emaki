"""Target file generation and verification, independent of archinstall."""
import json
import re

from .constants import BTRFS_OPTIONS
from .errors import Code, require

CONSOLE = {'us': 'us', 'ru': 'ru', 'ua': 'ua-utf', 'cz': 'cz', 'de': 'de-latin1',
           'fr': 'fr', 'es': 'es', 'gb': 'uk', 'dvorak': 'dvorak', 'colemak': 'colemak'}


def console_keymap(layout):
    return CONSOLE.get(layout, 'us')


def validate_xkb_layouts(layouts, rules):
    available, section = {'dvorak', 'colemak'}, None
    for line in rules.splitlines():
        line = line.strip()
        if line.startswith('!'):
            section = line[1:].strip()
        elif section == 'layout' and line:
            available.add(line.split()[0])
    require(all(layout in available for layout in layouts), Code.BAD_CONFIG,
            'A selected XKB layout is not available on this ISO.')


def niri_config(layouts, scale=None, outputs=()):
    # Dvorak/Colemak are XKB variants of us, though offered as layout names in v1.
    names = ['us' if x in ('dvorak', 'colemak') else x for x in layouts]
    variants = [x if x in ('dvorak', 'colemak') else '' for x in layouts]
    result = 'include "/usr/share/emaki/niri/default.kdl"\n\ninput {\n    keyboard {\n        xkb {\n'
    result += f'            layout {json.dumps(",".join(names))}\n'
    if any(variants):
        result += f'            variant {json.dumps(",".join(variants))}\n'
    result += '            options "grp:win_space_toggle"\n        }\n    }\n}\n'
    if scale is not None and scale != 1:
        for output in outputs:
            result += f'\noutput {json.dumps(output)} {{\n    scale {scale:g}\n}}\n'
    return result


def grub_defaults(alongside=False):
    return ('GRUB_DEFAULT=0\nGRUB_TIMEOUT=5\nGRUB_DISTRIBUTOR="Emaki"\n'
            'GRUB_TOP_LEVEL="/boot/vmlinuz-linux"\n'
            'GRUB_DISABLE_RECOVERY=true\nGRUB_DISABLE_BOOTNEXT=true\n'
            'GRUB_DISABLE_SUBMENU=y\nGRUB_TERMINAL_OUTPUT=gfxterm\n'
            'GRUB_GFXMODE=auto\nGRUB_GFXPAYLOAD_LINUX=keep\n'
            'GRUB_BACKGROUND=/usr/share/emaki/grub/background.png\n'
            'GRUB_CMDLINE_LINUX_DEFAULT="quiet zswap.enabled=0"\n'
            f'GRUB_DISABLE_OS_PROBER={"false" if alongside else "true"}\n')


def grub_btrfs_config(text):
    # Preserve upstream options in this pacman backup file, including comments.
    text = re.sub(r'^\s*(?:export\s+)?GRUB_BTRFS_SUBMENUNAME=.*\n?', '', text, flags=re.M)
    return text.rstrip() + '\nGRUB_BTRFS_SUBMENUNAME="Emaki snapshots"\n'


def mkinitcpio_config(btrfs):
    hooks = 'base udev autodetect microcode modconf kms keyboard keymap consolefont block filesystems'
    if btrfs:
        hooks += ' grub-btrfs-overlayfs'
    return f'MODULES=()\nBINARIES=()\nFILES=()\nHOOKS=({hooks} fsck)\n'


def mkinitcpio_preset(kernel):
    require(kernel in ('linux', 'linux-lts'), Code.BOOT_VERIFY, 'Unsupported kernel preset.')
    # An explicit ALL_config prevents inherited drop-ins from replacing HOOKS.
    # Images must stay under /boot, even when reusing a former UKI installation.
    return ('ALL_config="/etc/mkinitcpio.conf"\n'
            f'ALL_kver="/boot/vmlinuz-{kernel}"\n'
            "PRESETS=('default' 'fallback')\n"
            f'default_image="/boot/initramfs-{kernel}.img"\n'
            f'fallback_image="/boot/initramfs-{kernel}-fallback.img"\n'
            'fallback_options="-S autodetect"\n')


def verify_grub(text):
    # Match linux command paths, not menu labels/comments (linux-lts contains linux).
    for kernel in ('linux', 'linux-lts'):
        require(re.search(r'^\s*linux(?:efi)?\s+\S*/vmlinuz-' + kernel + r'(?:\s|$)', text, re.M),
                Code.BOOT_VERIFY, f'GRUB has no executable entry for {kernel}.')


def snapper_config(text):
    for key, value in [('NUMBER_LIMIT', '20'), ('TIMELINE_CREATE', 'yes')]:
        text, count = re.subn(r'^' + key + r'=.*$', f'{key}="{value}"', text, flags=re.M)
        if count == 0:
            text += f'\n{key}="{value}"\n'
    return text


def normalize_fstab(text, plan):
    """Drop volatile subvolid constraints and verify the actual generated mounts."""
    rows, entries = [], {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            rows.append(line)
            continue
        fields = line.split()
        require(len(fields) == 6, Code.FSTAB_VERIFY, 'Malformed genfstab output.')
        options = [x for x in fields[3].split(',') if not x.startswith('subvolid=')]
        fields[3] = ','.join(options)
        require(fields[1] not in entries, Code.FSTAB_VERIFY, 'Duplicate fstab mountpoint.')
        entries[fields[1]] = fields
        rows.append('\t'.join(fields))
    expected = {mp: (p, sv) for p, mp, sv in plan.mounts}
    require(set(entries) == set(expected), Code.FSTAB_VERIFY,
            'fstab must contain exactly the planned filesystem mounts and no swap.')
    for mp, (part, sv) in expected.items():
        row = entries[mp]
        require(row[0].startswith('UUID=') and row[2] == part.fs,
                Code.FSTAB_VERIFY, f'fstab filesystem or UUID missing at {mp}.')
        if part.uuid:
            require(row[0] == 'UUID=' + part.uuid, Code.FSTAB_VERIFY, f'Wrong filesystem UUID at {mp}.')
        opts = row[3].split(',')
        if part.fs == 'btrfs':
            require(set(BTRFS_OPTIONS) <= set(opts), Code.FSTAB_VERIFY, f'Btrfs mount options missing at {mp}.')
            if sv:
                require(f'subvol=/{sv}' in opts or f'subvol={sv}' in opts,
                        Code.FSTAB_VERIFY, f'Wrong btrfs subvolume at {mp}.')
    return '\n'.join(rows) + '\n'
