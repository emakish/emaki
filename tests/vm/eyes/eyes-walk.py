#!/usr/bin/env python3
"""Release walk: record every frame a person sees on the
RELEASE image under ~/VMs/eyes/<iso-sha12>/walk-<date>/ and keep walk.toml, the record that
eyes-gate.py judges.

  init --iso FILE [--root DIR] [--walked-by NAME]   new walk directory (never reused)
  plan                                              runs W1-W3 and their stages, in order
  capture --run W1 --stage 05 --label NAME [--series [--interval S --timeout S --stable S]]
  disk-unlock  --run W2 --stage 20                  3 chars, wrong + Enter, right + Enter (5.1 stage 20)
  greeter      --run W1 --stage 24 [--next 25]      untouched 20 s, wrong password, right password
  input-test   --run W1 --stage 29 --trigger keys:meta_l-l|click:X,Y|qmp:CMD|none [--duration 5]
  session-unlock --run W1 --stage 29                wrong password, then the right one (after input-test)
  index                                             index.html + index.txt, frame by frame

Every command but init/plan takes --walk DIR (or EYES_WALK) and drives the pass that
eyes-vm.sh started last. Typed text comes from <walk>/inputs.toml, never the command line.
walk.toml is rewritten when frames are recorded: edit only its values, comments are not kept.
"""
import argparse
import datetime
import html
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import tomllib

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import eyes  # noqa: E402

STAGES = HERE / 'stages.toml'
STAGE_FIELDS = ('result', 'verdict', 'seen', 'reason', 'hardware_line', 'reference')
INPUTS = '''# Text typed into the guest during the walk (disposable VM accounts only).
# eyes.py type-input NAME and the eyes-walk.py scenarios read it from here, never from a command line.
# US key positions only: the GRUB passphrase prompt reads US positions whatever the layout.
user_login = "walker"
user_password = "emaki-walk-pass1"
wrong_password = "wrong-pass-0000"
disk_passphrase = "emaki-disk-pass1"
wrong_passphrase = "wrong-disk-0000"
marker = "eyesmarker4711"
'''
SIGNOFF = '''# The signed sheet in machine form, filled in by the person who signs the release.
# eyes-gate.py reads it.
iso_sha256 = "{sha}"
signed_by = ""
date = ""
fixed_frames_seen = false       # the signer looked at stages {first} of every run
picked = []                     # the frames eyes-gate.py printed as "picked by the gate", e.g. "W1/24"
picked_agree = false            # false if the signer disagrees with the review on any of them

# One block per CONFUSING stage the signer accepts for this release:
# [[waive]]
# run = "W1"
# stage = "26"
# sentence = "the signer's words"

# One block per hardware line the signer checked on this image:
# [[hardware]]
# line = "H-M11"
# iso_sha256 = "{sha}"
# result = "seen"               # or "failed"
# note = "MacBook, date"
'''


RECORD_HEADER = '''# Walk record of one release image, judged by eyes-gate.py.
# Per stage write: result = PASS | FAIL | NOT TESTED | NOT APPLICABLE; verdict = OK | CONFUSING |
# BROKEN | BLACK; seen = a literal transcript of the frame (>= 40 characters, never the expected
# text); reason + hardware_line for NOT TESTED; reference (+ hidden = true and the frame of the
# absence) for NOT APPLICABLE. Lock stages: input_test.marker_absent after reading its frames.
# eyes-walk.py rewrites this file when it records frames; edit values only.
'''


def sha256_file(path):
    return eyes.file_sha256(path)


def load_stages():
    return tomllib.loads(STAGES.read_text())


class Walk:
    def __init__(self, directory):
        self.dir = Path(directory).resolve()
        if not (self.dir / 'walk.toml').is_file():
            raise SystemExit(f'{self.dir} is not a walk directory (no walk.toml): run eyes-walk.py init')
        self.stages = load_stages()
        self._vm = None

    @property
    def record(self):
        return tomllib.loads((self.dir / 'walk.toml').read_text())

    def save(self, record):
        record['walk']['qemu'] = sorted({p.read_text().strip() for p in self.dir.glob('*/*/qemu-cmdline.txt')})
        temporary = self.dir / 'walk.toml.tmp'
        temporary.write_text(eyes.dump_toml(record, RECORD_HEADER))
        temporary.replace(self.dir / 'walk.toml')

    def stage_table(self, record, run, stage):
        if stage not in self.stages['stages']:
            raise SystemExit(f'unknown stage {stage}; stages.toml lists the stages')
        runs = record.setdefault('runs', {})
        table = runs.setdefault(run, {}).setdefault('stages', {}).setdefault(stage, skeleton(self.stages, stage))
        return table

    def pass_(self):
        if self._vm is None:
            self._vm = eyes.Pass((self.dir / 'current-pass').read_text().strip(), eyes.load_inputs(self.dir))
        return self._vm

    def close(self):
        if self._vm is not None:
            self._vm.close()

    def saver(self, run, stage, label, store):
        folder = self.dir / 'frames' / run / stage
        folder.mkdir(parents=True, exist_ok=True)
        count = [len(list(folder.glob('*.png')))]
        current = (self.dir / 'current-pass').read_text().strip()

        def save(img, t):
            count[0] += 1
            name = folder / f'{count[0]:03d}-{label}-t{t:07.3f}.png'
            img.save(name)
            relative = str(name.relative_to(self.dir))
            digest = sha256_file(name)
            store.append((relative, digest))
            with (self.dir / 'frames.jsonl').open('a') as stream:
                stream.write(json.dumps({'run': run, 'stage': stage, 'label': label, 'file': relative,
                                         'sha256': digest, 't': t, 'pass': current,
                                         'size': list(img.size), 'time': time.strftime('%FT%T')}) + '\n')
            return relative
        return save

    def record_frames(self, run, stage, frames, timing=None, label=None, input_test=None):
        record = self.record
        table = self.stage_table(record, run, stage)
        target = table
        if input_test is not None:
            target = table.setdefault('input_test', {'marker_absent': False, 'frames': [], 'sha256': []})
            for key, value in input_test.items():
                target[key] = value
        target['frames'] = list(target.get('frames', [])) + [name for name, _ in frames]
        target['sha256'] = list(target.get('sha256', [])) + [digest for _, digest in frames]
        if timing is not None:
            table.setdefault('timing', {})[label] = timing
        self.save(record)

    def series(self, vm, run, stage, label, interval=1.0, timeout=60.0, stable=5.0, input_test=None):
        store = []
        timeline, result = vm.series(self.saver(run, stage, label, store), interval, timeout, stable)
        folder = self.dir / 'frames' / run / stage
        (folder / f'{label}-timeline.json').write_text(json.dumps({'timeline': timeline, **result}, indent=1) + '\n')
        timing = {k: v for k, v in result.items() if v is not None}
        self.record_frames(run, stage, store, timing, label, input_test)
        print(f'{run}/{stage} {label}: {len(store)} frames; ' + ', '.join(f'{k}={v}' for k, v in timing.items()))
        return store

    def shot(self, vm, run, stage, label, input_test=None):
        store = []
        img, _ = vm.grab()
        self.saver(run, stage, label, store)(img, 0.0)
        self.record_frames(run, stage, store, input_test=input_test)
        print(f'SHOT: {self.dir / store[0][0]} (not judged)')
        return store


def skeleton(stages, sid):
    stage = stages['stages'][sid]
    table = {'title': stage['title'], 'expected': stage['expected']}
    for field in STAGE_FIELDS:
        table[field] = ''
    table['hardware_line'] = stage.get('hardware_line', '')
    table['frames'] = []
    table['sha256'] = []
    if stage.get('lock'):
        table['input_test'] = {'marker_absent': False, 'frames': [], 'sha256': []}
    return table


def cmd_init(args):
    iso = Path(args.iso).resolve()
    if not iso.is_file():
        raise SystemExit(f'not an image file: {iso}')
    sha = sha256_file(iso)
    listed = Path(str(iso) + '.sha256')
    if not listed.is_file() or listed.read_text().split()[:1] != [sha]:
        raise SystemExit(f'{listed} is missing or does not list the computed sha256 {sha}')
    check = subprocess.run([sys.executable, str(ROOT / 'iso/verify-image.py'), str(iso)], capture_output=True, text=True)
    if check.returncode:
        raise SystemExit('iso/verify-image.py (release mode) refused the image; a walk is only made on a '
                         'release image:\n' + (check.stdout + check.stderr).strip())
    root = Path(args.root or os.environ.get('EYES_ROOT') or Path.home() / 'VMs/eyes')
    date = datetime.date.today().isoformat()
    parent = root / sha[:12]
    parent.mkdir(parents=True, exist_ok=True)
    walk = parent / f'walk-{date}'
    n = 1
    while walk.exists():
        n += 1
        walk = parent / f'walk-{date}-r{n}'
    walk.mkdir()
    stages = load_stages()
    commit = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
    record = {'walk': {'iso_path': str(iso), 'iso_sha256': sha,
                       'checkout_iso_version': (ROOT / 'iso/VERSION').read_text().strip(),
                       'harness_commit': commit, 'date': date, 'walked_by': args.walked_by or '',
                       'reviewed_by': '', 'version_on_screen': '', 'commit_on_screen': '', 'qemu': []},
              'runs': {}}
    for run, spec in stages['runs'].items():
        record['runs'][run] = {'title': spec['title'], 'passes': spec['passes'],
                               'stages': {sid: skeleton(stages, sid) for sid in spec['stages']}}
    (walk / 'iso.sha256').write_text(sha + '\n')
    (walk / 'inputs.toml').write_text(INPUTS)
    (walk / 'signoff.toml').write_text(SIGNOFF.format(sha=sha, first=', '.join(stages['first_impression'])))
    (walk / 'walk.toml').write_text(eyes.dump_toml(record, RECORD_HEADER))
    print(f'EYES_WALK={walk}')
    return 0


def cmd_plan(args):
    stages = load_stages()
    for run, spec in stages['runs'].items():
        print(f'{run}: {spec["title"]}\n    passes: {spec["passes"]}')
        for sid in spec['stages']:
            stage = stages['stages'][sid]
            extra = ' [lock: input-test + session-unlock]' if stage.get('lock') else ''
            extra += f' [hardware line {stage["hardware_line"]}]' if stage.get('hardware_line') else ''
            print(f'  {sid:>3} {stage["pass"]:<5} {stage["title"]}{extra}')
    return 0


def cmd_capture(walk, args):
    vm = walk.pass_()
    if args.series:
        walk.series(vm, args.run, args.stage, args.label, args.interval, args.timeout, args.stable)
    else:
        walk.shot(vm, args.run, args.stage, args.label)
    return 0


def cmd_disk_unlock(walk, args):
    vm = walk.pass_()
    wrong = vm.inputs['wrong_passphrase']
    walk.shot(vm, args.run, args.stage, 'prompt')
    vm.type_text(wrong[:3])
    time.sleep(.5)
    walk.shot(vm, args.run, args.stage, 'typed-3')
    vm.keys('backspace', 'backspace', 'backspace')
    vm.type_input('wrong_passphrase')
    vm.keys('ret')
    walk.series(vm, args.run, args.stage, 'after-wrong', interval=.5, timeout=60, stable=5)
    vm.type_input('disk_passphrase')
    vm.keys('ret')
    walk.series(vm, args.run, args.stage, 'after-right', interval=.5, timeout=180, stable=10)
    return 0


def cmd_greeter(walk, args):
    vm = walk.pass_()
    walk.series(vm, args.run, args.stage, 'untouched', interval=1, timeout=20, stable=21)
    vm.type_input('wrong_password')
    vm.keys('ret')
    walk.series(vm, args.run, args.stage, 'wrong-password', interval=.5, timeout=20, stable=3)
    vm.type_input('user_password')
    vm.keys('ret')
    walk.series(vm, args.run, args.next, 'session', interval=1, timeout=180, stable=10)
    return 0


def trigger(vm, spec):
    kind, _, value = spec.partition(':')
    if kind == 'keys':
        vm.key(value)
    elif kind == 'click':
        x, y = (float(v) for v in value.split(','))
        vm.click(x, y)
    elif kind == 'qmp':
        vm.cmd(value)
    elif kind != 'none':
        raise SystemExit(f'unknown trigger {spec!r}')
    vm.log(f'trigger {spec}')


def cmd_input_test(walk, args):
    vm = walk.pass_()
    marker = vm.inputs['marker']
    # 5.1a item 1: the focused, empty terminal before the trigger.
    walk.shot(vm, args.run, args.stage, 'terminal-before', input_test={})
    store = []

    def typing(stop):
        while not stop.is_set():
            for ch in marker + '\n':
                if stop.is_set():
                    return
                vm.key(eyes.qcode(ch), 30)
                time.sleep(.04)

    trigger(vm, args.trigger)
    timeline, interval = vm.burst(walk.saver(args.run, args.stage, 'burst', store), args.duration, typing)
    folder = walk.dir / 'frames' / args.run / args.stage
    (folder / 'burst-timeline.json').write_text(json.dumps({'timeline': timeline, **interval}, indent=1) + '\n')
    walk.record_frames(args.run, args.stage, store, input_test={**interval, 'trigger': args.trigger,
                                                                 'duration_s': args.duration})
    print(f'{args.run}/{args.stage} burst: {len(store)} frames, mean grab interval {interval["grab_interval_ms"]} ms, '
          f'max {interval["grab_interval_max_ms"]} ms; next: session-unlock, then judge the terminal frame')
    walk.series(vm, args.run, args.stage, 'after-transition', interval=.5, timeout=30, stable=3)
    return 0


def cmd_session_unlock(walk, args):
    vm = walk.pass_()
    vm.type_input('wrong_password')
    vm.keys('ret')
    walk.series(vm, args.run, args.stage, 'wrong-password', interval=.5, timeout=20, stable=3)
    vm.type_input('user_password')
    vm.keys('ret')
    walk.series(vm, args.run, args.stage, 'unlocked', interval=.5, timeout=30, stable=3)
    # 5.1a: the terminal after unlock is where the marker must be absent.
    walk.shot(vm, args.run, args.stage, 'terminal-after', input_test={})
    return 0


def cmd_index(walk, args):
    record = walk.record
    stages = walk.stages
    text = [f'Walk {walk.dir}', f'image {record["walk"]["iso_path"]} sha256 {record["walk"]["iso_sha256"]}', '']
    page = ['<!doctype html><meta charset="utf-8"><title>Release walk</title>',
            '<style>body{font:15px sans-serif;margin:16px;background:#fff;color:#111}'
            'img{max-width:100%;border:1px solid #888}.miss{color:#b00;font-weight:bold}'
            'section{border-top:2px solid #444;margin-top:24px}</style>',
            f'<h1>Release walk</h1><p>{html.escape(record["walk"]["iso_path"])}<br>sha256 '
            f'{record["walk"]["iso_sha256"]}<br>walked by {html.escape(record["walk"]["walked_by"] or "?")}, '
            f'reviewed by {html.escape(record["walk"]["reviewed_by"] or "?")}</p>']
    counts = {}
    for run, spec in stages['runs'].items():
        page.append(f'<h2>{run}: {html.escape(spec["title"])}</h2>')
        text.append(f'== {run}: {spec["title"]}')
        for sid in spec['stages']:
            table = record.get('runs', {}).get(run, {}).get('stages', {}).get(sid, {})
            stage = stages['stages'][sid]
            result = table.get('result') or 'UNJUDGED'
            frames = list(table.get('frames', [])) + list(table.get('input_test', {}).get('frames', []))
            counts[result] = counts.get(result, 0) + 1
            missing = not frames and result not in ('NOT TESTED', 'NOT APPLICABLE')
            text.append(f'{run}/{sid} {stage["title"]}')
            text.append(f'  expected: {stage["expected"]}')
            text.append(f'  result: {result}  verdict: {table.get("verdict") or "-"}  seen: {table.get("seen") or "-"}')
            for label, timing in table.get('timing', {}).items():
                text.append(f'  timing {label}: ' + ', '.join(f'{k}={v}' for k, v in timing.items()))
            text.extend(f'  frame: {walk.dir / name}' for name in frames)
            if missing:
                text.append('  MISSING: no frame recorded')
            page.append(f'<section><h3>{run}/{sid} {html.escape(stage["title"])}</h3>'
                        f'<p>expected: {html.escape(stage["expected"])}<br>result: <b>{result}</b> · verdict: '
                        f'{html.escape(table.get("verdict") or "-")}<br>seen: {html.escape(table.get("seen") or "-")}</p>')
            for label, timing in table.get('timing', {}).items():
                page.append(f'<p>{html.escape(label)}: ' + html.escape(', '.join(f'{k}={v}' for k, v in timing.items())) + '</p>')
            if missing:
                page.append('<p class="miss">MISSING: no frame recorded</p>')
            for name in frames:
                page.append(f'<p><a href="{html.escape(name)}"><img loading="lazy" src="{html.escape(name)}"></a>'
                            f'<br>{html.escape(name)}</p>')
            page.append('</section>')
    summary = 'stages: ' + ', '.join(f'{k} {v}' for k, v in sorted(counts.items()))
    page.append(f'<footer><p>{summary}</p></footer>')
    text.append(summary)
    (walk.dir / 'index.html').write_text('\n'.join(page) + '\n')
    (walk.dir / 'index.txt').write_text('\n'.join(text) + '\n')
    print(f'{walk.dir / "index.txt"}\n{walk.dir / "index.html"}\n{summary}')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--walk', default=os.environ.get('EYES_WALK'))
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init')
    init.add_argument('--iso', required=True)
    init.add_argument('--root')
    init.add_argument('--walked-by')
    sub.add_parser('plan')
    sub.add_parser('index')
    for name in ('capture', 'disk-unlock', 'greeter', 'input-test', 'session-unlock'):
        command = sub.add_parser(name)
        command.add_argument('--run', required=True)
        command.add_argument('--stage', required=True)
        if name == 'capture':
            command.add_argument('--label', required=True)
            command.add_argument('--series', action='store_true')
            command.add_argument('--interval', type=float, default=1.0)
            command.add_argument('--timeout', type=float, default=60.0)
            command.add_argument('--stable', type=float, default=5.0)
        if name == 'greeter':
            command.add_argument('--next', default='25')
        if name == 'input-test':
            command.add_argument('--trigger', required=True)
            command.add_argument('--duration', type=float, default=5.0)
    args = parser.parse_args(argv)
    if args.command == 'init':
        return cmd_init(args)
    if args.command == 'plan':
        return cmd_plan(args)
    if not args.walk:
        parser.error('name the walk with --walk DIR or EYES_WALK')
    walk = Walk(args.walk)
    if hasattr(args, 'label') and not re.fullmatch(r'[a-z0-9-]+', args.label):
        parser.error('--label: lowercase letters, digits and dashes')
    handler = {'capture': cmd_capture, 'disk-unlock': cmd_disk_unlock, 'greeter': cmd_greeter,
               'input-test': cmd_input_test, 'session-unlock': cmd_session_unlock, 'index': cmd_index}
    try:
        return handler[args.command](walk, args)
    finally:
        walk.close()


if __name__ == '__main__':
    sys.exit(main())
