#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Display transactions against private output state and real KDL validation."""
import importlib.util
import copy
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
HELPER = Path(os.environ.get('DISPLAY_TEST_HELPER', ROOT / 'shell/helpers/displays.py'))
spec = importlib.util.spec_from_file_location('display_backend', HELPER)
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)
VALIDATOR = shutil.which('niri')


class DisplaysTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='display-tests-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        bindir = self.root / 'bin'; bindir.mkdir()
        shutil.copy(ROOT / 'tests/fixtures/displays-niri.py', bindir / 'niri')
        (bindir / 'niri').chmod(0o700)
        self.env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ['PATH'],
                        DISPLAY_TEST_ROOT=str(self.root), DISPLAY_TEST_VALIDATOR=VALIDATOR or '',
                        XDG_CONFIG_HOME=str(self.root / 'different-config'),
                        XDG_STATE_HOME=str(self.root / 'state'), HOME=str(self.root / 'home'))
        for name in ('NIRI_SOCKET', 'WAYLAND_DISPLAY', 'DISPLAY'):
            self.env.pop(name, None)
        socket = self.root / 'compositor-identity'
        socket.touch()
        self.env['NIRI_SOCKET'] = str(socket)
        self.patch = patch.dict(os.environ, self.env, clear=True); self.patch.start()
        self.addCleanup(self.patch.stop)
        self.flags()
        self.initial = {name: dict(make='Example', model=name, modes=[dict(width=1920, height=1080, refresh_rate=60000, is_preferred=True), dict(width=1280, height=720, refresh_rate=59940, is_preferred=False)], current_mode=0, logical=dict(x=x, y=0, width=1920, height=1080, scale=1.0, transform='Normal')) for name, x in [('DP-1', 0), ('DP-2', 1920)]}
        self.write_outputs(self.initial)
        (self.root / 'inherited').write_text(json.dumps(self.initial))
        (self.root / 'focus').write_text('DP-1')
        self.controller = backend.Displays()
        self.addCleanup(self.controller.release)

    def flags(self, **values):
        (self.root / 'flags').write_text(json.dumps(values))

    def write_outputs(self, value):
        (self.root / 'outputs').write_text(json.dumps(value))

    def state(self):
        return json.loads((self.root / 'outputs').read_text())

    def change(self, key, value, name='DP-1'):
        return self.controller.request(dict(op='set', output=name, key=key, value=value))

    def client(self):
        process = subprocess.Popen([sys.executable, '-B', str(HELPER)], env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.communicate()
        self.addCleanup(cleanup)
        return process

    def receive(self, process, timeout=8):
        self.assertTrue(select.select([process.stdout], [], [], timeout)[0], 'helper response timed out')
        line = process.stdout.readline()
        self.assertTrue(line, 'helper terminated before responding')
        return json.loads(line)

    def send(self, process, request):
        process.stdin.write(json.dumps(request) + '\n'); process.stdin.flush()
        return self.receive(process)

    def test_read_failure_and_topology_change_use_specific_messages(self):
        with patch.object(backend.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', '')):
            with self.assertRaisesRegex(backend.Failure, 'information could not be read'):
                backend.outputs()
        before = backend.outputs()
        with self.assertRaisesRegex(backend.Failure, 'connected displays changed'):
            backend.reflow_positions(before, before[:1], 'DP-1')

    def test_identical_models_include_the_connector_in_their_description(self):
        initial = copy.deepcopy(self.initial)
        for item in initial.values():
            item['model'] = 'Monitor'
        self.write_outputs(initial)
        self.assertEqual([item['description'] for item in backend.outputs()],
                         ['Example Monitor (DP-1)', 'Example Monitor (DP-2)'])

    def test_process_apply_keep_and_eof_rollback(self):
        process = self.client()
        self.assertEqual(self.receive(process)['state'], 'ready')
        reply = self.send(process, dict(op='set', output='DP-1', key='mode', value='1280x720@59.940'))
        self.assertEqual(reply['state'], 'pending'); self.assertEqual(reply['seconds'], 15)
        self.assertFalse(self.controller.path.exists())
        self.assertEqual(self.send(process, dict(op='keep'))['state'], 'ready')
        saved = self.controller.path.read_text()
        confirmed = self.state()
        self.assertIn('1280x720@59.940', saved)
        self.assertEqual(self.send(process, dict(op='set', output='DP-1', key='scale', value=1.5))['state'], 'pending')
        process.stdin.close(); process.stdin = None
        process.wait(timeout=8)
        self.assertEqual(self.state()['DP-1']['logical']['scale'], 1.0)
        self.assertEqual(self.state(), confirmed)
        self.assertEqual(self.controller.path.read_text(), saved)
        self.assertFalse(self.controller.journal.exists())

    def test_process_reverts_after_fifteen_seconds_without_input(self):
        process = self.client()
        self.receive(process)
        started = time.monotonic()
        reply = self.send(process, dict(op='set', output='DP-1', key='scale', value=1.5))
        self.assertEqual(reply['seconds'], 15)
        self.assertEqual(self.state()['DP-1']['logical']['scale'], 1.5)
        reply = self.receive(process, timeout=19)
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 14)
        self.assertLess(elapsed, 19)
        self.assertFalse(reply['pending'])
        self.assertEqual(self.state()['DP-1']['logical']['scale'], 1.0)
        self.assertFalse(self.controller.path.exists())
        self.assertFalse(self.controller.journal.exists())

    def test_scale_keep_custom_root_and_real_validation(self):
        self.change('scale', 1.25)
        self.controller.request(dict(op='keep'))
        saved = backend.Displays().document['outputs']['DP-1']
        self.assertEqual(saved['scale'], 1.25)
        self.assertEqual(saved['mode'], '1920x1080@60.000')
        self.assertEqual(saved['rotation'], 'normal')
        self.assertEqual(saved['position'], dict(x=0, y=0))
        self.assertTrue(str(self.controller.path).startswith(str(self.root / 'different-config')))
        result = subprocess.run([VALIDATOR, 'validate', '--config', str(self.controller.path)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_rotation_reset_restores_inherited_block(self):
        inherited = copy.deepcopy(self.initial)
        inherited['DP-1']['logical'].update(transform='90', width=1080, height=1920)
        inherited['DP-2']['logical']['x'] = 1080
        self.write_outputs(inherited)
        (self.root / 'inherited').write_text(json.dumps(inherited))
        self.change('rotation', '180')
        self.controller.request(dict(op='keep'))
        self.controller.request(dict(op='reset', output='DP-1', key='rotation'))
        self.assertEqual(self.state()['DP-1']['logical']['transform'], '90')
        self.controller.request(dict(op='keep'))
        self.assertNotIn('DP-1', self.controller.document['outputs'])
        backend.run(['msg', 'action', 'load-config-file'])
        self.assertEqual(self.state()['DP-1'], inherited['DP-1'])

    def test_reset_keeps_other_overrides_until_all_match_inherited(self):
        self.change('scale', 1.25)
        self.controller.request(dict(op='keep'))
        self.change('rotation', '180')
        self.controller.request(dict(op='keep'))
        self.controller.request(dict(op='reset', output='DP-1', key='scale'))
        self.controller.request(dict(op='keep'))
        self.assertEqual(self.controller.document['outputs']['DP-1']['rotation'], '180')
        self.assertEqual(self.state()['DP-1']['logical']['scale'], 1)
        self.controller.request(dict(op='reset', output='DP-1', key='rotation'))
        self.controller.request(dict(op='keep'))
        self.assertNotIn('DP-1', self.controller.document['outputs'])

    def test_interrupted_inheritance_probe_restores_file_and_snapshot(self):
        self.change('scale', 1.25)
        self.controller.request(dict(op='keep'))
        snapshot = backend.outputs()
        original = self.controller.path.read_text()
        candidate = backend.render(dict(version=1, outputs={}, main=None))
        self.controller.path.write_text(candidate)
        self.controller.journal.write_text(json.dumps(dict(
            snapshot=snapshot, original=original, candidate=candidate,
            compositor=backend.compositor_identity(), preview=True)))
        backend.run(['msg', 'action', 'load-config-file'])
        recovery = backend.Displays()
        recovery.recover()
        self.assertEqual(self.controller.path.read_text(), original)
        self.assertEqual(recovery.document, self.controller.document)
        self.assertEqual(backend.outputs(), snapshot)
        self.assertFalse(self.controller.journal.exists())

    def test_reset_reads_updated_personal_rotation(self):
        self.change('rotation', '180')
        self.controller.request(dict(op='keep'))
        inherited = copy.deepcopy(self.initial)
        inherited['DP-1']['logical'].update(transform='90', width=1080, height=1920)
        (self.root / 'inherited').write_text(json.dumps(inherited))
        self.controller.request(dict(op='reset', output='DP-1', key='rotation'))
        self.controller.request(dict(op='keep'))
        self.assertEqual(self.state()['DP-1']['logical']['transform'], '90')
        self.assertNotIn('DP-1', self.controller.document['outputs'])

    def test_probe_failure_restores_original_and_keeps_display_on(self):
        self.change('rotation', '180')
        self.controller.request(dict(op='keep'))
        original, snapshot = self.controller.path.read_text(), self.state()
        inherited = copy.deepcopy(self.initial)
        inherited['DP-1'].update(current_mode=None, logical=None)
        (self.root / 'inherited').write_text(json.dumps(inherited))
        with self.assertRaisesRegex(backend.Failure, 'does not enable'):
            self.controller.request(dict(op='reset', output='DP-1', key='rotation'))
        self.assertEqual(self.state(), snapshot)
        self.assertEqual(self.controller.path.read_text(), original)
        self.assertFalse(self.controller.journal.exists())

    def test_probe_recovery_after_restart_restores_file_without_old_snapshot(self):
        original = backend.render(dict(version=1, outputs={'DP-1': dict(scale=1.25)}, main=None))
        candidate = backend.render(dict(version=1, outputs={}, main=None))
        self.controller.path.parent.mkdir(parents=True)
        self.controller.path.write_text(candidate)
        self.controller.state_dir.mkdir(parents=True)
        self.controller.journal.write_text(json.dumps(dict(
            snapshot=[], original=original, candidate=candidate,
            compositor={'different': 'session'}, preview=True)))
        self.controller.recover()
        self.assertEqual(self.controller.path.read_text(), original)
        self.assertEqual(self.state()['DP-1']['logical']['scale'], 1.25)
        self.assertFalse(self.controller.journal.exists())

    def test_reload_requires_a_fresh_completion_event(self):
        with patch.object(backend, 'run', return_value=''):
            with self.assertRaisesRegex(backend.Failure, 'reload was not confirmed'):
                backend.reload_config()

    def test_probe_recovery_does_not_overwrite_external_changes(self):
        candidate = backend.render(dict(version=1, outputs={}, main=None))
        foreign = backend.render(dict(version=1, outputs={'DP-2': dict(scale=2)}, main=None))
        self.controller.path.parent.mkdir(parents=True)
        self.controller.path.write_text(foreign)
        self.controller.state_dir.mkdir(parents=True)
        self.controller.journal.write_text(json.dumps(dict(
            snapshot=[], original=None, candidate=candidate,
            compositor=backend.compositor_identity(), preview=True)))
        with self.assertRaisesRegex(backend.Failure, 'changed elsewhere'):
            self.controller.recover()
        self.assertEqual(self.controller.path.read_text(), foreign)
        self.assertTrue(self.controller.journal.exists())

    def test_resize_reflows_previously_pinned_neighbor_and_saves_layout(self):
        self.change('mode', '1920x1080@60.000', 'DP-2')
        self.controller.request(dict(op='keep'))
        self.assertEqual(self.controller.document['outputs']['DP-2']['position']['x'], 1920)
        self.change('scale', 1.25)
        current = self.state()
        self.assertEqual(current['DP-1']['logical']['width'], 1536)
        self.assertEqual(current['DP-2']['logical']['x'], 1536)
        self.controller.request(dict(op='keep'))
        saved = backend.Displays().document['outputs']['DP-2']
        self.assertEqual(saved, dict(mode='1920x1080@60.000', scale=1,
                                     rotation='normal', position=dict(x=1536, y=0)))

    def test_mode_rotation_and_reset_reflow_using_logical_sizes(self):
        for key, value, expected in [('mode', '1280x720@59.940', 1280),
                                     ('rotation', '90', 1080), ('scale', 1.25, 1536)]:
            with self.subTest(key=key):
                self.change(key, value)
                self.assertEqual(self.state()['DP-2']['logical']['x'], expected)
                self.controller.request(dict(op='revert'))
                self.assertEqual(self.state(), self.initial)
        self.change('scale', 1.25)
        self.controller.request(dict(op='keep'))
        self.controller.request(dict(op='reset', output='DP-1', key='scale'))
        self.assertEqual(self.state()['DP-2']['logical']['x'], 1920)
        self.controller.request(dict(op='keep'))
        self.assertNotIn('DP-1', self.controller.document['outputs'])

    def test_reflow_preserves_inherited_neighbor_values_and_offsets(self):
        initial = copy.deepcopy(self.initial)
        initial['DP-1']['logical'].update(x=-1920, y=-200)
        initial['DP-2']['logical'].update(x=0, y=100, scale=2, transform='90', width=540, height=960)
        self.write_outputs(initial)
        self.change('scale', 1.25)
        self.assertEqual(self.state()['DP-2']['logical']['x'], -384)
        self.assertEqual(self.state()['DP-2']['logical']['y'], 100)
        self.controller.request(dict(op='keep'))
        saved = self.controller.document['outputs']['DP-2']
        self.assertEqual(saved, dict(position=dict(x=-384, y=100)))

    def test_reset_only_override_does_not_recapture_removed_scale(self):
        self.controller.path.parent.mkdir(parents=True)
        self.controller.path.write_text(backend.render(dict(version=1, outputs={'DP-1': dict(scale=1.25)}, main=None)))
        current = copy.deepcopy(self.initial)
        current['DP-1']['logical'].update(scale=1.25, width=1536, height=864)
        current['DP-2']['logical']['x'] = 1536
        self.write_outputs(current)
        self.controller.request(dict(op='reset', output='DP-1', key='scale'))
        self.assertEqual(self.state()['DP-2']['logical']['x'], 1920)
        self.controller.request(dict(op='keep'))
        self.assertNotIn('DP-1', self.controller.document['outputs'])

    def test_vertical_chain_reflows_without_moving_separate_screen(self):
        initial = copy.deepcopy(self.initial)
        initial['DP-2']['logical'].update(x=100, y=1080)
        initial['DP-3'] = copy.deepcopy(initial['DP-2'])
        initial['DP-3']['logical'].update(x=100, y=2160)
        initial['DP-4'] = copy.deepcopy(initial['DP-2'])
        initial['DP-4']['logical'].update(x=10000, y=10000)
        self.write_outputs(initial)
        self.change('rotation', '90')
        self.assertEqual(self.state()['DP-2']['logical']['y'], 1920)
        self.assertEqual(self.state()['DP-3']['logical']['y'], 3000)
        self.assertEqual(self.state()['DP-4'], initial['DP-4'])
        self.assertNotIn('DP-4', self.controller.pending['document']['outputs'])
        self.assertEqual(set(self.controller.pending['document']['outputs']['DP-2']), {'position'})
        self.controller.request(dict(op='revert'))
        self.assertEqual(self.state(), initial)

    def test_reflow_refuses_lost_contact_or_new_overlap(self):
        for extra in ('overlap', 'obstacle'):
            with self.subTest(layout=extra):
                initial = copy.deepcopy(self.initial)
                if extra in ('offset', 'overlap'):
                    initial['DP-2']['logical']['y' if extra == 'offset' else 'x'] = 1000 if extra == 'offset' else 1800
                    key, value = 'scale', 2
                else:
                    initial['DP-3'] = copy.deepcopy(initial['DP-1'])
                    initial['DP-3']['logical'].update(x=0, y=1200 if extra == 'obstacle' else 1080)
                    if extra == 'cycle':
                        initial['DP-4'] = copy.deepcopy(initial['DP-3'])
                        initial['DP-4']['logical']['x'] = 1920
                    key, value = 'rotation', '90'
                self.write_outputs(initial)
                with self.assertRaisesRegex(backend.Failure, 'separate or overlap'):
                    self.change(key, value)
                self.assertEqual(self.state(), initial)
                self.assertFalse(self.controller.path.exists())
                self.assertFalse(self.controller.journal.exists())

    def test_reflow_accepts_offset_and_redundant_contacts(self):
        for layout in ('offset', 'centered', 'grid'):
            for key, value in [('scale', 2), ('rotation', '90')]:
                with self.subTest(layout=layout, key=key):
                    initial = copy.deepcopy(self.initial)
                    if layout == 'offset':
                        initial['DP-2']['logical'].update(x=1500, y=-1080)
                    else:
                        initial['DP-3'] = copy.deepcopy(initial['DP-1'])
                        initial['DP-3']['logical'].update(x=960 if layout == 'centered' else 0, y=1080)
                        if layout == 'grid':
                            initial['DP-4'] = copy.deepcopy(initial['DP-3'])
                            initial['DP-4']['logical']['x'] = 1920
                    self.write_outputs(initial)
                    self.change(key, value)
                    current = list(self.state().values())
                    for i, first in enumerate(current):
                        for second in current[i + 1:]:
                            a, b = first['logical'], second['logical']
                            self.assertFalse(all(min(a[axis] + a[size], b[axis] + b[size]) >
                                                 max(a[axis], b[axis])
                                                 for axis, size in [('x', 'width'), ('y', 'height')]))
                    self.controller.request(dict(op='revert'))
                    self.assertEqual(self.state(), initial)

    def test_keep_checks_neighbor_positions_and_rolls_back_all_outputs(self):
        for field, value in [('x', 2000), ('height', 1)]:
            with self.subTest(changed=field):
                self.change('scale', 1.25)
                current = self.state()
                current['DP-2']['logical'][field] = value
                self.write_outputs(current)
                with self.assertRaises(backend.Failure):
                    self.controller.request(dict(op='keep'))
                self.assertEqual(self.state(), self.initial)
                self.assertFalse(self.controller.path.exists())

    def test_reflow_action_failure_restores_all_positions(self):
        original = self.controller.action
        rejected = False
        def fail_position(name, key, value):
            nonlocal rejected
            if name == 'DP-2' and key == 'position' and not rejected:
                rejected = True
                raise backend.Failure('The display service could not apply the change.')
            original(name, key, value)
        with patch.object(self.controller, 'action', side_effect=fail_position):
            with self.assertRaises(backend.Failure):
                self.change('scale', 1.25)
        self.assertTrue(rejected)
        self.assertEqual(self.state(), self.initial)
        self.assertFalse(self.controller.path.exists())
        self.assertFalse(self.controller.journal.exists())

    def test_deadline_rejects_late_keep(self):
        self.change('scale', 1.5)
        self.assertGreater(self.controller.pending['deadline'] - time.monotonic(), 13)
        self.controller.pending['deadline'] = time.monotonic() - 1
        with self.assertRaises(backend.Failure):
            self.controller.request(dict(op='keep'))
        self.assertEqual(self.state()['DP-1']['logical']['scale'], 1)
        self.assertFalse(self.controller.path.exists())
        self.assertFalse(self.controller.journal.exists())

    def test_output_controls_and_last_output(self):
        self.change('enabled', False, 'DP-2')
        self.assertFalse(self.controller.path.exists())
        self.controller.request(dict(op='keep'))
        self.assertFalse(self.controller.path.exists())
        (self.root / 'calls').write_text('')
        with self.assertRaisesRegex(backend.Failure, 'at least one'):
            self.change('enabled', False)
        self.assertNotIn('"off"', (self.root / 'calls').read_text())
        self.assertIsNotNone(self.state()['DP-1']['current_mode'])
        self.change('enabled', True, 'DP-2')
        self.controller.request(dict(op='keep'))
        self.change('rotation', '90')
        self.controller.request(dict(op='keep'))
        self.change('position', dict(x=-1200, y=-200))
        self.controller.request(dict(op='keep'))
        self.change('main', True, 'DP-2')
        self.assertEqual((self.root / 'focus').read_text(), 'DP-2')
        self.assertIn('focus-at-startup', self.controller.path.read_text())
        self.assertEqual(self.state()['DP-1']['logical']['x'], -1200)
        self.assertEqual(self.state()['DP-1']['logical']['transform'], '90')

    def test_every_risky_change_waits_for_keep_and_reverts(self):
        for key, value in [('mode', '1280x720@59.940'), ('scale', 1.5), ('rotation', '90'),
                           ('position', dict(x=10000, y=-200)), ('enabled', False)]:
            with self.subTest(key=key):
                self.assertTrue(self.change(key, value)['pending'])
                self.assertFalse(self.controller.path.exists())
                self.controller.request(dict(op='revert'))
                self.assertEqual(self.state(), self.initial)

    def test_disabled_state_is_never_saved_and_undock_restores_last_screen(self):
        self.change('scale', 1.5)
        self.controller.request(dict(op='keep'))
        saved = self.controller.path.read_text()
        self.change('enabled', False)
        self.controller.request(dict(op='keep'))
        self.assertEqual(self.controller.path.read_text(), saved)
        self.assertNotIn('off', saved)
        current = self.state()
        current.pop('DP-2')
        self.write_outputs(current)
        reply = self.controller.request(dict(op='state'))
        self.assertIsNotNone(self.state()['DP-1']['current_mode'])
        self.assertIn('turned back on', reply['message'])
        document = dict(version=1, outputs={'DP-1': dict(enabled=False)}, main=None)
        with self.assertRaises(backend.Failure):
            backend.render(document)

    def test_disconnect_while_off_is_pending_restores_last_screen(self):
        self.change('enabled', False)
        current = self.state()
        current.pop('DP-2')
        self.write_outputs(current)
        self.assertFalse(self.controller.request(dict(op='state'))['pending'])
        self.assertIsNotNone(self.state()['DP-1']['current_mode'])
        self.assertFalse(self.controller.path.exists())

    def test_crashed_helper_recovers_on_start_without_a_page(self):
        process = self.client()
        self.receive(process)
        self.send(process, dict(op='set', output='DP-1', key='rotation', value='90'))
        process.kill(); process.wait(timeout=5)
        recovery = self.client()
        reply = self.receive(recovery)
        self.assertEqual(self.state(), self.initial)
        self.assertIn('reverted', reply['message'])
        self.assertFalse(self.controller.journal.exists())

    def test_old_session_recovery_does_not_replay_snapshot(self):
        self.change('scale', 1.5)
        self.controller.release()
        Path(self.env['NIRI_SOCKET']).unlink()
        Path(self.env['NIRI_SOCKET']).touch()
        recovery = backend.Displays(); self.addCleanup(recovery.release)
        recovery.recover()
        self.assertEqual(self.state()['DP-1']['logical']['scale'], 1.5)
        self.assertIn('earlier session', recovery.message)
        self.assertFalse(self.controller.journal.exists())

    def test_disconnect_during_output_off_restores_the_remaining_screen(self):
        original = self.controller.action
        def disconnect(name, key, value):
            original(name, key, value)
            if name == 'DP-1' and key == 'enabled' and value is False:
                current = self.state()
                current.pop('DP-2')
                self.write_outputs(current)
        with patch.object(self.controller, 'action', side_effect=disconnect):
            with self.assertRaises(backend.Failure):
                self.change('enabled', False)
        self.assertIsNotNone(self.state()['DP-1']['current_mode'])
        self.assertFalse(self.controller.path.exists())
        self.assertFalse(self.controller.journal.exists())

    def test_disconnect_before_keep_does_not_publish_the_candidate(self):
        self.change('scale', 1.5)
        current = self.state()
        current.pop('DP-1')
        self.write_outputs(current)
        with self.assertRaises(backend.Failure):
            self.controller.request(dict(op='keep'))
        self.assertIsNotNone(self.state()['DP-2']['current_mode'])
        self.assertFalse(self.controller.path.exists())
        self.assertFalse(self.controller.pending)
        self.assertFalse(self.controller.journal.exists())

    def test_validation_failure_changes_nothing(self):
        self.flags(invalid=True)
        with self.assertRaises(backend.Failure):
            self.change('rotation', '90')
        self.assertEqual(self.state(), self.initial)
        self.assertFalse(self.controller.path.exists())
        self.assertFalse(self.controller.journal.exists())

    def test_ack_without_actual_change_is_refused(self):
        self.flags(ignore=True)
        with self.assertRaises(backend.Failure):
            self.change('scale', 1.5)
        self.assertFalse(self.controller.path.exists())
        self.assertFalse(self.controller.pending)

    def test_failed_rollback_retains_recoverable_journal(self):
        self.change('scale', 1.5)
        self.flags(fail=True)
        with self.assertRaises(backend.Failure):
            self.controller.request(dict(op='revert'))
        self.assertTrue(self.controller.journal.exists())
        self.flags()
        recovery = backend.Displays(); self.addCleanup(recovery.release)
        recovery.recover()
        self.assertEqual(self.state()['DP-1']['logical']['scale'], 1)
        self.assertFalse(self.controller.journal.exists())

    def test_single_writer(self):
        self.change('scale', 1.5)
        other = backend.Displays(); self.addCleanup(other.release)
        with self.assertRaisesRegex(backend.Failure, 'Another display'):
            other.request(dict(op='set', output='DP-2', key='rotation', value='90'))
        self.controller.request(dict(op='revert'))

    def test_invalid_requests(self):
        for key, value in [('scale', True), ('scale', 0), ('scale', float('nan')), ('position', {'x': True, 'y': 0}), ('rotation', 'bad'), ('mode', '1280x720@60.000'), ('enabled', 'false')]:
            with self.subTest(key=key, value=value), self.assertRaises(backend.Failure):
                self.change(key, value)
        self.assertEqual(self.state(), self.initial)
        self.assertFalse(self.controller.path.exists())

    def test_malformed_and_future_saved_files_untouched(self):
        self.controller.path.parent.mkdir(parents=True)
        for contents in ['broken', backend.HEADER + backend.MARKER + '{"version":2,"outputs":{},"main":null}\n', backend.HEADER + backend.MARKER + '{"version":true,"outputs":{},"main":null}\n']:
            self.controller.path.write_text(contents)
            with self.assertRaises(backend.Failure):
                backend.Displays()
            self.assertEqual(self.controller.path.read_text(), contents)
            self.assertEqual(self.state(), self.initial)

    def test_bad_output_data_refused(self):
        for value in [{'DP-1': dict(modes=[], current_mode=4)}, {'DP-1': dict(modes=[1], current_mode=0)}]:
            self.write_outputs(value)
            with self.subTest(value=value), self.assertRaises(backend.Failure):
                self.controller.response()

    def test_unhashable_request_field_refused(self):
        with self.assertRaises(backend.Failure):
            self.controller.request(dict(op='set', output='DP-1', key=[], value=1))
        self.assertEqual(self.state(), self.initial)



if __name__ == '__main__':
    if not VALIDATOR:
        raise SystemExit('niri is required to validate generated display KDL')
    unittest.main()
