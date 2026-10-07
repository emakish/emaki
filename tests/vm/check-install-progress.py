#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check a captured interval of the installer progress bar, eyes stage 16.

Use eyes-walk.py capture --series --stage 16 --interval 1 --stable 15
and supply its *-timeline.json, the walk root, native resolution, and a manually
selected x,y,width,height ROI containing only the progress fill (no clock,
spinner, cursor or elapsed-time label). Annotate every sample with the phase
using phase_text observed on the host frame, plus install_events binding
the original NDJSON and host receive offsets to the capture clock. Repeat at every required screen size.
Only copy_packages has measured progress; other phases have a static fill.
For a freeze over ten seconds, annotate step_id and the visible step_text;
one timestamped running step must explain the whole interval. Optional
clock_tolerance {seconds, reason} permits at most 0.5 seconds of alignment
uncertainty and requires an observed phase ID on each frame. Subsecond phases
between captures are reported as too_short_to_see, never as seen.

This checks observed changes in that region during that interval only. It does
not judge the region selection, prove capture provenance, or establish that the
whole install was recorded. The existing eyes review remains required. Timeline
timestamps are the host monotonic offsets recorded by the eyes capture harness.
Exit 0: observed freshness; 1: broken evidence or frozen region; 3: NOT TESTED.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

from PIL import Image


class NotTested(ValueError):
    pass


PHASES = ('prepare_disk', 'copy_packages', 'bootloader', 'account', 'settings',
          'snapshot', 'update', 'finish', 'complete')


def finite_offset(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value >= 0)


def worker_timeline(root, timeline):
    """Bind phase and step transitions to the original transcript by sequence ID."""
    evidence = timeline.get('install_events', {})
    if not evidence:
        raise NotTested('NDJSON and host event offsets are required to locate copy_packages')
    path = (root / evidence['file']).resolve()
    if not path.is_relative_to(root):
        raise ValueError('NDJSON escapes the walk directory')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != evidence['sha256']:
        raise ValueError('NDJSON hash differs from phase evidence')
    offsets = evidence.get('host_offsets', {})
    boundaries, steps = [], []
    active_step = None
    seen_ids, seen_seq = set(), set()
    last_t = 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get('type') == 'error':
            raise ValueError('installation transcript contains an error')
        kind = event.get('type')
        phase = 'complete' if kind == 'done' else event.get('phase')
        transition = kind in ('state', 'done') and (not boundaries or phase != boundaries[-1][1])
        step_event = kind == 'progress' and 'step' in event
        if not transition and not step_event:
            continue
        seq = str(event.get('seq'))
        if seq in seen_seq:
            raise ValueError('duplicate event sequence ID')
        seen_seq.add(seq)
        t = offsets.get(seq)
        if not finite_offset(t):
            raise NotTested('each phase, step transition and done need host offsets on the capture clock')
        if t < last_t:
            raise ValueError('host event offsets are out of order')
        last_t = t
        if transition:
            if phase not in PHASES:
                raise ValueError('unknown installation phase')
            if boundaries and PHASES.index(phase) <= PHASES.index(boundaries[-1][1]):
                raise ValueError('installation phase events are out of order')
            boundaries.append((t, phase))
        step = event.get('step') if step_event else None
        if step is not None:
            if (phase != 'copy_packages' or not boundaries or boundaries[-1][1] != phase
                    or not isinstance(step, dict) or step.get('state') != 'running'
                    or not isinstance(step.get('id'), str) or not step['id'].strip()
                    or not isinstance(step.get('text'), str) or not step['text'].strip()):
                raise ValueError('invalid running copy step')
            if active_step and step == active_step['step']:
                continue
            if step['id'] in seen_ids:
                raise ValueError('running step instance ID was reused')
            seen_ids.add(step['id'])
        if active_step:
            active_step['end'] = t
            active_step = None
        if step is not None:
            active_step = {'start': t, 'end': math.inf, 'step': step}
            steps.append(active_step)
    if not boundaries:
        raise NotTested('no installation phase events')
    return boundaries, steps


def phase_boundaries(root, timeline):
    return worker_timeline(root, timeline)[0]


def check(root, timeline, roi, resolution, *, require_complete=False):
    root = Path(root).resolve()
    if not isinstance(timeline, dict):
        raise ValueError('timeline record must be an object')
    entries = timeline.get('timeline', [])
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        raise ValueError('timeline must contain capture objects')
    if len(entries) < 2:
        raise NotTested('at least two host captures are required')
    boundaries, steps = worker_timeline(root, timeline)
    clock = timeline.get('clock_tolerance', {})
    tolerance = clock.get('seconds', 0)
    if not finite_offset(tolerance) or tolerance > 0.5:
        raise ValueError('clock tolerance must be between zero and 0.5 seconds')
    if tolerance and (not isinstance(clock.get('reason'), str) or not clock['reason'].strip()):
        raise ValueError('clock tolerance needs a stated reason')
    x, y, width, height = roi
    if min(x, y) < 0 or min(width, height) <= 0 or x + width > resolution[0] or y + height > resolution[1]:
        raise ValueError('progress ROI is outside the expected frame')
    previous_t = previous_hash = previous_text = None
    previous_phase = None
    crop = None
    observed_phases, samples = [], []
    phase_times = dict((name, start) for start, name in boundaries)

    def phase_at(t):
        return next((name for start, name in reversed(boundaries) if start <= t), 'before')

    for entry in entries:
        t = entry['t']
        if not finite_offset(t):
            raise ValueError('invalid monotonic capture offset')
        if previous_t is not None:
            if t <= previous_t:
                raise ValueError('capture offsets must strictly increase')
            if t - previous_t > 2:
                raise NotTested('capture gap exceeds two seconds')
        if entry.get('source') not in ('vnc', 'screendump'):
            raise NotTested('frame is not labelled as host scan-out capture')
        name = entry.get('file')
        if name is not None:
            path = (root / name).resolve()
            if not path.is_relative_to(root):
                raise ValueError('frame escapes the walk directory')
            with Image.open(path) as image:
                image = image.convert('RGB')
                if image.size != tuple(resolution):
                    raise ValueError(f'expected native resolution {resolution}, got {image.size}')
                if hashlib.sha256(image.tobytes()).hexdigest() != entry.get('sha256'):
                    raise ValueError('frame pixels do not match the capture hash')
                crop = image.crop((x, y, x + width, y + height)).tobytes()
        elif previous_hash is None or entry.get('sha256') != previous_hash or entry.get('changed') is not False:
            raise ValueError('missing changed frame')
        phase = entry.get('phase', phase_at(t))
        if tolerance and 'phase' not in entry:
            raise NotTested('clock tolerance requires an observed phase ID with each phase_text')
        possible = {phase_at(t - tolerance), phase_at(t + tolerance)}
        possible.update(name for start, name in boundaries if t - tolerance <= start <= t + tolerance)
        if phase not in possible:
            raise ValueError('observed phase is outside its timestamp tolerance')
        text = entry.get('phase_text')
        if phase != 'before':
            if not isinstance(text, str) or not text.strip():
                raise NotTested('host phase and observed phase_text are required for every sample')
            text = text.strip()
            if phase != previous_phase:
                if previous_phase not in (None, 'before'):
                    if PHASES.index(phase) <= PHASES.index(previous_phase):
                        raise ValueError('installation phases are out of order')
                    if text == previous_text:
                        raise ValueError('phase text did not change at the phase boundary')
                observed_phases.append(phase)
        elif previous_phase not in (None, 'before'):
            raise ValueError('installation phases are out of order')
        samples.append({'t': t, 'crop': crop, 'phase': phase,
                        'step_id': entry.get('step_id'), 'step_text': entry.get('step_text')})
        previous_t, previous_hash = t, entry.get('sha256')
        previous_phase, previous_text = phase, text

    if samples[-1]['t'] - samples[0]['t'] < 10:
        raise NotTested('captured interval is shorter than 10 seconds')
    if 'copy_packages' not in observed_phases:
        raise NotTested('no copy_packages samples were observed')

    # A single running instance must explain an entire unchanged region. Changing
    # captions alone cannot reset this clock or stitch unrelated work together.
    def explained(group):
        first, last = group[0], group[-1]
        if not first['step_id'] or not first['step_text']:
            return False
        if any((item['step_id'], item['step_text']) != (first['step_id'], first['step_text']) for item in group):
            return False
        return any(step['step']['id'] == first['step_id'] and step['step']['text'] == first['step_text']
                   and step['start'] <= first['t'] + tolerance
                   and step['end'] >= last['t'] - tolerance for step in steps)

    changes = 0
    longest = unexplained = copy_duration = 0.0
    group = []
    copy_start = phase_times['copy_packages']
    copy_end = next((t for t, name in boundaries if PHASES.index(name) > 1), math.inf)

    def finish_group(end):
        nonlocal longest, unexplained
        if not group:
            return
        bound = end - max(copy_start, group[0]['t'])
        longest = max(longest, bound)
        if not explained(group):
            unexplained = max(unexplained, bound)
            # The allowance concerns clock alignment only, never the ten-second
            # freshness threshold itself or missing samples inside an interval.
            if bound > 10:
                raise ValueError(f'progress region unchanged for more than 10 seconds without one visible running step (observed bound {bound:.3f}s)')

    for sample in samples:
        if sample['phase'] == 'copy_packages':
            if group and sample['crop'] != group[-1]['crop']:
                finish_group(min(sample['t'], copy_end + tolerance))
                changes += 1
                group = []
            group.append(sample)
        elif group:
            finish_group(min(sample['t'], copy_end + tolerance))
            group = []
    if group:
        finish_group(min(group[-1]['t'], copy_end + tolerance))
    copy_duration = max(0, min(samples[-1]['t'], copy_end) - max(samples[0]['t'], copy_start))
    if not changes:
        raise NotTested('no progress change observed during copy_packages')

    # A missed phase is exempt only when the bound timeline puts its entire
    # lifetime between adjacent captures. It is recorded as unseen, never seen.
    short = []
    for i, (start, phase) in enumerate(boundaries[:-1]):
        end = boundaries[i + 1][0]
        if phase in observed_phases or end - start >= 1:
            continue
        if any(max(-tolerance, left['t'] - start) <= min(tolerance, right['t'] - end)
               for left, right in zip(samples, samples[1:])):
            short.append(phase)
    complete = (set(observed_phases + short) == set(PHASES)
                and [phase for _, phase in boundaries] == list(PHASES)
                and samples[0]['t'] <= boundaries[0][0] + tolerance
                and samples[-1]['t'] >= boundaries[-1][0] - tolerance)
    if require_complete and not complete:
        raise NotTested('full installation needs every phase seen or proven too short to see, from prepare_disk through complete')
    return {'duration': samples[-1]['t'] - samples[0]['t'], 'changes': changes,
            'copy_duration': copy_duration, 'longest_observed_bound': longest,
            'longest_unexplained_bound': unexplained, 'clock_tolerance': tolerance,
            'phases': observed_phases, 'too_short_to_see': short, 'covers_installation': complete}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--timeline', type=Path, required=True)
    parser.add_argument('--roi', required=True, help='x,y,width,height of progress fill only')
    parser.add_argument('--resolution', required=True, help='native WIDTHxHEIGHT; no inferred screen coverage')
    args = parser.parse_args(argv)
    try:
        roi = tuple(map(int, args.roi.split(',')))
        resolution = tuple(map(int, args.resolution.split('x')))
        if len(roi) != 4 or len(resolution) != 2 or min(resolution) <= 0:
            raise ValueError('invalid ROI or resolution')
        result = check(args.root, json.loads(args.timeline.read_text()), roi, resolution)
    except (NotTested, FileNotFoundError) as error:
        print(f'NOT TESTED: {error}')
        return 3
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f'FAIL: {error}')
        return 1
    print('PASS: observed progress-region freshness only; visual review and whole-install coverage remain required; ' + json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
