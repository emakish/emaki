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
        for text in ('sudo pacman -Syu\n', 'sudo pacman -Syyu\n', 'cat /etc/pacman.d/emaki-mirrorlist\n',
                     check.CHANNEL_SCREEN, 'pacman -Q emaki\n',
                     fixture['user']['password'] + '\n'):
            self.assertTrue(check.sendkeys(text))
        self.assertEqual(check.sendkeys('sudo -Syu\n'),
                         ['sendkey s', 'sendkey u', 'sendkey d', 'sendkey o', 'sendkey spc', 'sendkey minus',
                          'sendkey shift-s', 'sendkey y', 'sendkey u', 'sendkey ret'])
        with self.assertRaises(ValueError):
            check.sendkeys('é')


class ChannelMigration(unittest.TestCase):
    class Guest:
        def __init__(self, channel, **overrides):
            self.values = {
                'pacman -Q': 'emaki 0.2.1\n',
                'cat ~/.config/niri/config.kdl': b'personal config\n',
                'cat /var/log/pacman.log': 'marker\n[ALPM] upgraded emaki (0.2.0-1 -> 0.2.1)\n[ALPM] transaction completed\n',
                'cat /etc/pacman.d/emaki-mirrorlist': 'Include = /etc/emaki/channel\n',
                'cat /etc/emaki/channel': f'# Choice\nInclude = /usr/share/emaki/mirrors/{channel}.conf\n',
                'cat /etc/pacman.d/emaki-mirrorlist.pacnew 2>/dev/null || true': '',
                'test ! -e /etc/pacman.d/emaki-mirrorlist.pacnew': 0,
                'pacman-conf --repo emaki Server': f'https://pkgs.emaki.sh/{channel}/x86_64\n',
                'emaki-update-channel': channel + '\n',
                'pacman -Qk emaki-config': 0,
            }
            self.values.update(overrides)

        def run(self, command, root=False, check=True):
            value = self.values[command]
            if not check:
                if isinstance(value, bytes):
                    return subprocess.CompletedProcess(command, 0, stdout=value, stderr=b'')
                return subprocess.CompletedProcess(command, value, stdout=b'', stderr=b'')
            return value

    def inspect(self, via, **overrides):
        channel = 'testing' if check.VIAS[via]['edited'] else 'stable'
        guest = self.Guest(channel, **overrides)
        return check.checks_after_upgrade(guest, via, {'versions': {'emaki': '0.2.1'}}, 'marker\n', '0.2.0', '0.2.0-1', b'personal config\n')

    def test_stable_and_testing_keep_their_effective_channel(self):
        for via in check.VIAS:
            with self.subTest(via=via):
                results = self.inspect(via)
                self.assertTrue(all(value['ok'] for value in results.values()), results)

    def test_package_files_are_checked_as_root(self):
        """0.3.0 ships /etc/sudoers.d/10-emaki-wheel, readable by root only: pacman -Qk run by
        the person reports it missing (2026-10-07, every start)."""
        guest = self.Guest('testing')
        plain = guest.run

        def run(command, root=False, check=True):
            if command == 'pacman -Qk emaki-config' and not root:
                return subprocess.CompletedProcess(command, 1, b'1 missing file', b'(Permission denied)')
            return plain(command, root=root, check=check)

        guest.run = run
        results = self.inspect_guest(guest, 'pkgs-testing')
        self.assertTrue(results['emaki-config files all present']['ok'], results)
        guest.values['pacman -Qk emaki-config'] = 1
        results = self.inspect_guest(guest, 'pkgs-testing')
        self.assertFalse(results['emaki-config files all present']['ok'], results)

    def inspect_guest(self, guest, via):
        return check.checks_after_upgrade(guest, via, {'versions': {'emaki': '0.2.1'}}, 'marker\n', '0.2.0',
                                          '0.2.0-1', b'personal config\n')

    def test_wrong_selector_server_report_or_pending_mirror_list_fails(self):
        for command, value in [
                ('cat /etc/pacman.d/emaki-mirrorlist', 'Server = https://pkgs.emaki.sh/testing/$arch\n'),
                ('cat /etc/emaki/channel', 'Include = /usr/share/emaki/mirrors/stable.conf\n'),
                ('test ! -e /etc/pacman.d/emaki-mirrorlist.pacnew', 1),
                ('pacman-conf --repo emaki Server', 'https://pkgs.emaki.sh/stable/x86_64\n'),
                ('emaki-update-channel', 'stable\n')]:
            with self.subTest(command=command):
                results = self.inspect('pkgs-testing', **{command: value})
                self.assertFalse(all(value['ok'] for value in results.values()), results)


class MirrorSwitch(unittest.TestCase):
    """The switch to testing, applied by the real sed to each start's packaged mirrorlist."""
    # packaging/emaki-mirrorlist/emaki-mirrorlist at v0.1.0/v0.1.1 and at v0.2.0.
    PACKAGED = {
        '0.1.1': ('# Emaki repository channels. Enable exactly one server.\n'
                  'Server = https://github.com/emakish/packages/releases/download/stable\n'
                  '# Server = https://github.com/emakish/packages/releases/download/testing\n'),
        '0.2.0': ('# Emaki repository channels. Enable exactly one server.\n'
                  '# After switching channels, run `sudo pacman -Syyu` once.\n'
                  'Server = https://pkgs.emaki.sh/stable/$arch\n'
                  '# Server = https://pkgs.emaki.sh/testing/$arch\n'),
    }

    # packaging/emaki-mirrorlist at v0.3.0: the mirrorlist and the channel selector it includes.
    SELECTOR = ('# Copyright (C) 2026 Artur Yakymenko\n'
                '# SPDX-License-Identifier: GPL-3.0-or-later\n'
                '# Choose stable or testing [update channel].\n'
                '# After switching channels, run `sudo pacman -Syyu` once.\n'
                'Include = /usr/share/emaki/mirrors/stable.conf\n'
                '# Include = /usr/share/emaki/mirrors/testing.conf\n')
    INCLUDING = ('# Copyright (C) 2026 Artur Yakymenko\n'
                 '# SPDX-License-Identifier: GPL-3.0-or-later\n'
                 '# Choose the update channel in /etc/emaki/channel.\n'
                 'Include = /etc/emaki/channel\n')

    class Guest:
        def __init__(self, directory, mirrorlist, selector=None):
            self.vm = Path(directory)
            self.file = self.vm / 'emaki-mirrorlist'
            self.file.write_text(mirrorlist)
            self.selector = self.vm / 'channel'
            if selector is not None:
                self.selector.write_text(selector)

        def run(self, command, data=b'', root=False, check=True):
            if '/etc/pacman.d/emaki-mirrorlist' in command or '/etc/emaki/channel ' in command + ' ':
                command = command.replace('/etc/pacman.d/emaki-mirrorlist', str(self.file))
                command = re.sub(r'/etc/emaki/channel(?=\s|$|;)', str(self.selector), command)
                command = command.removesuffix('; pacman -Q')
                result = subprocess.run(['sh', '-ec', command], capture_output=True)
            else:
                result = subprocess.CompletedProcess(command, 0, b'', b'')
            if check:
                if result.returncode:
                    raise RuntimeError(result.stderr.decode())
                return result.stdout.decode()
            return result

        desktop = run

        def screenshot(self, name):
            pass

    def active(self, via, mirrorlist):
        with tempfile.TemporaryDirectory() as directory:
            guest = self.Guest(directory, mirrorlist)
            check.prepare_old_state(guest, via)
            return [line for line in guest.file.read_text().splitlines() if not line.startswith('#')]

    def test_t1_takes_each_starts_own_testing_line(self):
        self.assertEqual(check.t1_via('0.1.0'), 'github-testing')
        self.assertEqual(check.t1_via('0.1.2-full'), 'github-testing')
        self.assertEqual(check.t1_via('0.2.0'), 'pkgs-testing-swap')
        self.assertEqual(check.t1_via('0.3.0'), 'pkgs-testing-swap')
        self.assertEqual(self.active(check.t1_via('0.1.1'), self.PACKAGED['0.1.1']),
                         ['Server = https://github.com/emakish/packages/releases/download/testing'])
        self.assertEqual(self.active(check.t1_via('0.2.0'), self.PACKAGED['0.2.0']),
                         ['Server = https://pkgs.emaki.sh/testing/$arch'])

    def test_t2_after_the_bridge_leaves_only_pkgs_testing(self):
        for start, mirrorlist in self.PACKAGED.items():
            with self.subTest(start=start):
                self.assertEqual(self.active('pkgs-testing', mirrorlist), [check.PKGS_TESTING])

    def test_a_switch_that_matched_nothing_stops_the_run(self):
        for via in ('github-testing', 'pkgs-testing-swap'):
            with self.subTest(via=via), self.assertRaises(RuntimeError):
                self.active(via, 'Server = https://mirror.example/emaki/stable/$arch/extra\n')

    def test_from_0_3_0_t1_and_t2_switch_the_selector_and_leave_the_mirrorlist(self):
        with tempfile.TemporaryDirectory() as directory:
            guest = self.Guest(directory, self.INCLUDING, self.SELECTOR)
            for via in ('pkgs-testing-swap', 'pkgs-testing'):
                self.assertEqual(check.selector_via(guest, via), 'channel-testing')
            for via in ('github-testing', 'old-address', 'github-stable'):
                self.assertEqual(check.selector_via(guest, via), via)
            check.prepare_old_state(guest, 'channel-testing')
            self.assertEqual(guest.file.read_text(), self.INCLUDING)
            selector = guest.selector.read_text()
            self.assertEqual([line for line in selector.splitlines() if not line.startswith('#')],
                             ['Include = /usr/share/emaki/mirrors/testing.conf'])
            self.assertIn('\n# Include = /usr/share/emaki/mirrors/stable.conf\n', selector)

    def test_older_starts_keep_their_mirrorlist_switch(self):
        for start, mirrorlist in self.PACKAGED.items():
            with self.subTest(start=start), tempfile.TemporaryDirectory() as directory:
                guest = self.Guest(directory, mirrorlist)
                for via in ('pkgs-testing-swap', 'pkgs-testing'):
                    self.assertEqual(check.selector_via(guest, via), via)

    def test_a_selector_switch_that_matched_nothing_stops_the_run(self):
        for selector in ('Include = /usr/share/emaki/mirrors/custom.conf\n',
                         'Server = https://mirror.example/emaki/stable/$arch\n'):
            with self.subTest(selector=selector), tempfile.TemporaryDirectory() as directory, \
                    self.assertRaises(RuntimeError):
                check.prepare_old_state(self.Guest(directory, self.INCLUDING, selector), 'channel-testing')


class UpgradeEvidence(unittest.TestCase):
    def test_visual_evidence_lists_and_binds_unjudged_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            vm = Path(directory)
            self.assertEqual(check.visual_evidence(vm)['reason'], 'No frames captured')
            (vm / 'desktop-before.png').write_bytes(b'first capture')
            (vm / 'desktop-after.png').write_bytes(b'second capture')
            evidence = check.visual_evidence(vm)
            self.assertEqual(evidence['status'], 'NOT TESTED')
            self.assertEqual([Path(f['path']).name for f in evidence['frames']],
                             ['desktop-after.png', 'desktop-before.png'])
            self.assertTrue(all(f['judgment'] == 'NOT TESTED' for f in evidence['frames']))
            previous = evidence['frames'][0]['sha256']
            (vm / 'desktop-after.png').write_bytes(b'changed capture')
            self.assertNotEqual(check.visual_evidence(vm)['frames'][0]['sha256'], previous)

    def test_current_transaction_must_upgrade_emaki(self):
        fixtures = ChannelMigration()
        for log in (
                'marker\n[ALPM] transaction completed\n',
                '[ALPM] upgraded emaki (0.2.0-1 -> 0.2.1)\n[ALPM] transaction completed\n',
                'marker\n[ALPM] upgraded emaki (0.1.0-1 -> 0.2.1)\n[ALPM] transaction completed\n',
                'marker\n[ALPM] upgraded emaki (0.2.0-1 -> 0.2.1)\n',
                'marker\n[ALPM] transaction completed\n[ALPM] upgraded emaki (0.2.0-1 -> 0.2.1)\n'):
            with self.subTest(log=log):
                results = fixtures.inspect('pkgs-testing', **{'cat /var/log/pacman.log': log})
                self.assertFalse(all(value['ok'] for value in results.values()), results)

    def test_starting_package_is_checked_before_update(self):
        guest = ChannelMigration.Guest('testing', **{'pacman -Q emaki': 'emaki 0.1.2-1\n'})
        self.assertEqual(check.check_start_version(guest, '0.1.2-full',
                                                 {'versions': {'emaki': '0.3.0-1'}}), '0.1.2-1')
        for start, candidate in (('0.1.1', '0.3.0-1'), ('0.1.2-full', '0.1.2-1')):
            with self.subTest(start=start, candidate=candidate), self.assertRaises(RuntimeError):
                check.check_start_version(guest, start, {'versions': {'emaki': candidate}})


class PersonalConfiguration(unittest.TestCase):
    def test_upgrade_rejects_changes_even_when_marker_survives(self):
        before = (check.KDL_MARK + '\n// Personal setting\n').encode()
        guest = ChannelMigration.Guest('testing')
        for after in (before, (check.KDL_MARK + '\n// Changed setting\n').encode()):
            with self.subTest(after=after):
                guest.values['cat ~/.config/niri/config.kdl'] = after
                results = check.checks_after_upgrade(guest, 'pkgs-testing',
                    {'versions': {'emaki': '0.2.1'}}, 'marker\n', '0.2.0', '0.2.0-1', before)
                self.assertEqual(all(v['ok'] for v in results.values()), after == before)

    def test_restart_compares_exact_bytes_and_rejects_missing_file(self):
        before = (check.KDL_MARK + '\n// Personal setting\n').encode()
        with tempfile.TemporaryDirectory() as directory:
            class Guest:
                vm = Path(directory)
                content = before
                failed_probe = None

                def run(self, command, root=False, check=True):
                    if self.failed_probe and command.endswith(' ' + self.failed_probe):
                        return subprocess.CompletedProcess(command, 1, b'', b'probe failed')
                    if command == 'cat ~/.config/niri/config.kdl':
                        if self.content is None:
                            return subprocess.CompletedProcess(command, 1, b'', b'missing')
                        return subprocess.CompletedProcess(command, 0, self.content, b'')
                    if command == 'systemctl is-enabled bluetooth.service':
                        output = b'disabled'
                    elif command == 'pacman -Syu --noconfirm':
                        output = b'there is nothing to do'
                    elif command.startswith('sha256sum '):
                        output = b'database'
                    else:
                        output = b''
                    return subprocess.CompletedProcess(command, 0, output, b'')

                desktop = run

            guest = Guest()
            (guest.vm / 'wallet-setup.txt').write_text('exit 1\nfixture')
            (guest.vm / 'portal-setup.txt').write_text('exit 0\nfixture')
            for after in (before, before + b'// Added setting\n', before.replace(b'\n', b'\r\n')):
                with self.subTest(after=after):
                    guest.content = after
                    results = {}
                    check.checks_after_restart(guest, results, {'db_sha256': 'database'}, before)
                    self.assertEqual(all(v['ok'] for v in results.values()), after == before)
            guest.content = before
            for mode, key in (('owner', 'ksecretd owns Secret Service'),
                              ('verify', 'portal master key kept')):
                guest.failed_probe = mode
                results = {}
                check.checks_after_restart(guest, results, {'db_sha256': 'database'}, before)
                self.assertFalse(results[key]['ok'])
            guest.failed_probe = None
            (guest.vm / 'portal-setup.txt').unlink()
            results = {}
            check.checks_after_restart(guest, results, {'db_sha256': 'database'}, before)
            self.assertFalse(results['portal master key kept']['ok'])
            guest.content = None
            with self.assertRaises(RuntimeError):
                check.checks_after_restart(guest, {}, {'db_sha256': 'database'}, before)


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

    def test_t1_from_0_2_0_reads_the_testing_snapshot_not_github(self):
        world = tp.World()
        try:
            one = world.base / 'one'
            tp.release(one, world.keys, '1-1')
            self.assertEqual(world.publish('publish', 'testing', one).returncode, 0)
            dry = subprocess.run([sys.executable, HERE / 'vm/upgrade-check.py', '--run', 'T1', '--start', '0.2.0',
                                  '--base', world.base, '--mirror', f'file://{world.bucket}', '--dry-run'],
                                 capture_output=True, text=True)
            self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
            self.assertIn('(pkgs-testing-swap)', dry.stdout)
            self.assertIn(', emaki 1-1', dry.stdout)
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
