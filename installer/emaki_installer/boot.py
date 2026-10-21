# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared installed-system and installer GRUB rendering and verification."""
from .i18n import _
import collections
import io
import re
from pathlib import Path
import struct
import tarfile

from .errors import Code, InstallError, require
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
        ignored, glyphs = grub_screen._read_font(font)
        if any(code not in glyphs or not any(glyphs[code][-1]) for code in range(33, 127)):
            raise ValueError(_('the fallback font must have visible printable ASCII glyphs'))
        # Checking is prepared artwork; stock GRUB never displays that state.
        pictures = {name: data for name, data in grub_screen.assets().items()
                    if not name.startswith('checking-')}
    except ValueError as error:
        require(False, Code.BOOT_VERIFY, _('The GRUB unlock-screen assets are invalid: ') + str(error))
    files = {'visible.pf2': font, 'unlock.cfg': script, **pictures}
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size, member.mode, member.mtime = len(data), 0o644, 0
            archive.addfile(member, io.BytesIO(data))
    return stream.getvalue()


def grub_loader_payload(grub, generation, fsroot, luks_uuid, load_cfg, font, extra=None):
    """(prefix, early config, memdisk) of a self-contained EFI loader.

    The memdisk carries every module, font and locale of the grub directory grub-install
    filled, so insmod and loadfont never read the disk: under Secure Boot GRUB refuses
    modules and fonts from disk but trusts its own signed image's memdisk. extra maps
    more memdisk names to bytes (the menu's fonts under Secure Boot). generation is the
    boot generation directory the boot refresh builds, or None for the installer's
    first loader, which reads only the conventional menu.
    """
    files = {}
    if luks_uuid:
        initial = grub_unlock_memdisk(font, load_cfg, luks_uuid)
        with tarfile.open(fileobj=io.BytesIO(initial)) as archive:
            files.update((member.name, archive.extractfile(member).read()) for member in archive)
        start = grub_early_config(load_cfg, luks_uuid)
        root = '(cryptouuid/' + luks_uuid.replace('-', '') + ')'
    else:
        files['start.cfg'] = load_cfg.encode()
        start = 'source (memdisk)/start.cfg\n'
        root = '($root)'
    prefix = '(memdisk)/boot/grub'
    start += 'set prefix=' + prefix + '\n'
    ordinary_menu = root + fsroot + '/boot/grub/grub.cfg'
    # source, not configfile: configfile nests the menu it reads, and Escape leaves a nested
    # menu for the empty one above it, a bare grub> prompt. Sourced, the disk's menu is the
    # top menu and the snapshot menu one level down, as when the loader reads them from disk.
    if generation is not None:
        start += 'set emaki_generation=' + generation.name + '\nexport emaki_generation\n'
        generation_menu = root + fsroot + str(generation / 'grub/grub.cfg')
        files['boot/grub/grub.cfg'] = (
            f'if [ -f {ordinary_menu} ]; then\n  source {ordinary_menu}\n'
            f'else\n  source {generation_menu}\nfi\n').encode()
    else:
        files['boot/grub/grub.cfg'] = f'source {ordinary_menu}\n'.encode()
    files['boot/grub/grub-btrfs.cfg'] = (
        f'source {root}{fsroot}/boot/grub/grub-btrfs.cfg\n').encode()
    for path in sorted(grub.rglob('*')):
        if path.is_file() and (path.suffix in ('.mod', '.lst', '.mo', '.pf2') or path.name == 'modinfo.sh'):
            require(not path.is_symlink(), Code.BOOT_VERIFY, _('{value1} is a symbolic link.').format(value1=path))
            files['boot/grub/' + path.relative_to(grub).as_posix()] = path.read_bytes()
    for name, data in (extra or {}).items():
        require(re.fullmatch(r'[a-z0-9_-]+(?:/[a-z0-9_.-]+)+', name) and name not in files
                and '..' not in name.split('/'), Code.BOOT_VERIFY, _('Invalid memdisk name {value1}.').format(value1=name))
        files[name] = data
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for name, data in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.size, member.mode, member.mtime = len(data), 0o644, 0
            archive.addfile(member, io.BytesIO(data))
    return prefix, start.encode(), stream.getvalue()


def _grub_load_lines(load_cfg, luks_uuid):
    lines = [line.strip() for line in load_cfg.splitlines() if line.strip()]
    require(f'cryptomount -u {luks_uuid}' in lines, Code.BOOT_VERIFY,
            _('grub-install did not write the cryptomount line of the encrypted root.'))
    require(all(ch == '\n' or 32 <= ord(ch) <= 126 for ch in '\n'.join(lines)), Code.BOOT_VERIFY,
            _('The GRUB early config must be printable ASCII.'))
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
            Code.BOOT_VERIFY, _('Encrypted root has no LUKS UUID.'))
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


MENU_THEME_DIR = '/usr/share/emaki/grub/theme'


def menu_theme(mode):
    """The theme drawn for the mode GRUB used; the smallest scale when it is unknown."""
    height = mode[1] if mode else 0
    scale = next((scale for limit, scale in grub_screen.SCALE_HEIGHTS if height < limit),
                 max(grub_screen.SCALES))
    return f'{MENU_THEME_DIR}/theme-{scale}.txt'


def generated_menu_theme(text):
    """The theme a generated menu sets: a path, None for no theme, '' if not ours."""
    themes = re.findall(r'^set theme=(\S+)$', text, re.M)
    if not themes:
        return None
    match = re.fullmatch(r'\(\$root\)\S*(/usr/share/emaki/grub/theme/theme-[0-9]+\.txt)', themes[0])
    return match[1] if len(themes) == 1 and match else ''


# 10_linux titles with GRUB_DISABLE_SUBMENU=y, after emaki-grub-title dropped " Linux".
LINUX_ENTRY = re.compile(r"^menuentry 'Emaki, with Linux (linux|linux-lts)( \(fallback initramfs\))?'([^\n]*\{)\n", re.M)
ADVANCED = "submenu 'Advanced options' $menuentry_id_option 'emaki-advanced' {\n"
FIRMWARE = re.compile(r'(### BEGIN /etc/grub\.d/30_uefi-firmware ###\n)(.*?)(### END /etc/grub\.d/30_uefi-firmware ###\n)', re.S)
SNAPSHOTS_END = '### END /etc/grub.d/41_snapshots-btrfs ###\n'
HEADER_END = '### END /etc/grub.d/00_header ###\n'
ENTRY = re.compile(r"^[ \t]*(?:menuentry|submenu) '((?:[^']|'\\'')*)'([^\n]*)\{$")
ENTRY_ID = re.compile(r"\$menuentry_id_option '([^']+)'")
GRUB_VARIABLE = re.compile(r'\$(?:\{[^}\n]*\}|[A-Za-z0-9_?#@*]+)')
# A command word that assigns a variable (`saved_entry="${chosen}"`) rather than naming a command.
GRUB_ASSIGNMENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*=')
# Options of menuentry/submenu that take a value (grub-core/commands/menuentry.c).
ENTRY_VALUE_OPTIONS = ('--class', '--users', '--hotkey', '--source', '--id')
# One entry GRUB numbers at the top level, or (`source`) a point after which it numbers what it reads.
MenuEntry = collections.namedtuple('MenuEntry', 'title id conditional start end source', defaults=(False,))
MenuEntries = collections.namedtuple('MenuEntries', 'entries complete')
# Set at the end of the header when an old default number needs the menu in its old order.
OLD_LAYOUT = 'emaki_old_layout'
# Commands that read entries from another file: only `source` adds them to this menu's top level.
GRUB_MENU_READERS = ('source', 'configfile', 'normal', 'extract_entries_source', 'extract_entries_configfile',
                     'extract_legacy_entries_source', 'extract_legacy_entries_configfile', 'syslinux_source',
                     'syslinux_configfile')
# What grub 2:2.16-1's 41_custom writes: custom.cfg runs after the header, may add entries and set the default.
CUSTOM_TEST = ('if [ -f  ${{config_directory}}/custom.cfg ]; then\n{0}'
               'elif [ -z "${{config_directory}}" -a -f  $prefix/custom.cfg ]; then\n{1}fi\n')
CUSTOM_END = '### END /etc/grub.d/41_custom ###\n'
CUSTOM_BLOCK = ('### BEGIN /etc/grub.d/41_custom ###\n'
                + CUSTOM_TEST.format('  source ${config_directory}/custom.cfg\n', '  source $prefix/custom.cfg\n')
                + CUSTOM_END)



def firmware_boot_order(listing):
    """Return an order only when all numbered entries are present and parsed."""
    orders = re.findall(r'^BootOrder: ([0-9A-Fa-f]{4}(?:,[0-9A-Fa-f]{4})*)$', listing, re.M)
    if len(orders) != 1 or len(re.findall(r'^BootOrder:', listing, re.M)) != 1:
        return None
    order = [entry.upper() for entry in orders[0].split(',')]
    if len(set(order)) != len(order):
        return None
    entries = set()
    for line in listing.splitlines():
        if not re.match(r'^Boot[0-9A-Fa-f]', line) or line.startswith('BootCurrent:'):
            continue
        entry = re.fullmatch(r'Boot([0-9A-Fa-f]{4})(?:\*| ) +[^\t\n]*\t\S[^\t\n]*', line)
        if not entry or entry[1].upper() in entries:
            return None
        entries.add(entry[1].upper())
    return order if all(number in entries for number in order) else None


WINDOWS_LOADER = '/EFI/Microsoft/Boot/bootmgfw.efi'
WINDOWS_UNSAFE = (_('Windows firmware startup could not be identified safely; boot menu setup stopped.'))


def windows_firmware_entry(listing, partuuid):
    """One exact Windows loader on the recorded partition, or genuine absence.

    Labels cannot identify a loader. Accept the File() and bare path formats of
    efibootmgr, and hardware nodes before the partition. Ignore inactive entries
    and other partitions; malformed or duplicate targets on this ESP refuse.
    """
    guid = r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'
    require(isinstance(partuuid, str) and re.fullmatch(guid, partuuid)
            and firmware_boot_order(listing) is not None, Code.BOOT_VERIFY, WINDOWS_UNSAFE)
    hardware = r'(?:(?!(?:HD|File)\()[A-Za-z][A-Za-z0-9]*\([^()\t\n]*\)/)*'
    loader = re.escape(WINDOWS_LOADER.replace('/', chr(92)))
    pattern = (rf'Boot([0-9A-Fa-f]{{4}})\* +[^\t\n]*\t{hardware}'
               rf'HD\([1-9][0-9]*,GPT,({guid}),0x[0-9a-f]+,0x[0-9a-f]+\)/'
               rf'(?:File\({loader}\)(?:[^/\\()\t\n]*)|{loader})')
    found = []
    records = re.split(r'(?m)(?=^Boot[0-9A-Fa-f]{4}(?:\*| ))', listing)
    for record in records:
        line = record.splitlines()[0] if record else ''
        if not re.match(r'^Boot[0-9A-Fa-f]{4}(?:\*| )', line):
            continue
        # The filename also catches truncated device paths and suffix lookalikes.
        if 'bootmgfw.efi' not in line.casefold():
            continue
        if line[8] != '*':
            continue
        partitions = re.findall(rf'HD\([^()]*,GPT,({guid}),', line, re.I)
        if partitions and all(value.lower() != partuuid.lower() for value in partitions):
            continue
        # efibootmgr appends optional data to a bare filename without a separator.
        # Its verbose data line proves exactly which suffix is data, not filename.
        data = re.findall(r'^    data: ([0-9a-fA-F]{2}(?: [0-9a-fA-F]{2})*)$', record, re.M)
        require(len(data) <= 1, Code.BOOT_VERIFY, WINDOWS_UNSAFE)
        if data and line.lower().endswith(data[0].replace(' ', '').lower()):
            line = line[:-len(data[0].replace(' ', ''))]
        match = re.fullmatch(pattern, line, re.I)
        require(match and match[2].lower() == partuuid.lower(), Code.BOOT_VERIFY, WINDOWS_UNSAFE)
        found.append(match[1].upper())
    require(len(found) <= 1, Code.BOOT_VERIFY, WINDOWS_UNSAFE)
    return found[0] if found else None


def windows_bootnext_menu(text, partuuid, esp_uuid, listing):
    """Replace only os-prober's verified Windows entry, preserving its title/id.

    Resolution is done each time the menu is generated. Stock GRUB bootnext
    checks that the entry still exists when selected; it cannot detect a number
    reused for a different entry after generation. Never chainload after a
    failed BootNext request. Genuine absence permits chainloading only when
    shim verification is off at menu execution, never under Secure Boot.
    """
    require(isinstance(esp_uuid, str) and re.fullmatch(r'[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}', esp_uuid),
            Code.BOOT_VERIFY, WINDOWS_UNSAFE)
    number = windows_firmware_entry(listing, partuuid)
    sections = list(re.finditer(r'(### BEGIN /etc/grub\.d/30_os-prober ###\n)(.*?)'
                               r'(### END /etc/grub\.d/30_os-prober ###)', text, re.S))
    require(len(sections) == 1, Code.BOOT_VERIFY, WINDOWS_UNSAFE)
    section = sections[0]
    entries = top_level_entries(section[2])
    require(entries.complete, Code.BOOT_VERIFY, WINDOWS_UNSAFE)
    candidates = []
    for entry in entries.entries:
        source = section[2][entry.start:entry.end]
        if 'bootmgfw.efi' not in source.casefold():
            continue
        chains = re.findall(r'^\s*chainloader\s+(\S+)\s*$', source, re.M)
        searches = re.findall(r'^\s*search\s+([^\n]+)$', source, re.M)
        require(chains and len(chains) == 1 and chains[0].casefold() == WINDOWS_LOADER.casefold()
                and searches and all('--fs-uuid' in row.split() and '--set=root' in row.split()
                                     and row.split()[-1].casefold() == esp_uuid.casefold()
                                     for row in searches), Code.BOOT_VERIFY, WINDOWS_UNSAFE)
        candidates.append(entry)
    require(len(candidates) == 1, Code.BOOT_VERIFY, WINDOWS_UNSAFE)
    entry = candidates[0]
    source = section[2][entry.start:entry.end]
    start, end = section.start(2) + entry.start, section.start(2) + entry.end
    if number is None:
        # Test at boot, not generation: firmware security can change between them.
        # Keep a discoverable help item, but never offer the crashing chainload
        # while shim verifies images. This operation writes neither ESP nor NVRAM.
        help_entry = (
            "menuentry 'Windows startup help' --class windows --id 'emaki-windows-startup-help' {\n"
            "  echo 'Windows has no active firmware startup entry.'\n"
            "  echo 'Restart and open the firmware boot menu.'\n"
            "  echo 'Choose Boot from File on the Windows EFI partition:'\n"
            "  echo '/EFI/Microsoft/Boot/bootmgfw.efi'\n"
            "  echo 'If Boot from File is unavailable, use Windows recovery media'\n"
            "  echo 'to restore its firmware entry.'\n"
            "  echo 'Press Escape to return to the boot menu.'\n"
            "  sleep --interruptible 300\n"
            "}")
        return (text[:start] + 'if [ "$shim_lock" = y ]; then\n' + help_entry
                + '\nelse\n' + source + '\nfi' + text[end:])
    opening = next(end for kind, ignored, ignored, end in grub_words(source) if kind == '{')
    # Positive nested guards matter: a failed GRUB command must not reach reboot.
    body = (f'\n  # Windows firmware target: {partuuid.lower()} {WINDOWS_LOADER}\n'
            '  if insmod efibootnext; then\n'
            '    if insmod reboot; then\n'
            f'      if bootnext {number}; then\n'
            '        reboot\n'
            '      else\n'
            "        echo 'Windows startup could not be selected. Return to Emaki and refresh the boot menu.'\n"
            '      fi\n'
            '    fi\n'
            '  fi\n'
            '}')
    start, end = section.start(2) + entry.start, section.start(2) + entry.end
    return text[:start] + source[:opening] + body + text[end:]


def grub_quote(value):
    return "'" + value.replace("'", "'\\''") + "'"


def grub_words(text):
    """GRUB script tokens (kind, value, start, end): a 'word', '{', '}' or ';' (also a newline).

    A word that expands a variable has the value None. Stops with ValueError at an unclosed quote.
    """
    position, size = 0, len(text)
    while position < size:
        char = text[position]
        if char in ' \t\r':
            position += 1
        elif char in ';\n{}':
            yield (';' if char == '\n' else char), char, position, position + 1
            position += 1
        elif char == '#':
            end = text.find('\n', position)
            position = size if end < 0 else end
        else:
            start, value, literal = position, [], True
            while position < size and text[position] not in ' \t\r;\n{}':
                char = text[position]
                if char == "'":
                    end = text.find("'", position + 1)
                    if end < 0:
                        raise ValueError(_('unclosed quote at {value1}').format(value1=position))
                    value.append(text[position + 1:end])
                    position = end + 1
                elif char == '"':
                    position += 1
                    while position < size and text[position] != '"':
                        if text[position] == '\\' and text[position + 1:position + 2] in ('\\', '"', '$', '\n'):
                            value.append(text[position + 1])
                            position += 2
                        elif (variable := GRUB_VARIABLE.match(text, position)):
                            literal, position = False, variable.end()
                        else:
                            value.append(text[position])
                            position += 1
                    if position >= size:
                        raise ValueError(_('unclosed quote at {value1}').format(value1=start))
                    position += 1
                elif char == '\\':
                    value.append(text[position + 1:position + 2])
                    position += 2
                elif (variable := GRUB_VARIABLE.match(text, position)):
                    literal, position = False, variable.end()
                else:
                    value.append(char)
                    position += 1
            yield 'word', ''.join(value) if literal else None, start, position


def entry_title_and_id(text, words):
    """The title and id of `menuentry|submenu <words> {`, or None where they depend on variables."""
    title = identifier = None
    index = 0
    while index < len(words):
        value, raw = words[index][1], text[words[index][2]:words[index][3]]
        if raw in ('$menuentry_id_option', '${menuentry_id_option}') or value in ENTRY_VALUE_OPTIONS:
            if index + 1 == len(words) or words[index + 1][1] is None:
                return None
            if value in (None, '--id'):
                identifier = words[index + 1][1]
            index += 2
            continue
        if value is None:
            return None
        if value.startswith('--id='):
            identifier = value[5:]
        elif not value.startswith('--') and title is None:
            title = value
        index += 1
    return None if title is None else (title, identifier)


def top_level_entries(text):
    """The top-level entries GRUB numbers 0, 1, ... whatever the machine, as MenuEntry.

    `conditional` marks an entry inside a top-level if/elif/else: whether it exists is only
    known at boot. A top-level `source` is listed too: it can add entries. Reading stops at the
    first construct this does not follow (a loop, an entry inside a function, a title built
    from variables, unbalanced braces or quotes); `complete` says whether it reached the end.
    """
    tokens, complete = [], True
    try:
        tokens.extend(grub_words(text))
    except ValueError:
        complete = False
    entries, blocks, index = [], [], 0

    def found():
        return MenuEntries([entry for entry in entries if entry.end is not None], False)

    while index < len(tokens):
        kind = tokens[index][0]
        if kind == ';':
            index += 1
            continue
        if kind == '}':
            if not blocks or blocks[-1][0] not in ('entry', 'function'):
                return found()
            number = blocks.pop()[1]
            if number is not None:
                entries[number] = entries[number]._replace(end=tokens[index][3])
            index += 1
            continue
        if kind == '{':
            return found()
        words = []
        while index < len(tokens) and tokens[index][0] == 'word':
            words.append(tokens[index])
            index += 1
        while words and words[0][1] in ('then', 'else', 'do'):
            words.pop(0)
        if not words:
            continue
        name = '=' if GRUB_ASSIGNMENT.match(text, words[0][2]) else words[0][1]
        kinds = [block[0] for block in blocks]
        # Outside every entry body: what runs here builds the top level (a loop could repeat it).
        outside = 'entry' not in kinds
        top = outside and 'function' not in kinds
        if name in ('if', 'while', 'until', 'for'):
            blocks.append(('if' if name == 'if' else 'loop', None))
        elif name in ('fi', 'done'):
            if not blocks or blocks[-1][0] != ('if' if name == 'fi' else 'loop'):
                return found()
            blocks.pop()
        elif name in ('function', 'menuentry', 'submenu'):
            if index == len(tokens) or tokens[index][0] != '{':
                return found()
            index += 1
            if name == 'function':
                blocks.append(('function', None))
            elif not outside:
                blocks.append(('entry', None))
            else:
                described = entry_title_and_id(text, words[1:])
                if not top or 'loop' in kinds or described is None:
                    return found()
                entries.append(MenuEntry(*described, bool(blocks), words[0][2], None))
                blocks.append(('entry', len(entries) - 1))
        elif outside and (name in GRUB_MENU_READERS or name is None):
            if not top or 'loop' in kinds or name != 'source':
                return found()
            entries.append(MenuEntry(None, None, bool(blocks), words[0][2], words[-1][3], True))
    if blocks or not complete:
        return found()
    return MenuEntries(entries, True)


def changes_default(text):
    """Whether GRUB script outside every entry body may change $default.

    That is an assignment to it, `unset default`, `load_env`, a command named by a variable,
    a command that reads another file, or script that cannot be read (unbalanced braces or quotes).
    A function body counts as outside: the top level may call it.
    """
    try:
        tokens = list(grub_words(text))
    except ValueError:
        return True
    bodies, words = [], []
    for kind, value, start, end in tokens:
        if kind == '{':
            bodies.append(bool(words) and words[0] in ('menuentry', 'submenu'))
            words = []
        elif kind in ('}', ';'):
            if kind == '}' and not bodies:
                return True
            if kind == '}':
                bodies.pop()
            words = []
        elif True not in bodies and (words or value not in ('then', 'else', 'do')):
            words.append('=' if not words and GRUB_ASSIGNMENT.match(text, start) else value)
            if (text[start:end].lstrip('\'"').startswith('default=') or words[0] is None
                    or words[0] in GRUB_MENU_READERS + ('.', 'load_env') or words[0] == 'unset' and value == 'default'):
                return True
    return bool(bodies)


def default_chain(aliases):
    """An if/elif chain that points $default at aliases[$default]."""
    return ''.join(f'{"elif" if index else "if"} [ "$default" = {grub_quote(value)} ]; then\n'
                   f'  set default={grub_quote(target)}\n'
                   for index, (value, target) in enumerate(aliases.items())) + ('fi\n' if aliases else '')


def selection_remap(before, moved, renamed, first_moved, custom):
    """GRUB commands that keep an old default (configured, saved or next entry): (header end, after custom.cfg).

    `moved` maps the id (or new title) of each entry put under Advanced options to its (old title,
    new title), `renamed` maps the old title of each kept entry to its id (or new title), and
    `first_moved` is where the first moved entry starts in `before`. A title, an id or a number known
    before boot is pointed at the same entry. Any other number (also the first part of a path such as
    `5>1`) may count entries that only exist at boot, so it sets OLD_LAYOUT: the menu then keeps its
    old order. With `custom` (41_custom's block), a custom.cfg that will run sets OLD_LAYOUT as well:
    it may add entries and set the default itself, in the old numbering. The second part, for the end
    of that block, then points old titles and names inside Advanced options at the entries in their
    old places. Returns None when even the numbers before the first moved entry are unknown.
    """
    aliases, old_aliases = {}, {}
    for key, (title, new) in moved.items():
        aliases.setdefault(key, 'emaki-advanced>' + key)
        aliases.setdefault(title, 'emaki-advanced>' + key)
        old_aliases.setdefault(title, key)
        for submenu in ('emaki-advanced', 'Advanced options'):
            for name in (key, new):
                old_aliases.setdefault(f'{submenu}>{name}', key)
    for title, target in renamed.items():
        aliases.setdefault(title, target)
        old_aliases.setdefault(title, target)
    old = top_level_entries(before).entries
    # These numbers name the same entry in both orders: nothing before them moves.
    unchanged = sum(1 for entry in old if entry.start < first_moved and not (entry.conditional or entry.source))
    if not unchanged:
        return None
    for number, entry in enumerate(old):
        # Up to the first entry that exists only at boot, a number names a known entry.
        if entry.conditional or entry.source:
            break
        target = aliases.get(entry.id) or aliases.get(entry.title) or entry.id
        if number >= unchanged and target:
            aliases.setdefault(str(number), target)
    keep = f'  set {OLD_LAYOUT}=y\n'
    # `-ge` reads a number the way GRUB reads a default: leading digits, up to a '>' or anything else.
    header = ('# A default chosen before entries moved under Advanced options keeps its entry.\n'
              + f'set {OLD_LAYOUT}=\n' + (CUSTOM_TEST.format(keep, keep) if custom else '')
              + f'if [ -z "${OLD_LAYOUT}" ]; then\n' + default_chain(aliases)
              + f'if [ "$default" -ge {unchanged} ]; then\n{keep}fi\nfi\n')
    after = (f'if [ "${OLD_LAYOUT}" ]; then\n'
             '# The old order: custom.cfg and the header may still name entries the short menu moved or renamed.\n'
             + default_chain(old_aliases) + 'fi\n') if custom else ''
    return header, after


LINUX_SECTION = re.compile(r'(### BEGIN /etc/grub\.d/10_linux ###\n)(.*?)(### END /etc/grub\.d/10_linux ###\n)', re.S)
# 10_linux prints a gettext message before each `linux` and `initrd` command: matched by place, not words.
LINUX_ECHO = re.compile(r"^([ \t]*)echo[ \t]+'(?:[^'\n]|'\\'')*'\n(?=\1(?:linux|linuxefi|initrd|initrdefi)[ \t])", re.M)
LINUX_ENTRY_START = re.compile(r'^([ \t]*)menuentry [^\n]*\{\n(?![ \t]*clear\n)(?=(?:(?!\n[ \t]*\}\n).)*?^[ \t]*linux(?:efi)?[ \t])',
                               re.M | re.S)


def quiet_linux_entries(text):
    """Kernel entries of 10_linux start on a clear screen and print nothing on success.

    After the menu GRUB writes into its terminal window: "Booting `<title>'" when the
    countdown ends (normal/menu.c notify_booting) and the stock "Loading Linux ..." and
    "Loading initial ramdisk ..." lines. With gfxpayload=keep that last frame stays on
    the screen until the startup logo, the black window with white text people saw. The
    theme makes the window the whole screen; `clear` as the entry's first command blanks
    it, and the two messages go. Errors of the following commands (a missing kernel or
    initramfs) still print, on the black screen, and wait for a key as before.
    """
    def section(match):
        body = LINUX_ECHO.sub('', match[2])
        body = LINUX_ENTRY_START.sub(lambda entry: entry[0] + entry[1] + '\tclear\n', body)
        return match[1] + body + match[3]

    return LINUX_SECTION.sub(section, text)


def menu_layout(text):
    """A short top level: Emaki, Emaki (LTS kernel), snapshots, Advanced options.

    The fallback initramfs entries and the firmware-settings entry move, unchanged
    apart from their titles, into one "Advanced options" submenu after the snapshot
    menu. Only the stock 10_linux and 30_uefi-firmware forms are touched; other
    entries (Windows, custom ones) keep their place. Entry 0 stays the default kernel.
    A default that named an entry by title, id or a number known before boot (GRUB_DEFAULT,
    the saved or the next entry) is pointed at the same entry where the header ends; for
    any other number, and when 41_custom will run a custom.cfg, the moved entries also stay
    in their old places, and only one of the two orders exists at boot. Without entries to
    move, the kernels are only renamed and an old title is pointed at its kernel. A menu that
    may change the default anywhere else after the header is left unchanged.
    """
    if "submenu 'Advanced options'" in text or HEADER_END not in text:
        return text
    original = text
    advanced = []
    moved = {}
    renamed = {}
    starts = []
    result = []
    position = 0
    old_only = f'if [ "${OLD_LAYOUT}" ]; then\n'
    for match in LINUX_ENTRY.finditer(text):
        end = text.find('\n}\n', match.end())
        if end < 0:
            return text
        body = text[match.end():end + 3]
        title = 'Emaki' if match[1] == 'linux' else 'Emaki (LTS kernel)'
        result.append(text[position:match.start()])
        identifier = ENTRY_ID.search(match[3])
        if match[2]:
            title = 'Emaki (all drivers)' if match[1] == 'linux' else 'Emaki (LTS kernel, all drivers)'
            advanced.append(f"menuentry '{title}'{match[3]}\n{body}")
            result.append(old_only + advanced[-1] + 'fi\n')
            moved[identifier[1] if identifier else title] = ('Emaki, with Linux {value1}{value2}'.format(value1=match[1], value2=match[2]), title)
            starts.append(match.start())
        else:
            result.append(f"menuentry '{title}'{match[3]}\n{body}")
            renamed['Emaki, with Linux {value1}'.format(value1=match[1])] = identifier[1] if identifier else title
        position = end + 3
    result.append(text[position:])
    text = ''.join(result)
    firmware = FIRMWARE.search(text)
    if firmware and firmware[2].strip():
        advanced.append(firmware[2])
        for line in firmware[2].splitlines():
            entry = ENTRY.match(line)
            if entry:
                identifier = ENTRY_ID.search(entry[2])
                title = entry[1].replace("'\\''", "'")
                moved[identifier[1] if identifier else title] = (title, title)
        starts.append(FIRMWARE.search(original).start(2))
        text = text[:firmware.start()] + firmware[1] + old_only + firmware[2] + 'fi\n' + firmware[3] + text[firmware.end():]
    if not advanced and not renamed:
        return text
    # The order is chosen where the header ends; a default changed later would be read in the wrong one.
    late = original[original.index(HEADER_END) + len(HEADER_END):]
    custom = late.count(CUSTOM_BLOCK)
    if custom > 1 or changes_default(late.replace(CUSTOM_BLOCK, '')):
        return original
    if not advanced:
        # Only the kernels are renamed: numbers name the same entries, an old title is pointed at its kernel.
        text = text.replace(CUSTOM_BLOCK, CUSTOM_BLOCK + default_chain(renamed), 1)
        return text.replace(HEADER_END, '# A default that named a kernel by its old title keeps its entry.\n'
                            + default_chain(renamed) + HEADER_END, 1)
    remap = selection_remap(original, moved, renamed, min(starts), custom)
    if remap is None:
        return original
    block = f'if [ -z "${OLD_LAYOUT}" ]; then\n' + ADVANCED + ''.join(advanced) + '}\nfi\n'
    if SNAPSHOTS_END in text:
        text = text.replace(SNAPSHOTS_END, SNAPSHOTS_END + block, 1)
    else:
        text = text.rstrip('\n') + '\n' + block
    text = text.replace(CUSTOM_BLOCK, CUSTOM_BLOCK + remap[1], 1)
    # The header has set $default from GRUB_DEFAULT, saved_entry or next_entry; no entry exists yet.
    return text.replace(HEADER_END, remap[0] + HEADER_END, 1)


def generated_menu_font(text):
    """The face a generated menu loads: a path, None for GRUB's default, '' if unknown."""
    loads = re.findall(r'^if loadfont (\S+) ; then$', text, re.M)
    if loads == ['$font']:
        return None
    return MENU_FONT_DIR + '/' + loads[0].rsplit('/', 1)[1] if (
        len(loads) == 1 and re.fullmatch(r'\S*/usr/share/emaki/grub/fonts/menu-[0-9]+\.pf2', loads[0])) else ''


# A generated loadfont of one of Emaki's packaged faces, whatever root prefix it carries.
SECURE_BOOT_FONT = re.compile(
    r'^(\s*(?:if\s+)?loadfont\s+)\S*?(/usr/share/emaki/grub/(?:fonts|theme)/[A-Za-z0-9_.-]+\.pf2)(?=\s|$)', re.M)


def secure_boot_menu(text):
    """Load Emaki's menu and theme faces from the signed loader's memdisk.

    Under Secure Boot GRUB refuses fonts read from disk (kern/efi/sb.c) but not files of
    its own signed image's memdisk. The loader carries the faces at their packaged paths
    (secure_boot_fonts), so generated_menu_font still names the face it loads.
    """
    return SECURE_BOOT_FONT.sub(lambda match: match[1] + '(memdisk)' + match[2], text)


def secure_boot_fonts(root=Path('/')):
    """Memdisk names and bytes of every packaged menu and theme face."""
    files = {}
    for directory in (MENU_FONT_DIR, MENU_THEME_DIR):
        for path in sorted((Path(root) / directory.lstrip('/')).glob('*.pf2')):
            require(path.is_file() and not path.is_symlink(), Code.BOOT_VERIFY,
                    _('The menu face {value1} is not a regular file.').format(value1=path.name))
            files[directory.lstrip('/') + '/' + path.name] = path.read_bytes()
    return files


def menu_font_fallback(text):
    """Retry gfxterm with the stock face if a saved enlarged face no longer fits.

    The framebuffer used to choose the face belongs to an earlier boot (or the live
    installer). Firmware may choose a smaller mode now. Only our generated face and
    the stock gfxterm command are changed; local fonts and other terminals stay local.
    """
    font = generated_menu_font(text)
    if not font or text.count('\nterminal_output gfxterm\n') != 1:
        return text
    size = next((size for size, ignored, ignored in MENU_FONTS if font.endswith(f'/menu-{size}.pf2')), None)
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
                Code.BOOT_VERIFY, _('GRUB has no executable entry for {value1}.').format(value1=kernel))
