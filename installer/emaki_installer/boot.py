# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared installed-system and installer GRUB rendering and verification."""
import io
import re
import struct
import tarfile

from .errors import Code, require
from . import grub_screen

# Keep the installed menu's existing preference; unlocking preserves the firmware mode.
GRUB_GFXMODE = '1024x768,800x600,640x480,auto'
# GRUB scripts cannot read the selected video mode, so the menu face is chosen where the
# menu is generated, from the framebuffer GRUB handed to Linux (GRUB_GFXPAYLOAD_LINUX=keep).
BOOT_PARAMS = '/sys/kernel/boot_params/data'
MENU_FONT_DIR = '/usr/share/emaki/grub/fonts'
# Committed DejaVu Sans Mono faces, largest first: (pixel size, gfxterm cell width, height).
MENU_FONTS = ((46, 28, 55), (32, 19, 38), (24, 14, 29))
# GRUB's 16 px Unifont gives 128x48 cells at 1024x768; a larger face must keep this much.
MENU_GRID = (100, 30)
GRUB_UNLOCK_GFXMODE = 'auto'
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
        # Checking is prepared artwork; stock GRUB never displays that state.
        pictures = {name: data for name, data in grub_screen.assets().items()
                    if not name.startswith('checking-')}
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
    contain large antialiased Adwaita Sans text. Stretch uses the actual framebuffer:
    gfxterm silently falls back to auto and does not export the selected dimensions.
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
            '  set gfxterm_font="Emaki Hidden Regular 4"',
            '  set emaki_unlock_size=' + 'x'.join(map(str, grub_screen.ARTWORK_SIZE)),
            f'  set gfxmode={GRUB_UNLOCK_GFXMODE}', '  if terminal_output gfxterm; then',
            '    if background_image --mode stretch (memdisk)/unlock-$emaki_unlock_size.png; then',
            '      set emaki_unlock_graphics=1', '      set color_normal=black/black',
            '      set color_highlight=black/black', '    fi', '  fi', 'fi',
            'if [ "$emaki_unlock_graphics" != 1 ]; then', '  emaki_unlock_console', 'fi',
            'set cryptodisk_passphrase_tries=1']
    for line in lines:
        if line == f'cryptomount -u {luks_uuid}':
            body += ['while true; do',
                     '  if [ "$emaki_unlock_graphics" = 1 ]; then', '    clear', '  fi',
                     f'  if {line}; then', '    break', '  fi',
                     '  if [ "$emaki_unlock_graphics" = 1 ]; then',
                     '    if background_image --mode stretch (memdisk)/wrong-$emaki_unlock_size.png; then',
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
             'unset gfxterm_font', 'unset cryptodisk_passphrase_tries',
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


def boot_framebuffer(path=BOOT_PARAMS):
    """Width and height of the EFI framebuffer this boot started with, or None.

    x86 boot_params begin with screen_info: orig_video_isVGA at 0x0f is 0x70 for an EFI
    framebuffer, lfb_width and lfb_height are little-endian at 0x12 and 0x14.
    """
    try:
        with open(path, 'rb') as stream:
            info = stream.read(0x16)
    except OSError:
        return None
    if len(info) < 0x16 or info[0x0f] != 0x70:
        return None
    width, height = struct.unpack_from('<HH', info, 0x12)
    return (width, height) if width and height else None


def menu_font(mode):
    """The largest face that keeps the menu grid; None keeps GRUB's own 16 px font."""
    if mode:
        for size, width, height in MENU_FONTS:
            if mode[0] // width >= MENU_GRID[0] and mode[1] // height >= MENU_GRID[1]:
                return f'{MENU_FONT_DIR}/menu-{size}.pf2'
    return None


def generated_menu_font(text):
    """The face a generated menu loads: a path, None for GRUB's default, '' if unknown."""
    loads = re.findall(r'^if loadfont (\S+) ; then$', text, re.M)
    if loads == ['$font']:
        return None
    return MENU_FONT_DIR + '/' + loads[0].rsplit('/', 1)[1] if (
        len(loads) == 1 and re.fullmatch(r'\S*/usr/share/emaki/grub/fonts/menu-[0-9]+\.pf2', loads[0])) else ''


def menu_font_fallback(text):
    """Retry gfxterm with the stock face if a saved enlarged face no longer fits.

    The framebuffer used to choose the face belongs to an earlier boot (or the live
    installer). Firmware may choose a smaller mode now. Only our generated face and
    the stock gfxterm command are changed; local fonts and other terminals stay local.
    """
    font = generated_menu_font(text)
    if not font or text.count('\nterminal_output gfxterm\n') != 1:
        return text
    size = next((size for size, _, _ in MENU_FONTS if font.endswith(f'/menu-{size}.pf2')), None)
    if size is None:
        return text
    # GRUB_FONT replaces the stock header's Unifont load. Keep its wider Unicode
    # coverage in the registry; gfxterm_font still makes our face primary.
    terminal = ('\nloadfont unicode\n'
                f'set gfxterm_font="DejaVu Sans Mono Regular {size}"\n'
                'if terminal_output gfxterm; then\n'
                '  true\n'
                'else\n'
                '  set gfxterm_font="Unifont Regular 16"\n'
                '  if loadfont unicode; then\n'
                '    terminal_output gfxterm\n'
                '  fi\n'
                'fi\n'
                # configfile opens a new environment for the snapshot menu. Carry
                # the face that actually initialized, including the stock retry.
                'export gfxterm_font\n')
    return text.replace('\nterminal_output gfxterm\n', terminal)


def grub_defaults(alongside=False, luks_uuid=None, resume_uuid=None, resume_offset=None):
    cmdline = []
    if luks_uuid:
        cmdline += [f'cryptdevice=UUID={luks_uuid}:emaki-root',
                    'cryptkey=rootfs:/etc/cryptsetup-keys.d/emaki-root.key']
    if resume_uuid:
        cmdline += [f'resume=UUID={resume_uuid}', f'resume_offset={resume_offset}']
    return ('GRUB_DEFAULT=0\nGRUB_TIMEOUT=5\nGRUB_DISTRIBUTOR="Emaki"\n'
            'GRUB_BTRFS_SCRIPT_CHECK="emaki-snapshot-menu-check"\n'
            'GRUB_BTRFS_GBTRFS_DIRNAME="/boot/grub/.emaki-snapshots"\n'
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


GRUB_BTRFS_SOURCE = ('if [ -r /usr/share/emaki/boot/grub-btrfs.conf ]; then '
                     '. /usr/share/emaki/boot/grub-btrfs.conf; fi')


def grub_btrfs_config(text, *, force=False):
    """Use package defaults for managed titles, preserving local overrides."""
    old_source = '. /usr/share/emaki/boot/grub-btrfs.conf'
    assignments = re.findall(r'^\s*(?:export\s+)?GRUB_BTRFS_SUBMENUNAME=.*$', text, re.M)
    if not force and assignments != ['GRUB_BTRFS_SUBMENUNAME="Emaki snapshots"']:
        # Once adopted, leave later administrator overrides in their original order.
        return text.replace(old_source + '\n', GRUB_BTRFS_SOURCE + '\n') if old_source in text.splitlines() else text
    text = re.sub(r'^\s*(?:export\s+)?GRUB_BTRFS_SUBMENUNAME=.*\n?', '', text, flags=re.M)
    text = '\n'.join(line for line in text.splitlines() if line not in (old_source, GRUB_BTRFS_SOURCE))
    return text.rstrip() + '\n' + GRUB_BTRFS_SOURCE + '\n'


def verify_grub(text):
    # Match linux command paths, not menu labels/comments (linux-lts contains linux).
    for kernel in ('linux', 'linux-lts'):
        require(re.search(r'^\s*linux(?:efi)?\s+\S*/vmlinuz-' + kernel + r'(?:\s|$)', text, re.M),
                Code.BOOT_VERIFY, f'GRUB has no executable entry for {kernel}.')
