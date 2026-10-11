#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline checks for upgrade coverage: real machines, released images and the launch marker."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('acceptance', ROOT / 'packaging/mirror/acceptance.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
CANDIDATE = 'a' * 64
OLD = 'b' * 64


class Backend:
    def __init__(self):
        self.records = {}

    def list(self, prefix):
        return [key for key in self.records if key.startswith(prefix)]

    def get(self, key):
        return self.records.get(key), None

    def release(self, *versions, digest=OLD, launch=False):
        for version in versions:
            self.records[m.PREFIX + version] = json.dumps(
                {'manifest': digest, **({'launch': True} if launch else {})}).encode()


def runs(starts):
    return [f'{run} {start} {size}' for run in m.REQUIRED_RUNS
            for start in starts for size in m.REQUIRED_SIZES]


class Acceptance(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.machines = Path(temporary.name) / 'machines.toml'

    def write_machines(self, *installed, launch=''):
        self.machines.write_text(f'launch = "{launch}"\n' + ''.join(
            f'[[machine]]\nname = "machine {n}"\ninstalled_from = "{v}"\ninstalled = 2026-10-10\n'
            for n, v in enumerate(installed)))

    def starts(self, candidate=CANDIDATE):
        return m.required_starts(self.backend, candidate, self.machines)

    def test_checked_in_file_requires_only_050_today(self):
        """The Mac was reinstalled from 0.5.0 on 2026-10-10: 0.5.0 is current and the oldest."""
        self.backend.release('0.2.0', '0.3.0', '0.4.0', '0.4.2', '0.5.0')
        starts = m.required_starts(self.backend, CANDIDATE)
        self.assertEqual(starts, ('0.5.0',))
        self.assertEqual(m.plan(starts), [(run, '0.5.0', size) for run in ('T1', 'T2')
                                          for size in ('1920x1080', '2560x1600')])

    def test_checked_in_file_has_license_header_and_make_check_runs_this_test(self):
        # The same two lines as acceptance.py's header, after its shebang.
        header = ''.join(Path(spec.origin).read_text().splitlines(keepends=True)[1:3])
        self.assertIn('SPDX-License-Identifier: GPL-3.0-or-later', header)
        self.assertTrue(m.MACHINES.read_text().startswith(header))
        make = (ROOT / 'Makefile').read_text()
        check = make[make.index('\ncheck:'):]
        check = check[:check.index('\n\n')]
        self.assertIn('\tpython3 tests/test-publish-acceptance.py\n', check + '\n')

    def test_before_launch_current_image_and_oldest_machine(self):
        self.backend.release('0.2.0', '0.3.0', '0.4.0', '0.4.2')
        self.write_machines('0.4.2', '0.4.0')
        self.assertEqual(self.starts(), ('0.4.0', '0.4.2'))
        self.write_machines('0.4.2')
        self.assertEqual(self.starts(), ('0.4.2',))
        self.write_machines()
        self.assertEqual(self.starts(), ('0.4.2',))
        self.write_machines('0.1.0')  # images before the release records still count
        self.assertEqual(self.starts(), ('0.1.0', '0.4.2'))

    def test_after_launch_only_every_image_since_the_launch(self):
        """The rule is replaced, not extended: the oldest machine no longer counts by itself."""
        self.backend.release('0.5.0', '0.6.0', '0.7.1', '0.10.0')
        self.backend.release('0.7.0', launch=True)
        self.write_machines('0.5.0', launch='0.7.0')
        self.assertEqual(self.starts(), ('0.7.0', '0.7.1', '0.10.0'))
        self.write_machines('0.7.1', launch='0.7.0')
        self.assertEqual(self.starts(), ('0.7.0', '0.7.1', '0.10.0'))

    def test_marker_set_before_its_image_is_published_keeps_the_rule_before_launch(self):
        self.backend.release('0.5.0', '0.6.0')
        self.write_machines('0.5.0', launch='0.7.0')
        self.assertEqual(self.starts(), ('0.5.0', '0.6.0'))
        self.assertTrue(m.launch_mark(self.backend, '0.7.0', self.machines))
        self.assertFalse(m.launch_mark(self.backend, '0.6.1', self.machines))
        self.write_machines('0.5.0')
        self.assertFalse(m.launch_mark(self.backend, '0.7.0', self.machines))

    def test_launch_marker_cannot_be_removed_or_moved(self):
        """The review's reproducer: after the launch a stale or edited file must not shrink the plan."""
        self.backend.release('0.5.0', '0.7.1', '0.10.0')
        self.backend.release('0.7.0', launch=True)
        for launch in ('', '0.5.0', '0.7.1', '0.10.0', '99.0.0'):
            self.write_machines('0.5.0', launch=launch)
            with self.subTest(launch=launch), self.assertRaisesRegex(ValueError, 'never removed or moved'):
                self.starts()
            with self.subTest(launch=launch, mark=True), self.assertRaisesRegex(ValueError, 'never removed'):
                m.launch_mark(self.backend, '0.11.0', self.machines)
        self.write_machines('0.5.0', launch='0.7.0')
        self.assertFalse(m.launch_mark(self.backend, '0.11.0', self.machines))
        self.assertTrue(m.launch_mark(self.backend, '0.7.0', self.machines))  # an interrupted `iso` retried

    def test_launch_naming_an_image_published_without_the_mark_refuses(self):
        self.backend.release('0.5.0', '0.6.0', '0.7.0')
        self.write_machines('0.5.0', launch='0.6.0')
        with self.assertRaisesRegex(ValueError, 'published without the launch mark'):
            self.starts()
        with self.assertRaisesRegex(ValueError, 'published without the launch mark'):
            m.launch_mark(self.backend, '0.6.0', self.machines)

    def test_more_than_one_launch_mark_refuses(self):
        self.backend.release('0.7.0', '0.8.0', launch=True)
        self.write_machines(launch='0.7.0')
        with self.assertRaisesRegex(ValueError, 'more than one'):
            self.starts()

    def test_launch_image_candidate_keeps_rule_before_launch(self):
        """`iso` rechecks the stamp of the launch image's own candidate after writing the record."""
        self.backend.release('0.5.0')
        self.backend.release('0.6.0', digest=CANDIDATE, launch=True)
        self.write_machines('0.5.0', launch='0.6.0')
        self.assertEqual(self.starts(), ('0.5.0',))
        self.assertEqual(self.starts('c' * 64), ('0.6.0',))

    def test_launch_image_candidate_after_later_images_needs_every_later_image(self):
        """The re-review's reproducer: the launch candidate re-tested after 0.7.1 and 0.10.0 were
        published, with a machine on 0.5.0, must not fall back to 0.5.0 and 0.10.0 only."""
        self.backend.release('0.5.0', digest='5' * 64)
        self.backend.release('0.7.0', digest=CANDIDATE, launch=True)
        self.write_machines('0.5.0', launch='0.7.0')
        self.assertEqual(self.starts(), ('0.5.0',))  # the immediate retry keeps the rule before launch
        self.backend.release('0.7.1', digest='1' * 64)
        self.assertEqual(self.starts(), ('0.7.1',))
        self.backend.release('0.10.0', digest='0' * 64)
        starts = self.starts()
        self.assertEqual(starts, ('0.7.1', '0.10.0'))
        with self.assertRaisesRegex(ValueError, 'T1 from 0.7.1 at 1920x1080'):
            m.validate_runs(runs(('0.5.0', '0.10.0')), starts)
        m.validate_runs(runs(starts), starts)

    def test_publication_and_retry_keep_original_matrix(self):
        self.backend.release('0.5.0')
        self.write_machines('0.5.0')
        original = runs(self.starts())
        self.assertEqual(self.starts(), ('0.5.0',))
        self.backend.release('0.6.0', digest=CANDIDATE)
        self.assertEqual(self.starts(), ('0.5.0',))
        m.validate_runs(original, self.starts())
        # A machine reinstalled from the candidate's own image needs no upgrade to it.
        self.write_machines('0.6.0')
        self.assertEqual(self.starts(), ('0.5.0',))

    def test_stamp_written_before_release_does_not_bypass_next_check(self):
        self.backend.release('0.5.0')
        self.write_machines('0.5.0')
        original = runs(self.starts())
        m.validate_runs(original, self.starts())
        self.backend.release('0.5.1')
        self.assertEqual(self.starts(), ('0.5.0', '0.5.1'))
        with self.assertRaisesRegex(ValueError, 'T1 from 0.5.1 at 1920x1080'):
            m.validate_runs(original, self.starts())

    def test_extra_runs_pass_and_every_missing_required_run_refuses(self):
        starts = ('0.4.0', '0.5.0')
        complete = runs(starts)
        m.validate_runs(complete + runs(('0.1.0', '0.3.0')), starts)
        for omitted in complete:
            with self.subTest(omitted=omitted), self.assertRaises(ValueError):
                m.validate_runs([run for run in complete if run != omitted] + runs(('0.3.0',)), starts)

    def test_post_promotion_t3_does_not_replace_required_t1_or_t2(self):
        starts = ('0.4.0', '0.5.0')
        t3 = [f'T3 {start} {size}' for start in starts for size in m.REQUIRED_SIZES]
        m.validate_runs(runs(starts), starts)
        for missing in ('T1', 'T2'):
            with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, missing + ' from'):
                m.validate_runs([run for run in runs(starts) if not run.startswith(missing + ' ')] + t3, starts)

    def test_records_fail_closed(self):
        self.write_machines()
        marks = [json.dumps({'manifest': OLD, 'launch': value}).encode() for value in (False, 1, 'true', None)]
        for data in (None, b'not json', b'[]', b'{}', b'{"manifest": "bad"}', b'\xff', *marks):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.backend.records['released/iso/0.2.0'] = data
                self.starts()
        self.backend.records = {'released/iso/not-a-version': b'{}'}
        with self.assertRaisesRegex(ValueError, 'record key'):
            self.starts()

    def test_machines_file_fails_closed(self):
        self.backend.release('0.5.0')
        machine = '[[machine]]\nname = "Mac"\ninstalled_from = "0.5.0"\ninstalled = 2026-10-10\n'
        bad = {
            'not toml': 'launch = \n',
            'no launch': machine,
            'launch not a string': 'launch = false\n' + machine,
            'launch not a version': 'launch = "soon"\n' + machine,
            'unknown top key': 'launch = ""\nlaunched = true\n' + machine,
            'machine not a table list': 'launch = ""\nmachine = "Mac"\n',
            'unknown machine key': 'launch = ""\n' + machine + 'owner = "x"\n',
            'missing date': 'launch = ""\n[[machine]]\nname = "Mac"\ninstalled_from = "0.5.0"\n',
            'date as text': 'launch = ""\n' + machine.replace('2026-10-10', '"2026-10-10"'),
            'date and time': 'launch = ""\n' + machine.replace('2026-10-10', '2026-10-10T10:00:00'),
            'empty name': 'launch = ""\n' + machine.replace('"Mac"', '"  "'),
            'short version': 'launch = ""\n' + machine.replace('"0.5.0"', '"0.5"'),
            'never released': 'launch = ""\n' + machine.replace('"0.5.0"', '"0.4.9"'),
        }
        for name, text in bad.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.machines.write_text(text)
                self.starts()
        self.machines.unlink()
        with self.assertRaisesRegex(ValueError, 'cannot read the machines file'):
            self.starts()

    def test_nothing_to_start_from_refuses(self):
        self.write_machines()
        with self.assertRaisesRegex(ValueError, 'no upgrade start is required'):
            self.starts()
        self.backend.release('0.6.0', digest=CANDIDATE)
        with self.assertRaisesRegex(ValueError, 'no upgrade start is required'):
            self.starts()

    def test_bad_candidate_and_stamp_fail_closed(self):
        self.write_machines('0.1.0')
        for candidate in (None, 'bad', 123):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                self.starts(candidate)
        for invalid in (None, 'T1 0.5.0 1920x1080', [{}]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                m.validate_runs(invalid, ('0.5.0',))


if __name__ == '__main__':
    unittest.main()
