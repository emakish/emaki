#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The parts of the upgrade acceptance (tests/vm/upgrade-check.py) that run without a VM: the
old-address proxy over TLS with its test CA, the guestfwd relay, the keys it types, the
candidate check, the installer fixture, and the stamp's refusal of rehearsal results. The VM
runs themselves (T1, T2, T3) are not exercised here."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = load('upgrade_check', HERE / 'vm/upgrade-check.py')
proxy = load('old_address_proxy', HERE / 'vm/old-address-proxy.py')
tp = load('test_publish', HERE / 'test-publish.py')


def curl(*arguments):
    return subprocess.run(['curl', '-s', '-o', '-', '-w', '\n%{http_code}', *arguments], capture_output=True)


class Keys(unittest.TestCase):
    def test_every_typed_text_has_keys(self):
        fixture = json.loads((HERE / 'vm/fixtures/plan-upgrade.json').read_text())
        for text in ('sudo pacman -Syu\n', 'cat /etc/pacman.d/emaki-mirrorlist\n',
                     'cat /etc/pacman.d/emaki-mirrorlist.pacnew\n', 'pacman -Q emaki\n',
                     fixture['user']['password'] + '\n'):
            self.assertTrue(check.sendkeys(text))
        self.assertEqual(check.sendkeys('sudo -Syu\n'),
                         ['sendkey s', 'sendkey u', 'sendkey d', 'sendkey o', 'sendkey spc', 'sendkey minus',
                          'sendkey shift-s', 'sendkey y', 'sendkey u', 'sendkey ret'])
        with self.assertRaises(ValueError):
            check.sendkeys('é')


class Fixture(unittest.TestCase):
    # Accepted config fields of the two 0.1.2 installers, read on 2026-10-05 from
    # emaki-installer-0.1.2-1 in ~/VMs/iso/release and ~/VMs/iso/release-full (the second one
    # also requires "encryption"). 0.1.0 and 0.1.1 are read from their git tags below.
    ISO_0_1_2 = {'release': {'mode', 'disk_id', 'fs', 'mounts', 'shrink_bytes', 'hostname', 'timezone', 'layouts',
                             'user', 'repo_server', 'online_update', 'scale_guess', 'software'}}
    ISO_0_1_2['full'] = ISO_0_1_2['release'] | {'partition_id', 'encryption', 'disk_password', 'hibernation'}

    def load(self, name):
        fixture = json.loads((HERE / 'vm/fixtures' / name).read_text())
        self.assertNotIn('repo_server', fixture)  # the worker would write its own mirrorlist
        self.assertIs(fixture['online_update'], False)
        self.assertEqual((fixture['mode'], fixture['fs']), ('erase', 'btrfs'))
        return fixture

    def test_old_installers_accept_it_and_it_keeps_the_packaged_mirrorlist(self):
        fixture = self.load('plan-upgrade.json')
        for tag in ('v0.1.0', 'v0.1.1'):
            source = subprocess.run(['git', '-C', ROOT, 'show', f'{tag}:installer/emaki_installer/planner.py'],
                                    capture_output=True, text=True)
            if source.returncode:
                self.skipTest(f'{tag} not in this checkout')
            allowed = set(re.findall(r"'(\w+)'", re.search(r'allowed = \{([^}]*)\}', source.stdout).group(1)))
            self.assertLessEqual(set(fixture), allowed, tag)
        self.assertLessEqual(set(fixture), self.ISO_0_1_2['release'])
        full = self.load('plan-upgrade-0.1.2-full.json')
        self.assertLessEqual(set(full), self.ISO_0_1_2['full'])
        self.assertEqual(full['encryption'], 'none')
        self.assertEqual(full['user'], fixture['user'])


class Proxy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (shutil.which('openssl') and shutil.which('curl')):
            raise unittest.SkipTest('needs openssl and curl')

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix='emaki-proxy-test-'))
        self.github = self.base / 'github'
        self.github.mkdir()
        (self.github / 'emaki.db').write_bytes(b'database bytes')
        self.bucket = self.base / 'bucket'
        (self.bucket / 'pointers').mkdir(parents=True)
        (self.bucket / 'pointers/stable').write_text('20261012T183000Z\n')
        certs = proxy.make_certificates(self.base / 'certs', ['github.com', 'pkgs.emaki.sh'])
        self.ca = certs[0]
        self.server = proxy.serve(self.github, certs, pkgs_root=self.bucket)
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.base)

    def url(self, host, path):
        return ['--cacert', str(self.ca), '--resolve', f'{host}:{self.port}:127.0.0.1', f'https://{host}:{self.port}{path}']

    def test_old_address_answers_with_the_files_and_only_them(self):
        result = curl(*self.url('github.com', '/emakish/packages/releases/download/stable/emaki.db'))
        self.assertEqual(result.stdout, b'database bytes\n200')
        for path in ('/emakish/packages/releases/download/testing/emaki.db',
                     '/emakish/packages/releases/download/stable/missing',
                     '/emakish/packages/releases/download/stable/../stable/emaki.db'):
            self.assertTrue(curl('--path-as-is', *self.url('github.com', path)).stdout.endswith(b'404'), path)

    def test_tls_needs_the_test_ca(self):
        result = subprocess.run(['curl', '-s', '--resolve', f'github.com:{self.port}:127.0.0.1',
                                 f'https://github.com:{self.port}/emakish/packages/releases/download/stable/emaki.db'])
        self.assertEqual(result.returncode, 60)  # certificate problem: the guest must trust the CA

    def test_rehearsal_answers_pkgs_like_the_worker(self):
        result = subprocess.run(['curl', '-s', '-o', '/dev/null', '-w', '%{http_code} %{redirect_url}',
                                 *self.url('pkgs.emaki.sh', '/stable/x86_64/emaki.db')], capture_output=True, text=True)
        self.assertEqual(result.stdout, f'302 https://pkgs.emaki.sh:{self.port}/snap/stable/20261012T183000Z/emaki.db')


class Relay(unittest.TestCase):
    def test_relay_carries_both_directions(self):
        listener = socket.create_server(('127.0.0.1', 0))
        port = listener.getsockname()[1]

        def serve():
            connection, _ = listener.accept()
            data = b''
            while not data.endswith(b'\n'):
                data += connection.recv(100)
            connection.sendall(data.upper())
            connection.close()

        thread = threading.Thread(target=serve)
        thread.start()
        result = subprocess.run([sys.executable, HERE / 'vm/tcp-relay.py', '127.0.0.1', str(port)],
                                input=b'hello pacman\n', capture_output=True, timeout=20)
        thread.join()
        listener.close()
        self.assertEqual(result.stdout, b'HELLO PACMAN\n')


class CandidateAndStamp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        missing = [tool for tool in tp.TOOLS if not shutil.which(tool)]
        if missing:
            raise unittest.SkipTest('missing tools: ' + ', '.join(missing))

    def test_candidate_must_be_the_snapshot_and_rehearsals_never_stamp(self):
        world = tp.World()
        try:
            packages = world.base / 'pkgs'
            tp.release(packages, world.keys, '1-1')
            for command in (('publish', 'testing', packages), ('promote', '--first')):
                result = world.publish(*command)
                self.assertEqual(result.returncode, 0, result.stderr)
            snap = world.bucket / 'snap/stable' / world.pointer('stable')
            mirror = f'file://{world.bucket}'
            cand = check.candidate(mirror, 'stable', (snap / 'emaki.db').read_bytes())
            self.assertEqual(cand['versions']['emaki'], '1-1')
            self.assertEqual(cand['manifest_sha256'],
                             __import__('hashlib').sha256((snap / 'MANIFEST').read_bytes()).hexdigest())
            with self.assertRaises(SystemExit):
                check.candidate(mirror, 'stable', b'another database')
            # T2 rehearsal: the GitHub copy made by publish.sh, checked without a VM (--dry-run).
            self.assertEqual(world.publish('github', '--tag', 'testing').returncode, 0)
            dry = subprocess.run([sys.executable, HERE / 'vm/upgrade-check.py', '--run', 'T2', '--start', '0.1.1',
                                  '--base', world.base, '--mirror', mirror, '--github-dir', world.github / 'testing',
                                  '--rehearsal-bucket', world.bucket, '--dry-run'], capture_output=True, text=True)
            self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
            self.assertIn(cand['manifest_sha256'], dry.stdout)
            results = []
            for run in ('T1', 'T2'):
                for start in ('0.1.0', '0.1.1'):
                    path = world.base / f'{run}-{start}.json'
                    path.write_text(json.dumps({'run': run, 'start': start, 'passed': True, 'rehearsal': run == 'T2',
                                                'manifest_sha256': cand['manifest_sha256'],
                                                'finished': __import__('datetime').datetime.now(
                                                    __import__('datetime').timezone.utc).isoformat()}))
                    results.append(path)
            refused = world.publish('stamp', *results)
            self.assertEqual(refused.returncode, 2)
            self.assertIn('rehearsal', refused.stderr)
        finally:
            world.close()

    def dry_t2(self, world, *extra):
        return subprocess.run([sys.executable, HERE / 'vm/upgrade-check.py', '--run', 'T2', '--start', '0.1.1',
                               '--base', world.base, '--mirror', f'file://{world.bucket}', '--dry-run', *extra],
                              capture_output=True, text=True)

    def test_after_the_bridge_t2_goes_through_testing_and_the_release_can_be_stamped(self):
        """After the bridge, stable moves only through the stamped promote, so the candidate is
        not in stable while it is tested: T2 must take it from testing, or no later release
        (the yearly key extension among them) could ever be stamped."""
        world = tp.World()
        try:
            (world.github / 'stable').mkdir(parents=True)
            one = world.base / 'one'
            tp.release(one, world.keys, '1-1')
            for command in (('publish', 'testing', one), ('promote', '--first'), ('github', '--tag', 'testing')):
                self.assertEqual(world.publish(*command).returncode, 0, command)
            stamp = self.results(world, 'old-address')
            for command in (('stamp', *stamp), ('github',)):
                result = world.publish(*command)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            # The bridge is out. Release 2 is in testing only.
            two = world.base / 'two'
            tp.release(two, world.keys, '2-1')
            self.assertEqual(world.publish('publish', 'testing', two).returncode, 0)
            snap = world.bucket / 'snap/testing' / world.pointer('testing')
            manifest = __import__('hashlib').sha256((snap / 'MANIFEST').read_bytes()).hexdigest()
            later = self.dry_t2(world)
            self.assertEqual(later.returncode, 0, later.stdout + later.stderr)
            self.assertIn(f'candidate: MANIFEST {manifest}, emaki 2-1', later.stdout)
            self.assertIn('(pkgs-testing)', later.stdout)
            wrong = self.dry_t2(world, '--github-dir', world.github / 'testing')
            self.assertEqual(wrong.returncode, 1)
            self.assertIn('https://pkgs.emaki.sh/testing', wrong.stderr)
            for command in (('stamp', *self.results(world, 'pkgs-testing')), ('promote',)):
                result = world.publish(*command)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            stable = world.bucket / 'snap/stable' / world.pointer('stable')
            self.assertEqual((stable / 'MANIFEST').read_bytes(), (snap / 'MANIFEST').read_bytes())
        finally:
            world.close()

    def test_t2_of_the_bridge_is_the_old_address(self):
        world = tp.World()
        try:
            one = world.base / 'one'
            tp.release(one, world.keys, '1-1')
            self.assertEqual(world.publish('publish', 'testing', one).returncode, 0)
            early = self.dry_t2(world)
            self.assertEqual(early.returncode, 1)
            self.assertIn('promote --first', early.stderr)  # stable serves nothing yet
            for command in (('promote', '--first'), ('github', '--tag', 'testing')):
                self.assertEqual(world.publish(*command).returncode, 0, command)
            bridge = self.dry_t2(world)
            self.assertEqual(bridge.returncode, 1, bridge.stdout + bridge.stderr)
            self.assertIn('T2 of the bridge needs --github-dir', bridge.stderr)
            bridge = self.dry_t2(world, '--github-dir', world.github / 'testing')
            self.assertEqual(bridge.returncode, 0, bridge.stdout + bridge.stderr)
            self.assertIn('(old-address)', bridge.stdout)
        finally:
            world.close()

    def results(self, world, t2_via):
        """Green results of T1 and T2 from 0.1.0 and 0.1.1 at both sizes for the candidate in testing."""
        snap = world.bucket / 'snap/testing' / world.pointer('testing')
        manifest = __import__('hashlib').sha256((snap / 'MANIFEST').read_bytes()).hexdigest()
        now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
        paths = []
        for run in ('T1', 'T2'):
            for start in ('0.1.0', '0.1.1'):
                for size in check.SIZES:
                    path = world.base / f'{run}-{start}-{size}-{manifest[:8]}.json'
                    path.write_text(json.dumps({'run': run, 'start': start, 'size': size, 'passed': True,
                                                'rehearsal': False, 'manifest_sha256': manifest, 'finished': now,
                                                'via': t2_via if run == 'T2' else 'github-testing'}))
                    paths.append(path)
        return paths

    def test_the_stamp_requires_every_size_the_check_runs(self):
        self.assertEqual(tuple(check.SIZES), check.publish.REQUIRED_SIZES)


if __name__ == '__main__':
    unittest.main(verbosity=2)
