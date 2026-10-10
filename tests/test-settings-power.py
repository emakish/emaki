#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Power settings fixtures never touch a real service or device."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / 'scripts' / name))
    spec = importlib.util.spec_from_loader(name, loader)
    result = importlib.util.module_from_spec(spec)
    loader.exec_module(result)
    return result


class Power(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='emaki-power-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.power = module('emaki-settings-power')
        self.charge = module('emaki-charge-limit')
        self.power.SUPPLIES = self.charge.SUPPLIES = self.root / 'supplies'
        self.power.SUPPLIES.mkdir()
        self.charge.STATE = self.root / 'state/charge-limits.json'
        self.capability = patch.object(self.power, 'available', return_value=True)
        self.capability.start()
        self.addCleanup(self.capability.stop)
        self.runtime = patch.object(self.power, 'runtime_error', return_value='')
        self.runtime.start()
        self.addCleanup(self.runtime.stop)
        self.env = patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.root / 'config')})
        self.env.start()
        self.addCleanup(self.env.stop)

    def battery(self, limit='100'):
        battery = self.power.SUPPLIES / 'BAT0'
        battery.mkdir()
        for key, value in {'type': 'Battery', 'energy_full': '40000',
                           'energy_full_design': '50000'}.items():
            (battery / key).write_text(value)
        if limit is not None:
            (battery / 'charge_control_end_threshold').write_text(limit)
        return battery

    def helper(self, *args):
        with patch.object(os, 'geteuid', return_value=0), contextlib.redirect_stdout(io.StringIO()) as output:
            code = self.charge.main(list(args))
        return code, json.loads(output.getvalue())

    def test_defaults_and_ac_transition(self):
        self.assertEqual(self.power.source(), 'ac')
        self.battery()
        self.assertEqual(self.power.status()['values']['blank_battery'], 0)
        self.assertEqual(self.power.source(), 'battery')
        ac = self.power.SUPPLIES / 'AC'
        ac.mkdir()
        (ac / 'type').write_text('Mains')
        (ac / 'online').write_text('1')
        self.assertEqual(self.power.source(), 'ac')

    def test_untouched_timers_match_reset_baseline_without_enabling(self):
        snapshot = self.power.status()
        self.assertEqual(snapshot['values'], snapshot['defaults'])
        with patch.object(self.power, 'call') as call:
            for key in ('blank_battery', 'blank_ac', 'lock_delay'):
                self.power.set_value(key, snapshot['defaults'][key])
            call.assert_not_called()
        self.assertFalse(self.power.config_path().exists())

    def test_partial_saved_settings_keep_effective_timer_fallback(self):
        self.power.save_settings({'blank_ac': 600})
        snapshot = self.power.status()
        self.assertEqual(snapshot['values']['blank_ac'], 600)
        self.assertEqual(snapshot['values']['blank_battery'], 330)
        self.assertEqual(snapshot['values']['lock_delay'], 300)
        self.assertEqual(snapshot['defaults']['lock_delay'], 0)

    def test_peripheral_battery_does_not_select_battery_policy(self):
        battery = self.battery()
        (battery / 'scope').write_text('Device')
        self.assertEqual(self.power.batteries(), [])
        self.assertEqual(self.power.source(), 'ac')
        (battery / 'scope').write_text('System')
        self.assertEqual(len(self.power.batteries()), 1)
        self.assertEqual(self.power.source(), 'battery')

    def test_health_and_optional_threshold(self):
        self.battery(None)
        row = self.power.batteries()[0]
        self.assertEqual(row['health'], 80)
        self.assertIsNone(row['charge_limit'])
        with self.assertRaises(ValueError):
            self.power.charge('BAT0', '80')

    def test_invalid_values_never_start_services(self):
        with patch.object(self.power, 'call') as call:
            for key, value in [('lid', 'suspend'), ('sleep', '300'), ('lock_delay', '-1'),
                               ('blank_ac', '999999'), ('blank_battery', True)]:
                with self.assertRaises(ValueError):
                    self.power.set_value(key, value)
            call.assert_not_called()
        self.assertFalse(self.power.config_path().exists())

    def test_set_enable_reset_and_failure_rollback(self):
        with patch.object(self.power, 'call') as call:
            result = self.power.set_value('blank_ac', '600')
            self.assertEqual(result['values']['blank_ac'], 600)
            self.assertEqual(call.call_args_list[0].args[0],
                             ['systemctl', '--user', 'enable', 'emaki-idle.service'])
            self.assertEqual(call.call_args_list[2].args[0],
                             ['systemctl', '--user', 'is-active', '--quiet', 'emaki-idle.service'])
            self.power.set_value('blank_ac', self.power.DEFAULTS['blank_ac'])
            self.assertEqual(self.power.read_settings()['blank_ac'], 0)
        with patch.object(self.power, 'call', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                self.power.set_value('blank_ac', 900)
        self.assertEqual(self.power.read_settings()['blank_ac'], 0)

    def test_root_helper_set_apply_and_missing_battery(self):
        battery = self.battery()
        code, response = self.helper('set', 'BAT0', '80')
        self.assertEqual((code, response['state']), (0, 'ready'))
        self.assertEqual((battery / 'charge_control_end_threshold').read_text().strip(), '80')
        self.assertEqual(json.loads(self.charge.STATE.read_text()), {'BAT0': 80})
        (battery / 'charge_control_end_threshold').write_text('100')
        self.assertEqual(self.helper('apply')[0], 0)
        self.assertEqual((battery / 'charge_control_end_threshold').read_text().strip(), '80')
        (battery / 'type').unlink()
        for path in battery.iterdir():
            path.unlink()
        battery.rmdir()
        self.assertEqual(self.helper('apply')[0], 0)

    def test_root_helper_rejects_traversal_nonbattery_and_range(self):
        battery = self.battery()
        for args in [('set', '../BAT0', '80'), ('set', 'BAT0', '20'),
                     ('set', 'BAT0', '81'), ('set', 'BAT0', '100; id'), ('shell',)]:
            self.assertEqual(self.helper(*args)[0], 1)
        (battery / 'type').write_text('Mains')
        self.assertEqual(self.helper('set', 'BAT0', '80')[0], 1)
        self.assertFalse(self.charge.STATE.exists())
        self.assertEqual((battery / 'charge_control_end_threshold').read_text(), '100')

    def test_root_helper_save_failure_restores_hardware(self):
        battery = self.battery()
        with patch.object(self.charge, 'save', side_effect=OSError('read only')):
            self.assertEqual(self.helper('set', 'BAT0', '80')[0], 1)
        self.assertEqual((battery / 'charge_control_end_threshold').read_text(), '100')

    def test_unprivileged_helper_is_refused(self):
        with patch.object(os, 'geteuid', return_value=1000), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.charge.main(['apply']), 1)
        self.assertFalse(self.charge.STATE.parent.exists())

    def test_inactive_runtime_is_reported(self):
        self.runtime.stop()
        self.power.save_settings(self.power.DEFAULTS)
        response = subprocess.CompletedProcess([], 0, stdout='inactive\n', stderr='')
        with patch.object(self.power.subprocess, 'run', return_value=response):
            self.assertIn('not running', self.power.status()['message'])
            self.assertEqual(self.power.status()['values']['lock_delay'], 0)

    def test_cli_invalid_command_is_json(self):
        result = subprocess.run(['python3', str(ROOT / 'scripts/emaki-settings-power'),
                                 'set', 'lid', 'suspend', '--json'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['state'], 'error')

    def test_supervisor_battery_to_ac_without_lid_inhibitor(self):
        class Child:
            pid = 999999
            def poll(self): return None
            def wait(self, timeout=None): return 0
        choices = dict(self.power.DEFAULTS, blank_battery=60, blank_ac=900, lid='ignore')
        calls = []
        def launch(command, **kwargs):
            calls.append((command, kwargs))
            return Child()
        class Done(Exception): pass
        with patch.object(self.power, 'read_settings', return_value=choices), \
                patch.object(self.power, 'source', side_effect=['battery', 'ac']), \
                patch.object(self.power.subprocess, 'Popen', side_effect=launch), \
                patch.object(self.power.os, 'killpg'), patch.object(self.power.signal, 'signal'), \
                patch.object(self.power.time, 'sleep', side_effect=[None, None, None, Done]):
            with self.assertRaises(Done):
                self.power.run()
        self.assertEqual(calls[0][1]['env']['EMAKI_IDLE_OFF'], '60')
        self.assertEqual(calls[1][1]['env']['EMAKI_IDLE_OFF'], '900')
        self.assertFalse(any('--what=handle-lid-switch' in command for command, _ in calls))
        self.assertFalse(any('suspend' in word for call in calls for word in call[0]))

    def test_missing_components_refuse_before_saving(self):
        with patch.object(self.power, 'available', return_value=False), \
                patch.object(self.power, 'call') as call:
            self.assertFalse(self.power.status()['idle_available'])
            self.assertEqual(self.power.status()['values']['lock_delay'], 0)
            with self.assertRaisesRegex(RuntimeError, 'components are missing'):
                self.power.set_value('lock_delay', 300)
            call.assert_not_called()
        self.assertFalse(self.power.config_path().exists())

    def test_child_exit_never_announces_readiness(self):
        class Child:
            def poll(self): return 127
        with patch.object(self.power, 'read_settings', return_value=self.power.SAVED_DEFAULTS), \
                patch.object(self.power.subprocess, 'Popen', return_value=Child()), \
                patch.object(self.power.signal, 'signal'), \
                patch.object(self.power.time, 'sleep'), \
                patch.object(self.power, 'ready') as ready:
            with self.assertRaises(SystemExit) as error:
                self.power.run()
            self.assertEqual(error.exception.code, 127)
            ready.assert_not_called()

    def test_retired_lid_override_uses_system_policy(self):
        self.power.save_settings(dict(self.power.DEFAULTS, lid='ignore'))
        self.assertEqual(self.power.status()['values']['lid'], 'system')
        with patch.object(self.power, 'call') as call:
            with self.assertRaisesRegex(ValueError, 'managed by the system'):
                self.power.set_value('lid', 'ignore')
            call.assert_not_called()

    def test_all_steps_disabled_has_no_child_or_restart(self):
        choices = dict(self.power.DEFAULTS, blank_ac=0, blank_battery=0, lock_delay=0)
        class Done(Exception): pass
        with patch.object(self.power, 'read_settings', return_value=choices), \
                patch.object(self.power.subprocess, 'Popen') as launch, \
                patch.object(self.power.signal, 'signal'), \
                patch.object(self.power.time, 'sleep', side_effect=Done):
            with self.assertRaises(Done):
                self.power.run()
        launch.assert_not_called()


class PowerPage(unittest.TestCase):
    @unittest.skipUnless(shutil.which('qs'), 'The shell runtime is unavailable.')
    def test_missing_threshold_and_visible_failure(self):
        with tempfile.TemporaryDirectory(prefix='power-page-') as temporary:
            base = Path(temporary)
            shutil.copytree(ROOT / 'shell/settings', base / 'settings')
            shutil.copy(ROOT / 'shell/SettingsPowerPage.qml', base / 'SettingsPowerPage.qml')
            (base / 'qmldir').write_text('singleton ShellPalette 1.0 ShellPalette.qml\nSettingsPowerPage 1.0 SettingsPowerPage.qml\nSystemService 1.0 SystemService.qml\n')
            (base / 'ShellPalette.qml').write_text('pragma Singleton\nimport QtQuick\nQtObject { property string uiFont: "sans-serif" }')
            (base / 'SystemService.qml').write_text('import QtQuick\nQtObject { property var profiles: ({state:"ready",profiles:["balanced"],current:"balanced"}); property string actionState: "idle"; property int actionSerial: 0; function act(kind,value) { actionSerial++; actionState = "pending"; return true; } }')
            (base / 'shell.qml').write_text('''import QtQuick
import Quickshell
ShellRoot {
    SystemService { id: system }
    FloatingWindow {
        implicitWidth: 900; implicitHeight: 900
        SettingsPowerPage { id: page; width: 800; service: system }
    }
    function find(item, name) {
        if (item.objectName === name) return item;
        for (const child of item.children || []) {
            const result = find(child, name);
            if (result) return result;
        }
        return null;
    }
    function check(condition, reason) {
        if (!condition) { console.error("POWER_PAGE_FAILURE " + reason); Qt.exit(1); }
    }
    Timer {
        interval: 300; running: true
        onTriggered: {
            for (const current of [0, 75, 85, 100]) {
                const options = page.chargeOptions(current);
                check(options.some(option => option.value === current), "current charge limit must be labelled");
            }
            const confirmed = page.snapshot;
            const noBattery = find(page, "settings-no-battery");
            check(!noBattery.visible, "present battery has no absence message");
            page.snapshot = {values: {blank_ac: 0, blank_battery: 0, lock_delay: 0},
                defaults: {blank_ac: 0, blank_battery: 0, lock_delay: 0},
                idle_available: true, batteries: []};
            check(!noBattery.visible, "unread battery state has no absence message");
            page.snapshot = Object.assign({}, page.snapshot, {state: "ready"});
            check(noBattery.visible && noBattery.text === "No battery", "ready empty battery list is explicit");
            check(find(page, "settings-blank-ac").visible, "no battery preserves AC timers");
            page.page = "lock";
            check(!noBattery.visible, "lock page has no battery message");
            page.page = "battery";
            for (const name of ["settings-blank-ac", "settings-blank-battery", "settings-lock-delay"])
                check(!find(page, name).changed, "untouched timer has no reset");
            page.snapshot = {values: {blank_ac: 600, blank_battery: 0, lock_delay: 0},
                defaults: {blank_ac: 0, blank_battery: 0, lock_delay: 0},
                idle_available: true, batteries: []};
            check(find(page, "settings-blank-ac").changed, "personal timer has reset");
            check(find(page, "settings-blank-ac").value === 600, "effective minutes are preserved");
            page.snapshot = confirmed;
            const row = find(page, "settings-charge-limit");
            check(row && !row.visible, "unsupported charge limit must be hidden");
            system.actionState = "target_gone";
            check(!find(page, "settings-power-action-status").visible, "foreign failure stays out of power page");
            page.request("profile", "balanced");
            system.actionState = "confirmation_timeout";
            check(page.actionResult === "confirmation_timeout", "own failure is retained");
            system.actionState = "confirmed";
            check(page.actionResult === "confirmation_timeout", "foreign completion cannot replace own result");
            page.page = "lock";
            check(page.actionResult === "idle", "page switch clears feedback");
            page.page = "battery";
            page.request("profile", "balanced");
            system.act("brightness", 30);
            system.actionState = "target_gone";
            check(page.actionResult === "idle", "overlapping foreign action is not attributed to page");
            page.set("blank_ac", 900);
        }
    }
    Timer {
        interval: 11000; running: true
        onTriggered: {
            check(page.error === "Fixture apply failure.", "failure must survive status polling");
            check(page.snapshot.values.blank_ac === 330, "failed apply must retain confirmed value");
            console.log("POWER_PAGE_PASS"); Qt.quit();
        }
    }
}
''')
            bindir = base / 'bin'
            bindir.mkdir()
            helper = bindir / 'emaki-settings-power'
            helper.write_text('''#!/bin/sh
if [ "$1" = set ]; then
    printf '%s\\n' '{"schema_version":1,"state":"error","message":"Fixture apply failure."}'
    exit 1
fi
printf '%s\\n' '{"schema_version":1,"state":"ready","values":{"blank_ac":330,"blank_battery":330,"lid":"system","lock_delay":300},"batteries":[{"name":"BAT0","health":80,"charge_limit":null,"percent":60,"charging":true}]}'
''')
            helper.chmod(0o755)
            runtime = base / 'runtime'
            runtime.mkdir(mode=0o700)
            env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                       QML_DISABLE_DISK_CACHE='1', XDG_RUNTIME_DIR=str(runtime),
                       PATH=str(bindir) + ':' + os.environ['PATH'],
                       DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
                       DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'))
            env.pop('WAYLAND_DISPLAY', None)
            result = subprocess.run(['qs', '-p', str(base / 'shell.qml'), '--no-color'],
                                    env=env, capture_output=True, text=True, timeout=16)
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, output)
            self.assertIn('POWER_PAGE_PASS', output)
            for error in ['TypeError', 'ReferenceError', 'Cannot assign', 'Unable to assign', 'Binding loop']:
                self.assertNotIn(error, output)
            helper.unlink()
            (base / 'shell.qml').write_text('''import QtQuick
import Quickshell
ShellRoot {
    SystemService { id: system }
    FloatingWindow {
        implicitWidth: 900; implicitHeight: 900
        SettingsPowerPage { id: page; width: 800; service: system }
    }
    Timer {
        interval: 500; running: true
        onTriggered: {
            if (!page.error.length) { console.error("Missing helper was silent"); Qt.exit(1); }
            console.log("POWER_MISSING_HELPER_PASS"); Qt.quit();
        }
    }
}
''')
            result = subprocess.run(['qs', '-p', str(base / 'shell.qml'), '--no-color'],
                                    env=env, capture_output=True, text=True, timeout=6)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('POWER_MISSING_HELPER_PASS', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
