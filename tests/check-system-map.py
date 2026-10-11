#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Every installed Emaki file belongs to exactly one system-map component.

Installed paths come from the packages' expected-files lists (emaki-config's is the whole
`make install install-niri-emaki` payload, diffed in its package()). Components are the
```system-map blocks of MANIFEST.md; where MANIFEST.md is not part of the tree, the blocks
repeated in the rendered docs/system-map.md. Today's uncovered paths are listed, grouped
with a reason, in tests/system-map-allow.txt; that list may only shrink. No root, network
or installed-system access.
"""
import argparse
import importlib.machinery
import importlib.util
from pathlib import Path
import re
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
ALLOW = 'tests/system-map-allow.txt'


def load_renderer(root=ROOT):
    path = root / 'scripts/render-system-map'
    loader = importlib.machinery.SourceFileLoader('render_system_map', str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


system_map = load_renderer()


def allowed(text, name=ALLOW):
    """Allow list: `[reason]` opens a group, then one exact installed path per line."""
    entries, problems, reason, counts = {}, [], None, {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('[') and line.endswith(']'):
            reason = line[1:-1].strip()
            if not reason:
                problems.append(f'{name}:{number}: a group needs a reason')
            counts.setdefault(reason, 0)
            continue
        if reason is None:
            problems.append(f'{name}:{number}: {line} is outside a [reason] group')
        elif not line.startswith('/') or any(char in line for char in '*?['):
            problems.append(f'{name}:{number}: {line} is not an exact installed path')
        elif line in entries:
            problems.append(f'{name}:{number}: {line} is listed twice')
        else:
            entries[line] = reason
            counts[reason] += 1
    problems += [f'{name}: group [{reason}] lists no path' for reason, count in counts.items() if not count]
    return entries, problems


def problems(entries, installed, allow, allow_name=ALLOW):
    """Coverage of `installed` (path -> list name) by `entries`, and the allow list's state."""
    errors = []
    patterns = [(entry['component'], pattern, system_map.glob_pattern(pattern))
                for entry in entries for pattern in entry['files']]
    # Runtime coordination and personal media are created during a session, not
    # installed into an image. Their ownership is still part of the system map.
    dynamic = {(entry['component'], pattern) for entry in entries for pattern in entry['files']
               if (entry['zone'] == 'person' and pattern.startswith('/home/'))
               or (entry['zone'] == 'package' and pattern.startswith('/run/'))}
    owners = {path: sorted({name for name, _, regex in patterns if regex.match(path)}) for path in installed}
    for name, pattern, regex in patterns:
        if (name, pattern) not in dynamic and not any(regex.match(path) for path in installed):
            errors.append(f'{name}: files entry {pattern} matches no installed path')
    for path in sorted(installed):
        found = owners[path]
        if len(found) > 1:
            errors.append(f'{path}: in the files of {len(found)} components ({", ".join(found)}); '
                          'a path belongs to exactly one')
        elif not found and path not in allow:
            errors.append(f'{path}: installed ({installed[path]}) but in no component; add it to the '
                          'files of a system-map block in MANIFEST.md (the allow list only shrinks)')
    for path in sorted(allow):
        if path not in installed:
            errors.append(f'{allow_name}: {path} is no longer installed; delete its line')
        elif owners[path]:
            errors.append(f'{allow_name}: {path} is now in component {owners[path][0]}; delete its line')
    programs = {Path(path).name for path in installed if Path(path).parent.name == 'bin'}
    for entry in entries:
        command = system_map.state_command(entry)
        if command and command.split()[0] not in programs:
            errors.append(f'{entry["component"]}: state command {command.split()[0]} is not a program '
                          'that Emaki installs')
    return errors


def installed_paths(root):
    result = {}
    for recipe in sorted(root.glob('packaging/*/PKGBUILD')):
        if not recipe.with_name('expected-files.list').is_file():
            raise ValueError(f'{recipe.parent.relative_to(root)}: missing expected-files.list')
    for listing in sorted(root.glob('packaging/*/expected-files.list')):
        for line in listing.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith('#'):
                path = line.strip()
                if not path.startswith('/') or '..' in Path(path).parts or any(
                        char in path for char in '*?['):
                    raise ValueError(f'{listing.relative_to(root)}: not an exact installed path: {path}')
                if path in result:
                    raise ValueError(f'{listing.relative_to(root)}: {path} is already listed in {result[path]}')
                result[path] = listing.relative_to(root).as_posix()
    return result


CLI = 'crates/emaki-cli/src'
MAP_SOURCE = 'crates/emaki-core/src/map.rs'


def cli_problems(renderer, root):
    """The rules' own commands are in the emaki CLI help; the map zones are those of `emaki map`."""
    errors = []
    sources = {name: root / CLI / name for name in ('main.rs', 'settings.rs')}
    if not all(path.is_file() for path in sources.values()):
        return [f'{CLI}: main.rs and settings.rs are needed to check the rules\' commands']
    for command in renderer.RULES_COMMANDS.values():
        program, *words = command.split()
        options = [word for word in words if word.startswith('--')]
        words = [word for word in words if not word.startswith('--')]
        source = sources['settings.rs' if words[0] == 'settings' else 'main.rs'].read_text()
        source = '\n'.join(re.findall(r'const \w*HELP: &str = "(.*?)";', source, re.S))
        usage = ' '.join(words[1:] if words[0] == 'settings' else words)
        lines = [line.strip() for line in source.splitlines()]
        found = [line for line in lines if line.startswith(usage + ' ') or line == usage]
        if program != 'emaki' or not found:
            errors.append(f'rules command {command!r} is not in the help of {CLI}')
        elif any(option not in source for option in options):
            errors.append(f'rules command {command!r}: an option is not in the help of {CLI}')
    zones = (root / MAP_SOURCE).read_text() if (root / MAP_SOURCE).is_file() else ''
    if set(renderer.MAP_ZONES) != set(renderer.ZONES):
        errors.append('MAP_ZONES and ZONES name different zones')
    for name in renderer.MAP_ZONES.values():
        if f'zone: "{name}"' not in zones:
            errors.append(f'map zone {name!r} is not a zone of {MAP_SOURCE}')
    return errors


def check(root):
    renderer = load_renderer(root)
    manifest = root / renderer.SOURCE
    if manifest.is_file():
        entries, errors = renderer.components(manifest.read_text(), renderer.SOURCE)
        if not errors:
            page = root / renderer.PAGE
            if not page.is_file() or page.read_text() != renderer.render(entries):
                errors.append(f'{renderer.PAGE} is stale; run make render')
    elif (root / 'scripts/make-public.sh').exists():
        return [f'{renderer.SOURCE} is missing; it is the source of the system map']
    else:
        # A tree without MANIFEST.md (the public sources): the rendered page carries the blocks.
        entries, errors = renderer.components((root / renderer.PAGE).read_text(), renderer.PAGE)
    if not errors:
        rules = root / renderer.RULES
        if not rules.is_file() or rules.read_text() != renderer.render_rules(entries):
            errors.append(f'{renderer.RULES} is stale; run make render')
        errors += cli_problems(renderer, root)
    allow, allow_errors = allowed((root / ALLOW).read_text())
    if not errors and not allow_errors:
        errors += problems(entries, installed_paths(root), allow)
    return errors + allow_errors


COMPLETE = '''```system-map
component: release-marker
what: The installed release.
files:
  /usr/lib/emaki-release
zone: package
state: emaki-release-state --json
change: Update the emaki-config package.
never: Edit the file.
rollback: Boot a snapshot.
```
'''


def self_test():
    installed = {'/usr/lib/emaki-release': 'fixture', '/usr/bin/emaki-release-state': 'fixture',
                 '/usr/share/emaki/old.txt': 'fixture'}
    allow_text = '[map content: fixture]\n/usr/bin/emaki-release-state\n/usr/share/emaki/old.txt\n'

    def run(manifest, paths=installed, allow=allow_text):
        entries, errors = system_map.components(manifest, 'fixture')
        listed, allow_errors = allowed(allow, 'allow')
        return errors + allow_errors + (problems(entries, paths, listed, 'allow')
                                         if not errors and not allow_errors else [])

    cases = [
        ('a complete block passes', run(COMPLETE), None),
        ('a new installed path without a component fails',
         run(COMPLETE, {**installed, '/usr/bin/emaki-new': 'fixture'}), '/usr/bin/emaki-new: installed'),
        ('a component missing rollback fails',
         run(COMPLETE.replace('rollback: Boot a snapshot.\n', '')), 'field rollback is missing'),
        ('an empty field fails', run(COMPLETE.replace('never: Edit the file.', 'never:')),
         'field never is missing or empty'),
        ('a stale allow entry (now covered) fails',
         run(COMPLETE, allow=allow_text + '/usr/lib/emaki-release\n'), 'is now in component release-marker'),
        ('a stale allow entry (no longer installed) fails',
         run(COMPLETE, allow=allow_text + '/usr/share/emaki/gone.txt\n'), 'gone.txt is no longer installed'),
        ('a path in two components fails',
         run(COMPLETE + COMPLETE.replace('component: release-marker', 'component: second')
             .replace('/usr/lib/emaki-release', '/usr/lib/emaki-*')), 'in the files of 2 components'),
        ('a files entry matching nothing fails',
         run(COMPLETE.replace('  /usr/lib/emaki-release\n', '  /usr/lib/emaki-release\n  /usr/lib/nothing/**\n')),
         '/usr/lib/nothing/** matches no installed path'),
        ('personal media can describe session-created files',
         run(COMPLETE + COMPLETE.replace('component: release-marker', 'component: personal-media')
             .replace('/usr/lib/emaki-release', '/home/*/Pictures/Screenshots/*')
             .replace('zone: package', 'zone: person')), None),
        ('runtime coordination can describe session-created files',
         run(COMPLETE + COMPLETE.replace('component: release-marker', 'component: capture-runtime')
             .replace('/usr/lib/emaki-release', '/run/user/*/emaki-capture-*.lock')), None),
        ('package ownership cannot hide an unmatched personal path',
         run(COMPLETE + COMPLETE.replace('component: release-marker', 'component: wrong-owner')
             .replace('/usr/lib/emaki-release', '/home/*/Pictures/Screenshots/*')),
         'matches no installed path'),
        ('an unknown zone fails', run(COMPLETE.replace('zone: package', 'zone: system')), "zone 'system'"),
        ('"none yet" without a reason fails',
         run(COMPLETE.replace('state: emaki-release-state --json', 'state: none yet:')), 'needs a reason'),
        ('a state command Emaki does not install fails',
         run(COMPLETE.replace('emaki-release-state --json', 'invented --json')), 'state command invented'),
        ('an allow entry outside a group fails', run(COMPLETE, allow='/usr/share/emaki/old.txt\n'),
         'outside a [reason] group'),
    ]
    failed = 0
    for label, errors, expected in cases:
        ok = not errors if expected is None else any(expected in error for error in errors)
        print(f'{"ok" if ok else "FAILED"}: {label}' + ('' if ok else f': {errors}'))
        failed += not ok
    # The renderer repeats each block: the page parses back into the same components.
    entries, _ = system_map.components(COMPLETE, 'fixture')
    again, errors = system_map.components(system_map.render(entries), 'page')
    ok = not errors and again == entries
    print(f'{"ok" if ok else "FAILED"}: the rendered page carries the same blocks')
    failed += not ok
    # State queries come from the map, including commands that do not accept --json.
    fixture = dict(entries[0], component='settings-runtime', state='emaki-shell call settings status')
    rules = system_map.render_rules([*entries, fixture])
    ok = ('`emaki-shell call settings status`' in rules
          and 'emaki-shell call settings status --json' not in rules)
    print(f'{"ok" if ok else "FAILED"}: rules preserve the map state command exactly')
    failed += not ok
    # Whole trees: a stale page fails; without MANIFEST.md the page's blocks are checked.
    with tempfile.TemporaryDirectory() as directory:
        tree = Path(directory)
        (tree / 'scripts').mkdir()
        shutil.copy(ROOT / 'scripts/render-system-map', tree / 'scripts')
        (tree / 'scripts/make-public.sh').touch()
        (tree / 'docs').mkdir()
        (tree / 'tests').mkdir()
        (tree / 'tests/system-map-allow.txt').write_text(allow_text)
        (tree / 'packaging/emaki-config').mkdir(parents=True)
        (tree / 'packaging/emaki-config/expected-files.list').write_text('\n'.join(installed) + '\n')
        (tree / 'MANIFEST.md').write_text(COMPLETE)
        (tree / 'docs/system-map.md').write_text(system_map.render(entries))
        (tree / system_map.RULES).write_text(system_map.render_rules(entries))
        (tree / CLI).mkdir(parents=True)
        (tree / MAP_SOURCE).parent.mkdir(parents=True)
        (tree / CLI / 'main.rs').write_text(
            'const HELP: &str = "Commands:\n  map [--json]  Paths\n  state [--json]  State";\n')
        settings = (tree / CLI / 'settings.rs')
        settings.write_text('const HELP: &str = "  history  Changes\n  undo ID  Restore\n  --json  JSON";\n')
        (tree / MAP_SOURCE).write_text('zone: "package"\nzone: "managed"\nzone: "yours"\n')
        cases = [('a fresh tree passes', check(tree), None)]
        settings.write_text('const HELP: &str = "  log  Changes\n  undo ID  Restore\n  --json  JSON";\n')
        cases.append(('a renamed command named by the rules fails', check(tree),
                      "'emaki settings history --json' is not in the help"))
        settings.write_text('// history\nconst HELP: &str = "  undo ID  Restore\n  --json  JSON";\n')
        cases.append(('a command outside the help text fails', check(tree), 'is not in the help'))
        settings.write_text('const HELP: &str = "  history  Changes\n  undo ID  Restore";\n')
        cases.append(('a missing option fails', check(tree), 'an option is not in the help'))
        settings.write_text('const HELP: &str = "  history  Changes\n  undo ID  Restore\n  --json  JSON";\n')
        (tree / MAP_SOURCE).write_text('zone: "package"\nzone: "settings"\nzone: "yours"\n')
        cases.append(('a renamed emaki map zone fails', check(tree), "map zone 'managed'"))
        (tree / MAP_SOURCE).write_text('zone: "package"\nzone: "managed"\nzone: "yours"\n')
        (tree / system_map.RULES).write_text('old instructions\n')
        cases.append(('a stale rules template fails', check(tree), 'is stale; run make render'))
        (tree / system_map.RULES).unlink()
        cases.append(('a missing rules template fails', check(tree), 'is stale; run make render'))
        (tree / system_map.RULES).write_text(system_map.render_rules(entries))
        (tree / 'MANIFEST.md').write_text(COMPLETE.replace('Edit the file.', 'Edit it.'))
        cases.append(('a stale rendered page fails', check(tree), 'is stale; run make render'))
        (tree / 'MANIFEST.md').unlink()
        cases.append(('a private tree without MANIFEST.md fails', check(tree), 'MANIFEST.md is missing'))
        (tree / 'scripts/make-public.sh').unlink()
        cases.append(('a public tree checks the blocks of its page', check(tree), None))
        (tree / system_map.RULES).write_text('old public instructions\n')
        cases.append(('a public tree rejects stale rules', check(tree), 'is stale; run make render'))
        (tree / system_map.RULES).write_text(system_map.render_rules(entries))
        changed = [dict(entries[0], state='emaki-state --json')]
        (tree / 'docs/system-map.md').write_text(system_map.render(changed))
        cases.append(('a changed map command invalidates the rules', check(tree), 'is stale; run make render'))
        (tree / 'docs/system-map.md').write_text(system_map.render(entries))
        (tree / 'packaging/emaki-config/PKGBUILD').touch()
        listing = tree / 'packaging/emaki-config/expected-files.list'
        listing.write_text('# Fixture inventory header\n'
                           + '\n'.join(installed) + '\n')
        cases.append(('inventory headers are not installed paths', check(tree), None))
        for label, content, expected in (
                ('an inventory wildcard fails', '/usr/lib/*\n', 'not an exact installed path'),
                ('a repeated inventory path fails', '/usr/bin/repeat\n/usr/bin/repeat\n', 'already listed'),
                ('a missing package inventory fails', None, 'missing expected-files.list')):
            if content is None:
                listing.unlink()
            else:
                listing.write_text(content)
            try:
                installed_paths(tree)
                errors = []
            except ValueError as error:
                errors = [str(error)]
            cases.append((label, errors, expected))
    for label, errors, expected in cases:
        ok = not errors if expected is None else any(expected in error for error in errors)
        print(f'{"ok" if ok else "FAILED"}: {label}' + ('' if ok else f': {errors}'))
        failed += not ok
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    root = args.root.resolve()
    try:
        errors = check(root)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        errors = [str(error)]
    for error in errors:
        print(f'system map: {error}', file=sys.stderr)
    if errors:
        return 1
    print('system map: every installed path is in one component or in the shrinking allow list')
    return 0


if __name__ == '__main__':
    sys.exit(main())
