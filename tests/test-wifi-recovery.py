#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Wireless recovery fixtures: no real devices, journal or network commands."""
import configparser
import io
import json
from pathlib import Path
import re
import shlex
import subprocess
import shutil
from unittest.mock import patch
import tempfile
import types
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'scripts/emaki-wifi-recover'


def load(source=None):
    module = types.ModuleType('wifi_recovery_fixture')
    module.__file__ = str(SOURCE)
    exec(compile(SOURCE.read_text() if source is None else source, str(SOURCE), 'exec'),
         module.__dict__)
    return module


RECOVERY = load()


class Fixture(unittest.TestCase):
    module = RECOVERY

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sysfs = self.root / 'sys'
        (self.sysfs / 'class/net').mkdir(parents=True)
        self.now = 100.0
        self.calls = []
        self.logs = []
        self.states = {}
        self.access_points = {}
        self.radio = 'enabled:enabled\n'
        self.runtime = self.root / 'runtime'
        self.recovery = self.new_recovery()

    def new_recovery(self):
        return self.module.Recovery(self.sysfs, self.runtime, self.command,
                                    lambda: self.now, self.logs.append)

    def command(self, *args):
        self.calls.append(args)
        if args == ('nmcli', '-g', 'WIFI,WIFI-HW', 'general'):
            return self.radio
        if args[:5] == ('nmcli', '-g', 'GENERAL.TYPE,GENERAL.STATE', 'device', 'show'):
            return self.states[args[5]]
        if args[:6] == ('nmcli', '-g', 'BSSID', 'device', 'wifi', 'list'):
            self.assertEqual(args[6:], ('ifname', args[7], '--rescan', 'no'))
            return self.access_points[args[7]]
        raise AssertionError(f'unexpected command: {args!r}')

    def adapter(self, name='wlan0', identity='0000:03:00.0', bus='pci',
                driver_name='wireless', wireless=True, device=None):
        bus_path = self.sysfs / 'bus' / bus
        driver = bus_path / 'drivers' / driver_name
        driver.mkdir(parents=True, exist_ok=True)
        for verb in ('bind', 'unbind'):
            (driver / verb).touch()
        if device is None:
            device = self.sysfs / 'devices' / identity
            device.mkdir(parents=True, exist_ok=True)
            (device / 'subsystem').symlink_to(bus_path)
            (device / 'driver').symlink_to(driver)
        net = self.sysfs / 'class/net' / name
        net.mkdir()
        (net / 'device').symlink_to(device)
        if wireless:
            phy = self.sysfs / 'class/ieee80211' / ('phy' + str(len(self.states)))
            phy.mkdir(parents=True)
            (net / 'phy80211').symlink_to(phy)
        (net / 'carrier').write_text('0\n')
        (net / 'operstate').write_text('down\n')
        self.states[name] = 'wifi\n30 (disconnected)'
        self.access_points[name] = ''
        return net, device, driver

    def row(self, message='brcmfmac 0000:03:00.0: scan error (-12)', age=0, nm=False):
        return {'MESSAGE': message, '__MONOTONIC_TIMESTAMP': str(int((self.now - age) * 1e6)),
                **({'_SYSTEMD_UNIT': 'NetworkManager.service'} if nm else {'_TRANSPORT': 'kernel'})}

    def assert_untouched(self, driver):
        self.assertEqual((driver / 'unbind').read_text(), '')
        self.assertEqual((driver / 'bind').read_text(), '')

    def test_kernel_and_byte_messages(self):
        for message, nm in [('brcmfmac 0000:03:00.0: scan error (-12)', False),
                            ('phy0: scanning failed', False),
                            ('ieee80211 phy0: brcmf_cfg80211_scan: scan error (-12)', False)]:
            with self.subTest(message=message):
                row = self.row(message, nm=nm)
                self.assertTrue(self.module.failure_for(row, ('wlan0', 'phy0', '0000:03:00.0'), self.now))
                row['MESSAGE'] = list(message.encode())
                self.assertTrue(self.module.failure_for(row, ('wlan0', 'phy0', '0000:03:00.0'), self.now))

    def test_reject_unrelated_spoofed_stale_and_invalid(self):
        rows = [self.row("connection 'wlan0: scan failed'", nm=True),
                self.row('device (wlan0): scan request failed', nm=True),
                self.row('wlan1: scan failed'), self.row('wlan01: scan failed'),
                self.row('other-wlan0: scan failed'), self.row('wlan0: scan complete'),
                self.row('wlan0: scan completed without error'),
                self.row('wlan0: scan completed; previous connection failed'),
                self.row('wlan0: scan failed', age=91), self.row('wlan0: scan failed', age=-1),
                {'MESSAGE': 'wlan0: scan failed', '__MONOTONIC_TIMESTAMP': '100000000',
                 'SYSLOG_IDENTIFIER': 'NetworkManager'},
                self.row('wlan0: scan failed') | {'MESSAGE': [999]},
                self.row('wlan0: scan failed') | {'MESSAGE': None},
                self.row('wlan0: scan failed') | {'__MONOTONIC_TIMESTAMP': 'bad'}]
        for row in rows:
            with self.subTest(row=row):
                self.assertFalse(self.module.failure_for(row, ('wlan0',), self.now))

    def test_watcher_exits_without_wireless_or_runtime_writes(self):
        self.assertFalse(self.recovery.watch_tick())
        self.assertEqual(self.calls, [])
        self.assertFalse(self.runtime.exists())

    def rule_starts_late_adapter(self, **adapter):
        # Interpret only the shipped rule's matches and wants; never contact host udev.
        rule = (SOURCE.parent.parent / 'systemd/90-emaki-wifi-recovery.rules').read_text()
        matches = re.findall(r'(ACTION|SUBSYSTEM|ENV\{DEVTYPE\})=="([^"]+)"', rule)
        self.assertEqual(len(matches), 3)
        def matches_event(event):
            return all(event.get(key) == value for key, value in matches)
        event = {'ACTION': 'add', 'SUBSYSTEM': 'net', 'ENV{DEVTYPE}': 'wlan'}
        self.assertTrue(matches_event(event))
        for key, value in [('ACTION', 'remove'), ('SUBSYSTEM', 'usb'), ('ENV{DEVTYPE}', '')]:
            self.assertFalse(matches_event(event | {key: value}))
        self.assertIn('TAG+="systemd"', rule)
        wants = re.findall(r'ENV\{SYSTEMD_WANTS\}\+="([^"]+)"', rule)
        self.assertEqual(len(wants), 1)
        unit = configparser.ConfigParser()
        unit.read(SOURCE.parent.parent / 'systemd' / wants[0])
        argv = shlex.split(unit['Service']['ExecStart'])
        self.assertEqual(argv, ['/usr/lib/emaki/emaki-wifi-recover', 'watch'])
        self.assertEqual(unit['Install']['WantedBy'], 'multi-user.target')
        with patch.object(self.module.os, 'geteuid', return_value=0), \
                patch.dict(self.module.os.environ, {}, clear=True), \
                patch.object(self.module.sys, 'argv', argv), \
                patch.object(self.module, 'Recovery', return_value=self.recovery), \
                patch.object(self.module.time, 'sleep') as sleep:
            self.assertEqual(self.module.main(), 0)
            sleep.assert_not_called()
        self.assertFalse(self.runtime.exists())
        self.assertEqual(self.calls, [])

        _, device, driver = self.adapter(**adapter)
        restarted = self.new_recovery()
        def next_poll(seconds):
            self.assertEqual(seconds, 10)
            if self.now > 100:
                raise StopIteration('end fixture watcher')
            self.now += self.module.QUIET
        with patch.object(self.module.os, 'geteuid', return_value=0), \
                patch.dict(self.module.os.environ, {}, clear=True), \
                patch.object(self.module.sys, 'argv', argv), \
                patch.object(self.module, 'Recovery', return_value=restarted), \
                patch.object(restarted, 'journal', side_effect=lambda: [self.row('wlan0: scan failed')]), \
                patch.object(self.module.time, 'sleep', side_effect=next_poll):
            with self.assertRaises(StopIteration):
                self.module.main()
        self.assertEqual((driver / 'unbind').read_text(), device.name)
        self.assertEqual((driver / 'bind').read_text(), device.name)
        self.assertTrue(any('source=auto' in log for log in self.logs))

    def test_rule_starts_watcher_after_late_boot_adapter(self):
        self.rule_starts_late_adapter()

    def test_rule_starts_watcher_after_usb_hotplug(self):
        self.rule_starts_late_adapter(identity='1-2:1.0', bus='usb')

    def test_idle_ticks_do_not_rewrite_unchanged_state(self):
        self.adapter()
        self.recovery.tick()
        path = self.runtime / 'state.json'
        first = path.stat().st_mtime_ns
        self.recovery.tick()
        self.assertEqual(path.stat().st_mtime_ns, first)

    def test_watcher_completes_pending_without_querying_journal(self):
        net, device, driver = self.adapter()
        def failed(candidate):
            self.interrupt_unbound(device, net)
            raise OSError('probe failed')
        self.recovery.rebind = failed
        self.recovery.tick(manual=True)
        self.calls.clear()
        self.assertTrue(self.new_recovery().watch_tick())
        self.assertEqual(self.calls, [])
        self.assertEqual((driver / 'bind').read_text(), device.name)

    def test_real_nmcli_radio_output(self):
        # NetworkManager 1.58.1 general uses one terse colon-separated row.
        self.adapter()
        self.radio = 'enabled:enabled\n'
        self.assertEqual(self.recovery.tick(manual=True), 0)

    def test_pci_reset_is_per_device_not_shared_module(self):
        _, device, driver = self.adapter()
        self.adapter('wlan1', '0000:04:00.0')
        self.states['wlan1'] = 'wifi\n100 (connected)'
        self.assertEqual(self.recovery.tick(manual=True), 0)
        for verb in ('bind', 'unbind'):
            self.assertEqual((driver / verb).read_text(), device.name)
        self.assertEqual(len(self.logs), 1)
        self.assertIn('source=manual reset=unbind-bind', self.logs[0])
        self.assertTrue(all(call[0] == 'nmcli' for call in self.calls))

    def test_usb_interface_not_parent(self):
        _, device, driver = self.adapter(identity='1-2:1.0', bus='usb')
        self.assertEqual(self.recovery.tick(manual=True), 0)
        self.assertEqual((driver / 'bind').read_text(), '1-2:1.0')
        self.assertEqual((driver / 'unbind').read_text(), '1-2:1.0')

    def test_usb_parent_and_wired_devices_ignored(self):
        _, _, driver = self.adapter(identity='1-2', bus='usb')
        self.adapter('eth0', '0000:04:00.0', wireless=False)
        self.assertEqual(self.recovery.devices(), [])
        self.assertNotEqual(self.recovery.tick(manual=True), 0)
        self.assert_untouched(driver)

    def test_evidence_refusal_names_guard_once_per_boot(self):
        _, _, driver = self.adapter()
        self.radio = 'disabled:enabled'
        self.recovery.tick([self.row()])
        self.new_recovery().tick([self.row()])
        self.assertEqual(self.logs, ['device=0000:03:00.0 evidence present, refused: radio'])
        self.assert_untouched(driver)

    def test_guard_reasons_do_not_include_connection_text(self):
        net, _, _ = self.adapter()
        candidate = self.recovery.devices()[0]
        self.states['wlan0'] = 'wifi\n100 (connected private-name)'
        self.assertEqual(self.recovery.guard(candidate), 'nm-state')
        self.states['wlan0'] = 'wifi\n30 (disconnected)'
        self.access_points['wlan0'] = 'private-address'
        self.assertEqual(self.recovery.guard(candidate), 'scan-cache')
        self.access_points['wlan0'] = ''
        (net / 'carrier').write_text('1')
        self.assertEqual(self.recovery.guard(candidate), 'carrier')

    def test_working_device_never_resets(self):
        _, _, driver = self.adapter()
        self.states['wlan0'] = 'wifi\n100 (connected)'
        self.assertNotEqual(self.recovery.tick(manual=True), 0)
        self.assert_untouched(driver)
        self.assertEqual(self.logs, [])

    def test_unsafe_states_fail_closed(self):
        net, _, driver = self.adapter()
        for state in ('10 (unmanaged)', '20 (unavailable)', '40 (prepare)', '70 (ip-config)',
                      '90 (secondaries)', '110 (deactivating)', 'unknown', '', '30'):
            with self.subTest(state=state):
                self.states['wlan0'] = 'wifi\n' + state if state != '30' else 'ethernet\n30'
                self.recovery.tick(manual=True)
                self.assert_untouched(driver)
        self.states['wlan0'] = 'wifi\n30 (disconnected)'
        for filename, value in [('carrier', '1'), ('operstate', 'up')]:
            original = (net / filename).read_text()
            (net / filename).write_text(value)
            self.recovery.tick(manual=True)
            self.assert_untouched(driver)
            (net / filename).write_text(original)
        self.access_points['wlan0'] = '01:02:03:04:05:06'
        self.recovery.tick(manual=True)
        self.assert_untouched(driver)
        self.access_points['wlan0'] = ''
        for radio in ('disabled:enabled', 'enabled:disabled', '', 'unknown:unknown', 'enabled\nenabled'):
            self.radio = radio
            self.recovery.tick(manual=True)
            self.assert_untouched(driver)

    def test_active_or_wired_sibling_blocks_entire_device(self):
        _, device, driver = self.adapter()
        self.adapter('wlan1', device=device)
        self.states['wlan1'] = 'wifi\n100 (connected)'
        self.recovery.tick(manual=True)
        self.assert_untouched(driver)
        self.states['wlan1'] = 'wifi\n30 (disconnected)'
        (self.sysfs / 'class/net/wlan1/phy80211').unlink()
        self.recovery.tick(manual=True)
        self.assert_untouched(driver)

    def test_unknown_command_state_blocks_reset(self):
        _, _, driver = self.adapter()
        def unavailable(*args):
            raise subprocess.TimeoutExpired(args, 8)
        self.recovery.command = unavailable
        self.recovery.tick(manual=True)
        self.assert_untouched(driver)

    def test_automatic_waits_for_failure_and_quiet_window(self):
        _, _, driver = self.adapter()
        self.recovery.tick()
        self.assert_untouched(driver)
        self.recovery.tick([self.row()])
        self.assert_untouched(driver)
        self.now += self.module.QUIET
        self.assertEqual(self.recovery.tick([self.row()]), 0)
        self.assertIn('source=automatic', self.logs[0])

    def test_backoff_survives_restart_and_manual_requests(self):
        self.adapter()
        self.assertEqual(self.recovery.tick(manual=True), 0)
        self.recovery = self.new_recovery()
        self.now += 1
        self.assertEqual(self.recovery.tick(manual=True), 75)
        self.assertEqual(len(self.logs), 1)
        for delay in self.module.BACKOFF[:2]:
            self.now += delay
            self.assertEqual(self.recovery.tick(manual=True), 0)
        self.now += 10000
        self.assertEqual(self.new_recovery().tick(manual=True), 75)
        self.assertEqual(len(self.logs), 3)
        records = json.loads((self.runtime / 'state.json').read_text())
        self.assertEqual(next(iter(records.values()))['attempts'], 3)

    def test_manual_and_automatic_share_budget_and_old_evidence(self):
        self.adapter()
        old = self.row()
        self.recovery.tick([old])
        self.now += self.module.QUIET
        self.assertEqual(self.recovery.tick([old]), 0)
        self.assertEqual(self.new_recovery().tick(manual=True), 75)
        self.now += self.module.BACKOFF[0]
        self.recovery.tick([old])
        self.assertEqual(len(self.logs), 1)
        self.recovery.tick([self.row()])
        self.now += self.module.QUIET
        self.assertEqual(self.recovery.tick([self.row()]), 0)
        self.assertEqual(len(self.logs), 2)

    def test_rechecks_working_device_immediately_before_reset(self):
        _, _, driver = self.adapter()
        safe = self.recovery.safe
        checks = []
        def changing(candidate):
            checks.append(candidate)
            if len(checks) == 2:
                self.states['wlan0'] = 'wifi\n100 (connected)'
            return safe(candidate)
        self.recovery.safe = changing
        self.recovery.tick(manual=True)
        self.assertEqual(len(checks), 2)
        self.assert_untouched(driver)

    def test_disappearing_failure_restarts_quiet_period(self):
        _, _, driver = self.adapter()
        self.recovery.tick([self.row()])
        self.now += self.module.QUIET
        self.recovery.tick([])
        self.recovery.tick([self.row()])
        self.assert_untouched(driver)
        self.now += self.module.QUIET
        self.assertEqual(self.recovery.tick([self.row()]), 0)

    def test_connection_after_budget_save_blocks_sysfs_write(self):
        _, _, driver = self.adapter()
        save = self.recovery.save
        def connecting(data):
            save(data)
            self.states['wlan0'] = 'wifi\n100 (connected)'
        self.recovery.save = connecting
        self.assertEqual(self.recovery.tick(manual=True), 1)
        self.assert_untouched(driver)
        self.assertEqual(len(self.logs), 1)

    def test_changed_driver_blocks_rebind(self):
        _, device, driver = self.adapter()
        candidate = self.recovery.devices()[0]
        other = driver.parent / 'replacement'
        other.mkdir()
        (device / 'driver').unlink()
        (device / 'driver').symlink_to(other)
        with self.assertRaises(OSError):
            self.recovery.rebind(candidate)
        self.assert_untouched(driver)

    def interrupt_unbound(self, device, net):
        (device / 'driver').unlink()
        shutil.rmtree(net)

    def test_killed_helper_completes_recorded_bind_without_netdev(self):
        net, device, driver = self.adapter()
        def killed(candidate):
            self.interrupt_unbound(device, net)
            raise SystemExit(9)
        self.recovery.rebind = killed
        with self.assertRaises(SystemExit):
            self.recovery.tick(manual=True)
        self.assertEqual(self.new_recovery().devices(), [])
        self.assertEqual(self.new_recovery().tick(), 0)
        self.assertEqual((driver / 'bind').read_text(), device.name)
        self.assertEqual((driver / 'unbind').read_text(), '')
        # A resolved binding clears pending without another write or reset.
        (device / 'driver').symlink_to(driver)
        (driver / 'bind').write_text('')
        self.new_recovery().tick(manual=True)
        self.assertEqual((driver / 'bind').read_text(), '')
        record = next(iter(json.loads((self.runtime / 'state.json').read_text()).values()))
        self.assertNotIn('pending', record)
        self.assertEqual(record['attempts'], 1)

    def test_failed_bind_is_retried_and_completion_is_bounded(self):
        net, device, driver = self.adapter()
        def failed(candidate):
            self.interrupt_unbound(device, net)
            raise OSError('probe failed')
        self.recovery.rebind = failed
        self.assertEqual(self.recovery.tick(manual=True), 1)
        # Even the final reset in the boot budget can finish its pending bind.
        with self.recovery.state() as data:
            data[device.name]['attempts'] = 3
            self.recovery.save(data)
        self.assertEqual(self.new_recovery().tick(manual=True), 0)
        self.assertEqual((driver / 'bind').read_text(), device.name)
        self.assertEqual(self.new_recovery().tick(), 75)
        for delay in self.module.BACKOFF[:2]:
            self.now += delay
            self.assertEqual(self.new_recovery().tick(), 0)
        self.now += 10000
        self.assertEqual(self.new_recovery().tick(), 75)
        self.assertEqual(sum('reset=bind-pending' in log for log in self.logs), 3)
        self.calls.clear()
        self.assertFalse(self.new_recovery().watch_tick())
        self.assertEqual(self.calls, [])

    def test_vanished_pending_device_does_not_keep_watcher_alive(self):
        net, device, driver = self.adapter(identity='1-2:1.0', bus='usb')
        def removed(candidate):
            self.interrupt_unbound(device, net)
            shutil.rmtree(device)
            raise OSError('adapter unplugged')
        self.recovery.rebind = removed
        self.assertEqual(self.recovery.tick(manual=True), 1)
        self.calls.clear()
        self.assertTrue(self.new_recovery().watch_tick())
        self.assertFalse(self.new_recovery().watch_tick())
        self.assertEqual(self.calls, [])
        self.assert_untouched(driver)
        record = json.loads((self.runtime / 'state.json').read_text())[device.name]
        self.assertNotIn('pending', record)
        self.assertEqual(record['attempts'], 1)

    def test_missing_pending_driver_keeps_completion_record(self):
        net, device, driver = self.adapter()
        def removed(candidate):
            self.interrupt_unbound(device, net)
            shutil.rmtree(driver)
            raise OSError('driver unavailable')
        self.recovery.rebind = removed
        self.assertEqual(self.recovery.tick(manual=True), 1)
        self.assertTrue(self.new_recovery().watch_tick())
        record = json.loads((self.runtime / 'state.json').read_text())[device.name]
        self.assertIn('pending', record)

    def test_bind_still_attempted_after_unbind_write_raises(self):
        _, device, driver = self.adapter()
        original = Path.open
        class FailedUnbind(io.StringIO):
            def write(self, text):
                raise OSError('partial unbind')
        def opened(path, *args, **kwargs):
            if path == driver / 'unbind':
                return FailedUnbind()
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', opened):
            self.assertEqual(self.recovery.tick(manual=True), 1)
        self.assertEqual((driver / 'bind').read_text(), device.name)

    def test_pending_cannot_bind_usb_parent_or_foreign_driver(self):
        net, device, driver = self.adapter(identity='1-2', bus='usb')
        self.interrupt_unbound(device, net)
        with self.recovery.state() as data:
            data[device.name] = {'attempts': 1, 'next': 0,
                                 'pending': str(driver.relative_to(self.sysfs))}
            self.recovery.save(data)
        self.new_recovery().tick()
        self.assert_untouched(driver)

    def test_failed_reset_consumes_attempt_and_logs_once(self):
        self.adapter()
        def failed(candidate):
            raise OSError('fixture bind failure')
        self.recovery.rebind = failed
        self.assertEqual(self.recovery.tick(manual=True), 1)
        self.assertEqual(self.new_recovery().tick(manual=True), 75)
        self.assertEqual(len(self.logs), 1)
        self.assertIn('attempt=1 source=manual reset=unbind-bind', self.logs[0])


class Mutants(unittest.TestCase):
    def test_real_mutations_are_rejected_by_safety_tests(self):
        source = SOURCE.read_text()
        mutations = [
            ('no backoff', " or now < record['next']", '',
             'test_backoff_survives_restart_and_manual_requests'),
            ('working device reset', '    def safe(self, candidate):\n',
             '    def safe(self, candidate):\n        return True\n',
             'test_working_device_never_resets'),
        ]
        for name, before, after, test in mutations:
            with self.subTest(mutant=name):
                self.assertEqual(source.count(before), 1)
                changed = load(source.replace(before, after, 1))
                case = type('MutatedFixture', (Fixture,), {'module': changed})(test)
                result = unittest.TextTestRunner(stream=io.StringIO()).run(case)
                self.assertEqual(len(result.errors), 0, result.errors)
                self.assertEqual(len(result.failures), 1, 'mutation survived')


if __name__ == '__main__':
    unittest.main()
