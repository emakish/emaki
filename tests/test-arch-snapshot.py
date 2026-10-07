#!/usr/bin/python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the ISO resolver configuration with disposable paths and no network."""

from datetime import date, timedelta
from pathlib import Path
import hashlib
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BUILDER = (ROOT / 'iso/build.sh').read_text()


def snapshot_date(path):
    return date.fromisoformat(next(line.removeprefix('date=')
                                  for line in path.read_text().splitlines()
                                  if line.startswith('date=')))


class ArchSnapshot(unittest.TestCase):
    def render(self, snapshot):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'ARCH-SNAPSHOT').write_text(snapshot)
            shutil.copyfile(ROOT / 'iso/snapshot.py', root / 'snapshot.py')
            read = BUILDER[BUILDER.index('snapshot_values=$(python3'):BUILDER.index('\nmode=release')]
            config = BUILDER[BUILDER.index('cat >"$work/download.conf"'):BUILDER.index('\nmapfile -t packages')]
            command = 'HERE=$1; work=$1; archiso_version="archiso 91-1"; fail() { exit 1; };\n' + read + '\n' + config
            result = subprocess.run(['bash', '-eu', '-c', command, '_', directory],
                                    capture_output=True, text=True)
            path = root / 'download.conf'
            return result, path.read_text() if path.exists() else ''

    def test_frozen_resolver_preserves_pacman_variables(self):
        result, config = self.render((ROOT / 'iso/ARCH-SNAPSHOT').read_text())
        self.assertEqual(result.returncode, 0, result.stderr)
        archive_date = snapshot_date(ROOT / 'iso/ARCH-SNAPSHOT').strftime('%Y/%m/%d')
        expected = f'Server = https://archive.archlinux.org/repos/{archive_date}/$repo/os/$arch'
        self.assertEqual(config.count(expected), 2)
        self.assertNotIn('mirrorlist', config)
        self.assertIn('Server = file://', config)
        self.assertIn('SigLevel = Required DatabaseOptional TrustedOnly', config)

    def test_explicit_date_change_is_honoured(self):
        result, config = self.render('# review this snapshot\ndate=2026-09-30\narchiso=91\narchinstall=4.5-1\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('/2026/09/30/$repo/os/$arch', config)

    def test_invalid_or_rolling_dates_stop_before_configuration(self):
        for value in ('last', '2026-02-30', '2026-1-02', '', '2026-10-05\n2026-10-06'):
            with self.subTest(value=value):
                result, config = self.render(f'date={value}\narchiso=91\narchinstall=4.5-1\n')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(config, '')

    def test_advance_records_all_inputs_and_refuses_invalid_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(ROOT / 'iso/snapshot.py', root / 'snapshot.py')
            path = root / 'ARCH-SNAPSHOT'
            current = date.today() - timedelta(days=2)
            path.write_text(f'date={current.isoformat()}\narchiso=91\narchinstall=4.5-1\n')
            previous = (current - timedelta(days=1)).isoformat()
            following = (current + timedelta(days=1)).isoformat()
            later = (current + timedelta(days=2)).isoformat()
            command = ['python3', str(root / 'snapshot.py'), 'advance']
            result = subprocess.run(command + [following, '--archiso', '91',
                                    '--archinstall', '4.5-2'], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f'date={following}\narchiso=91\narchinstall=4.5-2\n', path.read_text())
            good = path.read_bytes()
            for bad in ([previous, '--archiso', '91', '--archinstall', '4.5-2'],
                        [later, '--archiso', '92', '--archinstall', '4.5-2'],
                        [later, '--archiso', '91', '--archinstall', 'latest'],
                        [later]):
                result = subprocess.run(command + bad, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(path.read_bytes(), good)

    def test_future_advance_leaves_record_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(ROOT / 'iso/snapshot.py', root / 'snapshot.py')
            path = root / 'ARCH-SNAPSHOT'
            path.write_text('date=2020-01-01\narchiso=91\narchinstall=4.5-1\n')
            original = path.read_bytes()
            for future in ((date.today() + timedelta(days=1)).isoformat(), '9999-12-31'):
                with self.subTest(date=future):
                    result = subprocess.run(['python3', str(root / 'snapshot.py'), 'advance',
                                             future, '--archiso', '91', '--archinstall', '4.5-2'],
                                            capture_output=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(b'future', result.stderr)
                    self.assertEqual(path.read_bytes(), original)
                    self.assertFalse(path.with_suffix('.new').exists())

    def test_recorded_installer_release_must_match_the_package_closure(self):
        check = next(line for line in BUILDER.splitlines()
                     if line.startswith('grep -Fxq "archinstall-'))
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'closure.txt').write_text('archinstall-4.5-1-any.pkg.tar.zst\n')
            for version, status in [('4.5-1', 0), ('4.5-2', 1)]:
                result = subprocess.run(['bash', '-eu', '-c',
                    'work=$1; pinned_archinstall=$2; fail() { exit 1; };\n' + check,
                    '_', directory, version], capture_output=True)
                self.assertEqual(result.returncode, status)

    def test_host_and_resolver_archiso_releases_must_match(self):
        check = BUILDER[BUILDER.index('[[ $archiso_version == "$archive_archiso" ]]'):]
        check = check.splitlines()[0]
        for actual, expected, status in [('archiso 91-1', 'archiso 91-1', 0),
                                         ('archiso 91-2', 'archiso 91-1', 1)]:
            result = subprocess.run(['bash', '-eu', '-c',
                                     'archiso_version=$1; archive_archiso=$2; fail() { exit 1; };\n' + check,
                                     '_', actual, expected], capture_output=True)
            self.assertEqual(result.returncode, status)

    def test_archive_is_not_installed_as_mirrorlist(self):
        self.assertNotIn('archive.archlinux.org', (ROOT / 'iso/prepare-profile.py').read_text())
        for path in (ROOT / 'iso/profile').rglob('*'):
            if path.is_file() and 'mirrorlist' in path.name:
                self.assertNotIn(b'archive.archlinux.org', path.read_bytes())

    def test_image_provenance_records_exact_package_bytes(self):
        start = BUILDER.index("    printf 'arch_snapshot %s")
        block = BUILDER[BUILDER.rfind('{\n', 0, start):]
        block = block[:block.index('\nrm -- "$work/mk/iso._build_iso_image"')]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'offline').mkdir()
            (root / 'mk/iso/emaki').mkdir(parents=True)
            name = 'archinstall-4.5-1-any.pkg.tar.zst'
            payload = b'signed package fixture'
            (root / 'offline' / name).write_bytes(payload)
            (root / 'closure.txt').write_text(name + '\n')
            result = subprocess.run(['bash', '-eu', '-c',
                'work=$1; arch_snapshot=2026-10-05; arch_server=https://archive.example; '
                "archiso_version='archiso 91-1'; pinned_archinstall=4.5-1;\n" + block, '_', directory], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            recorded = (root / 'mk/iso/emaki/BUILDINFO').read_text()
            self.assertIn('arch_snapshot 2026-10-05\n', recorded)
            self.assertIn('archiso 91-1\narchinstall 4.5-1\n', recorded)
            self.assertIn(hashlib.sha256(payload).hexdigest() + '  ' + name, recorded)


if __name__ == '__main__':
    unittest.main()
