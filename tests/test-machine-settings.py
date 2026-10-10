#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The machine-settings provider against a fake system bus.

A fake `busctl` on PATH serves the properties of org.freedesktop.locale1 and
org.freedesktop.timedate1 from a JSON file, applies their Set* methods with the argument
format busctl uses, records every call and can fail a method with a service message. No real
bus, no root, no file outside a temporary directory.

  python3 tests/test-machine-settings.py             the tests, then the mutants
  python3 tests/test-machine-settings.py --no-mutants
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent.parent
PROGRAM = Path(os.environ.get('EMAKI_MACHINE_SETTINGS_UNDER_TEST', ROOT / 'scripts/emaki-machine-settings'))

FAKE_BUSCTL = r'''#!/usr/bin/env python3
import json, os, sys, time
state_path = os.environ['FAKE_BUS_STATE']
state = json.load(open(state_path))
args = sys.argv[1:]
with open(os.environ['FAKE_BUS_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\n')
assert args[0] == '--system', args
args = args[1:]
if args[0] == '--json=short' and args[1] == 'get-property':
    service, path, interface, names = args[2], args[3], args[4], args[5:]
    props = state[interface]
    if state.get('called'):
        sys.stderr.write('Failed to get property: Connection reset by peer\n')
        sys.exit(1)
    for name in names:
        if name not in props:
            sys.stderr.write('Failed to get property ' + name + '\n')
            sys.exit(1)
        value = props[name]
        kind = {bool: 'b', str: 's', list: 'as'}[type(value)]
        print(json.dumps({'type': kind, 'data': value}))
    sys.exit(0)
options = [a for a in args if a.startswith('--')]
assert options == ['--allow-interactive-authorization=yes', '--timeout=90'], args
rest = [a for a in args if not a.startswith('--')]
assert rest[0] == 'call', args
interface, method, signature, values = rest[3], rest[4], rest[5], rest[6:]
failure = os.environ.get('FAKE_BUS_FAIL', '')
if failure.startswith(method + ':'):
    sys.stderr.write('Call failed: ' + failure.split(':', 1)[1] + '\n')
    sys.exit(1)
if os.environ.get('FAKE_BUS_IGNORE'):
    sys.exit(0)
if os.environ.get('FAKE_BUS_DELAY'):
    # The service waits for an administrator, then applies to the state of that moment.
    open(os.environ['FAKE_BUS_CALLING'], 'w').close()
    time.sleep(float(os.environ['FAKE_BUS_DELAY']))
    state = json.load(open(state_path))
props = state[interface]
boolean = lambda text: {'true': True, 'false': False}[text]
if method == 'SetX11Keyboard':
    assert signature == 'ssssbb' and len(values) == 6, values
    props['X11Layout'], props['X11Model'], props['X11Variant'], props['X11Options'] = values[:4]
    state['convert'], state['interactive'] = boolean(values[4]), boolean(values[5])
elif method == 'SetTimezone':
    assert signature == 'sb' and len(values) == 2, values
    props['Timezone'] = values[0]
    state['interactive'] = boolean(values[1])
elif method == 'SetNTP':
    assert signature == 'bb' and len(values) == 2, values
    props['NTP'] = boolean(values[0])
    state['interactive'] = boolean(values[1])
elif method == 'SetLocale':
    assert signature == 'asb', values
    count = int(values[0])
    assert len(values) == count + 2, values
    props['Locale'] = values[1:1 + count]
    state['interactive'] = boolean(values[-1])
else:
    sys.stderr.write('Call failed: Unknown method\n')
    sys.exit(1)
if os.environ.get('FAKE_BUS_FOREIGN'):
    # Another writer changes the keyboard right after this call.
    state['org.freedesktop.locale1']['X11Model'] = 'foreign'
if os.environ.get('FAKE_BUS_BREAK_AFTER_CALL'):
    state['called'] = True
json.dump(state, open(state_path, 'w'))
late = os.environ.get('FAKE_BUS_FAIL_AFTER', '')
if late.startswith(method + ':'):
    # The service took the call, but busctl reports a failure (its reply timed out).
    sys.stderr.write('Call failed: ' + late.split(':', 1)[1] + '\n')
    sys.exit(1)
'''

SECRET = 'SERVICE-TEXT-NEVER-SHOWN'


def machine_state():
    return {
        'org.freedesktop.locale1': {
            'X11Layout': 'us,ru', 'X11Model': 'pc105', 'X11Variant': 'dvorak,',
            'X11Options': 'terminate:ctrl_alt_bksp,grp:caps_toggle',
            'VConsoleKeymap': 'us',
            'Locale': ['LANG=en_US.UTF-8', 'LC_MESSAGES=en_US.UTF-8'],
        },
        'org.freedesktop.timedate1': {'Timezone': 'Europe/Berlin', 'NTP': True, 'CanNTP': True},
    }


class Provider(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='emaki-machine-settings-'))
        self.bin = self.dir / 'bin'
        self.bin.mkdir()
        fake = self.bin / 'busctl'
        fake.write_text(FAKE_BUSCTL)
        fake.chmod(0o755)
        self.state = self.dir / 'state.json'
        self.state.write_text(json.dumps(machine_state()))
        self.log = self.dir / 'calls.log'
        self.log.write_text('')
        self.env = {'PATH': f'{self.bin}:{os.environ["PATH"]}', 'FAKE_BUS_STATE': str(self.state),
                    'FAKE_BUS_LOG': str(self.log),
                    'EMAKI_MACHINE_DECLARATIONS': str(self.dir / 'absent.json')}

    def tearDown(self):
        shutil.rmtree(self.dir)

    def run_provider(self, *args, **env):
        result = subprocess.run([sys.executable, '-I', str(PROGRAM), *args, '--json'],
                                capture_output=True, text=True, timeout=30,
                                env={**self.env, **env})
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '')
        return result.returncode, json.loads(result.stdout)

    def rows(self, reply):
        return {row['key']: row for row in reply['settings']}

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()
                if 'call' in json.loads(line)]

    def machine(self):
        return json.loads(self.state.read_text())

    def declare(self, text):
        path = self.dir / 'declared.json'
        path.write_text(text)
        self.env['EMAKI_MACHINE_DECLARATIONS'] = str(path)

    def test_installed_choices_without_machine_calls(self):
        for name, output in [('locale', 'C\nC.utf8\nen_US.utf8\nde_DE.UTF-8\nnot-a-locale'),
                             ('timedatectl', 'Europe/Berlin\nUTC\ninvalid zone')]:
            path = self.bin / name
            path.write_text('#!/usr/bin/env python3\nprint(' + repr(output) + ')\n')
            path.chmod(0o755)
        code, reply = self.run_provider('choices')
        self.assertEqual(code, 0)
        self.assertEqual(reply['locales'], ['C.UTF-8', 'de_DE.UTF-8', 'en_US.UTF-8'])
        self.assertEqual(reply['time_zones'], ['Europe/Berlin', 'UTC'])
        self.assertEqual(self.log.read_text(), '')
        (self.bin / 'locale').write_text('#!/bin/sh\nexit 1\n')
        self.assertEqual(self.run_provider('choices')[1]['locales'], [])

    def test_get_reads_every_key_from_the_services(self):
        code, reply = self.run_provider('get')
        self.assertEqual((code, reply['status'], reply['schema_version']), (0, 'read', 1))
        rows = self.rows(reply)
        self.assertEqual(list(rows), ['keyboard.layouts', 'keyboard.switch_key', 'time.zone',
                                      'time.automatic', 'locale.language', 'locale.formats'])
        self.assertEqual(rows['keyboard.layouts']['value'], ['dvorak', 'ru'])
        self.assertEqual(rows['keyboard.switch_key']['value'], 'Caps Lock')
        self.assertEqual(rows['time.zone']['value'], 'Europe/Berlin')
        self.assertIs(rows['time.automatic']['value'], True)
        self.assertEqual(rows['locale.language']['value'], 'en_US.UTF-8')
        self.assertIsNone(rows['locale.formats']['value'])
        for row in rows.values():
            self.assertEqual((row['source'], row['editable'], row['declared_by']),
                             ('machine', True, None), row)
        self.assertEqual(self.calls(), [])

    def test_get_one_key_and_capabilities(self):
        code, reply = self.run_provider('get', 'time.zone')
        self.assertEqual([row['key'] for row in reply['settings']], ['time.zone'])
        code, reply = self.run_provider('capabilities')
        self.assertEqual((code, reply['status'], reply['platform']), (0, 'read', 'systemd'))
        rows = self.rows(reply)
        self.assertEqual(rows['time.zone']['authorization'], 'org.freedesktop.timedate1.set-timezone')
        self.assertEqual(rows['keyboard.layouts']['service'], 'org.freedesktop.locale1')
        self.assertNotIn('lock.delay', rows)

    def test_layouts_keep_model_options_and_console_keymap(self):
        code, reply = self.run_provider('set', 'keyboard.layouts', 'de,colemak,ru')
        self.assertEqual((code, reply['status']), (0, 'applied'), reply)
        self.assertEqual(reply['before'], ['dvorak', 'ru'])
        self.assertEqual(reply['after'], ['de', 'colemak', 'ru'])
        machine = self.machine()
        locale1 = machine['org.freedesktop.locale1']
        self.assertEqual((locale1['X11Layout'], locale1['X11Variant']), ('de,us,ru', ',colemak,'))
        self.assertEqual(locale1['X11Model'], 'pc105')
        self.assertEqual(locale1['X11Options'], 'terminate:ctrl_alt_bksp,grp:caps_toggle')
        self.assertEqual((machine['convert'], machine['interactive']), (False, True))
        self.assertEqual(len(self.calls()), 1)

    def test_layout_variants_round_trip(self):
        code, reply = self.run_provider('set', 'keyboard.layouts', 'de(nodeadkeys),us')
        self.assertEqual((code, reply['after']), (0, ['de(nodeadkeys)', 'us']), reply)
        code, reply = self.run_provider('set', 'keyboard.layouts', 'us(dvorak)')
        self.assertEqual((code, reply['after']), (0, ['dvorak']), reply)
        # Variants with capitals, as the XKB rules list them; `before` sets the machine back.
        code, reply = self.run_provider('set', 'keyboard.layouts', 'de(T3),us(intl)')
        self.assertEqual((code, reply['after']), (0, ['de(T3)', 'us(intl)']), reply)
        code, reply = self.run_provider('set', 'keyboard.layouts', 'us')
        code, reply = self.run_provider('set', 'keyboard.layouts', ','.join(reply['before']))
        self.assertEqual((code, reply['after']), (0, ['de(T3)', 'us(intl)']), reply)

    def test_layouts_it_could_not_set_back_are_marked(self):
        state = machine_state()
        state['org.freedesktop.locale1'].update(X11Layout='us,ru,de,fr,ua', X11Variant='')
        self.state.write_text(json.dumps(state))
        row = self.rows(self.run_provider('get', 'keyboard.layouts')[1])['keyboard.layouts']
        self.assertEqual((row['value'], row['reason']),
                         (['us', 'ru', 'de', 'fr', 'ua'], 'layouts_unrecognised'))

    def test_switch_key_replaces_only_group_toggles(self):
        code, reply = self.run_provider('set', 'keyboard.switch_key', 'Alt+Shift')
        self.assertEqual((code, reply['after']), (0, 'Alt+Shift'), reply)
        locale1 = self.machine()['org.freedesktop.locale1']
        self.assertEqual(locale1['X11Options'], 'terminate:ctrl_alt_bksp,grp:alt_shift_toggle')
        self.assertEqual((locale1['X11Layout'], locale1['X11Variant']), ('us,ru', 'dvorak,'))
        code, reply = self.run_provider('set', 'keyboard.switch_key', 'Super+Space')
        self.assertEqual((code, reply['after']), (0, 'Super+Space'), reply)
        self.assertEqual(self.machine()['org.freedesktop.locale1']['X11Options'],
                         'terminate:ctrl_alt_bksp')

    def test_unknown_group_toggle_reads_as_unrecognised(self):
        state = machine_state()
        state['org.freedesktop.locale1']['X11Options'] = 'grp:win_space_toggle'
        self.state.write_text(json.dumps(state))
        row = self.rows(self.run_provider('get', 'keyboard.switch_key')[1])['keyboard.switch_key']
        self.assertEqual((row['value'], row['reason'], row['editable']),
                         (None, 'switch_key_unrecognised', True))

    def test_time_zone_and_automatic_time(self):
        code, reply = self.run_provider('set', 'time.zone', 'Asia/Tokyo')
        self.assertEqual((code, reply['before'], reply['after']), (0, 'Europe/Berlin', 'Asia/Tokyo'))
        self.assertTrue(self.machine()['interactive'])
        code, reply = self.run_provider('set', 'time.automatic', 'false')
        self.assertEqual((code, reply['after']), (0, False), reply)
        self.assertIs(self.machine()['org.freedesktop.timedate1']['NTP'], False)

    def test_locale_language_and_formats_keep_other_variables(self):
        code, reply = self.run_provider('set', 'locale.language', 'de_DE.UTF-8')
        self.assertEqual((code, reply['after']), (0, 'de_DE.UTF-8'), reply)
        self.assertEqual(self.machine()['org.freedesktop.locale1']['Locale'],
                         ['LANG=de_DE.UTF-8', 'LC_MESSAGES=en_US.UTF-8'])
        code, reply = self.run_provider('set', 'locale.formats', 'en_GB.UTF-8')
        self.assertEqual((code, reply['after']), (0, 'en_GB.UTF-8'), reply)
        locale = self.machine()['org.freedesktop.locale1']['Locale']
        self.assertEqual(locale[:2], ['LANG=de_DE.UTF-8', 'LC_MESSAGES=en_US.UTF-8'])
        self.assertEqual(sorted(locale[2:]), sorted(f'{name}=en_GB.UTF-8' for name in (
            'LC_NUMERIC', 'LC_TIME', 'LC_MONETARY', 'LC_MEASUREMENT', 'LC_PAPER')))
        code, reply = self.run_provider('set', 'locale.formats', '')
        self.assertEqual((code, reply['after']), (0, None), reply)
        self.assertEqual(self.machine()['org.freedesktop.locale1']['Locale'],
                         ['LANG=de_DE.UTF-8', 'LC_MESSAGES=en_US.UTF-8'])

    def test_mixed_formats_read_as_mixed(self):
        state = machine_state()
        state['org.freedesktop.locale1']['Locale'] = ['LANG=en_US.UTF-8', 'LC_TIME=de_DE.UTF-8']
        self.state.write_text(json.dumps(state))
        row = self.rows(self.run_provider('get', 'locale.formats')[1])['locale.formats']
        self.assertEqual((row['value'], row['reason']), (None, 'formats_mixed'))

    def test_unchanged_value_calls_nothing(self):
        for key, value in (('time.zone', 'Europe/Berlin'), ('keyboard.switch_key', 'Caps Lock'),
                           ('keyboard.layouts', 'dvorak,ru'), ('time.automatic', 'true')):
            code, reply = self.run_provider('set', key, value)
            self.assertEqual((code, reply['status']), (0, 'unchanged'), reply)
        self.assertEqual(self.calls(), [])

    def test_declared_keys_are_read_only(self):
        self.declare(json.dumps({'schema_version': 1, 'declared': {
            'time.zone': 'time.timeZone', 'keyboard.layouts': 'services.xserver.xkb.layout'}}))
        rows = self.rows(self.run_provider('get')[1])
        for key, option in (('time.zone', 'time.timeZone'),
                            ('keyboard.layouts', 'services.xserver.xkb.layout')):
            self.assertEqual((rows[key]['source'], rows[key]['editable'], rows[key]['declared_by'],
                              rows[key]['reason']),
                             ('declared', False, option, 'declared_by_system_configuration'))
        self.assertEqual(rows['time.zone']['value'], 'Europe/Berlin')
        self.assertTrue(rows['time.automatic']['editable'])
        code, reply = self.run_provider('set', 'time.zone', 'UTC')
        self.assertEqual((code, reply['status'], reply['reason']),
                         (1, 'rejected', 'declared_by_system_configuration'))
        code, reply = self.run_provider('set', 'keyboard.layouts', 'us')
        self.assertEqual(reply['reason'], 'declared_by_system_configuration')
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.machine(), machine_state())

    def test_unreadable_declarations_make_every_key_read_only(self):
        for text in ('{', json.dumps({'schema_version': 2, 'declared': {}}),
                     json.dumps({'schema_version': 1, 'declared': {'hostname': 'x'}}),
                     json.dumps({'schema_version': 1, 'declared': {'time.zone': ''}})):
            self.declare(text)
            rows = self.rows(self.run_provider('get')[1])
            self.assertTrue(all(not row['editable'] and row['reason'] == 'declarations_invalid'
                                for row in rows.values()), rows)
            code, reply = self.run_provider('set', 'time.automatic', 'false')
            self.assertEqual((code, reply['reason']), (1, 'declarations_invalid'))
        self.assertEqual(self.calls(), [])

    def test_invalid_values_are_refused_before_any_call(self):
        for key, value, reason in (
                ('keyboard.layouts', 'us,ru,ua,cz,de', 'invalid_layouts'),
                ('keyboard.layouts', 'us,us', 'invalid_layouts'),
                ('keyboard.layouts', 'US', 'invalid_layouts'),
                ('keyboard.layouts', '', 'invalid_layouts'),
                ('keyboard.layouts', 'us;rm -rf', 'invalid_layouts'),
                ('keyboard.switch_key', 'Ctrl+Shift', 'invalid_switch_key'),
                ('time.zone', '../../etc/passwd', 'unknown_time_zone'),
                ('time.zone', 'Europe/../x', 'unknown_time_zone'),
                ('time.automatic', 'yes', 'invalid_boolean'),
                ('locale.language', 'de_DE.ISO-8859-1', 'invalid_locale'),
                ('locale.language', 'LANG=C', 'invalid_locale'),
                ('locale.formats', 'en_GB', 'invalid_locale'),
                ('locale.language', 'en_US.UTF-8\nLC_ALL=C', 'invalid_value'),
                ('hostname', 'x', 'unknown_setting'),
                ('lock.delay', '300', 'unknown_setting')):
            code, reply = self.run_provider('set', key, value)
            self.assertEqual((code, reply['status'], reply['reason']), (1, 'rejected', reason),
                             (key, value, reply))
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.machine(), machine_state())

    def test_service_refusals_map_to_fixed_reasons(self):
        for method, message, key, value, reason in (
                ('SetTimezone', f'Access denied {SECRET}', 'time.zone', 'UTC', 'authorization_refused'),
                ('SetTimezone', f'Interactive authentication required. {SECRET}', 'time.zone', 'UTC',
                 'authorization_required'),
                ('SetTimezone', f"Invalid or not installed time zone 'Mars/Base' {SECRET}", 'time.zone',
                 'Mars/Base', 'unknown_time_zone'),
                ('SetLocale', f'Locale xx_XX.UTF-8 not installed, refusing. {SECRET}', 'locale.language',
                 'xx_XX.UTF-8', 'locale_not_installed'),
                ('SetX11Keyboard', f'Specified keymap cannot be compiled, refusing as invalid. {SECRET}',
                 'keyboard.layouts', 'zz', 'machine_value_refused')):
            code, reply = self.run_provider('set', key, value, FAKE_BUS_FAIL=f'{method}:{message}')
            self.assertEqual((code, reply['status'], reply['reason']), (1, 'rejected', reason), reply)
        self.assertEqual(self.machine(), machine_state())

    def assertUncertain(self, reply, reason, before, requested, after):
        self.assertEqual((reply['status'], reply['reason'], reply['before'], reply['requested'],
                          reply['after']), ('uncertain', reason, before, requested, after), reply)
        self.assertTrue(reply['recovery'], reply)

    def test_an_unknown_call_failure_is_uncertain_not_refused(self):
        code, reply = self.run_provider('set', 'time.automatic', 'false',
                                        FAKE_BUS_FAIL=f'SetNTP:Something else {SECRET}')
        self.assertEqual(code, 3)
        self.assertUncertain(reply, 'machine_service_unavailable', True, False, True)

    def test_a_call_that_applied_then_timed_out_is_uncertain(self):
        code, reply = self.run_provider('set', 'time.zone', 'Asia/Tokyo',
                                        FAKE_BUS_FAIL_AFTER='SetTimezone:Connection timed out')
        self.assertEqual(code, 3)
        self.assertUncertain(reply, 'machine_service_timeout', 'Europe/Berlin', 'Asia/Tokyo',
                             'Asia/Tokyo')

    def test_a_failed_read_back_keeps_before_and_requested(self):
        code, reply = self.run_provider('set', 'keyboard.layouts', 'us,de',
                                        FAKE_BUS_BREAK_AFTER_CALL='1')
        self.assertEqual(code, 3)
        self.assertUncertain(reply, 'machine_setting_unconfirmed', ['dvorak', 'ru'], ['us', 'de'],
                             None)
        self.assertEqual(reply['settings'][0]['source'], 'unavailable')
        self.assertEqual(self.machine()['org.freedesktop.locale1']['X11Layout'], 'us,de')

    def test_value_the_service_did_not_take_is_unconfirmed(self):
        code, reply = self.run_provider('set', 'time.zone', 'UTC', FAKE_BUS_IGNORE='1')
        self.assertEqual(code, 3)
        self.assertUncertain(reply, 'machine_setting_unconfirmed', 'Europe/Berlin', 'UTC',
                             'Europe/Berlin')

    def test_concurrent_keyboard_changes_keep_both(self):
        # The first change waits in its call (an administrator prompt) while the second starts:
        # the second must read the machine after the first, not resend the old layouts.
        calling = self.dir / 'calling'
        first = subprocess.Popen(
            [sys.executable, '-I', str(PROGRAM), 'set', 'keyboard.layouts', 'us,ru,de', '--json'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**self.env, 'FAKE_BUS_DELAY': '1.5', 'FAKE_BUS_CALLING': str(calling)})
        deadline = time.monotonic() + 20
        while not calling.exists():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.02)
        code, second = self.run_provider('set', 'keyboard.switch_key', 'Alt+Shift')
        out, err = first.communicate(timeout=30)
        self.assertEqual(err, '')
        first = json.loads(out)
        self.assertEqual((first['status'], first['after']), ('applied', ['us', 'ru', 'de']), first)
        self.assertEqual((code, second['status'], second['before']), (0, 'applied', 'Caps Lock'),
                         second)
        locale1 = self.machine()['org.freedesktop.locale1']
        self.assertEqual([locale1[name] for name in ('X11Layout', 'X11Model', 'X11Variant',
                                                    'X11Options')],
                         ['us,ru,de', 'pc105', '', 'terminate:ctrl_alt_bksp,grp:alt_shift_toggle'])

    def test_a_held_lock_refuses_before_reading_or_calling(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as held:
            held.bind('\0emaki-machine-settings')
            code, reply = self.run_provider('set', 'time.zone', 'UTC')
        self.assertEqual((code, reply['status'], reply['reason']),
                         (1, 'rejected', 'machine_settings_busy'))
        self.assertEqual(self.log.read_text(), '')
        self.assertEqual(self.machine(), machine_state())

    def test_a_foreign_keyboard_write_is_not_answered_as_applied(self):
        code, reply = self.run_provider('set', 'keyboard.switch_key', 'Alt+Shift',
                                        FAKE_BUS_FOREIGN='1')
        self.assertEqual(code, 3)
        self.assertUncertain(reply, 'machine_setting_unconfirmed', 'Caps Lock', 'Alt+Shift',
                             'Alt+Shift')

    def test_automatic_time_without_ntp_service_is_read_only(self):
        state = machine_state()
        state['org.freedesktop.timedate1']['CanNTP'] = False
        self.state.write_text(json.dumps(state))
        row = self.rows(self.run_provider('get', 'time.automatic')[1])['time.automatic']
        self.assertEqual((row['editable'], row['reason']), (False, 'automatic_time_unavailable'))
        code, reply = self.run_provider('set', 'time.automatic', 'false')
        self.assertEqual((code, reply['reason']), (1, 'automatic_time_unavailable'))
        self.assertEqual(self.calls(), [])

    def test_missing_bus_reports_unavailable_rows(self):
        (self.bin / 'busctl').unlink()
        code, reply = self.run_provider('get', PATH=str(self.bin))
        self.assertEqual(code, 0)
        for row in reply['settings']:
            self.assertEqual((row['source'], row['editable'], row['value'], row['reason']),
                             ('unavailable', False, None, 'machine_service_unavailable'))
        code, reply = self.run_provider('set', 'time.zone', 'UTC', PATH=str(self.bin))
        self.assertEqual((code, reply['reason']), (1, 'machine_service_unavailable'))

    def test_usage_errors(self):
        for args in ((), ('frobnicate',), ('set', 'time.zone'), ('capabilities', 'x'),
                     ('set', 'time.zone', 'UTC', 'extra')):
            code, reply = self.run_provider(*args)
            self.assertEqual((code, reply['reason']), (2, 'usage'), args)

    def test_program_writes_no_file_itself(self):
        text = PROGRAM.read_text()
        for needle in ("open(", "write_text", "write_bytes", "os.replace", "shutil", "sudo", "pkexec"):
            if needle == 'open(':
                self.assertNotIn('.open(', text)
                continue
            self.assertNotIn(needle, text)


MUTANTS = (
    ("'false', 'true')", "'true', 'true')", 'test_layouts_keep_model_options_and_console_keymap'),
    ("keyboard_call(machine, layout, variant, options):\n    # convert=false",
     "keyboard_call(machine, layout, variant, options):\n    options = ''\n    # convert=false",
     'test_layouts_keep_model_options_and_console_keymap'),
    ("if not option.startswith('grp:')]", "if True]", 'test_switch_key_replaces_only_group_toggles'),
    ('    if key in declared:\n', '    if False:\n', 'test_declared_keys_are_read_only'),
    ("    if not before['editable']:", "    if False:", 'test_declared_keys_are_read_only'),
    ("        return {key: None for key in KEYS}, 'declarations_invalid'\n    return declared",
     "        return {}, None\n    return declared", 'test_unreadable_declarations_make_every_key_read_only'),
    ("    if before['value'] == target:", "    if False:", 'test_unchanged_value_calls_nothing'),
    ("    if failure or after['value'] != target or foreign:", "    if failure or foreign:", 'test_value_the_service_did_not_take_is_unconfirmed'),
    ("'access denied', ", "", 'test_service_refusals_map_to_fixed_reasons'),
    ("--allow-interactive-authorization=yes", "--allow-interactive-authorization=no",
     'test_time_zone_and_automatic_time'),
    ("if not 1 <= len(items) <= 4", "if not 1 <= len(items) <= 5", 'test_invalid_values_are_refused_before_any_call'),
    ("ZONE = re.compile(r'[A-Za-z0-9_+-]{1,32}(?:/[A-Za-z0-9_+-]{1,32}){0,3}')", "ZONE = re.compile(r'[A-Za-z0-9_./+-]+')",
     'test_invalid_values_are_refused_before_any_call'),
    ("            for name in FORMATS:\n                current.pop(name, None)\n",
     "            for name in FORMATS:\n", 'test_locale_language_and_formats_keep_other_variables'),
    ("if key == 'time.automatic' and not self.time()['CanNTP']:", "if False:",
     'test_automatic_time_without_ntp_service_is_read_only'),
    ("            held.bind(LOCK)\n", "", 'test_concurrent_keyboard_changes_keep_both'),
    ("            held.bind(LOCK)\n", "", 'test_a_held_lock_refuses_before_reading_or_calling'),
    ("foreign = machine.sent is not None and check.keyboard() != machine.sent",
     "foreign = False", 'test_a_foreign_keyboard_write_is_not_answered_as_applied'),
    ("        if not refusal.sent:\n", "        if True:\n",
     'test_a_call_that_applied_then_timed_out_is_uncertain'),
    ("sent=reason not in REFUSED)", "sent=False)", 'test_an_unknown_call_failure_is_uncertain_not_refused'),
    ("sent=reason not in REFUSED)", "sent=True)", 'test_service_refusals_map_to_fixed_reasons'),
    ("(?:\\(([A-Za-z0-9_-]{1,32})\\))?", "(?:\\(([a-z0-9_-]{1,32})\\))?", 'test_layout_variants_round_trip'),
    ("                return result, 'layouts_unrecognised'", "                return result, None",
     'test_layouts_it_could_not_set_back_are_marked'),
)


def mutants():
    source = PROGRAM.read_text()
    failed = []
    for old, new, test in MUTANTS:
        if source.count(old) != 1:
            failed.append(f'mutant anchor not unique: {old!r}')
            continue
        with tempfile.TemporaryDirectory(prefix='emaki-machine-mutant-') as directory:
            mutant = Path(directory) / 'emaki-machine-settings'
            mutant.write_text(source.replace(old, new))
            result = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), '--no-mutants',
                                     f'Provider.{test}'], capture_output=True, text=True, timeout=300,
                                    env={**os.environ, 'EMAKI_MACHINE_SETTINGS_UNDER_TEST': str(mutant)})
            if result.returncode == 0:
                failed.append(f'mutant survived {test}: {old!r} -> {new!r}')
    for line in failed:
        print(line, file=sys.stderr)
    print(f'machine-settings mutants: {len(MUTANTS) - len(failed)}/{len(MUTANTS)} killed')
    return not failed


if __name__ == '__main__':
    run_mutants = '--no-mutants' not in sys.argv
    argv = [arg for arg in sys.argv if arg != '--no-mutants']
    program = unittest.main(argv=argv, exit=False)
    if not program.result.wasSuccessful():
        sys.exit(1)
    if run_mutants and not mutants():
        sys.exit(1)
