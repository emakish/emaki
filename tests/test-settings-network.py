#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Network settings use a private command fixture, never the system network."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
COMMAND = Path(os.environ.get('NETWORK_SETTINGS_COMMAND', ROOT / 'scripts/emaki-settings-network'))
UUID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
WIFI = '11111111-2222-3333-4444-555555555555'
loader = importlib.machinery.SourceFileLoader('settings_network', str(COMMAND))
spec = importlib.util.spec_from_loader(loader.name, loader)
backend = importlib.util.module_from_spec(spec)
loader.exec_module(backend)

FAKE = r'''
import json, os, sys
args = sys.argv[1:]
with open(os.environ['CALL_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\n')
a = args[2:]
uuid = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
wifi = '11111111-2222-3333-4444-555555555555'
if os.environ.get('FAIL_ON') and os.environ['FAIL_ON'] in a:
    print('PRIVATE DIAGNOSTIC', file=sys.stderr)
    sys.exit(4)
if a[-2:] == ['device', 'status']:
    print('enp1s0:ethernet:connected:Office\\: cable')
elif a[-2:] == ['connection', 'show']:
    print(uuid + ':Office\\: cable:802-3-ethernet:enp1s0')
    print(wifi + ':Cafe\\\\name:802-11-wireless:--')
elif 'GENERAL.CON-UUID' in a:
    print(os.environ.get('ACTIVE_UUID', uuid))
elif 'show' in a and 'uuid' in a:
    print('ipv4.dns:1.1.1.1,8.8.8.8')
    print('ipv6.dns:2001:4860:4860::8888')
    print('ipv4.ignore-auto-dns:yes\nipv6.ignore-auto-dns:yes')
    print('proxy.method:' + os.environ.get('PROXY_METHOD', '1 (auto)'))
    print('proxy.pac-url:https://proxy.example/pac')
    print('proxy.pac-script:' + os.environ.get('PROXY_SCRIPT', ''))
    if a[-1] == wifi:
        print('802-11-wireless.ssid:Cafe\\name')
        print('802-11-wireless-security.key-mgmt:wpa-psk')
elif a[:2] in (['connection', 'modify'], ['device', 'reapply'], ['connection', 'import']):
    pass
else:
    print('unexpected fixture command: ' + repr(a), file=sys.stderr)
    sys.exit(99)
'''


class Network(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='network-settings-')
        self.base = Path(self.tmp.name)
        fake = self.base / 'nmcli'
        fake.write_text('#!' + sys.executable + '\n' + FAKE)
        fake.chmod(0o700)
        self.log = self.base / 'calls'
        self.env = dict(os.environ, PATH=str(self.base), CALL_LOG=str(self.log))

    def tearDown(self):
        self.tmp.cleanup()

    def run_command(self, *args, **extra):
        result = subprocess.run([sys.executable, str(COMMAND), *args],
                                env=dict(self.env, **extra), capture_output=True, text=True, timeout=10)
        data = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0 if data['ok'] else 1)
        self.assertEqual(result.stderr, '')
        return data

    def calls(self):
        return [json.loads(line)[2:] for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_status_escaped_names_ipv6_and_saved_wifi(self):
        data = self.run_command('status', '--json')
        self.assertTrue(data['ok'])
        self.assertEqual(data['wired'][0]['connection'], 'Office: cable')
        self.assertEqual(data['connections'][0]['dns'], ['1.1.1.1', '8.8.8.8', '2001:4860:4860::8888'])
        self.assertFalse(data['connections'][0]['dnsAutomatic'])
        self.assertEqual(data['connections'][0]['proxyValue'], 'https://proxy.example/pac')
        self.assertEqual(data['wifi_profiles'][0]['ssid'], 'Cafe\\name')
        self.assertFalse(data['wifi_profiles'][0]['active'])
        self.assertTrue(all('modify' not in call for call in self.calls()))

    def test_status_failure_uses_read_wording(self):
        data = self.run_command('status', '--json', FAIL_ON='status')
        self.assertFalse(data['ok'])
        self.assertEqual(data['error'], 'Network settings could not be read. Try again.')

    def test_status_skips_only_a_confirmed_vanished_profile(self):
        vanished = dict(uuid=WIFI, type='wifi', device='', active=False)
        with patch.object(backend, 'nm', return_value=''), \
                patch.object(backend, 'profiles', side_effect=[[vanished], []]), \
                patch.object(backend, 'properties', side_effect=backend.MissingObject('missing')):
            self.assertEqual(backend.status()['connections'], [])
        with patch.object(backend, 'nm', return_value=''), \
                patch.object(backend, 'profiles', return_value=[vanished]), \
                patch.object(backend, 'properties', side_effect=backend.MissingObject('missing')):
            with self.assertRaises(backend.MissingObject):
                backend.status()

    def test_dns_targets_uuid_both_families_then_reapplies(self):
        self.assertTrue(self.run_command('dns', UUID, '9.9.9.9, 2606:4700:4700::1111')['ok'])
        change = [x for x in self.calls() if 'modify' in x][0]
        self.assertEqual(change, ['connection', 'modify', 'uuid', UUID,
                                 'ipv4.dns', '9.9.9.9', 'ipv4.ignore-auto-dns', 'yes',
                                 'ipv6.dns', '2606:4700:4700::1111', 'ipv6.ignore-auto-dns', 'yes'])
        self.assertEqual(self.calls()[-1], ['device', 'reapply', 'enp1s0'])

    def test_dns_reset_clears_servers_and_restores_auto(self):
        self.assertTrue(self.run_command('dns', WIFI, 'reset')['ok'])
        change = self.calls()[-1]
        self.assertEqual(change[-8:], ['ipv4.dns', '', 'ipv4.ignore-auto-dns', 'no',
                                     'ipv6.dns', '', 'ipv6.ignore-auto-dns', 'no'])
        self.assertFalse(any('reapply' in x for x in self.calls()))

    def test_dns_invalid_input_does_not_touch_nm(self):
        for value in ('1.2.3.999', '1.1.1.1;touch /tmp/no', '', 'reset 1.1.1.1'):
            self.assertFalse(self.run_command('dns', UUID, value)['ok'])
        self.assertEqual(self.calls(), [])

    def test_uuid_required(self):
        self.assertFalse(self.run_command('dns', 'Office: cable', '1.1.1.1')['ok'])
        self.assertEqual(self.calls(), [])

    def test_manual_proxy_is_literal_inline_pac_and_clears_url(self):
        self.assertTrue(self.run_command('proxy', WIFI, 'manual', 'proxy.example:8080')['ok'])
        self.assertEqual(self.calls()[-1][-6:], ['proxy.method', 'auto', 'proxy.pac-url', '',
            'proxy.pac-script', 'function FindProxyForURL(url, host) { return "PROXY proxy.example:8080"; }'])

    def test_manual_proxy_status_and_disabled_stale_script(self):
        script = backend.MANUAL_PREFIX + 'proxy.example:8080' + backend.MANUAL_SUFFIX
        row = self.run_command('status', '--json', PROXY_SCRIPT=script)['connections'][0]
        self.assertEqual((row['proxyMode'], row['proxyValue']), ('manual', 'proxy.example:8080'))
        row = self.run_command('status', '--json', PROXY_SCRIPT=script, PROXY_METHOD='0 (none)')['connections'][0]
        self.assertEqual((row['proxyMode'], row['proxyValue']), ('none', ''))
        row = self.run_command('status', '--json', PROXY_SCRIPT='custom script')['connections'][0]
        self.assertTrue(row['proxyCustomScript'])

    def test_proxy_none_clears_script_and_url(self):
        self.assertTrue(self.run_command('proxy', WIFI, 'none')['ok'])
        self.assertEqual(self.calls()[-1][-6:], ['proxy.method', 'none', 'proxy.pac-url', '', 'proxy.pac-script', ''])

    def test_proxy_auto_url_or_wpad(self):
        for value in ('https://proxy.example/pac', ''):
            self.assertTrue(self.run_command('proxy', WIFI, 'auto', value)['ok'])
            self.assertEqual(self.calls()[-1][-6:], ['proxy.method', 'auto', 'proxy.pac-url', value, 'proxy.pac-script', ''])

    def test_proxy_rejects_injection_and_credentials(self):
        for mode, value in [('manual', 'host:8080"; alert(1);'), ('manual', 'host:0'),
                            ('manual', 'host:65536'), ('auto', 'file:///tmp/a'),
                            ('auto', 'https://user:pass@example.com/pac'), ('manual', '[::::]:80')]:
            self.assertFalse(self.run_command('proxy', UUID, mode, value)['ok'])
        self.assertEqual(self.calls(), [])

    def test_reapply_failure_reports_saved_not_success(self):
        data = self.run_command('dns', UUID, '1.1.1.1', FAIL_ON='reapply')
        self.assertFalse(data['ok'])
        self.assertTrue(data['saved'])
        self.assertIn('Reconnect', data['error'])

    def test_changed_active_connection_is_not_reapplied(self):
        data = self.run_command('dns', UUID, '1.1.1.1', ACTIVE_UUID=WIFI)
        self.assertFalse(data['ok'])
        self.assertTrue(data['saved'])
        self.assertFalse(any('reapply' in x for x in self.calls()))

    def test_modify_failure_does_not_reapply_or_leak_diagnostics(self):
        data = self.run_command('dns', UUID, '1.1.1.1', FAIL_ON='modify')
        self.assertFalse(data['ok'])
        self.assertNotIn('PRIVATE', json.dumps(data))
        self.assertFalse(any('reapply' in x for x in self.calls()))

    def test_vpn_types_follow_installed_plugins(self):
        prefix = self.base / 'prefix'
        plugins = prefix / 'lib/NetworkManager/VPN'
        plugins.mkdir(parents=True)
        executable = prefix / 'bin/nmcli'
        with patch.object(backend.shutil, 'which', return_value=str(executable)):
            self.assertEqual([item['value'] for item in backend.vpn_types()], ['wireguard'])
            (plugins / 'nm-openvpn-service.name').write_text(
                '[VPN Connection]\nservice=org.freedesktop.NetworkManager.openvpn\n')
            self.assertEqual([item['value'] for item in backend.vpn_types()], ['wireguard', 'openvpn'])

    def test_missing_vpn_plugin_explains_requirement(self):
        self.assertEqual(self.run_command('import-vpn', 'openvpn', str(self.base))['error'],
                         'This VPN type needs its NetworkManager plugin.')
        self.assertEqual(self.calls(), [])

    def test_vpn_import_preserves_filename_as_single_argument(self):
        config = self.base / 'office $(example).ovpn'
        config.write_text('configuration')
        self.assertTrue(self.run_command('import-vpn', 'wireguard', str(config))['ok'])
        self.assertEqual(self.calls()[-1], ['connection', 'import', 'type', 'wireguard', 'file', str(config)])

    def test_vpn_local_file_url(self):
        config = self.base / 'office vpn.ovpn'
        config.write_text('configuration')
        self.assertTrue(self.run_command('import-vpn', 'wireguard', config.as_uri())['ok'])
        self.assertEqual(self.calls()[-1][-1], str(config))
        self.assertFalse(self.run_command('import-vpn', 'wireguard', 'file://remote/path')['ok'])

    def test_vpn_import_requires_readable_regular_file(self):
        self.assertFalse(self.run_command('import-vpn', 'wireguard', str(self.base))['ok'])
        self.assertFalse(self.run_command('import-vpn', '--help', str(self.base))['ok'])
        self.assertEqual(self.calls(), [])

    def test_timeout_and_missing_backend(self):
        with patch.object(backend.subprocess, 'run', side_effect=subprocess.TimeoutExpired('nmcli', 10)):
            with self.assertRaisesRegex(backend.Failure, 'could not be reached'):
                backend.nm('status')
        with patch.object(backend.subprocess, 'run', side_effect=FileNotFoundError()):
            with self.assertRaisesRegex(backend.Failure, 'not available'):
                backend.nm('status')

    def test_terse_parser_preserves_literal_backslash_and_colon(self):
        self.assertEqual(backend.split_row(r'name\:here:path\\name::'), ['name:here', 'path\\name', '', ''])


def mutants():
    source = COMMAND.read_text()
    changes = [
        ("family + '.ignore-auto-dns', 'no' if reset else 'yes'", "family + '.ignore-auto-dns', 'yes' if reset else 'no'"),
        ("nm('connection', 'modify', 'uuid', item['uuid'], *values)", "nm('connection', 'modify', item['name'], *values)"),
        ("'proxy.pac-url', url, 'proxy.pac-script', script", "'proxy.pac-url', url, 'proxy.pac-script', 'retained'"),
        ("return dict(ok=False, saved=True", "return dict(ok=True, saved=True"),
        ("if active != item['uuid']:", "if False:"),
    ]
    with tempfile.TemporaryDirectory(prefix='network-mutants-') as temporary:
        for index, (before, after) in enumerate(changes):
            if source.count(before) != 1:
                raise AssertionError('Mutation target must be unique: ' + before)
            mutated = Path(temporary) / ('network-' + str(index))
            mutated.write_text(source.replace(before, after))
            result = subprocess.run([sys.executable, str(Path(__file__).resolve())],
                                    env=dict(os.environ, NETWORK_SETTINGS_COMMAND=str(mutated)),
                                    capture_output=True, text=True, timeout=30)
            if result.returncode == 0 or 'FAIL:' not in result.stderr:
                raise AssertionError('Mutation was not caught: ' + before + '\n' + result.stderr)
    print('5 network settings mutants rejected')


if __name__ == '__main__':
    if sys.argv[1:] == ['--mutants']:
        mutants()
    else:
        unittest.main()
