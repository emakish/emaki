"""Target file generation and verification, independent of archinstall."""
import io
import json
import re
import tarfile

from .constants import BTRFS_OPTIONS
from .errors import Code, require
from . import grub_screen
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


def niri_config(scale=None, outputs=()):
    # No input section: keyboard layouts live in /etc/vconsole.conf (vconsole_conf above).
    result = 'include "/usr/share/emaki/niri/default.kdl"\n'
    if scale is not None and scale != 1:
        for output in outputs:
            result += f'\noutput {json.dumps(output)} {{\n    scale {scale:g}\n}}\n'
    return result


# One mode list for every GRUB screen: the menu (GRUB_GFXMODE in /etc/default/grub) and the
# disk-password prompt an encrypted install draws before its root is open.
GRUB_GFXMODE = '1024x768,800x600,640x480,auto'
# What the EFI image of an encrypted install embeds so GRUB can ask in graphics mode before
# the unlock: what grub-install embeds for a LUKS2 root (partition table, cryptodisk, ciphers,
# the root filesystem), the memdisk with the font and the picture, and gfxterm.
# grub-mkimage adds the dependencies from moddep.lst; grub 2:2.16-1 has every name here.
# Use firmware GOP, not native PCI video drivers: the firmware console must be restored
# if a picture fails after gfxterm started. all_video can select a driver EFI cannot undo.
GRUB_EARLY_MODULES = ('part_gpt', 'cryptodisk', 'luks2', 'argon2', 'gcry_rijndael', 'gcry_sha256', 'pbkdf2',
                      'btrfs', 'ext2', 'memdisk', 'tar', 'font', 'gfxterm', 'gfxterm_background', 'png',
                      'efi_gop', 'terminal', 'echo', 'gzio', 'normal', 'configfile', 'test', 'true',
                      'sleep')


def grub_unlock_memdisk(font, load_cfg, luks_uuid):
    """The self-contained unlock script, fonts and pictures, in GRUB's ustar format.

    GRUB opens it as (memdisk) through its tar driver, which knows only the ustar header.
    Fixed owner, mode and time make the archive reproducible. Visible letters come from
    the committed Adwaita Sans artwork; the terminal itself uses a blank font.
    """
    script = grub_unlock_script(load_cfg, luks_uuid).encode('ascii')
    try:
        _, glyphs = grub_screen._read_font(font)
        if any(code not in glyphs or not any(glyphs[code][-1]) for code in range(33, 127)):
            raise ValueError('the fallback font must have visible printable ASCII glyphs')
        pictures = grub_screen.assets()
    except ValueError as error:
        require(False, Code.BOOT_VERIFY, 'The GRUB unlock-screen assets are invalid: ' + str(error))
    files = {'visible.pf2': font, 'unlock.cfg': script, **pictures}
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size, member.mode, member.mtime = len(data), 0o644, 0
            archive.addfile(member, io.BytesIO(data))
    return stream.getvalue()


def _grub_load_lines(load_cfg, luks_uuid):
    lines = [line.strip() for line in load_cfg.splitlines() if line.strip()]
    require(f'cryptomount -u {luks_uuid}' in lines, Code.BOOT_VERIFY,
            'grub-install did not write the cryptomount line of the encrypted root.')
    require(all(ch == '\n' or 32 <= ord(ch) <= 126 for ch in '\n'.join(lines)), Code.BOOT_VERIFY,
            'The GRUB early config must be printable ASCII.')
    return lines


def grub_early_config(load_cfg, luks_uuid):
    """Rescue-parser entry into the normal-parser script carried on the memdisk.

    The embedded parser accepts only simple commands, even with normal.mod loaded. The
    embedded configfile module's source command runs the memdisk script in batch mode,
    where if/while work without entering a menu or command line. Nothing is read from the
    locked root to draw the screen. The source returns only after unlocking successfully;
    the final console switch lets the installed menu start gfxterm with its own font.
    """
    _grub_load_lines(load_cfg, luks_uuid)
    return 'source (memdisk)/unlock.cfg\nterminal_output console\n'


def grub_unlock_script(load_cfg, luks_uuid):
    """Normal-parser unlock loop; keep grub-install's commands in their original order.

    A finite cryptodisk_passphrase_tries value is not an infinite retry policy. One attempt
    per cryptomount lets the loop handle a wrong password, empty input and Escape alike.
    Its output remains hidden only after graphics and the initial picture both succeed:
    blank terminal glyphs over static pictures, with a black cursor in the top band. Any
    graphics or image failure switches to the light firmware console. Password input still
    belongs to cryptomount, never to a script variable or command argument. The pictures
    contain antialiased Adwaita Sans text, loaded without scaling for each selected mode.
    """
    lines = _grub_load_lines(load_cfg, luks_uuid)
    # Empty disables gettext without trying to read a C locale from the locked prefix.
    body = ['set lang=', 'unset debug', 'set color_normal=light-gray/black',
            'set color_highlight=black/light-gray', 'set emaki_unlock_graphics=',
            'function emaki_unlock_console {', '  terminal_output console',
            '  clear',
            '  set color_normal=light-gray/black', '  set color_highlight=black/light-gray',
            '  unset gfxterm_font', '  set emaki_unlock_graphics=', '  clear',
            *(f'  echo "{sentence}"' for sentence in grub_screen.TEXT), '}',
            'if loadfont (memdisk)/hidden.pf2; then',
            '  set gfxterm_font="Emaki Hidden Regular 4"', '  set emaki_unlock_size=640x480',
            '  for emaki_mode in ' + GRUB_GFXMODE.replace(',', ' ') + '; do',
            '    set gfxmode=$emaki_mode', '    if terminal_output gfxterm; then',
            '      if [ "$emaki_mode" != auto ]; then',
            '        set emaki_unlock_size=$emaki_mode', '      fi',
            '      if background_image --mode normal (memdisk)/unlock-$emaki_unlock_size.png; then',
            '        set emaki_unlock_graphics=1', '        set color_normal=black/black',
            '        set color_highlight=black/black', '      fi',
            '      break', '    fi', '  done', 'fi',
            'if [ "$emaki_unlock_graphics" != 1 ]; then', '  emaki_unlock_console', 'fi',
            'set cryptodisk_passphrase_tries=1']
    for line in lines:
        if line == f'cryptomount -u {luks_uuid}':
            body += ['while true; do',
                     '  if [ "$emaki_unlock_graphics" = 1 ]; then', '    clear', '  fi',
                     f'  if {line}; then', '    break', '  fi',
                     '  if [ "$emaki_unlock_graphics" = 1 ]; then',
                     '    if background_image --mode normal (memdisk)/wrong-$emaki_unlock_size.png; then',
                     '      true', '    else', '      set emaki_unlock_graphics=', '    fi', '  fi',
                     '  if [ "$emaki_unlock_graphics" != 1 ]; then',
                     '    emaki_unlock_console',
                     f'    echo "{grub_screen.WRONG}"', '  fi', 'done']
        else:
            body.append(line)
    # Poll all queued keys for a complete interval. Escape/F4 interrupt sleep, so repeat
    # until an interval completes; a zero-second sleep returns without polling input.
    body += ['while ! sleep --interruptible 1; do', '  true', 'done',
             'if [ "$emaki_unlock_graphics" = 1 ]; then', '  background_image', 'fi',
             'loadfont (memdisk)/visible.pf2',
             'set color_normal=light-gray/black', 'set color_highlight=black/light-gray',
             'unset gfxterm_font', 'unset cryptodisk_passphrase_tries', 'unset emaki_mode',
             'unset emaki_unlock_size', 'unset emaki_unlock_graphics', f'set gfxmode={GRUB_GFXMODE}']
    return '\n'.join(body) + '\n'


def grub_prefix(plan):
    """The prefix grub-install hardcodes into the image of an encrypted install.

    With the root on a cryptodisk grub-install does not search for the filesystem: the prefix
    names the unlocked device and the path of /boot/grub on that filesystem, which on btrfs
    starts with the subvolume mounted at /. The device is named by the device-mapper UUID of
    the open mapping (CRYPT-LUKS2-<uuid without dashes>-<name>), so the dashes of the LUKS
    UUID are not in it; GRUB compares UUIDs without dashes at boot.
    """
    root = plan.root
    require(isinstance(root.luks_uuid, str) and re.fullmatch(r'[0-9a-fA-F-]{36}', root.luks_uuid),
            Code.BOOT_VERIFY, 'Encrypted root has no LUKS UUID.')
    subvolume = next((name for name, mp in root.subvolumes.items() if mp == '/'), None)
    device = 'cryptouuid/' + root.luks_uuid.replace('-', '')
    return f'({device})' + (f'/{subvolume}' if subvolume else '') + '/boot/grub'


def grub_image_carries(image, prefix):
    """True for a GRUB EFI image (a PE file) whose embedded prefix is exactly `prefix`.

    grub-mkimage stores the prefix NUL-terminated and uncompressed in x86_64-efi images.
    """
    return image[:2] == b'MZ' and prefix.encode() + b'\0' in image


def grub_defaults(alongside=False, luks_uuid=None, resume_uuid=None, resume_offset=None):
    cmdline = []
    if luks_uuid:
        cmdline += [f'cryptdevice=UUID={luks_uuid}:emaki-root',
                    'cryptkey=rootfs:/etc/cryptsetup-keys.d/emaki-root.key']
    if resume_uuid:
        cmdline += [f'resume=UUID={resume_uuid}', f'resume_offset={resume_offset}']
    return ('GRUB_DEFAULT=0\nGRUB_TIMEOUT=5\nGRUB_DISTRIBUTOR="Emaki"\n'
            'GRUB_TOP_LEVEL="/boot/vmlinuz-linux"\n'
            # The microcode hook puts the CPU's microcode into every initramfs; a separate
            # early image would load it twice and multiply grub-btrfs's rows per snapshot.
            'GRUB_EARLY_INITRD_LINUX_STOCK=""\n'
            'GRUB_DISABLE_RECOVERY=true\nGRUB_DISABLE_BOOTNEXT=true\n'
            'GRUB_DISABLE_SUBMENU=y\nGRUB_TERMINAL_OUTPUT=gfxterm\n'
            f'GRUB_GFXMODE={GRUB_GFXMODE}\nGRUB_GFXPAYLOAD_LINUX=keep\n'
            'GRUB_BACKGROUND=/usr/share/emaki/grub/background.png\n'
            'GRUB_CMDLINE_LINUX_DEFAULT="quiet zswap.enabled=0"\n'
            f'GRUB_CMDLINE_LINUX="{" ".join(cmdline)}"\n'
            + ('GRUB_ENABLE_CRYPTODISK=y\nGRUB_PRELOAD_MODULES="part_gpt cryptodisk luks2 argon2"\n' if luks_uuid else '') +
            f'GRUB_DISABLE_OS_PROBER={"false" if alongside else "true"}\n')


def grub_btrfs_config(text):
    # Preserve upstream options in this pacman backup file, including comments.
    text = re.sub(r'^\s*(?:export\s+)?GRUB_BTRFS_SUBMENUNAME=.*\n?', '', text, flags=re.M)
    return text.rstrip() + '\nGRUB_BTRFS_SUBMENUNAME="Emaki snapshots"\n'


def mkinitcpio_config(btrfs, encrypted=False, hibernation=False):
    hooks = 'base udev autodetect microcode modconf kms keyboard keymap consolefont block'
    if encrypted:
        hooks += ' encrypt'
    if hibernation:
        hooks += ' resume'
    hooks += ' filesystems'
    if btrfs:
        hooks += ' grub-btrfs-overlayfs emaki-snapshot-fstab'
    else:
        # With /sbin/fsck in the image, mkinitcpio's fsck_root() prints a WARNING banner
        # on every read-only snapshot boot; fsck.btrfs is a stub, so btrfs loses nothing.
        hooks += ' fsck'
    files = '/etc/cryptsetup-keys.d/emaki-root.key' if encrypted else ''
    return ('umask 0077\n' if encrypted else '') + f'MODULES=()\nBINARIES=()\nFILES=({files})\nHOOKS=({hooks})\n'


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
