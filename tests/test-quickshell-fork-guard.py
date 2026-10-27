#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The parts of the shell tests' qs guard that need no qs.

tests/test-quickshell-fork.py accepts only the patch list it was reviewed against (names, order
and the PKGBUILD's sha256), its advice puts the directory tests/build-quickshell-fork.sh installs
qs into first in PATH, and that helper refuses a tarball path that does not exist instead of
downloading one."""
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import tempfile
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'tests/test-quickshell-fork.py'
HELPER = ROOT / 'tests/build-quickshell-fork.sh'
RECIPE = (ROOT / 'packaging/quickshell-emaki/PKGBUILD').read_text()
failures = []

# The advice names the helper's install directory: it installs qs into <directory>/bin.
assert '"$OUT/bin/qs" --version' in HELPER.read_text(), 'the helper no longer installs into <directory>/bin'
advice = re.findall(r'PATH=<directory>/(\S*)bin:\$PATH', SOURCE.read_text())
if not advice or any(advice):
    failures.append(f'the advice puts <directory>/{advice}bin first in PATH, the helper installs into '
                    '<directory>/bin')
# The reviewed patch list: the real recipe passes, any change to it fails.
if 'def recipe_patches(' not in SOURCE.read_text():
    failures.append('test-quickshell-fork.py has no recipe_patches(): the patch list is not pinned')
else:
    spec = importlib.util.spec_from_file_location('quickshell_fork', SOURCE)
    fork = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fork)
    assert tuple(fork.recipe_patches(RECIPE)) == fork.REVIEWED

    def line(name):
        return f'\tpatch -Np1 -i "$srcdir/{name}"\n'

    (first, first_sum), (second, _) = fork.REVIEWED[:2]
    last = fork.REVIEWED[-1][0]
    new = '0009-core-something-new.patch'
    changed = {
        'a new 0009 in place of 0001': RECIPE.replace(first, new),
        'a patch applied twice': RECIPE.replace(line(first), line(first) * 2),
        'two patches swapped': RECIPE.replace(line(first) + line(second), line(second) + line(first)),
        'an edited patch (another sha256)': RECIPE.replace(first_sum, '0' * 64),
        'a patch dropped': RECIPE.replace(line(last), ''),
        'a new patch appended': RECIPE.replace(line(last), line(last) + line(new)),
    }
    for what, text in changed.items():
        assert text != RECIPE, f'the mutation "{what}" did not change the recipe'
        try:
            fork.recipe_patches(text)
        except ValueError:
            continue
        failures.append(f'the patch-list check accepted {what}')

# The helper downloads only when no tarball is given. A fake curl records the attempt.
cache = ROOT / '.cache'
cache.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='qs-helper-', dir=cache) as temporary:
    base = Path(temporary)
    (base / 'bin').mkdir()
    (base / 'bin/curl').write_text('#!/bin/sh\ntouch "$FAKE_CURL_CALLED"\nexit 7\n')
    (base / 'bin/curl').chmod(0o755)
    called = base / 'curl-called'
    env = dict(os.environ, PATH=f'{base / "bin"}:{os.environ["PATH"]}', FAKE_CURL_CALLED=str(called))

    def helper(*args):
        return subprocess.run(['bash', str(HELPER), *map(str, args)], env=env, capture_output=True,
                              text=True, timeout=60)

    run = helper(base / 'given', base / 'missing.tar.gz')
    if run.returncode == 0 or called.exists() or 'no such tarball' not in run.stderr:
        failures.append(f'a missing tarball given by path: rc {run.returncode}, download attempted '
                        f'{called.exists()}, stderr {run.stderr.strip()!r}')
    called.unlink(missing_ok=True)
    run = helper(base / 'omitted')
    if run.returncode == 0 or not called.exists():
        failures.append(f'no tarball given: rc {run.returncode}, download attempted {called.exists()}')

if failures:
    raise SystemExit('FAIL:\n' + ''.join(f'  {failure}\n' for failure in failures))
print('PASS: the patch list is pinned, the advice matches the helper, a given tarball must exist')
