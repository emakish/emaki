#!/usr/bin/env python3
"""Eyes harness: QMP input and VNC capture for one QEMU pass started by eyes-vm.sh.

No ssh and no guest agent: input is QMP send-key / input-send-event, output is the real
scan-out read over VNC (QMP screendump only while VNC has no surface yet).

Library use: Pass(dir) -> key(), type_text(), click(), grab(), series(), burst().
CLI (one input or capture per call):
  eyes.py [--walk DIR | --pass DIR] key meta_l-l [ret ...]
  eyes.py [...] type-input NAME          text from <walk>/inputs.toml, never the command line
  eyes.py [...] move|click|rclick|dclick X Y   pixels of the pass resolution
  eyes.py [...] scroll up|down N
  eyes.py [...] shot FILE.png
  eyes.py [...] qmp COMMAND [JSON-ARGS]
  eyes.py [...] quit
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import tomllib

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import vncshot  # noqa: E402

from PIL import Image, ImageStat  # noqa: E402

# Characters -> QEMU qcodes (US layout positions). Anything else is refused: a silently
# mistyped password would make a wrong-password frame look like a product defect.
KEYS = {' ': 'spc', '-': 'minus', '/': 'slash', '.': 'dot', '=': 'equal', ',': 'comma',
        ';': 'semicolon', ':': 'shift-semicolon', '_': 'shift-minus', '@': 'shift-2',
        '!': 'shift-1', '#': 'shift-3', '$': 'shift-4', '%': 'shift-5', '^': 'shift-6',
        '&': 'shift-7', '*': 'shift-8', '(': 'shift-9', ')': 'shift-0', '+': 'shift-equal',
        '\n': 'ret', '\t': 'tab', "'": 'apostrophe', '"': 'shift-apostrophe', '?': 'shift-slash',
        '<': 'shift-comma', '>': 'shift-dot', '[': 'bracket_left', ']': 'bracket_right',
        '{': 'shift-bracket_left', '}': 'shift-bracket_right', '\\': 'backslash',
        '|': 'shift-backslash', '`': 'grave_accent', '~': 'shift-grave_accent'}
BLACK = 0.02  # mean luminance below this counts as a black frame (plan 5.1 timing)


def qcode(ch):
    if ch in KEYS:
        return KEYS[ch]
    if ch.isascii() and ch.isalpha():
        return 'shift-' + ch.lower() if ch.isupper() else ch
    if ch.isascii() and ch.isdigit():
        return ch
    raise ValueError(f'no US key position for {ch!r}')


def _toml_key(key):
    return key if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*', key) else json.dumps(key)


def _toml_value(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)  # JSON escapes are valid TOML basic strings
    if isinstance(value, list):
        return '[' + ', '.join(_toml_value(item) for item in value) + ']'
    raise TypeError(f'cannot write {type(value).__name__} to TOML')


def dump_toml(data, header=''):
    """TOML for walk records: strings, numbers, booleans, lists of scalars, nested tables."""
    lines = [header.rstrip('\n')] if header else []

    def table(path, values):
        scalars = {k: v for k, v in values.items() if not isinstance(v, dict) and v is not None}
        tables = {k: v for k, v in values.items() if isinstance(v, dict)}
        if path and (scalars or not tables):
            lines.append('')
            lines.append('[' + '.'.join(_toml_key(p) for p in path) + ']')
        for key, value in scalars.items():
            lines.append(f'{_toml_key(key)} = {_toml_value(value)}')
        for key, value in tables.items():
            table(path + [key], value)

    table([], data)
    return '\n'.join(lines).lstrip('\n') + '\n'


class QMP:
    def __init__(self, path, timeout=20):
        self.s = socket.socket(socket.AF_UNIX)
        self.s.settimeout(timeout)
        self.s.connect(str(path))
        self.f = self.s.makefile('rw')
        self.f.readline()
        self.cmd('qmp_capabilities')

    def cmd(self, name, **arguments):
        self.f.write(json.dumps({'execute': name, 'arguments': arguments}) + '\n')
        self.f.flush()
        while True:
            line = self.f.readline()
            if not line:
                raise RuntimeError('QMP connection closed')
            reply = json.loads(line)
            if 'return' in reply or 'error' in reply:
                return reply

    def close(self):
        self.f.close()
        self.s.close()


def stats(img):
    """Pixel hash (change detection) and mean luminance 0..1 of one frame."""
    return hashlib.sha256(img.tobytes()).hexdigest(), ImageStat.Stat(img.convert('L')).mean[0] / 255


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def metrics(timeline):
    """Timing numbers printed on every tile (plan 5.1): first change after the trigger, last
    change (the frame is stable from then on), longest black run, longest unchanged run."""
    if not timeline:
        return {'t_first_change': None, 't_stable': None, 'longest_black': 0.0, 'longest_frozen': 0.0}
    first = next((e['t'] for e in timeline[1:] if e['changed']), None)
    last_change = max(e['t'] for e in timeline if e['changed']) if any(e['changed'] for e in timeline) \
        else timeline[0]['t']
    black = frozen = 0.0
    black_start = None
    frozen_start = timeline[0]['t']
    for entry in timeline:
        if entry['luminance'] < BLACK:
            black_start = entry['t'] if black_start is None else black_start
            black = max(black, entry['t'] - black_start)
        else:
            black_start = None
        if entry['changed']:
            frozen_start = entry['t']
        frozen = max(frozen, entry['t'] - frozen_start)
    return {'t_first_change': first, 't_stable': last_change,
            'longest_black': round(black, 3), 'longest_frozen': round(frozen, 3)}


class Pass:
    """One running QEMU started by eyes-vm.sh: sockets and resolution live in its directory."""

    def __init__(self, directory, inputs=None):
        self.dir = Path(directory)
        if not (self.dir / 'qmp.sock').exists():
            raise RuntimeError(f'no qmp.sock in {self.dir}: start the pass with eyes-vm.sh')
        width, height = (self.dir / 'res').read_text().strip().split('x')
        self.width, self.height = int(width), int(height)
        self.inputs = inputs
        self._qmp = None
        self._lock = threading.Lock()

    def close(self):
        if self._qmp is not None:
            self._qmp.close()
            self._qmp = None

    @property
    def qmp(self):
        if self._qmp is None:
            self._qmp = QMP(self.dir / 'qmp.sock')
        return self._qmp

    def log(self, message):
        with (self.dir / 'timeline.log').open('a') as stream:
            stream.write(time.strftime('%T ') + message + '\n')

    def cmd(self, name, **arguments):
        with self._lock:
            reply = self.qmp.cmd(name, **arguments)
        if 'error' in reply:
            raise RuntimeError(f'QMP {name}: {reply["error"]}')
        return reply

    # -- input ---------------------------------------------------------------------------
    def key(self, combo, hold=60):
        self.cmd('send-key', keys=[{'type': 'qcode', 'data': k} for k in combo.split('-')],
                 **{'hold-time': hold})

    def keys(self, *combos):
        for combo in combos:
            self.key(combo)
            time.sleep(.15)
        self.log('key ' + ' '.join(combos))

    def type_text(self, text, secret=True):
        codes = [qcode(ch) for ch in text]  # refuse before the first key is sent
        for code in codes:
            self.key(code, 40)
            time.sleep(.08)
        self.log('type ' + ('*' * len(text) if secret else text))

    def type_input(self, name):
        if not self.inputs or name not in self.inputs:
            raise KeyError(f'input {name!r} is not defined in inputs.toml')
        self.type_text(self.inputs[name], secret=name != 'marker')

    def _abs(self, x, y):
        return [{'type': 'abs', 'data': {'axis': 'x', 'value': int(x / self.width * 32767)}},
                {'type': 'abs', 'data': {'axis': 'y', 'value': int(y / self.height * 32767)}}]

    def click(self, x, y, button='left', count=1, move_only=False):
        # Approach in two steps so hover states fire, as a hand would.
        self.cmd('input-send-event', events=self._abs(max(x - 6, 0), y))
        time.sleep(.08)
        self.cmd('input-send-event', events=self._abs(x, y))
        time.sleep(.25)
        if not move_only:
            for _ in range(count):
                for down in (True, False):
                    self.cmd('input-send-event', events=[{'type': 'btn', 'data': {'down': down, 'button': button}}])
                    time.sleep(.07 if down else .12)
        self.log(f'{"move" if move_only else button + " x" + str(count)} {int(x)},{int(y)}')

    def scroll(self, direction, count):
        button = 'wheel-up' if direction == 'up' else 'wheel-down'
        for _ in range(count):
            for down in (True, False):
                self.cmd('input-send-event', events=[{'type': 'btn', 'data': {'down': down, 'button': button}}])
            time.sleep(.05)
        self.log(f'scroll {direction} {count}')

    # -- capture -------------------------------------------------------------------------
    def grab(self):
        """One frame of the real scan-out: VNC first, QMP screendump while VNC has no surface."""
        vnc = self.dir / 'vnc.sock'
        if vnc.exists():
            try:
                client = vncshot.Client(str(vnc))
                try:
                    return client.frame(), 'vnc'
                finally:
                    client.close()
            except (OSError, RuntimeError):
                pass
        with tempfile.NamedTemporaryFile(suffix='.png', dir=self.dir) as temporary:
            self.cmd('screendump', filename=temporary.name, format='png')
            img = Image.open(temporary.name)
            img.load()
            return img.convert('RGB'), 'screendump'

    def series(self, save, interval=1.0, timeout=60.0, stable=5.0):
        """Grab every `interval` s; save a frame at each change; stop once unchanged for `stable` s.

        `save(img, t)` stores one frame and returns its file name. Returns the timeline
        (t, pixel sha256, luminance, changed, file) and its metrics.
        """
        start = time.monotonic()
        timeline = []
        previous = None
        last_change = 0.0
        while True:
            img, source = self.grab()
            t = round(time.monotonic() - start, 3)
            digest, luminance = stats(img)
            changed = digest != previous
            entry = {'t': t, 'sha256': digest, 'luminance': round(luminance, 4), 'changed': changed,
                     'source': source, 'file': None}
            if changed:
                entry['file'] = save(img, t)
                last_change = t
                previous = digest
            timeline.append(entry)
            if t - last_change >= stable or t >= timeout:
                break
            time.sleep(max(0.0, interval - (time.monotonic() - start - t)))
        result = metrics(timeline)
        result['timed_out'] = timeline[-1]['t'] >= timeout and timeline[-1]['t'] - last_change < stable
        return timeline, result

    def burst(self, save, duration=5.0, during=None):
        """Grab back to back for `duration` s and keep every frame (plan 5.1a item 2).

        `during(stop_event)` runs in a thread meanwhile (the marker input of 5.1a item 1).
        Frames go to uncompressed BMP files while grabbing and are handed to `save` (PNG)
        only afterwards, so compression never stretches the grab interval. Returns the
        timeline and the measured mean and maximum interval between kept frames in ms.
        """
        stop = threading.Event()
        worker = None
        if during:
            worker = threading.Thread(target=during, args=(stop,), daemon=True)
            worker.start()
        start = time.monotonic()
        timeline = []
        previous = None
        client = None
        vnc = self.dir / 'vnc.sock'
        with tempfile.TemporaryDirectory(prefix='burst-', dir=self.dir) as spool:
            try:
                while time.monotonic() - start < duration:
                    if vnc.exists():
                        try:
                            client = client or vncshot.Client(str(vnc))
                            img, source = client.frame(), 'vnc'
                        except (OSError, RuntimeError):
                            if client:
                                client.close()
                            client = None
                            img, source = self.grab()
                    else:
                        img, source = self.grab()
                    t = round(time.monotonic() - start, 3)
                    digest, luminance = stats(img)
                    raw = Path(spool) / f'{len(timeline):05d}.bmp'
                    img.save(raw)
                    timeline.append({'t': t, 'sha256': digest, 'luminance': round(luminance, 4),
                                     'changed': digest != previous, 'source': source, 'file': str(raw)})
                    previous = digest
            finally:
                stop.set()
                if worker:
                    worker.join(timeout=10)
                if client:
                    client.close()
            for entry in timeline:
                with Image.open(entry['file']) as img:
                    entry['file'] = save(img.convert('RGB'), entry['t'])
        gaps = [b['t'] - a['t'] for a, b in zip(timeline, timeline[1:])]
        interval = {'grab_interval_ms': round(1000 * sum(gaps) / len(gaps), 1) if gaps else None,
                    'grab_interval_max_ms': round(1000 * max(gaps), 1) if gaps else None}
        return timeline, interval


def load_inputs(walk):
    path = Path(walk) / 'inputs.toml' if walk else None
    if path and path.is_file():
        return tomllib.loads(path.read_text())
    return {}


def pass_dir(args):
    if args.pass_dir:
        return Path(args.pass_dir)
    walk = args.walk or os.environ.get('EYES_WALK')
    if not walk:
        raise SystemExit('name the walk (--walk DIR or EYES_WALK) or the pass (--pass DIR)')
    current = Path(walk) / 'current-pass'
    if not current.is_file():
        raise SystemExit(f'{current} missing: start a pass with eyes-vm.sh first')
    return Path(current.read_text().strip())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--walk')
    parser.add_argument('--pass', dest='pass_dir')
    parser.add_argument('command')
    parser.add_argument('args', nargs='*')
    args = parser.parse_args(argv)
    walk = args.walk or os.environ.get('EYES_WALK')
    vm = Pass(pass_dir(args), load_inputs(walk))
    c, a = args.command, args.args
    if c == 'key':
        vm.keys(*a)
    elif c == 'type-input':
        vm.type_input(a[0])
    elif c in ('move', 'click', 'rclick', 'dclick'):
        x, y = float(a[0]), float(a[1])
        vm.click(x, y, button='right' if c == 'rclick' else 'left', count=2 if c == 'dclick' else 1,
                 move_only=c == 'move')
    elif c == 'scroll':
        vm.scroll(a[0], int(a[1]))
    elif c == 'shot':
        img, source = vm.grab()
        img.save(a[0])
        print(f'SHOT: {a[0]} (not judged) {img.size[0]}x{img.size[1]} via {source}')
    elif c == 'qmp':
        print(json.dumps(vm.cmd(a[0], **(json.loads(a[1]) if len(a) > 1 else {}))))
    elif c == 'quit':
        vm.cmd('quit')
        vm.log('quit')
    else:
        parser.error(f'unknown command {c}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
