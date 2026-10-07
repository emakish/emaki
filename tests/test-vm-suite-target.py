#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fixture and SSH transport contracts, using an executable fake SSH client."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / 'tests/vm'
sys.path.insert(0, str(HERE))
from suite_target import Target

EVIDENCE = ROOT / '.cache/evidence'
EVIDENCE.mkdir(parents=True, exist_ok=True)


class TargetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='suite-', dir=EVIDENCE)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.key = self.base / 'test key'
        self.key.touch()
        self.fixture = self.base / 'plan.json'
        self.write_plan()
        executable = self.base / 'ssh'
        executable.write_text('#!/usr/bin/env python3\nimport json,sys\n'
                              'print(json.dumps(dict(argv=sys.argv[1:], stdin=sys.stdin.read())))\n')
        executable.chmod(0o755)
        self.env = patch.dict(os.environ, {'PATH': str(self.base) + ':' + os.environ['PATH']})
        self.env.start()
        self.addCleanup(self.env.stop)

    def write_plan(self, *, wrapped=False, user='release_test', password='fixture-secret-42', transport=None):
        config = {'user': {'login': user, 'password': password}}
        data = {'config': config} if wrapped else config
        if transport:
            data['transport'] = transport
        self.fixture.write_text(json.dumps(data))

    def test_development_defaults_and_stdin_are_unchanged(self):
        with patch.dict(os.environ, {'EMAKI_VM_DIR': str(self.base)}, clear=True):
            target = Target()
        self.assertEqual((target.user, target.password, target.port), ('arch', 'arch', 2222))
        self.assertEqual(target.key, self.base / 'id_vm')
        row = json.loads(target.remote('cat', data='test payload').stdout)
        self.assertIn('arch@127.0.0.1', row['argv'])
        self.assertEqual(row['stdin'], 'test payload')
        self.assertIn('UserKnownHostsFile=' + str(self.base / 'known_hosts'), row['argv'])

    def test_iso_helper_uses_fixture_and_recorded_transport(self):
        (self.base / 'ssh-port').write_text('2288\n')
        (self.base / 'ssh-host-generation').write_text('installed-2\n')
        target = Target(fixture=self.fixture, directory=self.base, key=self.key)
        row = json.loads(target.remote('python3 helper.py', data='payload', privileged=True).stdout)
        self.assertIn('release_test@127.0.0.1', row['argv'])
        self.assertIn('2288', row['argv'])
        self.assertIn(str(self.key), row['argv'])
        self.assertIn('UserKnownHostsFile=' + str(self.base / 'known_hosts-release_test-installed-2'), row['argv'])
        self.assertEqual(row['stdin'], 'fixture-secret-42\npayload')
        self.assertNotIn(target.password, ' '.join(row['argv']))

    def test_wrapped_plan_and_explicit_transport_override(self):
        self.write_plan(wrapped=True, transport={'kind': 'ssh', 'ssh_port': 2281,
                        'dir': str(self.base), 'key': str(self.key), 'known_hosts': str(self.base / 'old')})
        target = Target(fixture=self.fixture, port=2282, known_hosts=self.base / 'new')
        row = json.loads(target.remote('true', data='').stdout)
        self.assertEqual(target.port, 2282)
        self.assertIn('UserKnownHostsFile=' + str(self.base / 'new'), row['argv'])
        self.assertIn('release_test@127.0.0.1', row['argv'])

    def test_iso_explicit_known_hosts_reaches_ssh(self):
        target = Target(fixture=self.fixture, directory=self.base, key=self.key,
                        port=2290, known_hosts=self.base / 'custom hosts')
        row = json.loads(target.remote('true', data='').stdout)
        self.assertIn('UserKnownHostsFile=' + str(self.base / 'custom hosts'), row['argv'])

    def test_ssh_wrapper_passes_binary_stdin_without_decoding(self):
        fake = self.base / 'ssh'
        fake.write_text('#!/usr/bin/env python3\nimport sys\nsys.stdout.buffer.write(sys.stdin.buffer.read())\n')
        payload = bytes(range(256))
        result = subprocess.run([str(HERE / 'ssh.sh'), '--fixture', str(self.fixture), '--dir', str(self.base),
                                 '--key', str(self.key), '--', 'cat'], input=payload, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, payload)

    def test_invalid_credentials_and_ports_fail_before_transport(self):
        for kwargs in ({'user': 'bad;name'}, {'password': 'bad\nline'}, {'password': 'bad\0line'}):
            self.write_plan(**kwargs)
            with self.assertRaises(ValueError):
                Target(fixture=self.fixture)
        for port in (0, 65536, -1):
            with self.assertRaises(ValueError):
                Target(port=port)

    def test_actual_release_fixture_account_is_loaded(self):
        with self.assertRaisesRegex(ValueError, 'needs the VM directory'):
            Target(fixture=HERE / 'fixtures/plan-erase-btrfs.json')
        target = Target(fixture=HERE / 'fixtures/plan-erase-btrfs.json', directory=self.base)
        self.assertEqual((target.user, target.password), ('emaki', 'emaki-vm-test-only'))
        self.assertEqual(target.transport, 'iso')

    def test_privilege_authentication_never_reaches_helper_stdin(self):
        fake = self.base / 'sudo'
        fake.write_text('#!/usr/bin/env python3\nimport os,sys,pathlib\n'
                        'if "-v" in sys.argv:\n'
                        '    pathlib.Path(os.environ["FAKE_SUDO_PARENT"]).write_text(str(os.getppid()))\n'
                        '    if not os.environ.get("FAKE_NOPASSWD"): assert sys.stdin.readline().rstrip("\\n") == "fixture-secret-42"\n'
                        '    sys.exit(0)\n'
                        'assert pathlib.Path(os.environ["FAKE_SUDO_PARENT"]).read_text() == str(os.getppid())\n'
                        'assert sys.argv[1:3] == ["-n", "--"]\n'
                        'os.execvp(sys.argv[3], sys.argv[3:])\n')
        fake.chmod(0o755)
        target = Target(fixture=self.fixture, directory=self.base)
        for shell in ('bash', 'sh'):
            for nopasswd in ('', '1'):
                with self.subTest(shell=shell, nopasswd=bool(nopasswd)):
                    command, data = target.privileged_command('cat', 'keyboard payload')
                    result = subprocess.run([shell, '-c', command], input=data, text=True, capture_output=True,
                                            env=dict(os.environ, FAKE_NOPASSWD=nopasswd,
                                                     FAKE_SUDO_PARENT=str(self.base / 'sudo-parent')))
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, 'keyboard payload')


if __name__ == '__main__':
    unittest.main()
