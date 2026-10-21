#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read the shipped shortcut reference, never execute configuration contents."""

import sys as _i18n_sys
from pathlib import Path as _I18nPath
_i18n_sys.path.insert(0, str(_I18nPath(__file__).resolve().parent))
_i18n_sys.path.insert(0, str(_I18nPath(__file__).resolve().parents[2] / 'scripts'))
try:
    from emaki_i18n import translation
except ModuleNotFoundError:
    import emaki_paths as _i18n_paths
    _i18n_sys.path.insert(0, _i18n_paths.LIBEXECDIR)
    from emaki_i18n import translation
gettext = translation('shell').gettext

import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import emaki_paths

TOKEN = re.compile(r'(?P<space>[ \t\r]+)|(?P<comment>//[^\n]*|/\*.*?\*/)|(?P<raw>r?(?P<hash>\#+)".*?"(?P=hash))|(?P<string>"(?:\\.|[^"\\])*")|(?P<punct>[{};\n=])|(?P<word>[^\s{};="/]+|/(?![/*-])[^\s{};=]*)|(?P<disabled>/-)', re.S)

# Shell descriptions are independent of the compositor overlay titles.
ACTION_TITLES = {
    ('focus-column-left',): gettext('Focus left column'),
    ('focus-column-right',): gettext('Focus right column'),
    ('focus-window-or-workspace-up',): gettext('Focus window or workspace above'),
    ('focus-window-or-workspace-down',): gettext('Focus window or workspace below'),
    ('move-column-left',): gettext('Move column left'),
    ('move-column-right',): gettext('Move column right'),
    ('move-window-up-or-to-workspace-up',): gettext('Move window up or to workspace above'),
    ('move-window-down-or-to-workspace-down',): gettext('Move window down or to workspace below'),
    ('fullscreen-window',): gettext('Toggle fullscreen'),
    ('switch-preset-column-width',): gettext('Cycle column widths'),
    ('maximize-column',): gettext('Maximize column'),
    ('toggle-overview',): gettext('Toggle overview'),
    ('switch-layout', 'next'): gettext('Switch keyboard layout'),
    ('screenshot',): gettext('Take an interactive screenshot'),
    ('show-hotkey-overlay',): gettext('Show shortcut overview'),
    ('spawn', 'emaki-shell', 'call', 'launcher', 'toggle'): gettext('Open launcher'),
    ('spawn', 'brightnessctl', '--class=leds', '--device=*::kbd_backlight', 'set', '10%+'): gettext('Make keyboard brighter'),
    ('spawn', 'brightnessctl', '--class=leds', '--device=*::kbd_backlight', 'set', '10%-'): gettext('Make keyboard dimmer'),
    ('spawn', 'playerctl', 'play-pause'): gettext('Play or pause media'),
    ('spawn', 'playerctl', 'previous'): gettext('Previous track'),
    ('spawn', 'playerctl', 'next'): gettext('Next track'),
    ('spawn', 'playerctl', 'stop'): gettext('Stop media'),
    ('spawn', 'wpctl', 'set-volume', '-l', '1.0', '@DEFAULT_AUDIO_SINK@', '5%+'): gettext('Raise volume'),
    ('spawn', 'wpctl', 'set-volume', '@DEFAULT_AUDIO_SINK@', '5%-'): gettext('Lower volume'),
    ('spawn', 'wpctl', 'set-mute', '@DEFAULT_AUDIO_SINK@', 'toggle'): gettext('Mute or unmute sound'),
    ('spawn', 'wpctl', 'set-mute', '@DEFAULT_AUDIO_SOURCE@', 'toggle'): gettext('Mute or unmute microphone'),
    ('spawn', 'brightnessctl', '--class=backlight', 'set', '5%+'): gettext('Make screen brighter'),
    ('spawn', 'brightnessctl', '--class=backlight', 'set', '5%-'): gettext('Make screen dimmer'),
    **{('focus-workspace', str(number)): gettext("Go to workspace {number}").format(number=number) for number in range(1, 11)},
    **{('move-column-to-workspace', str(number)): gettext("Move column to workspace {number}").format(number=number) for number in range(1, 11)},
}

OVERLAY_TITLES = {
    'Battery, profiles and session': gettext('Battery, profiles and session'),
    'Bluetooth': gettext('Bluetooth'),
    'Brightness and night light': gettext('Brightness and night light'),
    'Clipboard history': gettext('Clipboard history'),
    'Close window': gettext('Close window'),
    'Float or tile window': gettext('Float or tile window'),
    'Focus dock': gettext('Focus dock'),
    'Focus newest notification': gettext('Focus newest notification'),
    'Focus top bar': gettext('Focus top bar'),
    'Keyboard layouts': gettext('Keyboard layouts'),
    'Lock screen': gettext('Lock screen'),
    'Make column narrower': gettext('Make column narrower'),
    'Make column wider': gettext('Make column wider'),
    'Move column to left monitor': gettext('Move column to left monitor'),
    'Move column to monitor above': gettext('Move column to monitor above'),
    'Move column to monitor below': gettext('Move column to monitor below'),
    'Move column to right monitor': gettext('Move column to right monitor'),
    'Move window into or out of left column': gettext('Move window into or out of left column'),
    'Move window into or out of right column': gettext('Move window into or out of right column'),
    'Notifications, calendar and music': gettext('Notifications, calendar and music'),
    'Open files': gettext('Open files'),
    'Open launcher': gettext('Open launcher'),
    'Open terminal': gettext('Open terminal'),
    'Open terminal (works without Super)': gettext('Open terminal (works without Super)'),
    'Power menu': gettext('Power menu'),
    'Privacy and capture activity': gettext('Privacy and capture activity'),
    'Record screen': gettext('Record screen'),
    'Search keyboard shortcuts': gettext('Search keyboard shortcuts'),
    'Show that the password window is real': gettext('Show that the password window is real'),
    'Sound and app volume': gettext('Sound and app volume'),
    'Switch windows': gettext('Switch windows'),
    'Switch windows backwards': gettext('Switch windows backwards'),
    'Switch windows of the same app': gettext('Switch windows of the same app'),
    'Switch windows of the same app backwards': gettext('Switch windows of the same app backwards'),
    'Take an interactive screenshot': gettext('Take an interactive screenshot'),
    'Tray applications': gettext('Tray applications'),
    'Welcome to Emaki': gettext('Welcome to Emaki'),
    'Wi-Fi and VPN': gettext('Wi-Fi and VPN'),
}

def tokens(text):
    out = []
    pos = 0
    while pos < len(text):
        match = TOKEN.match(text, pos)
        if not match:
            raise ValueError("Unsupported configuration syntax")
        pos = match.end()
        kind = match.lastgroup
        if kind in ("space", "comment"):
            continue
        value = match.group()
        if kind == "string":
            value = json.loads(value)
        elif kind == "raw":
            value = value[value.index('"') + 1:value.rindex('"')]
        out.append((kind, value))
    return out

def nodes(text):
    stream = tokens(text)
    pos = 0
    def block(nested=False):
        nonlocal pos
        result = []
        while pos < len(stream):
            kind, value = stream[pos]
            if value in ("\n", ";") and kind == "punct":
                pos += 1
                continue
            if value == "}" and kind == "punct":
                if not nested:
                    raise ValueError("Unexpected closing brace")
                pos += 1
                return result
            disabled = kind == "disabled"
            if disabled:
                pos += 1
            head = []
            children = []
            while pos < len(stream):
                kind, value = stream[pos]
                if kind == "punct" and value in ("\n", ";", "}"):
                    break
                pos += 1
                if kind == "punct" and value == "{":
                    children = block(True)
                    break
                head.append(value)
            if not head:
                raise ValueError("Missing node name")
            if not disabled:
                result.append((head, children))
        if nested:
            raise ValueError("Missing closing brace")
        return result
    return block()

def read_shortcuts(path, *additional_paths):
    rows = {}
    active = set()
    seen = 0
    modifier = "Super"
    def read(file, optional=False):
        nonlocal seen, modifier
        file = file.expanduser().resolve()
        try:
            size = file.stat().st_size
        except FileNotFoundError:
            if optional:
                return
            raise ValueError("Configuration is not a bounded regular file") from None
        seen += 1
        if file in active or seen > 64:
            raise ValueError("Include cycle or too many files")
        active.add(file)
        if not file.is_file() or size > 1024 * 1024:
            raise ValueError("Configuration is not a bounded regular file")
        data = file.read_bytes()
        if len(data) > 1024 * 1024:
            raise ValueError("Configuration is too large")
        for head, children in nodes(data.decode("utf-8")):
            if head[0] == "include":
                arguments = head[1:]
                optional = False
                if len(arguments) == 4:
                    for at in (0, 1):
                        if arguments[at:at + 2] == ["optional", "="] and arguments[at + 2] in ("true", "false"):
                            optional = arguments[at + 2] == "true"
                            arguments = arguments[:at] + arguments[at + 3:]
                            break
                if len(arguments) != 1:
                    raise ValueError("Unsupported include")
                read(file.parent / Path(arguments[0]).expanduser(), optional)
            elif head[0] == "input":
                for setting, _ in children:
                    if setting[0] == "mod-key" and len(setting) == 2:
                        modifier = setting[1]
            elif head[0] == "binds":
                for binding, actions in children:
                    title = ""
                    if "hotkey-overlay-title" in binding:
                        at = binding.index("hotkey-overlay-title")
                        if binding[at + 1:at + 2] == ["="]:
                            title = binding[at + 2]
                    if title == "null":
                        title = ""
                    description = OVERLAY_TITLES.get(title, title) or "; ".join(
                        ACTION_TITLES.get(tuple(action), " ".join(action))
                        for action, _ in actions)
                    rows[binding[0].casefold()] = {"keys": binding[0], "description": description or gettext("Disabled")}
        active.remove(file)
    read(Path(path))
    for additional in additional_paths:
        read(Path(additional))
    for row in rows.values():
        row["keys"] = row["keys"].replace("Mod+", modifier + "+")
    return list(rows.values())

def main():
    parser = argparse.ArgumentParser()
    nearby = Path(__file__).resolve().parents[2] / "niri/default.kdl"
    default = nearby if nearby.is_file() else Path(emaki_paths.DATADIR) / "niri/default.kdl"
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    try:
        if args.config is not None:
            rows = read_shortcuts(args.config)
        else:
            fork_rules = default.with_name("fork-rules.kdl")
            rows = read_shortcuts(default, *([fork_rules] if fork_rules.is_file() else []))
        result = {"rows": rows, "state": "ready"}
    except (OSError, ValueError, IndexError, RecursionError):
        result = {"rows": [], "state": "unavailable"}
    print(json.dumps(result))

if __name__ == "__main__":
    main()
