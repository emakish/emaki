#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline checks for upgrade coverage as published images accumulate."""
import importlib.util
import json
from pathlib import Path
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

    def release(self, version, digest):
        self.records[m.PREFIX + version] = json.dumps({'manifest': digest}).encode()


def runs(starts):
    return [f'{run} {start} {size}' for run in m.REQUIRED_RUNS
            for start in starts for size in m.REQUIRED_SIZES]


class Acceptance(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()

    def test_020_publication_and_retry_keep_original_matrix(self):
        original = runs(('0.1.0', '0.1.1'))
        self.assertEqual(m.required_starts(self.backend, CANDIDATE), ('0.1.0', '0.1.1'))
        m.validate_runs(original, m.required_starts(self.backend, CANDIDATE))
        self.backend.release('0.2.0', CANDIDATE)
        self.assertEqual(m.required_starts(self.backend, CANDIDATE), ('0.1.0', '0.1.1'))
        m.validate_runs(original, m.required_starts(self.backend, CANDIDATE))

    def test_new_candidate_requires_every_published_start_and_both_sizes(self):
        self.backend.release('0.2.0', OLD)
        self.backend.release('0.10.0', OLD)
        starts = m.required_starts(self.backend, CANDIDATE)
        self.assertEqual(starts, ('0.1.0', '0.1.1', '0.2.0', '0.10.0'))
        complete = runs(starts)
        m.validate_runs(complete, starts)
        for omitted in runs(('0.2.0',)):
            with self.subTest(omitted=omitted), self.assertRaises(ValueError):
                m.validate_runs([run for run in complete if run != omitted], starts)

    def test_stamp_written_before_release_does_not_bypass_next_check(self):
        original = runs(m.required_starts(self.backend, CANDIDATE))
        m.validate_runs(original, m.required_starts(self.backend, CANDIDATE))
        self.backend.release('0.2.0', OLD)
        with self.assertRaisesRegex(ValueError, 'T1 from 0.2.0 at 1920x1080'):
            m.validate_runs(original, m.required_starts(self.backend, CANDIDATE))

    def test_records_fail_closed(self):
        for data in (None, b'not json', b'[]', b'{}', b'{"manifest": "bad"}', b'\xff'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.backend.records['released/iso/0.2.0'] = data
                m.required_starts(self.backend, CANDIDATE)
        self.backend.records = {'released/iso/not-a-version': b'{}'}
        with self.assertRaisesRegex(ValueError, 'record key'):
            m.required_starts(self.backend, CANDIDATE)

    def test_bad_candidate_and_stamp_fail_closed(self):
        for candidate in (None, 'bad', 123):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                m.required_starts(self.backend, candidate)
        for invalid in (None, 'T1 0.1.0 1920x1080', [{}]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                m.validate_runs(invalid, m.LEGACY_STARTS)


if __name__ == '__main__':
    unittest.main()
