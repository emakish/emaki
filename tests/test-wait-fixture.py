#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Readiness waits tolerate delayed startup while keeping a finite deadline."""
import unittest
from unittest.mock import patch
import wait_fixture


class Readiness(unittest.TestCase):
    def setUp(self):
        self.elapsed = 0
        self.addCleanup(patch.stopall)
        patch.object(wait_fixture.time, 'monotonic', lambda: self.elapsed).start()
        patch.object(wait_fixture.time, 'sleep', self.advance).start()

    def advance(self, seconds):
        self.elapsed += seconds

    def test_startup_after_the_old_five_second_limit(self):
        self.assertEqual(wait_fixture.wait_for(lambda: 'ready' if self.elapsed >= 6 else None), 'ready')
        self.assertLess(self.elapsed, 7)

    def test_ready_condition_does_not_sleep(self):
        self.assertTrue(wait_fixture.wait_for(lambda: True))
        self.assertEqual(self.elapsed, 0)

    def test_stalled_condition_reaches_the_ceiling(self):
        with self.assertRaises(TimeoutError):
            wait_fixture.wait_for(lambda: False)
        self.assertGreaterEqual(self.elapsed, 30)
        self.assertLess(self.elapsed, 31)

    def test_failed_child_is_reported_without_waiting(self):
        def failed():
            raise AssertionError('child exited')
        with self.assertRaisesRegex(AssertionError, 'child exited'):
            wait_fixture.wait_for(failed)
        self.assertEqual(self.elapsed, 0)


if __name__ == '__main__':
    unittest.main()
