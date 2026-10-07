# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared installed-system and installer GRUB rendering and verification."""
import io
import re
import tarfile

from .errors import Code, require
from . import grub_screen

# Keep the installed menu's existing preference; unlocking preserves the firmware mode.
GRUB_GFXMODE = '1024x768,800x600,640x480,auto'
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


def grub_defaults(alongside=False, luks_uuid=None, resume_uuid=None, resume_offset=None):
    cmdline = []
    if luks_uuid:
        cmdline += [f'cryptdevice=UUID={luks_uuid}:emaki-root',
                    'cryptkey=rootfs:/etc/cryptsetup-keys.d/emaki-root.key']
    if resume_uuid:
        cmdline += [f'resume=UUID={resume_uuid}', f'resume_offset={resume_offset}']
    return ('GRUB_DEFAULT=0\nGRUB_TIMEOUT=5\nGRUB_DISTRIBUTOR="Emaki"\n'
            'GRUB_BTRFS_SCRIPT_CHECK="emaki-snapshot-menu-check"\n'
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


