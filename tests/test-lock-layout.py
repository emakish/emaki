#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Private fake-compositor checks for lock layout selection and ownership."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from runtime_fixture import short_runtime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tests/fixtures'))
from niri_server import Server

HELPER = ROOT / 'shell/helpers/lock-environment.py'
RULES = '''! layout
  us English (US)
  ru Russian
  de German
! variant
  tib             cn: Tibetan
  tib_asciinum    cn: Tibetan (with ASCII numerals)
  colemak         us: English (Colemak)
  dvorak          us: English (Dvorak)
  chr             us: Cherokee
  rus             us: Russian (US, phonetic)
  ru              de: Russian (Germany, phonetic)
  kana            jp: Japanese (Kana)
  cyrillic        me: Montenegrin (Cyrillic)
  cyrillicyz      me: Montenegrin (Cyrillic, ZE and ZHE swapped)
  cyrillicalternatequotes me: Montenegrin (Cyrillic, with guillemets)
'''


def module(path):
    spec = importlib.util.spec_from_file_location(path.stem.replace('-', '_'), path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.directory = short_runtime()
        self.work = Path(self.directory.name)
        self.rules = self.work / 'evdev.lst'
        self.rules.write_text(RULES)
        try:
            self.server = Server(self.work / 'niri.sock')
        except PermissionError:
            self.directory.cleanup()
            if os.environ.get('EMAKI_TEST_SANDBOX') == '1':
                self.skipTest('sandbox prohibits Unix listener; fake request tests run separately')
            raise
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.children = []
        self.env = dict(os.environ, NIRI_SOCKET=str(self.work / 'niri.sock'),
                        EMAKI_XKB_RULES=str(self.rules), PYTHONDONTWRITEBYTECODE='1')

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=3)
        self.server.stop.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.assertEqual(self.server.errors, [])
        self.directory.cleanup()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.01)
        self.fail('Timed out waiting for fake compositor state')

    def start(self, names, active, helper=HELPER):
        with self.server.lock:
            self.server.layouts = dict(names=names, current_idx=active)
        child = subprocess.Popen([sys.executable, '-B', str(helper), '--switch-for-lock'],
                                 env=self.env, stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.children.append(child)
        self.wait_for(lambda: self.server.streams or child.poll() is not None)
        return child

    def finish(self, child, restore=True):
        out, err = child.communicate('restore\n' if restore else '', timeout=3)
        self.assertEqual(child.returncode, 0, (out, err))

    def indices(self):
        return [action['SwitchLayout']['layout']['Index'] for action in self.server.actions]

    def switch_manually(self, index):
        with self.server.lock:
            self.server.layouts['current_idx'] = index
            self.server.broadcast({'KeyboardLayoutSwitched': {'idx': index}})

    def round_trip(self, helper=HELPER, names=None, active=1, target=0):
        child = self.start(names or ['English (US)', 'Russian'], active, helper)
        self.wait_for(lambda: bool(self.server.actions) or child.poll() is not None)
        self.assertEqual(self.indices(), [target])
        self.finish(child)
        self.assertEqual(self.indices(), [target, active])
        self.assertEqual(self.server.layouts['current_idx'], active)

    def test_switch_and_restore(self):
        self.round_trip()

    def test_first_latin_is_not_index_zero(self):
        self.round_trip(names=['Russian', 'German', 'English (US)'], active=0, target=1)

    def test_already_latin_is_unchanged(self):
        child = self.start(['English (US)', 'German', 'Russian'], 1)
        self.finish(child)
        self.assertEqual(self.indices(), [])
        self.assertEqual(self.server.layouts['current_idx'], 1)

    def test_no_latin_is_unchanged(self):
        child = self.start(['Russian'], 0)
        self.finish(child)
        self.assertEqual(self.indices(), [])

    def test_variant_uses_installer_knowledge(self):
        self.round_trip(names=['Russian', 'English (Dvorak)'], active=0, target=1)

    def test_person_changes_layout(self):
        child = self.start(['English (US)', 'Russian', 'German'], 1)
        self.wait_for(lambda: self.indices() == [0])
        self.switch_manually(2)
        self.finish(child)
        self.assertEqual(self.indices(), [0])
        self.assertEqual(self.server.layouts['current_idx'], 2)

    def test_person_changes_away_and_back(self):
        child = self.start(['English (US)', 'Russian'], 1)
        self.wait_for(lambda: self.indices() == [0])
        self.switch_manually(1)
        self.switch_manually(0)
        self.finish(child)
        self.assertEqual(self.indices(), [0])
        self.assertEqual(self.server.layouts['current_idx'], 0)

    def test_replaced_configuration_is_not_restored(self):
        child = self.start(['English (US)', 'Russian'], 1)
        self.wait_for(lambda: self.indices() == [0])
        with self.server.lock:
            self.server.layouts = dict(names=['German', 'English (US)'], current_idx=0)
            self.server.broadcast({'KeyboardLayoutsChanged': {'keyboard_layouts': self.server.layouts}})
        self.finish(child)
        self.assertEqual(self.indices(), [0])

    def test_eof_does_not_restore(self):
        child = self.start(['English (US)', 'Russian'], 1)
        self.wait_for(lambda: self.indices() == [0])
        self.finish(child, restore=False)
        self.assertEqual(self.indices(), [0])

    def test_real_lock_environment_updates(self):
        qml = self.work / 'qml'
        (qml / 'helpers').mkdir(parents=True)
        shutil.copy(HELPER, qml / 'helpers/lock-environment.py')
        shutil.copy(ROOT / 'shell/LockEnvironment.qml', qml / 'LockEnvironment.qml')
        shutil.copy(ROOT / 'tests/fixtures/LockLayoutTest.qml', qml / 'check.qml')
        with self.server.lock:
            self.server.layouts = dict(names=['English (US)', 'Russian'], current_idx=1)
        env = dict(self.env, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QML_DISABLE_DISK_CACHE='1', XDG_RUNTIME_DIR=str(self.work),
                   XDG_CONFIG_HOME=str(self.work / 'config'),
                   XDG_CACHE_HOME=str(self.work / 'cache'),
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(self.work / 'none'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(self.work / 'none-system'))
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_LOGGING_RULES'):
            env.pop(key, None)
        log = self.work / 'qml.log'
        with log.open('w') as output:
            qml_child = subprocess.Popen(['qs', '-p', str(qml / 'check.qml'), '--no-color'],
                                         env=env, stdout=output, stderr=subprocess.STDOUT)
            self.children.append(qml_child)
            self.wait_for(lambda: 'LOCK_LAYOUT_READY' in log.read_text())
            child = self.start(['English (US)', 'Russian'], 1)
            self.wait_for(lambda: self.indices() == [0])
            qml_child.wait(timeout=6)
        self.assertIn('LOCK_LAYOUT_PASS', log.read_text())
        self.assertNotIn('LOCK_LAYOUT_TIMEOUT', log.read_text())
        self.finish(child)
        self.assertEqual(self.indices(), [0, 1])



class LeaseTests(unittest.TestCase):
    """Run policy and mutation checks even when Unix listeners are prohibited."""
    def setUp(self):
        self.helper = module(HELPER)

    def scenario(self, names, active, events=(), helper=None, snapshot=None):
        helper = helper or self.helper
        state = dict(names=list(names), current_idx=active)
        actions = []
        def request(path, value):
            self.assertEqual(path, 'fixture-only')
            if value == 'KeyboardLayouts':
                return {'KeyboardLayouts': dict(snapshot or state)}
            index = value['Action']['SwitchLayout']['layout']['Index']
            actions.append(index)
            state['current_idx'] = index
            return 'Handled'
        with patch.object(helper, 'request', request), patch.object(helper.Path, 'read_text', return_value=RULES):
            lease = helper.LayoutLease('fixture-only')
            lease.begin(dict(names=list(names), current_idx=active))
            if actions:
                lease.observe({'KeyboardLayoutSwitched': {'idx': state['current_idx']}})
            for event in events:
                if isinstance(event, int):
                    state['current_idx'] = event
                    lease.observe({'KeyboardLayoutSwitched': {'idx': event}})
                else:
                    lease.observe(event)
            lease.restore()
        return actions, state['current_idx']

    def test_switch_restore_and_nonzero_latin(self):
        self.assertEqual(self.scenario(['English (US)', 'Russian'], 1), ([0, 1], 1))
        self.assertEqual(self.scenario(['Russian', 'German', 'English (US)'], 0), ([1, 0], 0))

    def test_already_latin_and_ru_only(self):
        self.assertEqual(self.scenario(['English (US)', 'German', 'Russian'], 1), ([], 1))
        self.assertEqual(self.scenario(['Russian'], 0), ([], 0))

    def test_non_latin_variants_never_inherit_the_base_layout(self):
        for name in ('Russian (US, phonetic)', 'Cherokee', 'Tibetan',
                     'Tibetan (with ASCII numerals)', 'Japanese (Kana)',
                     'Montenegrin (Cyrillic)', 'Montenegrin (Cyrillic, ZE and ZHE swapped)',
                     'Montenegrin (Cyrillic, with guillemets)', 'Russian (Germany, phonetic)',
                     'Unknown layout'):
            with self.subTest(name=name):
                self.assertEqual(self.scenario(['English (US)', name], 1), ([0, 1], 1))
                self.assertEqual(self.scenario(['Russian', name, 'English (US)'], 0), ([2, 0], 0))
                self.assertEqual(self.scenario(['Russian', name], 0), ([], 0))

    def test_variant_uses_installer_knowledge(self):
        for name in ('English (Dvorak)', 'English (Colemak)'):
            with self.subTest(name=name):
                self.assertEqual(self.scenario(['Russian', name], 0), ([1, 0], 0))
                self.assertEqual(self.scenario(['English (US)', name], 1), ([], 1))

    def test_variant_display_codes_are_unchanged(self):
        with patch.object(self.helper.Path, 'read_text', return_value=RULES):
            codes = self.helper.layout_codes()
        self.assertEqual(codes['Russian (US, phonetic)'], 'US')
        self.assertEqual(codes['English (Dvorak)'], 'US')
        self.assertEqual(codes['Japanese (Kana)'], 'JP')

    def test_person_choice_and_away_back_are_kept(self):
        self.assertEqual(self.scenario(['English (US)', 'Russian', 'German'], 1, [2]), ([0], 2))
        self.assertEqual(self.scenario(['English (US)', 'Russian'], 1, [1, 0]), ([0], 0))

    def test_reconfiguration_cancels_restore(self):
        self.assertEqual(self.scenario(['English (US)', 'Russian'], 1,
            [{'KeyboardLayoutsChanged': {'keyboard_layouts': {'names': ['German'], 'current_idx': 0}}}]), ([0], 0))

    def test_snapshot_race_cancels_switch(self):
        self.assertEqual(self.scenario(['English (US)', 'Russian'], 1,
            snapshot=dict(names=['English (US)', 'Russian'], current_idx=0)), ([], 1))

    def test_controller_drains_events_and_requires_restore_command(self):
        for command, manual, disconnected, expected in (
                (b'restore\n', [], False, [0, 1]),
                (b'restore\n', [1, 0], False, [0]),
                (b'', [], False, [0]),
                (b'restore\n', [], True, [0])):
            with self.subTest(command=command, manual=manual, disconnected=disconnected):
                state = dict(names=['English (US)', 'Russian'], current_idx=1)
                actions = []
                queued = [[{'KeyboardLayoutsChanged': {'keyboard_layouts': dict(state)}}]]
                class Stream:
                    closed = False
                    def close(self):
                        self.closed = True
                class Events:
                    stream = Stream()
                    def read(self):
                        batch = queued.pop(0)
                        if batch is None:
                            raise OSError('disconnected fixture')
                        return batch
                def request(path, value):
                    if value == 'KeyboardLayouts':
                        return {'KeyboardLayouts': dict(state)}
                    index = value['Action']['SwitchLayout']['layout']['Index']
                    actions.append(index)
                    state['current_idx'] = index
                    if len(actions) == 1:
                        queued.append([{'KeyboardLayoutSwitched': {'idx': i}} for i in [index, *manual]])
                        if manual:
                            state['current_idx'] = manual[-1]
                        if disconnected:
                            queued.append(None)
                    return 'Handled'
                def select_ready(readers, writers, errors, timeout):
                    return ([Events.stream] if queued else [self.helper.sys.stdin], [], [])
                read_fd, write_fd = os.pipe()
                os.write(write_fd, command)
                os.close(write_fd)
                with os.fdopen(read_fd) as commands, patch.dict(os.environ, NIRI_SOCKET='fixture-only'), \
                        patch.object(self.helper, 'LayoutEvents', return_value=Events()), \
                        patch.object(self.helper, 'request', request), \
                        patch.object(self.helper.Path, 'read_text', return_value=RULES), \
                        patch.object(self.helper.select, 'select', side_effect=select_ready), \
                        patch.object(self.helper.sys, 'stdin', commands):
                    if disconnected:
                        with self.assertRaises(OSError):
                            self.helper.switch_for_lock()
                    else:
                        self.helper.switch_for_lock()
                self.assertEqual(actions, expected)
                self.assertTrue(Events.stream.closed)

    def test_installer_lists_stay_in_sync(self):
        installer = module(ROOT / 'installer/emaki_installer/latin_layouts.py')
        self.assertEqual(self.helper.LATIN_LAYOUTS, installer.LATIN_LAYOUTS)
        self.assertEqual(self.helper.LATIN_VARIANTS, installer.LATIN_VARIANTS)

    def test_late_helper_does_not_switch_after_owner_finishes(self):
        for command in (b'restore\n', b''):
            read_fd, write_fd = os.pipe()
            os.write(write_fd, command)
            os.close(write_fd)
            events = Mock()
            with os.fdopen(read_fd) as commands, patch.dict(os.environ, NIRI_SOCKET='fixture-only'), \
                    patch.object(self.helper, 'LayoutEvents', return_value=events), \
                    patch.object(self.helper, 'request') as request, \
                    patch.object(self.helper.select, 'select', return_value=([events.stream, commands], [], [])), \
                    patch.object(self.helper.sys, 'stdin', commands):
                self.helper.switch_for_lock()
            request.assert_not_called()
            events.read.assert_not_called()
            events.stream.close.assert_called_once_with()

    def test_event_reader_handles_split_lines_and_disconnect(self):
        events = self.helper.LayoutEvents.__new__(self.helper.LayoutEvents)
        events.stream, events.buffer = Mock(), b''
        events.stream.recv.side_effect = [
            b'{"Ok":"Handled"}\n{"KeyboardLayoutSwitched":',
            b'{"idx":0}}\n{"KeyboardLayoutSwitched":{"idx":1}}\n', b'']
        self.assertEqual(events.read(), [])
        self.assertEqual(events.read(), [
            {'KeyboardLayoutSwitched': {'idx': 0}},
            {'KeyboardLayoutSwitched': {'idx': 1}}])
        with self.assertRaises(OSError):
            events.read()

    def test_window_events_are_discarded_without_output(self):
        events = self.helper.LayoutEvents.__new__(self.helper.LayoutEvents)
        events.stream, events.buffer = Mock(), b''
        events.stream.recv.return_value = (
            b'{"WindowsChanged":{"windows":[{"title":"private title",'
            b'"app_id":"private app"}]}}\n'
            b'{"WindowOpenedOrChanged":{"window":{"title":"private title",'
            b'"app_id":"private app"}}}\n'
            b'{"KeyboardLayoutSwitched":{"idx":1}}\n')
        with patch('builtins.print') as output:
            self.assertEqual(events.read(), [{'KeyboardLayoutSwitched': {'idx': 1}}])
        output.assert_not_called()
        self.assertEqual(events.buffer, b'')

    def mutant(self, old, new):
        source = HELPER.read_text()
        self.assertEqual(source.count(old), 1, 'mutation anchor changed')
        with tempfile.TemporaryDirectory(prefix='lock-layout-mutant-') as directory:
            path = Path(directory) / 'lock-environment.py'
            path.write_text(source.replace(old, new))
            return module(path)

    def test_blind_index_zero_mutant_is_rejected(self):
        mutant = self.mutant('list(names), index, latin[0]', 'list(names), index, 0')
        with self.assertRaises(AssertionError):
            self.assertEqual(self.scenario(['Russian', 'German', 'English (US)'], 0, helper=mutant), ([1, 0], 0))

    def test_never_restore_mutant_is_rejected(self):
        mutant = self.mutant('def restore(self):', 'def restore(self):\n        return')
        with self.assertRaises(AssertionError):
            self.assertEqual(self.scenario(['English (US)', 'Russian'], 1, helper=mutant), ([0, 1], 1))

    def test_missing_latin_variants_mutant_is_rejected(self):
        mutant = self.mutant("LATIN_VARIANTS = frozenset(('dvorak', 'colemak'))",
                             'LATIN_VARIANTS = frozenset()')
        with self.assertRaises(AssertionError):
            self.assertEqual(self.scenario(['Russian', 'English (Dvorak)'], 0, helper=mutant), ([1, 0], 0))


if __name__ == '__main__':
    unittest.main()
