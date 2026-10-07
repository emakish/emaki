"""Target file generation and verification, independent of archinstall."""
import hashlib
import io
import json
import math
import re

from .constants import BTRFS_OPTIONS
# Re-export the boot API used by existing installer callers.
from .boot import (GRUB_GFXMODE, GRUB_UNLOCK_GFXMODE, GRUB_EARLY_MODULES,
                   grub_unlock_memdisk, grub_early_config, grub_unlock_script,
                   grub_prefix, grub_image_carries, grub_defaults, grub_btrfs_config,
                   verify_grub)
from .errors import Code, require
from .latin_layouts import is_latin

# XKB layout -> the kbd console map that types the same characters on the same keys. Vetted: a
# map that merely exists or shares the name is not enough (kbd's plain cz is QWERTY while XKB cz
# is QWERTZ; kbd's fr is an old ASCII map). Chosen per layout as the closest kbd map by
# installer/tests/test_console_keymaps.py, which lists what still differs: gb types the euro
# sign only with ie, Swedish is kbd's fi, Norwegian se-lat6, Moldavian ro. A Latin layout
# without an entry gets us, which differs from it on some keys (sk, lt, lv letters on AltGr...).
CONSOLE = {'us': 'us', 'ru': 'ru', 'ua': 'ua-utf', 'dvorak': 'dvorak', 'colemak': 'colemak',
           'at': 'de-latin1', 'ba': 'croat', 'be': 'be-latin1', 'br': 'br-abnt2', 'ca': 'cf',
           'ch': 'de_CH-latin1', 'cz': 'cz-qwertz', 'de': 'de-latin1', 'dk': 'dk-latin1',
           'dz': 'fr-pc', 'ee': 'et', 'es': 'es', 'fi': 'fi', 'fo': 'dk-latin1', 'fr': 'fr-pc',
           'gb': 'ie', 'hr': 'croat', 'hu': 'hu', 'ie': 'ie', 'is': 'is-latin1', 'it': 'it',
           'jp': 'jp106', 'latam': 'la-latin1', 'md': 'ro', 'me': 'croat', 'ml': 'fr-pc',
           'nl': 'nl2', 'no': 'se-lat6', 'pl': 'pl', 'pt': 'pt-latin9', 'ro': 'ro', 'se': 'fi',
           'si': 'slovene', 'tg': 'fr-pc', 'tr': 'trq'}
# Offered as layout names, written as XKB variants of us.
US_VARIANTS = ('dvorak', 'colemak')


def console_keymap(layout):
    return CONSOLE.get(layout, 'us')


def vconsole_conf(layouts):
    """The whole text of /etc/vconsole.conf; nothing else produces that file.

    KEYMAP is the console map of the first layout (text console, getty, and the keymap baked
    into the initramfs when the image is built). XKBLAYOUT is the full chosen list in order:
    systemd-localed serves it, and niri (login screen, session, lock screen) follows localed
    while its own xkb section is empty. No XKBOPTIONS: a grp: toggle would switch a second
    time next to niri's switch-layout bind. No XKBMODEL.
    """
    names = ['us' if x in US_VARIANTS else x for x in layouts]
    variants = [x if x in US_VARIANTS else '' for x in layouts]
    text = f'KEYMAP={console_keymap(layouts[0])}\nXKBLAYOUT={",".join(names)}\n'
    if any(variants):
        text += f'XKBVARIANT={",".join(variants)}\n'
    return text


def unlock_layout(layouts, prompt):
    """The layout a disk passphrase is typed in at startup; derived, never stored.

    GRUB reads US key positions. An initramfs text prompt reads the console map built from
    KEYMAP: the first layout's own map when that layout is Latin and has a vetted map,
    otherwise a map that types ASCII on the US positions (us itself, or ru/ua-utf).
    """
    if prompt == 'grub':
        return 'us'
    if prompt != 'initramfs':
        raise ValueError('unknown startup prompt: ' + repr(prompt))
    first = layouts[0]
    return first if is_latin(first) and first in CONSOLE else 'us'


def wireless_regdom(timezone, zone_tab):
    """/etc/conf.d/wireless-regdom for the country of the chosen timezone, or None.

    Without a country the kernel stays on the world domain and some firmware (brcmfmac)
    keeps its own default, which can hide 5 GHz channels. zone.tab maps a zone to one
    country; zones outside it (UTC, Etc/*) leave the setting alone.
    """
    for line in zone_tab.splitlines():
        fields = line.split('\t')
        if len(fields) >= 3 and not line.startswith('#') and fields[2] == timezone \
                and re.fullmatch(r'[A-Z]{2}', fields[0]):
            return ('# Set by the Emaki installer from the timezone ' + timezone + '.\n'
                    'WIRELESS_REGDOM="' + fields[0] + '"\n')
    return None


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


def keyboard_summary(layouts, rules=''):
    """The review line: the chosen layouts by their evdev.lst names, codes where unknown."""
    labels, section = {}, None
    for line in rules.splitlines():
        if line.startswith('!'):
            section = line[1:].strip()
        elif section == 'layout' and line.strip():
            code, _, label = line.strip().partition(' ')
            labels[code] = label.strip()
        elif section == 'variant' and line.strip():
            match = re.fullmatch(r'\s*(\S+)\s+us:\s*(.+)', line)
            if match and match[1] in US_VARIANTS:
                labels[match[1]] = match[2].strip()
    names = [labels.get(x) or x for x in layouts]
    if len(names) == 1:
        return f'Keyboard: {names[0]}.'
    return f"Keyboard: {names[0]} (default), {', '.join(names[1:])}. Super+Space switches."


def niri_config(scale=None, outputs=(), output_scales=None):
    # Output blocks use first-match precedence; personal settings must come before
    # the initial display choices, which only the person may edit after install.
    result = ('include "/etc/emaki/niri.kdl"\n\n'
              '// Add your settings below this line, before the installation defaults.\n\n')
    scales = output_scales if output_scales else {output: scale for output in outputs}
    blocks = ''
    for output, value in sorted(scales.items()):
        if value is None or value == 1:
            continue
        blocks += f'\noutput {json.dumps(output)} {{\n    scale {value:g}\n'
        if output_scales:
            # Cancel the gap rounded to a physical pixel (docs/shell.md).
            gap = math.floor(max(1, 2 * value) + .5) / value
            blocks += f'    layout {{\n        struts {{\n            top {-gap:g}\n        }}\n    }}\n'
        blocks += '}\n'
    if blocks:
        result += ('// Installation defaults: edit these blocks or put overrides above them.\n'
                   + blocks)
    return result


def home_files_manifest(files):
    """Root-owned record of the exact home files this installation wrote."""
    return json.dumps({'version': 1, 'files': {
        path: {'sha256': hashlib.sha256(data).hexdigest()}
        for path, data in sorted(files.items())}}, indent=2) + '\n'


def managed_niri_config():
    # Machine settings cannot define outputs ahead of personal first-match blocks.
    return 'include "/usr/share/emaki/niri/default.kdl"\n'


def mkinitcpio_config(btrfs, encrypted=False, hibernation=False, *, vmd=False):
    hooks = 'base udev autodetect microcode modconf kms keyboard keymap consolefont block'
    if encrypted:
        hooks += ' encrypt'
    if hibernation:
        hooks += ' emaki-resume'
    hooks += ' filesystems'
    if btrfs:
        hooks += ' grub-btrfs-overlayfs emaki-snapshot-fstab'
    else:
        # With /sbin/fsck in the image, mkinitcpio's fsck_root() prints a WARNING banner
        # on every read-only snapshot boot; fsck.btrfs is a stub, so btrfs loses nothing.
        hooks += ' fsck'
    files = '/etc/cryptsetup-keys.d/emaki-root.key' if encrypted else ''
    return ('umask 0077\n' if encrypted else '') + f'MODULES=({"vmd" if vmd else ""})\nBINARIES=()\nFILES=({files})\nHOOKS=({hooks})\n'


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


def mkinitcpio_machine_config(btrfs, encrypted=False, hibernation=False, *, vmd=False):
    """Write native hooks and remember the exact managed line for later updates."""
    config = mkinitcpio_config(btrfs, encrypted, hibernation, vmd=vmd)
    hooks = next(line for line in config.splitlines() if line.startswith('HOOKS='))
    return (config + f'# Emaki btrfs={int(btrfs)} encrypted={int(encrypted)} '
            f'hibernation={int(hibernation)} ' + hooks + '\n')


def mkinitcpio_package_preset(kernel):
    require(kernel in ('linux', 'linux-lts'), Code.BOOT_VERIFY, 'Unsupported kernel preset.')
    return f'. /usr/share/emaki/boot/{kernel}.preset\n'


def grub_btrfs_package_config(text):
    """Select the packaged title while retaining upstream options and comments."""
    return grub_btrfs_config(text, force=True)


def snapper_config(text):
    for key, value in [('NUMBER_LIMIT', '6-10'), ('NUMBER_LIMIT_IMPORTANT', '2-10'),
                       ('TIMELINE_CREATE', 'yes'), ('TIMELINE_LIMIT_HOURLY', '0-10'),
                       ('TIMELINE_LIMIT_DAILY', '0-10'), ('TIMELINE_LIMIT_MONTHLY', '0-2'),
                       ('TIMELINE_LIMIT_YEARLY', '0')]:
        text, count = re.subn(r'^' + key + r'=.*$', f'{key}="{value}"', text, flags=re.M)
        if count == 0:
            text += f'\n{key}="{value}"\n'
    marker = '# Emaki snapshot limits migration 1\n'
    if marker not in text:
        text = text.rstrip('\n') + '\n' + marker
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
