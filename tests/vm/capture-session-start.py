#!/usr/bin/env python3
"""HOST ONLY: disposable VM login-to-desktop evidence, never the host session.

Capture starts before successful Enter, follows both guest Wayland sockets, and
attempts independent QEMU screendumps across the DRM handoff. Exit 1 means a
capture failure or observed visual regression; exit 2 requires frame review and
is never a visual pass. --analyze-only reads old evidence without VM operations.
All remote operations use the configured guest transport.
"""
import argparse
import importlib.util
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import selectors
import shlex
import socket
import subprocess
import tarfile
import threading
import time

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('c9_start_greeter', HERE / 'check-greeter.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)
ROIS = {'bar-left': (0, 0, .33, .14), 'bar-center': (.33, 0, .67, .14),
        'bar-right': (.67, 0, 1, .14), 'dock': (.12, .74, .88, 1)}
MATERIAL_ROIS = {'bar-material': (0, 0, 1, .12), 'bar-shadow': (0, .025, 1, .12)}
# Every pixel and every RGB channel must remain within two byte values.
# No tiles, downsampling, masked regions or mean-error cancellation.
FRAME_CHANNEL_TOLERANCE = 2


def lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line] if path.exists() else []


def extract_evidence(archive, destination):
    """Do not trust archive names, links, devices, sizes or extraction defaults."""
    destination.mkdir(mode=0o700)
    total = 0
    with tarfile.open(archive) as source:
        for index, entry in enumerate(source):
            name = PurePosixPath(entry.name)
            if (index >= 2000 or not entry.isfile() or name.is_absolute() or '..' in name.parts
                    or not re.fullmatch(r'(?:frames/(?:user|greeter)-[0-9]{5}\.png|(?:frames|metadata|config-events|cover-events)\.jsonl|manifest\.json|handoff\.png)', entry.name)
                    or not 0 <= entry.size <= 32 * 1024 * 1024):
                raise ValueError('unsafe guest capture archive')
            total += entry.size
            if total > 512 * 1024 * 1024:
                raise ValueError('guest capture archive exceeds its bound')
            target = destination / entry.name
            target.parent.mkdir(mode=0o700, exist_ok=True)
            with source.extractfile(entry) as stream, target.open('xb') as output:
                remaining = entry.size
                while remaining:
                    data = stream.read(min(65536, remaining))
                    if not data:
                        raise ValueError('truncated guest capture archive')
                    output.write(data)
                    remaining -= len(data)


def clock_sync():
    probes = []
    for _ in range(3):
        before = time.time_ns()
        guest = int(check.checked_remote("python3 -c 'import time; print(time.time_ns())'", timeout=5))
        after = time.time_ns()
        probes.append(dict(hostSendNs=before, hostReceiveNs=after, guestNs=guest))
    best = min(probes, key=lambda row: row['hostReceiveNs'] - row['hostSendNs'])
    return dict(probes=probes, guestMinusHostNs=best['guestNs'] - (best['hostSendNs'] + best['hostReceiveNs']) // 2,
                uncertaintyNs=(best['hostReceiveNs'] - best['hostSendNs']) // 2)


def host_frames(output, stop):
    directory = output / 'host'
    directory.mkdir(mode=0o700)
    index = 0
    with (output / 'host-frames.jsonl').open('a') as timeline:
        while not stop.is_set():
            started = time.time_ns()
            row = dict(startNs=started, index=index, state='unavailable', source='host-screendump')
            ppm = directory / f'{index:05d}.ppm'
            try:
                with socket.socket(socket.AF_UNIX) as monitor:
                    monitor.settimeout(.75)
                    monitor.connect(str((check.VM / 'mon.sock').resolve()))
                    check.hmp_response(monitor)
                    monitor.sendall(('screendump ' + json.dumps(str(ppm)) + '\n').encode())
                    check.hmp_response(monitor)
                if ppm.exists() and 0 < ppm.stat().st_size < 64 * 1024 * 1024:
                    filename = f'host/{index:05d}.png'
                    with Image.open(ppm) as frame:
                        frame.save(output / filename)
                    row.update(state='frame', file=filename)
                else:
                    row['reason'] = 'no-host-display-surface'
            except (OSError, ValueError):
                row['reason'] = 'host-monitor-or-image-unavailable'
            finally:
                ppm.unlink(missing_ok=True)
            row['endNs'] = time.time_ns()
            timeline.write(json.dumps(row) + '\n')
            timeline.flush()
            index += 1
            stop.wait(max(0, .08 - (time.time_ns() - started) / 1e9))


def roi_image(image, region):
    box = tuple(round(value * (image.width if index % 2 == 0 else image.height)) for index, value in enumerate(region))
    crop = image.crop(box).convert('RGB')
    crop.thumbnail((320, 160))
    return crop


def roi_score(image, reference, region):
    current, final = roi_image(image, region), roi_image(reference, region)
    if current.size != final.size:
        return None
    # Compare final foreground/plate edges, not mostly unchanged empty wallpaper.
    mask = final.convert('L').filter(ImageFilter.FIND_EDGES).point(lambda value: 255 if value > 24 else 0).filter(ImageFilter.MaxFilter(3))
    pixels = ImageStat.Stat(mask).sum[0] / 255
    if pixels < 30:
        return None
    difference = ImageChops.difference(current, final)
    error = sum(ImageStat.Stat(difference, mask).mean) / (3 * 255)
    return round(error, 5)


def material_score(image, reference, region):
    # Include gaps between islands and shadow pixels, which edge-only foreground
    # masks can miss when a full-width flat strip becomes separated glass.
    current, final = roi_image(image, region), roi_image(reference, region)
    if current.size != final.size:
        return None
    return round(sum(ImageStat.Stat(ImageChops.difference(current, final)).mean) / (3 * 255), 5)


def percentile(histogram, fraction):
    target = sum(histogram) * fraction
    count = 0
    for value, frequency in enumerate(histogram):
        count += frequency
        if count >= target:
            return value
    return 255


def predrain_boundary(events, metadata):
    explicit = [event['atMs'] * 1_000_000 for event in events
                if event.get('phase') == 'drain-ready' and type(event.get('atMs')) in (int, float)]
    return (min(explicit), 'recorded-ready-drain', True) if explicit else (None, 'unknown', False)


def full_frame_difference(current, reference):
    if current.size != reference.size:
        return None
    difference = ImageChops.difference(current.convert('RGB'), reference.convert('RGB'))
    extrema = difference.getextrema()
    maximum = max(hi for _, hi in extrema)
    # A single changed pixel is enough; all pixels participate in the comparison.
    return dict(maxChannelError=maximum, matches=maximum <= FRAME_CHANNEL_TOLERANCE)


def continuity_analysis(evidence, physical, greeter, metadata, events, single_output, *, period_ms=80,
                        capture_start_ns=None):
    report = dict(reference='guest/handoff.png', boundary=None, frames=[], failures=[], incomplete=[],
                  thresholds=dict(maxChannelError=FRAME_CHANNEL_TOLERANCE),
                  coverage=dict(requestedPeriodMs=period_ms, maximumGapMs=None, overTwoPeriods=[]))
    cutoff, origin, exact = predrain_boundary(events, metadata)
    report.update(boundary=cutoff, boundarySource=origin)
    starts = [event['atMs'] * 1e6 for event in events if event.get('phase') == 'greeter-frozen'
              and type(event.get('atMs')) in (int, float)]
    start = min(starts) if starts else None
    if any(event.get('phase') in ('drain-cap', 'drain-deadline', 'skipped-deadline') for event in events):
        report['failures'].append(dict(kind='deadline-instead-of-readiness'))
    ready_events = [event for event in events if event.get('phase') == 'drain-ready']
    if ready_events and not all(event.get('observation', {}).get('ready') is True for event in ready_events):
        report['failures'].append(dict(kind='drain-without-readiness-proof'))
    if not metadata:
        report['incomplete'].append('metadata-unavailable')
    if any(row.get('startupStateScan', {}).get(key) is True for row in metadata
           for key in ('walkTruncated', 'candidatesTruncated')):
        report['incomplete'].append('startup-state-scan-truncated')
    if not exact:
        report['incomplete'].append('ready-drain-start-unavailable')
    if start is None:
        report['incomplete'].append('frozen-greeter-timestamp-unavailable')
    if not single_output:
        report['incomplete'].append('single-output-oracle-unavailable')
    path = evidence / report['reference']
    if not path.exists():
        report['incomplete'].append('frozen-greeter-png-unavailable')
        return report
    with Image.open(path) as image:
        reference = image.convert('RGB')
    if start is None:
        return report
    # Without an observed drain, continue testing every available physical frame.
    selected = sorted((row for row in physical if row.get('startNs', row['endNs']) >= start
                       and (cutoff is None or row['endNs'] < cutoff)), key=lambda row: row['endNs'])
    for row in selected:
        with Image.open(evidence / row['file']) as current:
            score = full_frame_difference(current, reference)
        report['frames'].append(dict(file=row['file'], epochNs=row['endNs'], difference=score))
        if score is None:
            report['failures'].append(dict(kind='frame-size-mismatch', file=row['file']))
        elif not score['matches']:
            report['failures'].append(dict(kind='different-frame-before-drain', file=row['file'], **score))
    if not selected:
        report['incomplete'].append('physical-pre-drain-frames-unavailable')
    timestamps = [start, *[row['endNs'] for row in selected]]
    if cutoff is not None:
        timestamps.append(cutoff)
    intervals = [dict(fromNs=a, toNs=b, ms=round((b-a)/1e6, 3)) for a, b in zip(timestamps, timestamps[1:])]
    report['coverage']['maximumGapMs'] = max((row['ms'] for row in intervals), default=None)
    if type(period_ms) not in (int, float) or not math.isfinite(period_ms) or not 0 < period_ms <= 200:
        report['incomplete'].append('capture-period-unavailable')
    else:
        report['coverage']['overTwoPeriods'] = [row for row in intervals if row['ms'] > period_ms*2]
        if report['coverage']['overTwoPeriods']:
            report['incomplete'].append('physical-capture-gap-over-two-periods')
    return report


def analysis_summary(report):
    continuity = report.get('plateContinuity', {})
    return (report['status'] + ': whole-frame continuity, RGB channel tolerance 2/255; '
            + str(len(continuity.get('failures', []))) + ' observed failures')


def continuity_status(report):
    # A directly observed bad frame remains FAIL even if other coverage is
    # incomplete. Missing evidence by itself is never a visual acceptance.
    return 'FAIL' if report['failures'] else 'INCOMPLETE' if report['incomplete'] else 'REVIEW_REQUIRED'


def cover_phase(events, timestamp):
    preceding = [event for event in events if type(event.get('atMs')) in (int, float)
                 and event['atMs'] * 1_000_000 <= timestamp]
    # Late diagnostic delivery cannot turn a revealing frame back into a hold.
    revealing = [event for event in preceding if event['phase'] in ('drain-ready', 'drain-cap', 'finished')]
    if revealing:
        preceding = revealing
    return max(preceding, key=lambda event: event['atMs'])['phase'] if preceding else 'unknown'


def nearest_metadata(metadata, timestamp):
    if not metadata:
        return None
    closest = min(metadata, key=lambda row: abs((row['epochNs'] + row.get('endNs', row['epochNs'])) // 2 - timestamp))
    midpoint = (closest['epochNs'] + closest.get('endNs', closest['epochNs'])) // 2
    return closest if abs(midpoint - timestamp) <= 400_000_000 else None


def frame_gaps(frames, start_ns=None, end_ns=None):
    good = sorted((row for row in frames if row.get('state') == 'frame'), key=lambda row: row['endNs'])
    intervals = []
    previous = start_ns
    for row in good:
        if previous is not None:
            intervals.append(dict(fromNs=previous, toNs=row['endNs'], ms=round((row['endNs'] - previous) / 1e6, 2)))
        previous = row['endNs']
    if previous is not None and end_ns is not None:
        intervals.append(dict(fromNs=previous, toNs=end_ns, ms=round((end_ns - previous) / 1e6, 2)))
    return dict(frames=len(good), maximumMs=max((row['ms'] for row in intervals), default=None),
                over180Ms=[row for row in intervals if row['ms'] > 180],
                firstNs=good[0]['endNs'] if good else None, lastNs=good[-1]['endNs'] if good else None)


def top_contact_sheets(output, rows, evidence=None):
    evidence = output if evidence is None else evidence
    directory = output / 'top-roi'
    directory.mkdir(exist_ok=True)
    entries = []
    sheets = []
    for index, row in enumerate(rows):
        with Image.open(evidence / row['file']) as frame:
            crop = frame.convert('RGB').crop((0, 0, frame.width, round(frame.height * .22)))
            crop.thumbnail((320, 120))
        crop_name = f'top-roi/{index:05d}.png'
        crop.save(output / crop_name)
        entries.append(dict(file=row['file'], topCrop=crop_name, epochNs=row['endNs'], source=row.get('side', 'host')))
        cell = Image.new('RGB', (320, 146), '#222222')
        cell.paste(crop, (0, 22))
        ImageDraw.Draw(cell).text((4, 3), f"{row.get('side', 'host')} {row['endNs'] / 1e9:.3f}", fill='white')
        sheets.append(cell)
        if len(sheets) == 60 or index == len(rows) - 1:
            sheet = Image.new('RGB', (1600, 146 * ((len(sheets) + 4) // 5)), 'black')
            for slot, item in enumerate(sheets):
                sheet.paste(item, ((slot % 5) * 320, (slot // 5) * 146))
            sheet.save(output / f'top-message-sheet-{index // 60:03d}.png')
            sheets = []
    (output / 'top-roi-index.json').write_text(json.dumps(entries, indent=2))


def analyze(evidence, synchronization=None, *, destination=None):
    output = evidence if destination is None else destination
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    manifest = json.loads((evidence / 'guest/manifest.json').read_text())
    frames = lines(evidence / 'guest/frames.jsonl')
    metadata = [row for row in lines(evidence / 'guest/metadata.jsonl') if row['side'] == 'user']
    cover_events = lines(evidence / 'guest/cover-events.jsonl')
    user = [dict(row, file='guest/' + row['file']) for row in frames if row.get('side') == 'user' and row.get('state') == 'frame']
    greeter = [dict(row, file='guest/' + row['file']) for row in frames if row.get('side') == 'greeter' and row.get('state') == 'frame']
    first = manifest.get('firstSockets', {}).get('user', {}).get('epochNs')
    gaps = frame_gaps([row for row in frames if row.get('side') == 'user'], first, manifest['endNs'])
    report = dict(status='REVIEW_REQUIRED', visualPass=False, userCapture=gaps, evidenceDirectory=str(evidence.resolve()),
                  hostCapture=frame_gaps(lines(evidence / 'host-frames.jsonl')),
                  reasons=[], candidates=[], finalReference=None,
                  materialCandidates=[], materialMethod='Whole top material/shadow ROIs versus settled frame, RGB MAE > 0.012; visual review required, including during drain.',
                  method='Final-frame edge masks in three top islands and dock; masked RGB MAE <= 0.08. Heuristic candidates require visual review.',
                  limitations=['No finite screenshot sampling proves every displayed frame.',
                               'Grim cannot observe the DRM/VT handoff; absent host screendumps leave that interval unknown.',
                               'Mapped layer/service state alone is never a visual pass.',
                               'Niri layer IPC has namespaces but no surface IDs; window IDs are preserved without titles.'])
    if not user:
        report['status'] = 'INCOMPLETE'
        report['reasons'].append('No user-session frames captured.')
    else:
        report['finalReference'] = user[-1]['file']
        with Image.open(evidence / user[-1]['file']) as image:
            reference = image.convert('RGB')
        final_meta = nearest_metadata(metadata, user[-1]['endNs'])
        namespaces = {item.get('namespace') for item in (final_meta or {}).get('layers') or []}
        report['outputsObserved'] = sorted({item['output'] for row in metadata for item in row.get('layers') or [] if item.get('output')})
        report['roiApplicable'] = len(report['outputsObserved']) == 1
        if not report['roiApplicable']:
            report['reasons'].append('Combined screenshot has unknown or multiple output geometry; default ROI scores cannot judge each monitor.')
        if not {'emaki-test-bar', 'emaki-test-dock'} <= namespaces or 'emaki-session-cover' in namespaces:
            report['reasons'].append('Final frame lacks fresh evidence of both shell layers after cover removal.')
        stable = True
        for row in user[-5:]:
            with Image.open(evidence / row['file']) as image:
                scores = [roi_score(image, reference, region) for region in ROIS.values()]
            stable = stable and all(score is not None and score <= .08 for score in scores)
        report['referenceStable'] = stable and len(user) >= 5
        if not report['referenceStable']:
            report['reasons'].append('Final shell ROI reference is unstable or has insufficient foreground detail.')
        with (output / 'visual-trace.jsonl').open('w') as trace:
            for row in user:
                with Image.open(evidence / row['file']) as image:
                    scores = {name: roi_score(image, reference, region) for name, region in ROIS.items()}
                    materials = {name: material_score(image, reference, region) for name, region in MATERIAL_ROIS.items()}
                observation = nearest_metadata(metadata, row['endNs'])
                layers = {item.get('namespace') for item in (observation or {}).get('layers') or []}
                covered = 'emaki-session-cover' in layers if observation and observation.get('layers') is not None else None
                matches = {name: score is not None and score <= .08 for name, score in scores.items()}
                partial = any(matches.values()) and not all(matches.values())
                unfinished = covered is False and not all(matches.values())
                phase = cover_phase(cover_events, row['endNs'])
                material_changed = any(score is not None and score > .012 for score in materials.values())
                material_candidate = material_changed and (phase in ('drain-ready', 'drain-cap', 'finished') or covered is False)
                result = dict(file=row['file'], epochNs=row['endNs'], scores=scores, matches=matches,
                              coverMapped=covered, partialRoiCandidate=partial,
                              uncoveredUnfinishedCandidate=unfinished,
                              coverPhase=phase, materialScores=materials, materialChangeCandidate=material_candidate,
                              startup=(observation or {}).get('startup', []))
                trace.write(json.dumps(result) + '\n')
                if partial or unfinished:
                    report['candidates'].append(dict(file=row['file'], epochNs=row['endNs'], coverMapped=covered,
                                                     kind='uncovered-unfinished' if unfinished else 'different-ROI-progress-during-cover'))
                if material_candidate:
                    report['materialCandidates'].append(dict(file=row['file'], epochNs=row['endNs'], coverPhase=phase,
                                                             scores=materials, kind='bar-material-or-shadow-change'))
        if report['candidates']:
            report['reasons'].append('Review candidate frames for a bar without dock, moving islands, or legitimate plate drain.')
        if report['materialCandidates']:
            report['reasons'].append('Review bar material/shadow candidates against the final frame; the legitimate draining sheet can differ while still covering this ROI.')
    if gaps['over180Ms'] or not gaps['frames']:
        report['reasons'].append('User capture has missing frames or gaps over 180ms; the intervening display is unknown.')
    if not report['hostCapture']['frames'] or report['hostCapture']['over180Ms']:
        report['reasons'].append('Host display capture is absent or discontinuous; DRM handoff coverage is incomplete.')
    states = [state for row in metadata for state in row.get('startup', [])]
    report['observedStartup'] = {key: any(state.get(key) is True for state in states)
                                 for key in ('compositorClaimed', 'shellClaimed', 'shellReady', 'coverFinished')}
    if not all(report['observedStartup'].values()):
        report['reasons'].append('Some startup lifecycle claims were not observed; inspect trace and cover diagnostics.')
    guest_host_offset = (synchronization or {}).get('guestMinusHostNs', 0)
    physical = [dict(row, startNs=row['startNs'] + guest_host_offset,
                     endNs=row['endNs'] + guest_host_offset)
                for row in lines(evidence / 'host-frames.jsonl') if row.get('state') == 'frame']
    report['plateContinuity'] = continuity_analysis(evidence, physical, greeter, metadata, cover_events,
                                                   report.get('roiApplicable', False),
                                                   period_ms=manifest.get('requestedIntervalMs'), capture_start_ns=first)
    if first is None:
        report['plateContinuity']['incomplete'].append('first-user-socket-timestamp-unavailable')
    report['plateContinuity']['status'] = continuity_status(report['plateContinuity'])
    if report['plateContinuity']['failures']:
        report['status'] = 'FAIL'
        report['reasons'].append('Observed pre-drain plate regression; inspect plateContinuity.failures. This is a visual failure, not merely review-required evidence.')
    elif report['plateContinuity']['incomplete']:
        report['status'] = 'INCOMPLETE'
        report['reasons'].append('Required plate proof is incomplete: ' + ', '.join(report['plateContinuity']['incomplete']) + '.')
    report['configEvents'] = lines(evidence / 'guest/config-events.jsonl')
    report['coverEvents'] = cover_events
    report['clockSync'] = synchronization
    guest_host_offset = (synchronization or {}).get('guestMinusHostNs', 0)
    host_good = [dict(row, rawHostEndNs=row['endNs'], endNs=row['endNs'] + guest_host_offset)
                 for row in lines(evidence / 'host-frames.jsonl') if row.get('state') == 'frame']
    greeter_good = [row for row in frames if row.get('side') == 'greeter' and row.get('state') == 'frame']
    report['handoff'] = dict(firstUserSocketNs=first,
                            firstUserFrameNs=user[0]['endNs'] if user else None,
                            firstUserCaptureLatencyMs=round((user[0]['endNs'] - first) / 1e6, 2) if user and first else None,
                            lastGreeterFrameNs=greeter_good[-1]['endNs'] if greeter_good else None,
                            hostFrames=len(host_good),
                            drmContinuity='UNKNOWN; inspect clock-aligned host frames and gaps, or no host evidence if screendump failed')
    report['topMessageSource'] = 'UNKNOWN until full-size frames identify it; compositor config notices/hotkey overlay may have no layer/window entry.'
    report['reasons'].append('Review full-size frames and top-message sheets; automation never marks this visual acceptance PASS.')
    report['summary'] = analysis_summary(report)
    review_frames = [dict(row, file='guest/' + row['file']) for row in frames if row.get('state') == 'frame']
    review_frames.extend(host_good)
    top_contact_sheets(output, sorted(review_frames, key=lambda row: row['endNs']), evidence)
    (output / 'analysis.json').write_text(json.dumps(report, indent=2))
    return report


def ready_line(process):
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(10):
            raise RuntimeError('guest capture did not become ready')
        row = json.loads(process.stdout.readline())
    if row.get('event') != 'ready' or row.get('greeterFrameReady') is not True:
        raise RuntimeError('no greeter frame before authentication; refusing to type')
    if row.get('userSocketBeforeAuth') is True:
        raise RuntimeError('user socket existed before authentication; refusing to type')
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    check.add_arguments(parser)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--analyze-only', type=Path, help='read existing evidence without SSH/VM operations; write reports only under --output')
    args = parser.parse_args()
    check.configure(args)
    output = args.output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(output.iterdir()):
        parser.error('choose a new empty evidence directory')
    if args.analyze_only:
        source = args.analyze_only.resolve()
        run = json.loads((source / 'run.json').read_text()) if (source / 'run.json').exists() else {}
        report = analyze(source, run.get('clockSync'), destination=output)
        print(report['summary'] + '; ' + str(output / 'analysis.json'))
        return 1 if report['status'] in ('FAIL', 'INCOMPLETE') else 2
    check.OUT = output
    assert check.checked_remote('systemd-detect-virt --vm').strip() in ('qemu', 'kvm'), 'requires disposable QEMU/KVM'
    for name in ('guest-greeter.py', 'guest-keys.py', 'guest-session-start.py'):
        check.checked_remote('cat > ' + shlex.quote('/tmp/c9-' + name), data=(HERE / name).read_text())
    token = secrets.token_hex(16)
    result = dict(version=1, token=token, freshPamSessionOpened=False, captureComplete=False)
    process = None
    stop = threading.Event()
    worker = None
    login_started = None
    phase = 'greeter-recovery'
    fixture_attempted = False
    remote_command = ('python3 /tmp/c9-guest-session-start.py --token ' + token
                      + ' --user ' + shlex.quote(check.TARGET.user))
    try:
        check.recover()
        check.guest('select-emaki')
        phase = 'wallpaper-fixture'
        fixture_attempted = True
        result['wallpaperFixture'] = json.loads(check.checked_remote(remote_command + ' --operation wallpaper-prepare', privileged=True))
        check.restart()
        phase = 'clock-sync'
        result['clockSync'] = clock_sync()
        command, authentication = check.TARGET.privileged_command(remote_command)
        process = subprocess.Popen(check.TARGET.ssh_argv() + [command], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        process.stdin.write(authentication)
        process.stdin.close()
        process.stdin = None
        worker = threading.Thread(target=host_frames, args=(output, stop), daemon=True)
        worker.start()
        phase = 'pre-auth-capture'
        result['ready'] = ready_line(process)
        mark = check.journal_mark()
        result['pamSince'] = mark
        login_started = time.monotonic()
        phase = 'uinput-login'
        result['inputBeginHostNs'] = time.time_ns()
        check.keys('text', 'key:Return', text=check.TARGET.password)  # Disposable credentials, stdin only.
        result['inputEndHostNs'] = time.time_ns()
        phase = 'continuous-capture'
        stdout, stderr = process.communicate(timeout=45)
        result['captureExit'] = process.returncode
        result['captureEvents'] = [json.loads(line) for line in stdout.splitlines() if line.startswith('{')]
        result['captureStderrPresent'] = bool(stderr)  # Never copy arbitrary remote text into evidence.
        stop.set()
        worker.join(timeout=10)
        if worker.is_alive():
            raise RuntimeError('host capture did not stop within its bound')
        phase = 'guest-export'
        archive = output / 'guest.tar'
        with archive.open('xb') as stream:
            command, authentication = check.TARGET.privileged_command(remote_command + ' --operation export')
            export = subprocess.run(check.TARGET.ssh_argv() + [command], input=authentication.encode(),
                                    stdout=stream, stderr=subprocess.PIPE, timeout=45)
        assert export.returncode == 0, 'guest capture export failed; evidence remains under /run/c9-session-start/' + token
        extract_evidence(archive, output / 'guest')
        archive.unlink()
        result['captureComplete'] = process.returncode == 0
        phase = 'frame-analysis'
        report = analyze(output, result['clockSync'])
        result['analysisStatus'] = report['status']
        result['analysisSummary'] = report['summary']
        phase = 'fresh-PAM-and-session-proof'
        state = check.session('niri-emaki.service', mark)
        result['freshPamSessionOpened'] = check.pam_session_opened(state['journal'])
        result['sessionServices'] = {key: state['services'].get(key) for key in ('niri-emaki.service', 'emaki-shell.service')}
    except Exception as error:
        result['errorType'] = type(error).__name__
        result['error'] = 'failed during ' + phase
    finally:
        stop.set()
        if worker:
            worker.join(timeout=10)
        if process and process.poll() is None:
            # Only the exact local SSH child is terminated; remote capture has
            # its own 35-second bound and retains evidence if retrieval failed.
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        try:
            # Avoid making this diagnostic logout look like an early session death.
            if login_started is not None:
                time.sleep(max(0, login_started + 34 - time.monotonic()))
            check.recover(force='error' in result)
            result['cleanup'] = 'healthy-greeter'
        except Exception as error:
            result['cleanup'] = 'failed: ' + type(error).__name__
        # Try restoration independently even when compositor recovery failed.
        # A retained per-token backup and the command below make interrupted
        # diagnostics recoverable without reading a user file as root.
        if fixture_attempted:
            try:
                result['wallpaperCleanup'] = json.loads(check.checked_remote(remote_command + ' --operation wallpaper-restore', privileged=True))
                check.restart()  # The next greeter must see the restored publication.
            except Exception as error:
                result['cleanup'] = 'wallpaper restore failed: ' + type(error).__name__
                result['wallpaperRestoreCommand'] = 'sudo -- ' + remote_command + ' --operation wallpaper-restore'
        (output / 'run.json').write_text(json.dumps(result, indent=2))
    print('Session-start evidence: ' + str(output))
    print(result.get('analysisSummary', 'INCOMPLETE: physical whole-frame continuity unavailable')
          + '. Read run.json, analysis.json, visual-trace.jsonl and top-message sheets.')
    return 1 if (result.get('error') or result.get('cleanup') != 'healthy-greeter' or not result['captureComplete']
                 or result.get('analysisStatus') in ('FAIL', 'INCOMPLETE')) else 2


if __name__ == '__main__':
    raise SystemExit(main())
