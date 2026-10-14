#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check a pristine image's launcher inventory before accepting package changes.

Run on a fresh live, Minimal or Rich image, before adding personal applications.
The baseline records the existing application set; it does not hide new entries.
"""
import argparse
import configparser
import json
from pathlib import Path
import re
import tempfile

ROOT = Path(__file__).resolve().parents[1]
# The update manager and Report a problem come with emaki-config, so every set shows them;
# Blueman's two entries are hidden (Bluetooth lives in the shell panel); Printers comes with
# emaki-apps (Rich only).
MINIMAL = {'emaki-welcome', 'firefox', 'kitty', 'org.kde.dolphin',
           'emaki-update-manager', 'emaki-report'}
LIVE = MINIMAL | {'emaki-install', 'gparted'}
RICH = MINIMAL | {
    'org.kde.ark', 'org.kde.discover', 'org.kde.elisa', 'org.kde.filelight',
    'org.kde.gwenview', 'org.kde.haruna', 'org.kde.isoimagewriter', 'org.kde.kate',
    'org.kde.kcharselect', 'emaki-printers',
    'org.keepassxc.KeePassXC', 'com.obsproject.Studio',
    'org.kde.okular', 'org.kde.partitionmanager', 'org.kde.plasma-systemmonitor',
    'org.qbittorrent.qBittorrent', 'org.kde.skanlite', 'org.kde.spectacle',
    'org.mozilla.Thunderbird', 'cups',
    *('libreoffice-' + app for app in ('base', 'calc', 'draw', 'impress', 'math', 'startcenter', 'writer')),
}


# XDG_CURRENT_DESKTOP of an Emaki session; quickshell-emaki's 0006 applies OnlyShowIn and
# NotShowIn against it as GLib does (the first named desktop decides).
DESKTOPS = ('niri',)


ESCAPES = {'s': ' ', 'n': '\n', 't': '\t', 'r': '\r', '\\': '\\', ';': ';'}


def string_list(value):
    """A desktop entry string list: an unescaped ';' ends an element, and \\s, \\n, \\t, \\r,
    \\\\ and \\; decode inside elements (specification, "Possible value types")."""
    items, item, characters = [], '', iter(value)
    for character in characters:
        if character == '\\':
            escaped = next(characters, '')
            item += ESCAPES.get(escaped, '\\' + escaped)
        elif character == ';':
            items.append(item)
            item = ''
        else:
            item += character
    return items + [item] if item else items


def shown_here(entry, desktops=DESKTOPS):
    only = string_list(entry.get('onlyshowin', ''))
    never = string_list(entry.get('notshowin', ''))
    for desktop in desktops:
        if desktop in only:
            return True
        if desktop in never:
            return False
    # A present OnlyShowIn hides the entry from unnamed desktops even when it names none.
    return 'onlyshowin' not in entry


def hidden_ids(catalog):
    return set(json.loads(re.search(r'hiddenIds: (\[[^\n]+\])', catalog.read_text())[1]))


def inventory(directories, hidden, desktops=DESKTOPS):
    entries = {}
    for directory in directories:
        for path in sorted(directory.rglob('*.desktop')):
            entry_id = str(path.relative_to(directory)).removesuffix('.desktop').replace('/', '-')
            if entry_id in entries:
                continue
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.read(path, encoding='utf-8')
            entries[entry_id] = dict(parser['Desktop Entry']) if parser.has_section('Desktop Entry') else {}
    visible, redundant = set(), set()
    for entry_id, entry in entries.items():
        concealed = entry.get('nodisplay') == 'true' or entry.get('hidden') == 'true' or not shown_here(entry, desktops)
        if concealed and entry_id in hidden:
            redundant.add(entry_id)
        if entry.get('type') == 'Application' and not concealed and entry_id not in hidden:
            visible.add(entry_id)
    return visible, redundant


def check(directories, hidden, expected):
    visible, redundant = inventory(directories, hidden)
    faults = []
    if visible - expected:
        faults.append('Unexpected visible entries: ' + ', '.join(sorted(visible - expected)))
    if expected - visible:
        faults.append('Missing visible entries: ' + ', '.join(sorted(expected - visible)))
    if redundant:
        faults.append('Redundant hidden IDs (already hidden by the package): ' + ', '.join(sorted(redundant)))
    if faults:
        raise ValueError('\n'.join(faults))
    return visible


def self_test():
    with tempfile.TemporaryDirectory(prefix='launcher-inventory-') as temporary:
        apps = Path(temporary)
        def entry(name, extra=''):
            (apps / (name + '.desktop')).write_text('[Desktop Entry]\nType=Application\nName=Fixture\nExec=true\n' + extra)
        entry('app')
        entry('utility')
        entry('helper', 'NoDisplay=true\n')
        check([apps], {'utility'}, {'app'})
        entry('new-dependency')
        try:
            check([apps], {'utility'}, {'app'})
        except ValueError as error:
            assert 'Unexpected visible entries: new-dependency' in str(error)
        else:
            raise AssertionError('A new dependency escaped the inventory check')
        (apps / 'new-dependency.desktop').unlink()
        try:
            check([apps], {'utility', 'helper'}, {'app'})
        except ValueError as error:
            assert 'Redundant hidden IDs' in str(error)
        else:
            raise AssertionError('An already hidden helper remained on the list')
        # OnlyShowIn/NotShowIn hide an entry as NoDisplay does (Blueman's adapter dialog).
        entry('other-desktops', 'OnlyShowIn=XFCE;MATE;\n')
        entry('not-here', 'NotShowIn=niri;\n')
        entry('also-here', 'OnlyShowIn=GNOME;niri;\n')
        entry('not-kde', 'NotShowIn=KDE;\n')
        check([apps], {'utility'}, {'app', 'also-here', 'not-kde'})
        try:
            check([apps], {'utility', 'other-desktops'}, {'app', 'also-here', 'not-kde'})
        except ValueError as error:
            assert 'Redundant hidden IDs (already hidden by the package): other-desktops' in str(error)
        else:
            raise AssertionError('An entry hidden by OnlyShowIn remained on the list')
        for name in ('other-desktops', 'not-here', 'also-here', 'not-kde'):
            (apps / (name + '.desktop')).unlink()
        # A present but empty OnlyShowIn names no desktop, so it hides the entry everywhere.
        entry('only-empty', 'OnlyShowIn=\n')
        entry('only-empty-element', 'OnlyShowIn=;\n')
        check([apps], {'utility'}, {'app'})
        assert not shown_here({'onlyshowin': ''}, ()) and shown_here({}, ())
        for name in ('only-empty', 'only-empty-element'):
            (apps / (name + '.desktop')).unlink()
        # List escapes decode before matching: "ni\sri" names the desktop "ni ri".
        assert string_list(r'ni\sri;a\;b;c\\;\t\n\r;') == ['ni ri', 'a;b', 'c\\', '\t\n\r']
        assert string_list('XFCE;MATE') == ['XFCE', 'MATE'] and string_list(';') == ['']
        assert shown_here({'onlyshowin': r'XFCE;ni\sri;'}, ('ni ri',))
        assert not shown_here({'notshowin': r'ni\sri;'}, ('ni ri',))
        assert shown_here({'onlyshowin': r'a\;b;'}, ('a;b',))
        entry('only-escaped', 'OnlyShowIn=XFCE;ni\\sri;\n')
        entry('not-escaped', 'NotShowIn=ni\\sri;\n')
        assert inventory([apps], {'utility'})[0] == {'app', 'not-escaped'}
        assert inventory([apps], {'utility'}, ('ni ri',))[0] == {'app', 'only-escaped'}
        for name in ('only-escaped', 'not-escaped'):
            (apps / (name + '.desktop')).unlink()
        # The packaged helpers remain valid desktop entries; only the launcher
        # hides them, while the chosen player and editor remain visible.
        for path in apps.glob('*.desktop'):
            path.unlink()
        for name in ('mpv', 'org.kde.kwrite', 'org.kde.haruna', 'org.kde.kate'):
            entry(name)
        check([apps], hidden_ids(ROOT / 'shell/AppCatalog.qml'), {'org.kde.haruna', 'org.kde.kate'})
        assert {'mpv', 'org.kde.kwrite'}.isdisjoint(RICH)
        assert {'org.kde.haruna', 'org.kde.kate'} <= RICH
    print('PASS: unexpected dependency and redundant hide-list regression checks')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=('minimal', 'rich', 'live'))
    parser.add_argument('--applications', action='append', type=Path)
    parser.add_argument('--catalog', type=Path, default=ROOT / 'shell/AppCatalog.qml')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        if not args.profile:
            parser.error('--profile is required for an image inventory')
        expected = {'minimal': MINIMAL, 'rich': RICH, 'live': LIVE}[args.profile]
        try:
            actual = check(args.applications or [Path('/usr/share/applications')], hidden_ids(args.catalog), expected)
        except ValueError as error:
            parser.exit(1, str(error) + '\n')
        print(f'PASS: {args.profile} launcher inventory ({len(actual)} visible entries)')


if __name__ == '__main__':
    main()
