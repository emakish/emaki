#!/usr/bin/env python3
"""Release-walk gate (Phase 0): accept a walk record only under the rules of plan 5.3a.

  eyes-gate.py --record WALK/walk.toml --iso RELEASE.iso [--signoff WALK/signoff.toml]
               [--image-verified]

Exit 0 and "RESULT: PASSED" only when: the record's sha256 equals the image's (computed
here); the image passes iso/verify-image.py in release mode (skipped with --image-verified
when the caller just ran it); every stage of every required run (stages.toml) has a result;
every named frame exists with its sha256; and nothing below stops the release:
  FAIL            a stage FAIL (verdict BROKEN/BLACK, missing frame, ...); a CONFUSING FAIL
                  only passes when the owner waived that stage by name in signoff.toml;
  NOT TESTED      the VM could not show it: blocks until signoff.toml holds a "seen" result
                  of its hardware line for this image sha256;
  NOT APPLICABLE  needs its reference (and the frame of the absence when a feature was hidden).
Result lines never mix the states. Exit 1 = FAILED, 3 = NOT TESTED, 2 = usage.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import subprocess
import sys
import tomllib

from PIL import Image

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
from menu_mode import MissingMode, configured_size
RESULTS = ('PASS', 'FAIL', 'NOT TESTED', 'NOT APPLICABLE')
VERDICTS = ('OK', 'CONFUSING', 'BROKEN', 'BLACK')
MIN_SEEN = 40
PICKS = 5


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


class Findings:
    def __init__(self):
        self.items = []  # (state, where, message)

    def fail(self, where, message):
        self.items.append(('FAIL', where, message))

    def not_tested(self, where, message):
        self.items.append(('NOT TESTED', where, message))

    def first(self, state):
        return next((item for item in self.items if item[0] == state), None)


def check_frames(base, where, table, findings, required):
    frames, sums = table.get('frames', []), table.get('sha256', [])
    if not isinstance(frames, list) or not isinstance(sums, list) or len(frames) != len(sums):
        findings.fail(where, 'frames and sha256 must be lists of equal length')
        return False
    if required and not frames:
        findings.fail(where, 'no frame named')
        return False
    ok = True
    for name, expected in zip(frames, sums):
        path = (base / name).resolve()
        if base.resolve() not in path.parents:
            findings.fail(where, f'frame outside the walk directory: {name}')
            ok = False
        elif not path.is_file():
            findings.fail(where, f'frame missing: {name}')
            ok = False
        elif sha256_file(path) != expected:
            findings.fail(where, f'frame sha256 differs from the record: {name}')
            ok = False
    return ok


def check_seen(where, table, expected, findings):
    seen = str(table.get('seen', '')).strip()
    if len(seen) < MIN_SEEN:
        findings.fail(where, f'seen has {len(seen)} characters; a literal transcript of at least {MIN_SEEN} is required')
        return False
    if seen == expected.strip():
        findings.fail(where, 'seen repeats the expected text instead of describing the frame')
        return False
    return True


def check_resolutions(base, where, stage, table, findings, signoff=None):
    """A judgement at one size cannot accept controls at another size."""
    checks = table.get('resolution_checks', {})
    for size in stage.get('resolutions', []):
        item = checks.get(size, {})
        label = f'{where} at {size}'
        if not item:
            findings.not_tested(label, 'no separately judged frame at this resolution')
            continue
        run, sid = where.split('/')
        waived = item.get('verdict') == 'CONFUSING' and any(
            w.get('run') == run and w.get('stage') == sid and w.get('resolution') == size
            and str(w.get('sentence', '')).strip() for w in (signoff or {}).get('waive', []))
        if item.get('verdict') != 'OK' and not waived:
            if item.get('verdict') in ('BROKEN', 'BLACK', 'CONFUSING'):
                findings.fail(label, 'resolution judgement: ' + item['verdict'])
            else:
                findings.not_tested(label, 'frame has not been judged')
            continue
        if waived:
            print(f'{label}: CONFUSING, waived by resolution on the signed sheet')
        if not check_frames(base, label, item, findings, required=True):
            continue
        check_seen(label, item, stage['expected'], findings)
        expected = tuple(map(int, size.split('x')))
        if stage.get('configured_menu_mode'):
            try:
                expected = configured_size(base, item, expected)
            except MissingMode as error:
                findings.not_tested(label, str(error))
                continue
            except (OSError, ValueError, TypeError) as error:
                findings.fail(label, str(error))
                continue
        for name in item['frames']:
            try:
                with Image.open(base / name) as frame:
                    frame.load()
                    if frame.size != expected:
                        findings.fail(label, f'actual frame size {frame.size} differs from {expected}')
            except (OSError, ValueError) as error:
                findings.fail(label, f'frame cannot be decoded: {error}')


def check_progress(base, where, stage, table, findings):
    spec = importlib.util.spec_from_file_location('install_progress', HERE.parent / 'check-install-progress.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for size in stage['resolutions']:
        label = f'{where} progress at {size}'
        item = table.get('progress_checks', {}).get(size, {})
        if item.get('covers_installation') is not True or item.get('roi_reviewed') is not True:
            findings.not_tested(label, 'review must confirm the full installation interval and progress-only region')
            continue
        try:
            path = (base / item['timeline']).resolve()
            if not path.is_relative_to(base.resolve()):
                raise ValueError('timeline escapes walk directory')
            if sha256_file(path) != item['sha256']:
                raise ValueError('timeline hash differs from reviewed evidence')
            progress = module.check(base, json.loads(path.read_text()), item['roi'], tuple(map(int, size.split('x'))),
                                    require_complete=True)
            if progress.get('too_short_to_see'):
                print(f'{label}: timeline-proven, too short to see (not seen): '
                      + ', '.join(progress['too_short_to_see']))
        except (module.NotTested, FileNotFoundError) as error:
            findings.not_tested(label, str(error))
        except (OSError, ValueError, KeyError, TypeError) as error:
            findings.fail(label, f'invalid progress evidence: {error}')


def check_actions(base, where, stage, table, findings):
    for action in stage.get('actions', []):
        label = f'{where} {action}'
        item = table.get('action_checks', {}).get(action, {})
        if not item:
            findings.not_tested(label, 'no UI action and outcome recorded')
            continue
        if not str(item.get('trigger', '')).startswith(('keys:', 'click:')):
            findings.fail(label, 'action requires a UI key or click trigger')
        if item.get('completed') is not True:
            findings.fail(label, 'action did not complete')
        if item.get('verdict') != 'OK':
            findings.not_tested(label, 'action outcome has not been judged')
        check_seen(label, item, stage.get('action_expected', {}).get(action, stage['expected']), findings)
        check_frames(base, label, item, findings, required=True)
        event = {'restart': 'RESET', 'shutdown': 'SHUTDOWN',
                 'lock-restart': 'RESET', 'lock-shutdown': 'SHUTDOWN'}.get(action)
        if event != 'SHUTDOWN' and len(item.get('frames', [])) < 2:
            findings.fail(label, 'action needs before and after frames')
        if event:
            try:
                path = (base / item['events']).resolve()
                if not path.is_relative_to(base.resolve()):
                    raise ValueError('event log escapes walk directory')
                if sha256_file(path) != item['events_sha256']:
                    raise ValueError('event log hash differs from recorded evidence')
                events = json.loads(path.read_text())
                if not any(e.get('event') == event and e.get('data', {}).get('guest') is True
                           for e in events):
                    raise ValueError(f'no guest {event} event; a monitor stop or reset is not proof')
            except (OSError, KeyError, TypeError, ValueError, AttributeError) as error:
                findings.fail(label, f'invalid guest event evidence: {error}')


def check_stage(base, run, sid, stage, table, signoff, findings, passes):
    where = f'{run}/{sid}'
    result = table.get('result', '')
    verdict = table.get('verdict', '')
    if result not in RESULTS:
        findings.fail(where, f'no result (unjudged); one of {", ".join(RESULTS)} is required')
        return
    if verdict and verdict not in VERDICTS:
        findings.fail(where, f'unknown verdict {verdict!r}')
        return
    if result == 'PASS':
        if verdict != 'OK':
            findings.fail(where, f'PASS needs verdict OK, not {verdict or "empty"}')
            return
        frames_ok = check_frames(base, where, table, findings, required=True)
        seen_ok = check_seen(where, table, stage['expected'], findings)
        check_resolutions(base, where, stage, table, findings, signoff)
        check_actions(base, where, stage, table, findings)
        if sid == '16':
            check_progress(base, where, stage, table, findings)
        if sid == '39' and not any('usb-storage,drive=liveiso' in p.read_text()
                                   for p in base.glob('*/*/qemu-cmdline.txt')):
            findings.not_tested(where, 'no recorded QEMU command boots the image as usb-storage; CD boot does not cover this stage')
            return
        if stage.get('lock'):
            test = table.get('input_test', {})
            if not (test.get('marker_absent') is True and test.get('frames')
                    and isinstance(test.get('grab_interval_ms'), (int, float)) and test['grab_interval_ms'] > 0):
                findings.fail(where, 'a lock stage passes only with the 5.1a input test: marker_absent = true, '
                                     'its frames and the measured grab_interval_ms')
                return
            if not check_frames(base, where + ' input test', test, findings, required=True):
                return
        if frames_ok and seen_ok:
            passes.append(where)
    elif result == 'FAIL':
        if verdict in ('OK', ''):
            if not str(table.get('reason', '')).strip():
                findings.fail(where, 'FAIL needs a verdict CONFUSING/BROKEN/BLACK or the reason (missing frame, over-limit interval, failed input test)')
                return
        else:
            check_frames(base, where, table, findings, required=True)
            check_seen(where, table, stage['expected'], findings)
        waived = verdict == 'CONFUSING' and any(
            w.get('run') == run and w.get('stage') == sid and not w.get('resolution')
            and str(w.get('sentence', '')).strip()
            for w in signoff.get('waive', []))
        if waived:
            print(f'{where}: FAIL, waived by name on the signed sheet: {stage["title"]}')
        else:
            detail = table.get('seen') or table.get('reason') or ''
            findings.fail(where, f'{verdict or "FAIL"}: {stage["title"]}' + (f' - {detail}' if detail else ''))
    elif result == 'NOT TESTED':
        line = str(table.get('hardware_line', '')).strip()
        if not str(table.get('reason', '')).strip() or not line:
            findings.fail(where, 'NOT TESTED needs the reason and the hardware-sheet line that covers it')
            return
        check_frames(base, where, table, findings, required=False)
        results = [h for h in signoff.get('hardware', [])
                   if h.get('line') == line and h.get('iso_sha256') == signoff.get('_iso')]
        if any(h.get('result') == 'failed' for h in results):
            findings.fail(where, f'hardware line {line} failed on this image')
        elif not any(h.get('result') == 'seen' for h in results):
            findings.not_tested(where, f'{stage["title"]}: {table["reason"]}; hardware line {line} has no result for this image')
    else:  # NOT APPLICABLE
        if not str(table.get('reference', '')).strip():
            findings.fail(where, 'NOT APPLICABLE needs its reference (DECISIONS entry or scenario parameter)')
            return
        check_frames(base, where, table, findings, required=bool(table.get('hidden')))


def gate(record_path, iso, signoff_path, image_verified, stages_path, out=print):
    findings = Findings()
    stages_doc = tomllib.loads(Path(stages_path).read_text())
    record = tomllib.loads(Path(record_path).read_text())
    base = Path(record_path).resolve().parent
    header = record.get('walk', {})
    iso_sha = sha256_file(iso)
    out(f'image: {Path(iso).resolve()} sha256 {iso_sha}')
    if header.get('iso_sha256') != iso_sha:
        findings.fail('header', f'record sha256 {header.get("iso_sha256")!r} is not the image sha256 {iso_sha}')
    for field in ('date', 'walked_by', 'reviewed_by'):
        if not str(header.get(field, '')).strip():
            findings.fail('header', f'{field} is empty')
    if header.get('walked_by') and header.get('walked_by') == header.get('reviewed_by'):
        findings.fail('header', 'the reviewer must not be the one who walked')
    if not header.get('qemu'):
        findings.fail('header', 'no QEMU command line recorded')
    if not image_verified:
        check = subprocess.run([sys.executable, str(ROOT / 'iso/verify-image.py'), str(iso)],
                               capture_output=True, text=True)
        if check.returncode:
            text = (check.stdout + check.stderr).strip()
            findings.fail('image', 'iso/verify-image.py (release mode) refused the image (a test ISO?): '
                          + (text.splitlines()[-1] if text else f'exit {check.returncode}'))
    signoff = {}
    if signoff_path and Path(signoff_path).is_file():
        signoff = tomllib.loads(Path(signoff_path).read_text())
    signoff['_iso'] = iso_sha
    passes = []
    runs = record.get('runs', {})
    for run, spec in stages_doc['runs'].items():
        for sid in spec['stages']:
            table = runs.get(run, {}).get('stages', {}).get(sid)
            if table is None:
                findings.fail(f'{run}/{sid}', 'stage missing from the record')
                continue
            check_stage(base, run, sid, stages_doc['stages'][sid], table, signoff, findings, passes)
    # Five PASS frames for the owner, chosen from the image sha so the reviewer cannot know them.
    picks = sorted(random.Random(int(iso_sha, 16)).sample(sorted(passes), min(PICKS, len(passes))))
    out('frames for the owner to look at in person: first impression '
        + ', '.join(stages_doc['first_impression']) + ' of every run; picked by the gate: '
        + (', '.join(picks) or 'none'))
    if not signoff_path or not Path(signoff_path).is_file():
        findings.not_tested('signoff', 'no signed sheet (signoff.toml)')
    else:
        if signoff.get('iso_sha256') != iso_sha:
            findings.fail('signoff', 'the signed sheet names another image sha256')
        if not str(signoff.get('signed_by', '')).strip():
            findings.not_tested('signoff', 'the sheet is not signed')
        if signoff.get('fixed_frames_seen') is not True:
            findings.not_tested('signoff', 'the owner has not confirmed the six first-impression frames')
        if sorted(signoff.get('picked', [])) != picks:
            findings.not_tested('signoff', 'the sheet does not list the frames this gate picked: ' + ', '.join(picks))
        elif signoff.get('picked_agree') is not True:
            findings.not_tested('signoff', 'the owner disagrees with the review on a picked frame: the review counts as NOT TESTED')
    for state, where, message in findings.items:
        out(f'{where}: {state}: {message}')
    failed = findings.first('FAIL')
    if failed:
        out(f'RESULT: FAILED at {failed[1]}')
        return 1
    missing = findings.first('NOT TESTED')
    if missing:
        out(f'RESULT: NOT TESTED at {missing[1]}')
        return 3
    out(f'RESULT: PASSED (walk record of {iso_sha[:12]})')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--record', required=True)
    parser.add_argument('--iso', required=True)
    parser.add_argument('--signoff')
    parser.add_argument('--image-verified', action='store_true')
    parser.add_argument('--stages', default=str(HERE / 'stages.toml'))
    args = parser.parse_args(argv)
    for path in (args.record, args.iso, args.stages):
        if not Path(path).is_file():
            print(f'RESULT: FAILED at input: {path} is not a file')
            return 1
    try:
        return gate(args.record, args.iso, args.signoff, args.image_verified, args.stages)
    except (tomllib.TOMLDecodeError, KeyError, TypeError, ValueError, AttributeError) as error:
        print(f'RESULT: FAILED at record: unreadable ({type(error).__name__}: {error})')
        return 1


if __name__ == '__main__':
    sys.exit(main())
