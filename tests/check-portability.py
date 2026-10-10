#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Portability check: the session code stays free of one distribution's paths and tools.

The rules are the "Portability" section of the directives. This check enforces the four that a
search can see, in the components that would move to another distribution as they are (the
shell, the core, the session scripts, the update window, niri and login configs, the session's
user units):

  usr-path         no literal /usr/bin, /usr/lib, /usr/libexec, /usr/share (rule 1)
  python-path      no /usr/bin/python3 in a command (rule 2)
  package-manager  no pacman, vercmp, pacman-conf, checkupdates, snapper, grub-*,
                   /var/lib/pacman or pacman.log outside a backend (rule 4)
  etc-write        nothing writes /etc at run time (rule 3)

Comment lines and shebangs are not read. Today's violations are listed one per line in
tests/portability-allow.txt (path, the offending line, the reason). The check fails on a
violation that is not listed and on a listed line that no longer matches, so the list only
shrinks and never hides a spot that was fixed.

  python3 tests/check-portability.py              check the tree
  python3 tests/check-portability.py --self-test  check the checker on fixtures
  python3 tests/check-portability.py --write-allow
      rewrite the allow list from the tree (keeps existing reasons; new lines get "TODO")
"""
import argparse
import collections
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOW = 'tests/portability-allow.txt'

# Every top-level directory is either checked or exempt with a reason; a new one must be
# classified here before the check passes. Files at the top level (docs, build files) are exempt.
CHECKED = ('shell', 'crates', 'scripts', 'update-manager', 'niri', 'greetd', 'systemd')
EXEMPT_TOP = {
    # Build-time directories a package build creates next to the sources (no git there to ignore them).
    '.cache': 'build cache of the package build, not shipped source',
    '.cargo': 'Cargo home of the package build, not shipped source',
    'vendor': 'vendored Cargo dependencies of the package build, not Emaki source',
    'installer': 'class C: the Arch installer',
    'iso': 'class C: the Arch installation image',
    'packaging': 'class C: Arch packages and release tooling',
    'upkeep': 'class C: pacman hooks, migrations and package-cache upkeep',
    'grub': 'class C: boot menu look and snapshot entries',
    'boot': 'class C: boot trial counter and automatic return',
    'initcpio': 'class C: initramfs hooks',
    'os-release': 'class C: identity installed by a pacman hook',
    'polkit': 'policy files: paths are templated at install time (rule 8), not a file kind read here',
    'art': 'build-time artwork sources, not installed as code',
    'cursors': 'build-time cursor sources',
    'docs': 'prose: rule 10 is kept by review',
    'tests': 'tests and fixtures name these paths on purpose',
    'fetch': 'config files of another program; Emaki paths are substituted at install (rule 7)',
    'fuzzel': 'config files of another program; Emaki paths are substituted at install (rule 7)',
    'gtk': 'theme data',
    'hypr': 'config files of another program; Emaki paths are substituted at install (rule 7)',
    'kitty': 'config files of another program; Emaki paths are substituted at install (rule 7)',
    'qt6ct': 'config files of another program; Emaki paths are substituted at install (rule 7)',
    'etc-skel': 'seed files of other programs; rule 9 replaces them with read-time defaults',
    '.github': 'CI configuration of the build machine',
}

# Exempt paths inside checked directories (prefix match on the repository path).
EXEMPT = {
    # Build outputs contain the selected paths; tests/test-paths.py checks rendering.
    'shell/Platform.qml': 'generated install paths',
    'shell/helpers/emaki_paths.py': 'generated install paths',
    'scripts/emaki_paths.py': 'generated install paths',
    'scripts/paths': 'generated install paths',
    # Class C scripts: Arch by nature, replaced by a different concept elsewhere.
    'scripts/emaki-boot-refresh': 'class C: automatic return after a failed boot (snapper, GRUB)',
    'scripts/emaki-update-boot': 'class C: automatic return after a failed boot (snapper, GRUB)',
    'scripts/emaki-rollback': 'class C: snapshot rollback (snapper, btrfs, GRUB)',
    'scripts/emaki-update': 'class C: terminal update path around pacman -Syu',
    'scripts/emaki-update-channel': 'class C: channel switch through pacman.conf',
    'scripts/emaki-transaction-inhibit': 'class C: pacman PreTransaction hook',
    'scripts/emaki-qt-check': 'class C: Qt mismatch guard for pacman upgrades',
    'scripts/emaki-wallet-migrate': 'class C: migration from the old keyring on Arch upgrades',
    'scripts/emaki-keyring-recover': 'class C: migration from the old keyring on Arch upgrades',
    'scripts/emaki-migrate-installer-config': 'class C: installer config migration',
    # Build and release tools: run on the build machine, never in the session.
    'scripts/build-': 'build tool',
    'scripts/render-': 'build tool (make render)',
    'scripts/check-arch-apps.py': 'release tool',
    'scripts/check-delivery.py': 'release tool',
    'scripts/core-package.py': 'build tool',
    'scripts/cleanup': 'development tool',
    'scripts/make-public.sh': 'release tool',
    'scripts/public/': 'release tool',
    'scripts/release-notes': 'release tool',
    'scripts/try-palette.py': 'development tool',
    # The update backend: the one place allowed to speak to the package manager.
    'update-manager/backend.py': 'backend module',
    'update-manager/apply.py': 'backend module',
    'update-manager/catalog.py': 'backend module',
    'update-manager/emaki_update_space.py': 'backend module (class C terminal update path)',
    'scripts/emaki_session_arch.py': 'backend module: Arch producer of the desktop state (seam 4)',
    'update-manager/emaki-update.service': 'backend system service',
    'update-manager/tests/': 'tests',
    'update-manager/ui/tests/': 'tests',
    'crates/emaki-cli/tests/': 'tests',
    'crates/emaki-core/tests/': 'tests',
    # System units: the check reads the user units the session runs (rule 8 covers the rest).
    'systemd/50-emaki.preset': 'system preset (rule 8: enablement list)',
    'systemd/emaki-boot-': 'class C: system unit of the automatic return',
    'systemd/emaki-update-boot.service': 'class C: system unit of the automatic return',
    'systemd/emaki-snapshot-menu.service': 'class C: snapshot boot entries',
    'systemd/emaki-refresh-mirrors.': 'class C: Arch mirror list',
    'systemd/emaki-wallet-migrate.service': 'class C: keyring migration',
    'systemd/emaki-drm-hold.service': 'system unit (rule 8)',
    'systemd/emaki-wifi-recovery.service': 'system unit (rule 8)',
    'systemd/90-emaki-wifi-recovery.rules': 'udev rule (rule 8)',
    'systemd/greetd.service.d/': 'system drop-in (rule 8)',
    'systemd/logind.conf.d/': 'system drop-in',
}

# Privileged helpers that may write /etc (none yet: rule 3 wants machine state in /var/lib/emaki).
ETC_WRITERS = {}

RULES = ('usr-path', 'python-path', 'package-manager', 'etc-write')
FIX = {
    'usr-path': 'call programs by name through PATH; reach Emaki files through the paths module',
    'python-path': 'run python3 by name or through EMAKI_PYTHON',
    'package-manager': 'ask the update or restore-point backend instead',
    'etc-write': 'keep machine state in /var/lib/emaki or go through a systemd D-Bus service',
}

USR = re.compile(r'''/usr/(?:bin|libexec|lib|share)(?=$|[/\s'"`,;:)\]}])[^\s'"`,;)\]}]*''')
PYTHON = re.compile(r'^/usr/bin/python3?$')
PACKAGE = re.compile(
    r'(?<![\w.-])(?:pacman-conf|pacman-key|pacman|vercmp|checkupdates|snapper|grub-[a-z][\w-]*)(?![\w-])'
    r'|/var/lib/pacman|pacman\.log')
ETC_WRITE = (
    re.compile(r'''\bopen\(\s*f?['"]/etc/[^'"]*['"]\s*,\s*['"][^'"]*[wax+]'''),
    re.compile(r'''['"]/etc/[^'"]*['"]\s*\)\s*\.(?:write_text|write_bytes|touch|unlink|symlink_to)\b'''),
    re.compile(r'''(?<![\w-])['"]?(?:install|cp|mv|tee)['"]?[\s,].*/etc(?:/|['"\s]|$)'''),
    re.compile(r'''(?<![\w-])['"]?sed['"]?(?=[\s,]).*[\s,'"]-i.*/etc/'''),
    re.compile(r'''>>?\s*['"]?/etc/'''),
)

SHELL_SHEBANG = re.compile(r'^#!\s*(?:/usr)?/bin/(?:env\s+)?(?:sh|bash|ash|dash)\b')
PYTHON_SHEBANG = re.compile(r'^#!.*\bpython')
UNIT_SUFFIXES = ('.service', '.path', '.timer', '.socket', '.target', '.conf')


def kind(path, first_line):
    """The language of a checked file, or None for files this check does not read."""
    name = path.rsplit('/', 1)[-1]
    if name.endswith(('.qml', '.js', '.mjs')):
        return 'qml'
    if name.endswith('.py') or PYTHON_SHEBANG.match(first_line):
        return 'python'
    if name.endswith('.sh') or SHELL_SHEBANG.match(first_line):
        return 'shell'
    if name.endswith('.kdl'):
        return 'kdl'
    if name.endswith('.rs'):
        return 'rust'
    if name.endswith('.desktop'):
        return 'desktop'
    if path.startswith('systemd/') and name.endswith(UNIT_SUFFIXES):
        return 'unit'
    return None


COMMENT = {
    'qml': ('//', '/*', '*'), 'rust': ('//', '/*', '*'), 'kdl': ('//', '/*', '*'),
    'python': ('#',), 'shell': ('#',), 'desktop': ('#',), 'unit': ('#', ';'),
}


def exemption(path):
    top = path.split('/', 1)[0]
    if '/' not in path:
        return 'top-level repository file'
    if top in EXEMPT_TOP:
        return EXEMPT_TOP[top]
    if top not in CHECKED:
        return None
    for prefix, reason in EXEMPT.items():
        if path.startswith(prefix):
            return reason
    if top == 'crates' and path.endswith('_tests.rs'):
        return 'tests'
    return ''


def normalize(line):
    return ' '.join(line.split())


def scan_line(path, line):
    """Rule -> sorted tokens found on one code line."""
    found = collections.defaultdict(set)
    for match in USR.finditer(line):
        token = match.group(0)
        if token == '/usr/bin/env':
            continue  # one of the two paths every distribution has
        found['python-path' if PYTHON.match(token) else 'usr-path'].add(token)
    for match in PACKAGE.finditer(line):
        found['package-manager'].add(match.group(0))
    if path not in ETC_WRITERS:
        for pattern in ETC_WRITE:
            if pattern.search(line):
                found['etc-write'].add('/etc')
                break
    return {rule: sorted(tokens) for rule, tokens in found.items()}


def scan_file(path, text, language):
    """Violations of one file: (line number, normalized line, {rule: tokens})."""
    hits = []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if number == 1 and stripped.startswith('#!'):
            continue
        if not stripped or stripped.startswith(COMMENT[language]):
            continue
        if language == 'rust' and stripped == '#[cfg(test)]':
            break  # the test module closes a Rust file; its fixtures name paths on purpose
        found = scan_line(path, line)
        if found:
            hits.append((number, normalize(line), found))
    return hits


def tracked_files(root):
    if (root / '.git').exists():
        out = subprocess.run(
            ['git', '-C', str(root), 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
            check=True, capture_output=True).stdout
        return sorted({p for p in out.decode().split('\0') if p and (root / p).is_file()})
    files = []
    for directory, _, names in os.walk(root):
        for name in names:
            files.append(str((Path(directory) / name).relative_to(root)))
    return sorted(files)


def read_allow(path):
    entries = []
    if not path.exists():
        return entries
    for number, raw in enumerate(path.read_text().splitlines(), 1):
        if not raw.strip() or raw.startswith('#'):
            continue
        parts = raw.split('\t')
        if len(parts) != 3 or not all(part.strip() for part in parts):
            raise SystemExit(f'{ALLOW}:{number}: expected path<TAB>line<TAB>reason')
        entries.append((number, parts[0], normalize(parts[1]), parts[2]))
    return entries


def check(root, allow_path):
    """Scan the tree. Returns (scanned, violations, unclassified, allow entries, unlisted, stale)."""
    scanned, violations, unclassified = 0, [], set()
    for path in tracked_files(root):
        reason = exemption(path)
        if reason is None:
            unclassified.add(path.split('/', 1)[0])
            continue
        if reason:
            continue
        try:
            text = (root / path).read_text()
        except (UnicodeDecodeError, OSError):
            continue
        language = kind(path, text.split('\n', 1)[0])
        if language is None:
            continue
        scanned += 1
        for number, line, found in scan_file(path, text, language):
            violations.append((path, number, line, found))
    entries = read_allow(allow_path)
    pool = collections.defaultdict(list)
    for entry in entries:
        pool[(entry[1], entry[2])].append(entry)
    unlisted = []
    for violation in violations:
        key = (violation[0], violation[2])
        if pool.get(key):
            pool[key].pop(0)
        else:
            unlisted.append(violation)
    stale = sorted((e for left in pool.values() for e in left), key=lambda e: e[0])
    return scanned, violations, sorted(unclassified), entries, unlisted, stale


def summary(scanned, violations, entries, unlisted, stale):
    by_rule = collections.Counter(rule for v in violations for rule in v[3])
    rules = ', '.join(f'{rule} {by_rule[rule]}' for rule in RULES)
    allowed = len(violations) - len(unlisted)
    return (f'portability: {scanned} files scanned; violations by rule: {rules}; '
            f'allowed {allowed}, new {len(unlisted)}, stale {len(stale)}')


def run(root, allow_path, out=sys.stdout):
    scanned, violations, unclassified, entries, unlisted, stale = check(root, allow_path)
    for top in unclassified:
        print(f'{top}/: not classified. Add it to CHECKED or EXEMPT_TOP in tests/check-portability.py '
              'with the reason (see the Portability section of the directives).', file=out)
    for path, number, line, found in unlisted:
        for rule, tokens in found.items():
            print(f'{path}:{number}: {rule}: {", ".join(tokens)} — {FIX[rule]}.', file=out)
        print(f'    {line[:160]}', file=out)
    for number, path, line, reason in stale:
        print(f'{ALLOW}:{number}: stale: {path} no longer has this line; delete the entry '
              f'(or, if you edited the line, list the new one): {line[:120]}', file=out)
    print(summary(scanned, violations, entries, unlisted, stale), file=out)
    if unlisted:
        print('A new violation of the Portability rules. Fix it as named above; list it in '
              f'{ALLOW} only with a reason and the step that removes it.', file=out)
    return 1 if unlisted or stale or unclassified else 0


def write_allow(root, allow_path):
    _, violations, _, entries, _, _ = check(root, allow_path)
    reasons = collections.defaultdict(list)
    for _, path, line, reason in entries:
        reasons[(path, line)].append(reason)
    lines = [l for l in allow_path.read_text().splitlines() if l.startswith('#')] if allow_path.exists() else []
    for path, _, line, _ in violations:
        kept = reasons.get((path, line))
        lines.append(f'{path}\t{line}\t{kept.pop(0) if kept else "TODO"}')
    allow_path.write_text('\n'.join(lines) + '\n')


def self_test():
    failures, cases = [], [0]

    def expect(name, files, allow, code, needle=None):
        cases[0] += 1
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, body in files.items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(body)
            allow_path = root / ALLOW
            allow_path.parent.mkdir(parents=True, exist_ok=True)
            allow_path.write_text(allow)
            output = []

            class Sink:
                def write(self, text):
                    output.append(text)

            got = run(root, allow_path, Sink())
            text = ''.join(output)
            if got != code or (needle and needle not in text):
                failures.append(f'{name}: exit {got}, expected {code}\n{text}')

    qml = 'import QtQuick\nItem { property string p: "/usr/bin/foo" }\n'
    expect('new QML literal fails', {'shell/New.qml': qml}, '', 1, 'shell/New.qml:2: usr-path: /usr/bin/foo')
    expect('class C path passes', {'scripts/emaki-rollback': '#!/bin/sh\nexec "/usr/bin/foo"\n'}, '', 0)
    expect('listed violation passes', {'shell/New.qml': qml},
           'shell/New.qml\tItem { property string p: "/usr/bin/foo" }\tseam 1\n', 0, 'allowed 1, new 0, stale 0')
    expect('stale entry fails', {'shell/New.qml': 'Item {}\n'},
           'shell/New.qml\tItem { property string p: "/usr/bin/foo" }\tseam 1\n', 1, 'stale')
    expect('pacman in shell fails', {'shell/Up.qml': 'Item { property var c: ["pacman", "-Qu"] }\n'},
           '', 1, 'package-manager: pacman')
    expect('shebang passes', {'scripts/emaki-x': '#!/usr/bin/python3 -I\nprint(1)\n',
                              'scripts/emaki-y': '#!/usr/bin/bash\ntrue\n'}, '', 0)
    expect('python in argv fails', {'shell/helpers/x.py': 'run(["/usr/bin/python3", "-I", f])\n'},
           '', 1, 'python-path: /usr/bin/python3')
    expect('/etc write fails', {'scripts/emaki-z': '#!/bin/sh\nsed -i s/a/b/ /etc/x.conf\n'},
           '', 1, 'etc-write')
    expect('comment passes', {'shell/C.qml': '// reads /usr/share/foo and pacman\nItem {}\n'}, '', 0)
    expect('env passes', {'shell/helpers/e.py': 'run(["/usr/bin/env", "python3"])\n'}, '', 0)
    expect('unclassified directory fails', {'newdir/x.qml': qml}, '', 1, 'newdir/: not classified')
    expect('duplicate line needs its own entry', {'shell/New.qml': qml + qml.split('\n')[1] + '\n'},
           'shell/New.qml\tItem { property string p: "/usr/bin/foo" }\tseam 1\n', 1, 'shell/New.qml:3:')
    for failure in failures:
        print(f'FAIL {failure}')
    print(f'portability self-test: {cases[0] - len(failures)} of {cases[0]} cases pass')
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--write-allow', action='store_true')
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.write_allow:
        write_allow(args.root, args.root / ALLOW)
        return 0
    return run(args.root, args.root / ALLOW)


if __name__ == '__main__':
    sys.exit(main())
