#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the ISO runner's real argument parser and QEMU argv without starting a VM."""
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MemoryTests(unittest.TestCase):
    def run_parser(self, *args):
        source = (ROOT / 'tests/vm/run-iso.sh').read_text()
        # Keep the real parser, stopping before disk creation and host-device checks.
        parser = source.split('disk=$(realpath -m -- ', 1)[0]
        self.assertNotEqual(parser, source)
        lines = parser.splitlines()
        includes = [i for i, line in enumerate(lines) if line.startswith('source ')]
        self.assertEqual(len(includes), 1)
        lines[includes[0]] = 'source ' + shlex.quote(str(ROOT / 'tests/vm/iso-common.sh'))
        # Keep the shipped final argv, replacing execution with a NUL-delimited capture.
        marker = 'exec qemu-system-x86_64 '
        self.assertEqual(source.count(marker), 1)
        capture = "printf '%s\\0' " + source.split(marker, 1)[1]
        fixture = ('\n'.join(lines)
                   + '\ncode=fixture; disk=fixture; network=fixture; socket_runtime=fixture; cd_args=()\n'
                   + capture)
        with tempfile.TemporaryDirectory() as temporary:
            return subprocess.run(['bash', '-c', fixture, 'run-iso-memory-test',
                                   '--dir', temporary, '--no-cd', *args], capture_output=True)

    def test_default_and_explicit_memory_reach_qemu_argv(self):
        for args, expected in (((), '6G'), (('--memory', '2G'), '2G'),
                               (('--memory', '3G'), '3G'), (('--memory', '4G'), '4G')):
            with self.subTest(args=args):
                result = self.run_parser(*args)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                argv = result.stdout.decode().split('\0')
                self.assertEqual(argv.count('-m'), 1)
                self.assertEqual(argv[argv.index('-m') + 1], expected)

    def test_invalid_memory_fails_before_vm_setup(self):
        for value in ('', '0G', '-1G', '1.5G', '2048', '2g', '2G,slots=2', '01G', '2G 3G'):
            with self.subTest(value=value):
                result = self.run_parser('--memory', value)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b'--memory must be', result.stderr)
                self.assertEqual(result.stdout, b'')

    def test_missing_memory_value(self):
        result = self.run_parser('--memory')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'missing value for --memory', result.stderr)


if __name__ == '__main__':
    unittest.main()
