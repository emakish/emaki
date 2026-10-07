#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline good, frozen and incomplete host-frame evidence; never starts a VM."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

SCRIPT = Path(__file__).resolve().parent / 'vm/check-install-progress.py'
SPEC = importlib.util.spec_from_file_location('progress_check', SCRIPT)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class ProgressCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def evidence(self, *, frozen=False, count=15, size=(1280, 800), outside_motion=False):
        timeline = []
        previous = None
        for t in range(count):
            image = Image.new('RGB', size, '#202020')
            draw = ImageDraw.Draw(image)
            fill = 5 if frozen else 5 + (t // 3) * 5
            draw.rectangle((10, 10, 10 + fill, 19), fill='orange')
            if outside_motion:
                draw.rectangle((200, 200, 200 + t, 210), fill='white')
            digest = hashlib.sha256(image.tobytes()).hexdigest()
            name = f'{t}.png' if digest != previous else None
            if name:
                image.save(self.root / name)
            timeline.append({'t': t, 'file': name, 'source': 'vnc', 'sha256': digest,
                             'changed': digest != previous, 'phase_text': 'Copying packages'})
            previous = digest
        return {'timeline': timeline, 'install_events': self.events([(0, 'copy_packages')])}

    def events(self, phases):
        path = self.root / 'install.ndjson'
        events = [{'seq': i, 'type': 'done' if phase == 'complete' else 'state', 'phase': phase}
                  for i, (_, phase) in enumerate(phases)]
        path.write_text(''.join(json.dumps(event) + '\n' for event in events))
        return {'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'host_offsets': {str(i): t for i, (t, _) in enumerate(phases)}}

    def test_static_noncopy_phases_alone_are_not_tested(self):
        data = self.evidence(frozen=True, count=35)
        data['install_events'] = self.events([(0, 'prepare_disk'), (20, 'bootloader')])
        for entry in data['timeline']:
            entry['phase_text'] = 'Preparing disk' if entry['t'] < 20 else 'Installing bootloader'
        self.assertEqual(self.run_check(data).returncode, 3)
        for phase, start in [('bootloader', 0), ('copy_packages', 100)]:
            data['install_events'] = self.events([(start, phase)])
            self.assertEqual(self.run_check(data).returncode, 3)

    def timed_events(self, data, events):
        path = self.root / 'install.ndjson'
        path.write_text(''.join(json.dumps(dict(event, seq=i)) + '\n'
                                for i, (_, event) in enumerate(events)))
        data['install_events'] = {'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                  'host_offsets': {str(i): t for i, (t, _) in enumerate(events)}}

    def running_step(self):
        data = self.evidence(count=25)
        # A real change first, then one long unchanged interval with a named step.
        last = data['timeline'][3]
        for entry in data['timeline'][4:]:
            entry.update(file=None, sha256=last['sha256'], changed=False)
        step = {'id': 'copy-1', 'text': 'Checking packages (pacman integrity).', 'state': 'running'}
        self.timed_events(data, [(0, {'type': 'state', 'phase': 'copy_packages'}),
                                 (3, {'type': 'progress', 'phase': 'copy_packages', 'step': step})])
        for entry in data['timeline'][3:]:
            entry.update(step_id=step['id'], step_text=step['text'])
        return data, step

    def test_one_visible_running_step_explains_whole_freeze(self):
        data, _ = self.running_step()
        result = self.run_check(data)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('"longest_observed_bound": 21', result.stdout)

    def test_missing_or_different_step_at_any_sample_fails(self):
        for change in ({'step_text': ''}, {'step_id': 'other'}, {'step_text': 'Unrelated work'}):
            data, _ = self.running_step()
            data['timeline'][10].update(change)
            result = self.run_check(data)
            self.assertEqual(result.returncode, 1, result.stdout)

    def test_ended_and_stitched_steps_cannot_explain_a_freeze(self):
        for replacement in (None, {'id': 'copy-2', 'text': 'Another task', 'state': 'running'}):
            data, step = self.running_step()
            self.timed_events(data, [(0, {'type': 'state', 'phase': 'copy_packages'}),
                                     (3, {'type': 'progress', 'phase': 'copy_packages', 'step': step}),
                                     (12, {'type': 'progress', 'phase': 'copy_packages', 'step': replacement})])
            if replacement:
                for entry in data['timeline'][12:]:
                    entry.update(step_id=replacement['id'], step_text=replacement['text'])
            result = self.run_check(data)
            self.assertEqual(result.returncode, 1, result.stdout)

    def test_running_steps_need_host_timestamps(self):
        data, _ = self.running_step()
        del data['install_events']['host_offsets']['1']
        self.assertEqual(self.run_check(data).returncode, 3)

    def complete_evidence(self, skew=0):
        data = self.evidence(count=25)
        starts = [0, 1, 14, 14, 14.15, 14.3, 14.3, 20, 21]
        phases = list(CHECK.PHASES)
        data['install_events'] = self.events(list(zip(starts, phases)))
        for entry in data['timeline']:
            active = [phase for start, phase in zip(starts, phases) if start + skew <= entry['t']]
            phase = active[-1] if active else 'before'
            entry.update(phase=phase, phase_text=phase)
        return data

    def test_zero_and_subsecond_phases_are_unseen_but_covered(self):
        data = self.complete_evidence()
        result = CHECK.check(self.root, data, (10, 10, 100, 10), (1280, 800), require_complete=True)
        self.assertTrue(result['covers_installation'])
        self.assertEqual(result['too_short_to_see'], ['bootloader', 'settings', 'snapshot'])
        self.assertNotIn('settings', result['phases'])

    def test_stated_clock_tolerance_accepts_point_two_and_point_five_skew(self):
        for skew in (-0.5, -0.2, 0.2, 0.5):
            data = self.complete_evidence(skew)
            data['clock_tolerance'] = {'seconds': abs(skew), 'reason': 'Bounded capture delivery delay.'}
            result = CHECK.check(self.root, data, (10, 10, 100, 10), (1280, 800), require_complete=True)
            self.assertTrue(result['covers_installation'])

    def test_clock_tolerance_does_not_hide_late_phase_or_lift_freeze_limit(self):
        data = self.complete_evidence(0.6)
        data['clock_tolerance'] = {'seconds': 0.5, 'reason': 'Capture delivery bound.'}
        data['timeline'][14]['phase'] = 'copy_packages'
        # At 15 s the copy phase ended a full second earlier.
        data['timeline'][15].update(phase='copy_packages', phase_text='copy_packages')
        self.assertEqual(self.run_check(data).returncode, 1)
        data = self.evidence(frozen=True)
        data['clock_tolerance'] = {'seconds': 0.5, 'reason': 'Capture delivery bound.'}
        for entry in data['timeline']:
            entry['phase'] = 'copy_packages'
        self.assertEqual(self.run_check(data).returncode, 1)

    def test_long_missing_phase_cannot_be_called_too_short(self):
        data = self.complete_evidence()
        # Missing bootloader for three seconds cannot be waived as unobservable.
        data['install_events'] = self.events(list(zip([0, 1, 11, 14, 14.15, 14.3, 14.3, 20, 21], CHECK.PHASES)))
        self.assertEqual(self.run_check(data).returncode, 1)

    def test_missing_or_out_of_order_phase_evidence(self):
        data = self.evidence()
        del data['install_events']
        self.assertEqual(self.run_check(data).returncode, 3)
        data['install_events'] = self.events([(0, 'copy_packages'), (5, 'prepare_disk')])
        self.assertEqual(self.run_check(data).returncode, 1)

    def test_unchanged_phase_text_and_tampered_events_fail(self):
        data = self.evidence()
        data['install_events'] = self.events([(0, 'prepare_disk'), (5, 'bootloader')])
        self.assertEqual(self.run_check(data).returncode, 1)
        data['install_events']['sha256'] = '0' * 64
        self.assertEqual(self.run_check(data).returncode, 1)

    def run_check(self, data, *, resolution='1280x800', roi='10,10,100,10'):
        path = self.root / 'progress-timeline.json'
        path.write_text(json.dumps(data))
        return subprocess.run([sys.executable, str(SCRIPT), '--root', str(self.root),
                               '--timeline', str(path), '--resolution', resolution, '--roi', roi],
                              text=True, capture_output=True)

    def test_good_changes_at_both_native_sizes(self):
        for width, height in ((1280, 800), (2560, 1600)):
            with self.subTest(size=(width, height)):
                result = self.run_check(self.evidence(size=(width, height)), resolution=f'{width}x{height}')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('observed progress-region freshness only', result.stdout)

    def test_frozen_bar_fails_even_when_elapsed_label_changes(self):
        for outside_motion in (False, True):
            with self.subTest(outside_motion=outside_motion):
                result = self.run_check(self.evidence(frozen=True, outside_motion=outside_motion))
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn('unchanged for more than 10 seconds', result.stdout)

    def test_empty_short_and_sparse_capture_are_not_tested(self):
        empty = {'timeline': []}
        short = self.evidence(count=5)
        sparse = self.evidence()
        sparse['timeline'] = sparse['timeline'][::3]
        for data in (empty, short, sparse):
            result = self.run_check(data)
            self.assertEqual(result.returncode, 3, result.stdout)
            self.assertIn('NOT TESTED:', result.stdout)

    def test_guest_capture_is_not_host_evidence(self):
        data = self.evidence()
        data['timeline'][0]['source'] = 'grim'
        self.assertEqual(self.run_check(data).returncode, 3)

    def test_nonmonotonic_capture_is_invalid(self):
        data = self.evidence()
        data['timeline'][2]['t'] = 0
        self.assertEqual(self.run_check(data).returncode, 1)

    def test_tampered_frame_and_missing_changed_frame_fail(self):
        data = self.evidence()
        data['timeline'][0]['sha256'] = '0' * 64
        self.assertEqual(self.run_check(data).returncode, 1)
        data = self.evidence()
        data['timeline'][3]['file'] = None
        self.assertEqual(self.run_check(data).returncode, 1)

    def test_missing_frame_is_not_tested(self):
        data = self.evidence()
        (self.root / '0.png').unlink()
        self.assertEqual(self.run_check(data).returncode, 3)

    def test_resolution_and_roi_cannot_be_inferred(self):
        data = self.evidence()
        self.assertEqual(self.run_check(data, resolution='2560x1600').returncode, 1)
        self.assertEqual(self.run_check(data, roi='1275,10,100,10').returncode, 1)


if __name__ == '__main__':
    unittest.main()
