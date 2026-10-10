#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Display IPC, atomic managed overrides, and a client-independent confirmation timer."""
import copy
import fcntl
import json
import math
import os
from pathlib import Path
import re
import select
import signal
import stat
import subprocess
import sys
import tempfile
import time

HEADER = '// Copyright (C) 2026 Artur Yakymenko\n// SPDX-License-Identifier: GPL-3.0-or-later\n'
MARKER = '// Display preferences: '
DEFAULTS = dict(mode='auto', scale='auto', rotation='normal', position='auto', enabled=True)
KEYS = set(DEFAULTS) | {'main'}


class Failure(Exception):
    pass


def run(args):
    try:
        result = subprocess.run(['niri', *args], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        raise Failure('The display service is unavailable.') from None
    if result.returncode:
        if args[:2] == ['msg', '--json']:
            raise Failure('Display information could not be read.')
        raise Failure('The display service could not apply the change.')
    if len(result.stdout) > 1024 * 1024:
        raise Failure('The display service returned an invalid response.')
    return result.stdout


def reload_config():
    """Wait for the reload result, not merely the asynchronous action receipt."""
    try:
        events = subprocess.Popen(['niri', 'msg', '--json', 'event-stream'],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL)
    except OSError:
        raise Failure('The display service is unavailable.') from None
    buffered = b''
    deadline = time.monotonic() + 4

    def loaded():
        nonlocal buffered
        while True:
            while b'\n' in buffered:
                line, buffered = buffered.split(b'\n', 1)
                try:
                    event = json.loads(line)
                except ValueError:
                    raise Failure('The display service returned an invalid response.') from None
                if isinstance(event, dict) and 'ConfigLoaded' in event:
                    result = event['ConfigLoaded']
                    if not isinstance(result, dict) or type(result.get('failed')) is not bool:
                        raise Failure('The display service returned an invalid response.')
                    return result['failed']
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([events.stdout], [], [], remaining)[0]:
                raise Failure('The display configuration reload was not confirmed.')
            data = os.read(events.stdout.fileno(), 8192)
            if not data or len(buffered) + len(data) > 1024 * 1024:
                raise Failure('The display configuration reload was not confirmed.')
            buffered += data

    try:
        loaded()  # Initial state belongs to the previous configuration load.
        run(['msg', 'action', 'load-config-file'])
        if loaded():
            raise Failure('The display configuration could not be loaded.')
    finally:
        events.terminate()
        try:
            events.wait(timeout=1)
        except subprocess.TimeoutExpired:
            events.kill()
            events.wait()
        events.stdout.close()


def outputs():
    try:
        raw = json.loads(run(['msg', '--json', 'outputs']))
        if not isinstance(raw, dict) or len(raw) > 64:
            raise ValueError()
        result = []
        for name, item in raw.items():
            if not isinstance(name, str) or not name or len(name) > 256 or any(ord(c) < 32 for c in name):
                raise ValueError()
            if not isinstance(item, dict):
                raise ValueError()
            modes = item['modes']
            index = item.get('current_mode')
            logical = item.get('logical')
            if not isinstance(modes, list) or len(modes) > 1024:
                raise ValueError()
            for mode in modes:
                if not isinstance(mode, dict) or not all(type(mode.get(key)) is int and mode[key] > 0 for key in ('width', 'height', 'refresh_rate')):
                    raise ValueError()
            if index is not None and (type(index) is not int or not 0 <= index < len(modes)):
                raise ValueError()
            if logical is not None:
                if not isinstance(logical, dict) or not all(type(logical.get(k)) in (int, float) and math.isfinite(logical[k]) for k in ('x', 'y', 'width', 'height', 'scale')):
                    raise ValueError()
                if any(logical[key] <= 0 for key in ('scale', 'width', 'height')) or logical.get('transform') not in ('Normal', '90', '180', '270', 'Flipped', 'Flipped90', 'Flipped180', 'Flipped270', 'normal', 'flipped', 'flipped-90', 'flipped-180', 'flipped-270'):
                    raise ValueError()
            if logical is not None:
                logical['transform'] = transform(logical['transform'])
            result.append(dict(name=name, description=' '.join(str(item.get(k) or '') for k in ('make', 'model')).strip() or name,
                               modes=modes, current_mode=index, logical=logical,
                               enabled=index is not None and logical is not None))
        descriptions = [item['description'] for item in result]
        for item in result:
            if descriptions.count(item['description']) > 1:
                item['description'] += ' (' + item['name'] + ')'
        return sorted(result, key=lambda item: item['name'])
    except (ValueError, KeyError, TypeError):
        raise Failure('The display service returned an invalid response.') from None


def mode_text(mode):
    return f"{mode['width']}x{mode['height']}@{mode['refresh_rate'] / 1000:.3f}"


def transform(value):
    return {'Normal': 'normal', 'Flipped': 'flipped', 'Flipped90': 'flipped-90',
            'Flipped180': 'flipped-180', 'Flipped270': 'flipped-270'}.get(value, value)


def live_settings(item):
    logical = item['logical']
    return dict(mode=mode_text(item['modes'][item['current_mode']]),
                scale=logical['scale'], rotation=transform(logical['transform']),
                position={axis: logical[axis] for axis in ('x', 'y')})


def reflow_positions(before, after, selected):
    """Follow a spanning tree of contacts, then verify overlap and connectivity."""
    old = {item['name']: item['logical'] for item in before if item['enabled']}
    new = {item['name']: item['logical'] for item in after if item['enabled']}
    if old.keys() != new.keys():
        raise Failure('The connected displays changed. Reopen Displays and try again.')
    if all(old[selected][axis] == new[selected][axis] for axis in ('width', 'height')):
        return {}
    edges = {name: [] for name in old}
    names = list(old)
    for i, name in enumerate(names):
        a = old[name]
        for other in names[i + 1:]:
            b = old[other]
            for axis, size, cross, span in [('x', 'width', 'y', 'height'), ('y', 'height', 'x', 'width')]:
                if max(a[cross], b[cross]) >= min(a[cross] + a[span], b[cross] + b[span]):
                    continue
                delta = None
                if a[axis] + a[size] == b[axis]:
                    delta = new[name][size] - a[size]
                elif b[axis] + b[size] == a[axis]:
                    delta = b[size] - new[other][size]
                if delta is not None:
                    shift = (delta, 0) if axis == 'x' else (0, delta)
                    edges[name].append((other, shift, cross, span))
                    edges[other].append((name, tuple(-value for value in shift), cross, span))
    failure = 'The new display size would separate or overlap screens. Adjust their positions first.'
    shifts = {selected: (0, 0)}
    queue = [selected]
    for name in queue:
        for other, delta, cross, span in edges[name]:
            if other in shifts:
                continue
            shift = list(shifts[name][i] + delta[i] for i in range(2))
            # Preserve the old offset where possible. A shrinking screen may
            # need its neighbour pulled sideways to retain a shared edge.
            index = 0 if cross == 'x' else 1
            start = old[name][cross] + shifts[name][index]
            proposed = old[other][cross] + shift[index]
            minimum = start - new[other][span] + 1
            maximum = start + new[name][span] - 1
            shift[index] += min(max(proposed, minimum), maximum) - proposed
            candidate = dict(new[other], x=old[other]['x'] + shift[0],
                             y=old[other]['y'] + shift[1])
            if any(all(min(candidate[axis] + candidate[size],
                           old[placed][axis] + offset[i] + new[placed][size]) >
                       max(candidate[axis], old[placed][axis] + offset[i])
                       for i, (axis, size) in enumerate([('x', 'width'), ('y', 'height')]))
                   for placed, offset in shifts.items()):
                continue
            shifts[other] = tuple(shift)
            queue.append(other)
    component = {selected}
    pending = [selected]
    for name in pending:
        for other, *_ in edges[name]:
            if other not in component:
                component.add(other)
                pending.append(other)
    if component != shifts.keys():
        raise Failure(failure)
    positions = {name: dict(x=old[name]['x'] + shift[0], y=old[name]['y'] + shift[1])
                 for name, shift in shifts.items()}
    if any(any(new[name][axis] != old[name][axis] for axis in ('x', 'y'))
           for name in old if name not in positions):
        raise Failure(failure)
    rectangles = {name: dict(new[name], **positions.get(name, {axis: old[name][axis] for axis in ('x', 'y')}))
                  for name in old}
    for name, position in positions.items():
        if not valid_value('position', position):
            raise Failure(failure)
        a = rectangles[name]
        for other in old:
            if other == name:
                continue
            b = rectangles[other]
            if all(min(old[name][axis] + old[name][size], old[other][axis] + old[other][size]) >
                   max(old[name][axis], old[other][axis]) for axis, size in [('x', 'width'), ('y', 'height')]):
                raise Failure(failure)
            overlap = {axis: min(a[axis] + a[size], b[axis] + b[size]) - max(a[axis], b[axis])
                       for axis, size in [('x', 'width'), ('y', 'height')]}
            if all(value > 0 for value in overlap.values()):
                raise Failure(failure)
    # Redundant old contacts may open after a size change, but the affected
    # component must remain connected by some chain of shared edges.
    connected = {selected}
    queue = [selected]
    for name in queue:
        a = rectangles[name]
        for other in positions.keys() - connected:
            b = rectangles[other]
            overlap = {axis: min(a[axis] + a[size], b[axis] + b[size]) - max(a[axis], b[axis])
                       for axis, size in [('x', 'width'), ('y', 'height')]}
            if ((overlap['x'] == 0 and overlap['y'] > 0) or
                    (overlap['y'] == 0 and overlap['x'] > 0)):
                connected.add(other)
                queue.append(other)
    if connected != positions.keys():
        raise Failure(failure)
    return positions


def valid_value(key, value):
    if key == 'mode':
        return isinstance(value, str) and bool(re.fullmatch(r'[1-9][0-9]{0,4}x[1-9][0-9]{0,4}@[0-9]{1,4}\.[0-9]{3}', value))
    if key == 'scale':
        return type(value) in (float, int) and math.isfinite(value) and 0.5 <= value <= 4
    if key == 'rotation':
        return value in ('normal', '90', '180', '270', 'flipped', 'flipped-90', 'flipped-180', 'flipped-270')
    if key == 'position':
        return isinstance(value, dict) and set(value) == {'x', 'y'} and all(type(v) is int and abs(v) <= 100000 for v in value.values())
    return key == 'enabled' and type(value) is bool


def render(document):
    if not isinstance(document, dict) or set(document) != {'version', 'outputs', 'main'} or type(document['version']) is not int or document['version'] != 1 or not isinstance(document['outputs'], dict):
        raise Failure('Saved display settings cannot be read. Restore the managed display file to continue.')
    main = document['main']
    names = set(document['outputs'])
    if main is not None:
        if not isinstance(main, str):
            raise Failure('Saved display settings cannot be read. Restore the managed display file to continue.')
        names.add(main)
    text = HEADER + MARKER + json.dumps(document, sort_keys=True, separators=(',', ':')) + '\n'
    for name in sorted(names):
        if not name or len(name) > 256 or any(ord(c) < 32 for c in name):
            raise Failure('The display name is invalid.')
        values = document['outputs'].get(name, {})
        if not isinstance(values, dict) or any(key not in DEFAULTS or not valid_value(key, value) for key, value in values.items()):
            raise Failure('Saved display settings cannot be read. Restore the managed display file to continue.')
        if not values and name != main:
            continue
        text += 'output ' + json.dumps(name, ensure_ascii=False) + ' {\n'
        if values.get('enabled') is False:
            raise Failure('Disabled displays are session-only and cannot be saved.')
        if 'mode' in values:
            text += '    mode ' + json.dumps(values['mode']) + '\n'
        if 'scale' in values:
            text += f"    scale {float(values['scale'])}\n"
        if 'rotation' in values:
            text += '    transform ' + json.dumps(values['rotation']) + '\n'
        if 'position' in values:
            text += f"    position x={values['position']['x']} y={values['position']['y']}\n"
        if name == main:
            text += '    focus-at-startup\n'
        text += '}\n'
    return text


def xdg(variable, fallback):
    value = os.environ.get(variable, '')
    return Path(value) if value.startswith('/') else Path.home() / fallback


def compositor_identity():
    """A restarted compositor must never receive an earlier session's snapshot."""
    path = os.environ.get('NIRI_SOCKET')
    if not path:
        return None
    try:
        info = os.stat(path)
        return [path, info.st_dev, info.st_ino, info.st_ctime_ns]
    except OSError:
        return None


def safe_read(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_size > 65536:
        raise Failure('The managed display file is not safe to use.')
    return path.read_text()


def private_directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or path.stat().st_uid != os.getuid() or not path.is_dir():
        raise Failure('The managed display directory is not safe to use.')


def atomic(path, contents):
    private_directory(path.parent)
    safe_read(path)
    fd, name = tempfile.mkstemp(prefix='.displays-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Displays:
    def __init__(self):
        self.path = xdg('XDG_CONFIG_HOME', '.config') / 'emaki/displays.kdl'
        self.state_dir = xdg('XDG_STATE_HOME', '.local/state') / 'emaki'
        self.journal = self.state_dir / 'displays-pending.json'
        self.lock = None
        self.pending = None
        self.disabled = set()
        self.message = ''
        self.document = dict(version=1, outputs={}, main=None)
        self.original = None
        self.read()

    def read(self):
        self.original = safe_read(self.path)
        if self.original is None:
            self.document = dict(version=1, outputs={}, main=None)
            return
        try:
            line = next(line for line in self.original.splitlines() if line.startswith(MARKER))
            document = json.loads(line[len(MARKER):])
            if render(document) != self.original:
                raise ValueError()
            self.document = document
        except (ValueError, StopIteration):
            raise Failure('Saved display settings cannot be read. Restore the managed display file to continue.') from None

    def acquire(self):
        if self.lock is not None:
            return
        private_directory(self.state_dir)
        self.lock = os.open(self.state_dir / 'settings.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        info = os.fstat(self.lock)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            self.release()
            raise Failure('The display settings lock is not safe to use.')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.lock)
            self.lock = None
            raise Failure('Another display change is waiting for confirmation.') from None

    def release(self):
        if self.lock is not None:
            os.close(self.lock)
            self.lock = None

    def response(self, state=None):
        current = outputs()
        for item in current:
            item['overrides'] = self.document['outputs'].get(item['name'], {})
            item['defaults'] = DEFAULTS
        return dict(schema_version=1, state=state or ('pending' if self.pending else 'ready'),
                    outputs=current, main=self.document['main'], pending=self.pending is not None,
                    seconds=max(0, math.ceil(self.pending['deadline'] - time.monotonic())) if self.pending else 0,
                    message=self.message)

    def validate(self, document):
        contents = render(document)
        private_directory(self.state_dir)
        fd, name = tempfile.mkstemp(prefix='.display-check-', suffix='.kdl', dir=self.state_dir)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(contents)
            run(['validate', '--config', name])
        finally:
            os.unlink(name)
        return contents

    def action(self, output, key, value):
        args = {'mode': ['mode', str(value)], 'scale': ['scale', str(value)],
                'rotation': ['transform', str(value)], 'enabled': ['on' if value else 'off']}
        if key == 'main':
            run(['msg', 'action', 'focus-monitor', output])
        elif key == 'position':
            run(['msg', 'output', output, 'position', 'auto'] if value == 'auto' else
                ['msg', 'output', output, 'position', 'set', '--', str(value['x']), str(value['y'])])
        else:
            run(['msg', 'output', output, *args[key]])

    def verify(self, name, key, value):
        deadline = time.monotonic() + 1.5
        while True:
            current = {item['name']: item for item in outputs()}
            item = current.get(name)
            okay = False
            if item:
                logical = item['logical'] or {}
                if key == 'enabled':
                    okay = item['enabled'] == value and (value or any(output['enabled'] for output in current.values()))
                elif key == 'main':
                    try:
                        okay = json.loads(run(['msg', '--json', 'focused-output']))['name'] == name
                    except (ValueError, TypeError, KeyError):
                        okay = False
                elif item['enabled']:
                    if value == 'auto':
                        okay = True
                    elif key == 'mode':
                        okay = mode_text(item['modes'][item['current_mode']]) == value
                    elif key == 'scale':
                        # niri rounds fractional scale to multiples of 1/120.
                        okay = abs(logical.get('scale', 0) - value) <= 1 / 120 + 0.00001
                    elif key == 'rotation':
                        okay = transform(logical.get('transform')) == value
                    elif key == 'position':
                        okay = all(logical.get(k) == value[k] for k in ('x', 'y'))
            if okay:
                return
            if time.monotonic() >= deadline:
                raise Failure('The display did not confirm the requested settings.')
            time.sleep(.05)

    def restore(self, snapshot):
        current = {item['name']: item for item in outputs()}
        failures = []
        for item in snapshot:
            name = item['name']
            if name not in current or not item['enabled']:
                continue
            logical = item['logical']
            try:
                self.action(name, 'enabled', True)
                for key, value in [('mode', mode_text(item['modes'][item['current_mode']])),
                                   ('scale', logical['scale']), ('rotation', transform(logical['transform']))]:
                    self.action(name, key, value)
                    self.verify(name, key, value)
            except Failure:
                failures.append(name)
        # Geometry changes can rearrange automatic outputs. Restore positions only
        # after every connected output has regained its previous logical size.
        for item in snapshot:
            if item['name'] in current and item['enabled']:
                try:
                    position = {axis: item['logical'][axis] for axis in ('x', 'y')}
                    self.action(item['name'], 'position', position)
                    self.verify(item['name'], 'position', position)
                except Failure:
                    failures.append(item['name'])
        for item in snapshot:
            if item['name'] in current and not item['enabled']:
                try:
                    # Never restore an off state if it would blank the only remaining screen.
                    live = outputs()
                    if sum(output['enabled'] for output in live) > 1:
                        self.action(item['name'], 'enabled', False)
                        self.verify(item['name'], 'enabled', False)
                except Failure:
                    failures.append(item['name'])
        if failures:
            raise Failure('The previous display settings could not be fully restored. Reconnect the display and try again.')

    def verify_layout(self, expected):
        deadline = time.monotonic() + 1.5
        while True:
            current = {item['name']: item['logical'] for item in outputs() if item['enabled']}
            if all(name in current and all(current[name][key] == value for key, value in geometry.items())
                   for name, geometry in expected.items()):
                return
            if time.monotonic() >= deadline:
                raise Failure('The display did not confirm the requested settings.')
            time.sleep(.05)

    def restore_probe(self, record, restore_snapshot=True):
        current = safe_read(self.path)
        if current not in (record['candidate'], record['original']):
            raise Failure('Display settings changed elsewhere. Reopen Displays and try again.')
        if record['original'] is None:
            self.path.unlink(missing_ok=True)
        else:
            atomic(self.path, record['original'])
        reload_config()
        if restore_snapshot:
            self.restore(record['snapshot'])

    def inherited_settings(self, name, snapshot):
        """Ask the compositor to resolve personal includes under a recovery journal."""
        document = copy.deepcopy(self.document)
        document['outputs'].pop(name, None)
        if document['main'] == name:
            document['main'] = None
        candidate = self.validate(document)
        record = dict(snapshot=snapshot, candidate=candidate, original=self.original,
                      compositor=compositor_identity(), preview=True)
        atomic(self.journal, json.dumps(record))
        try:
            if safe_read(self.path) != self.original:
                raise Failure('Display settings changed elsewhere. Reopen Displays and try again.')
            atomic(self.path, candidate)
            reload_config()
            current = next((item for item in outputs() if item['name'] == name), None)
            if current is None or not current['enabled']:
                raise Failure('The inherited configuration does not enable this display. Its settings were restored.')
            inherited = live_settings(current)
        finally:
            self.restore_probe(record)
            self.journal.unlink(missing_ok=True)
        return inherited

    def recover(self):
        if safe_read(self.journal) is None:
            return
        self.acquire()
        try:
            record = json.loads(safe_read(self.journal))
            identity = compositor_identity()
            if record.get('preview'):
                self.restore_probe(record, identity is not None and record.get('compositor') == identity)
                self.read()
                self.message = 'An interrupted display reset was reverted.'
            elif identity is None or record.get('compositor') != identity:
                self.message = 'An interrupted display change from an earlier session was discarded.'
            # Session-only changes never publish a file, even after Keep.
            elif record.get('session_only') or record['candidate'] == record.get('original') or safe_read(self.path) != record['candidate']:
                self.restore(record['snapshot'])
                self.message = 'An interrupted display change was reverted.'
            self.journal.unlink()
        except (ValueError, KeyError, TypeError):
            raise Failure('The pending display change cannot be recovered.') from None
        finally:
            self.release()

    def publish(self, pending):
        if safe_read(self.path) != self.original:
            raise Failure('Display settings changed elsewhere. Reopen Displays and try again.')
        if pending['key'] == 'enabled':
            if pending['value']:
                self.disabled.discard(pending['output'])
            else:
                self.disabled.add(pending['output'])
            self.message = 'This display change applies until the session ends.'
        else:
            atomic(self.path, pending['candidate'])
            self.document = pending['document']
            self.original = pending['candidate']
        self.journal.unlink(missing_ok=True)

    def ensure_visible(self):
        if not self.disabled:
            return
        current = outputs()
        if any(item['enabled'] for item in current):
            return
        for item in current:
            if item['name'] in self.disabled:
                self.action(item['name'], 'enabled', True)
                self.verify(item['name'], 'enabled', True)
                self.disabled.discard(item['name'])
                self.message = 'The remaining display was turned back on.'
                return

    def revert(self):
        pending = self.pending
        if not pending:
            return
        try:
            self.restore(pending['snapshot'])
            self.journal.unlink(missing_ok=True)
            self.message = 'The previous display settings have been restored.'
        finally:
            self.pending = None
            self.release()

    def change(self, request):
        if self.pending:
            raise Failure('Keep or revert the current display change first.')
        self.acquire()
        try:
            self.read()
            if safe_read(self.journal) is not None:
                raise Failure('A previous display change needs recovery. Reopen Displays and try again.')
            before = outputs()
            name, key = request.get('output'), request.get('key')
            selected = next((item for item in before if item['name'] == name), None)
            if not selected or not isinstance(key, str) or key not in KEYS:
                raise Failure('The selected display is no longer connected.')
            reset = request['op'] == 'reset'
            inherited = (self.inherited_settings(name, before)
                         if reset and key in ('mode', 'scale', 'rotation', 'position') else None)
            value = inherited[key] if inherited is not None else DEFAULTS.get(key) if reset else request.get('value')
            if key == 'main' and not reset and value is not True:
                raise Failure('Choose a valid display setting.')
            if key != 'main' and not reset and not valid_value(key, value):
                raise Failure('Choose a valid display setting.')
            if key == 'mode' and value != 'auto' and value not in [mode_text(mode) for mode in selected['modes']]:
                raise Failure('That resolution or refresh rate is not available on this display.')
            if key == 'enabled' and not value and not any(item['enabled'] for item in before if item['name'] != name):
                raise Failure('Keep at least one display turned on.')
            if key not in ('enabled',) and not selected['enabled']:
                raise Failure('Turn this display on before changing its settings.')
            document = copy.deepcopy(self.document)
            if key != 'enabled' and not reset and name not in document['outputs'] and selected['enabled']:
                # Output blocks replace the whole inherited block. Preserve the live
                # values that this first edit is not changing across later reloads.
                document['outputs'][name] = live_settings(selected)
            if key == 'main':
                document['main'] = None if reset else name
            elif key != 'enabled':
                settings = document['outputs'].setdefault(name, {})
                settings[key] = value
                if inherited is not None and all(inherited.get(field) == saved for field, saved in settings.items()):
                    settings.clear()
                if not settings:
                    document['outputs'].pop(name, None)
            candidate = self.validate(document)
            pending = dict(snapshot=before, candidate=candidate, document=document,
                           output=name, key=key, value=value,
                           deadline=time.monotonic() + 15)
            atomic(self.journal, json.dumps(dict(snapshot=before, candidate=candidate, original=self.original,
                                                compositor=compositor_identity(), session_only=key == 'enabled')))
            self.pending = pending
            target = name
            if key == 'main' and reset:
                target = next(item['name'] for item in before if item['enabled'])
            try:
                self.action(target, key, value)
                self.verify(target, key, value)
                if key in ('mode', 'scale', 'rotation'):
                    resized = outputs()
                    positions = reflow_positions(before, resized, name)
                    pending['layout'] = {
                        item['name']: dict(positions[item['name']], width=item['logical']['width'],
                                           height=item['logical']['height'])
                        for item in resized if item['name'] in positions}
                    for item in before:
                        neighbor = item['name']
                        if neighbor not in positions:
                            continue
                        position = positions[neighbor]
                        if position == {axis: item['logical'][axis] for axis in ('x', 'y')}:
                            continue
                        if neighbor == name and inherited is not None and name not in document['outputs'] and position == inherited['position']:
                            continue
                        # Only moved neighbours need an override; retain any fields
                        # they already own without capturing unrelated live values.
                        document['outputs'].setdefault(neighbor, {})['position'] = position
                    pending['candidate'] = self.validate(document)
                    atomic(self.journal, json.dumps(dict(snapshot=before, candidate=pending['candidate'],
                                                        original=self.original, compositor=compositor_identity(),
                                                        session_only=False)))
                    for neighbor, position in positions.items():
                        self.action(neighbor, 'position', position)
                    self.verify_layout(pending['layout'])
                # The person receives the whole confirmation interval after acknowledgement.
                pending['deadline'] = time.monotonic() + 15
                if key == 'main':
                    self.publish(pending)
                    self.pending = None
            except (Failure, OSError):
                self.revert()
                raise
        finally:
            if not self.pending:
                self.release()

    def request(self, request):
        if not isinstance(request, dict):
            raise Failure('The display request is invalid.')
        op = request.get('op')
        if op != 'state':
            self.message = ''
        if self.pending and time.monotonic() >= self.pending['deadline']:
            self.revert()
            if op == 'keep':
                raise Failure('The confirmation time expired. The previous settings were restored.')
        if op == 'state' and self.pending and self.pending['key'] == 'enabled' and not self.pending['value']:
            try:
                self.verify(self.pending['output'], 'enabled', False)
            except Failure:
                self.revert()
        if op in ('set', 'reset'):
            self.change(request)
        elif op == 'keep':
            if not self.pending:
                raise Failure('There is no display change to keep.')
            try:
                self.verify(self.pending['output'], self.pending['key'], self.pending['value'])
                self.verify_layout(self.pending.get('layout', {}))
                self.publish(self.pending)
            except (Failure, OSError):
                self.revert()
                raise
            self.pending = None
            self.release()
        elif op == 'revert':
            self.revert()
        elif op != 'state':
            raise Failure('The display request is invalid.')
        elif not self.pending:
            self.read()
            self.ensure_visible()
        return self.response()


def error_response(error, controller=None):
    message = str(error) if isinstance(error, Failure) else 'Display settings could not be saved. Check the available space and try again.'
    if controller:
        controller.message = message
    try:
        response = controller.response('error') if controller else {}
    except (Failure, OSError):
        response = {}
    response.update(schema_version=1, state='error', message=message)
    return response


def main():
    controller = None
    def emit(response):
        print(json.dumps(response), flush=True)
    try:
        controller = Displays()
        if '--json' in sys.argv:
            emit(controller.response())
            return
        controller.recover()
        emit(controller.response())
        stopping = False
        def stop(*_):
            nonlocal stopping
            stopping = True
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        buffer = b''
        while not stopping:
            if controller.pending and time.monotonic() >= controller.pending['deadline']:
                try:
                    controller.revert()
                    emit(controller.response())
                except (Failure, OSError) as error:
                    emit(error_response(error, controller))
            ready = select.select([sys.stdin], [], [], .2)[0]
            if not ready:
                continue
            chunk = os.read(sys.stdin.fileno(), 8192)
            if not chunk:
                break
            buffer += chunk
            if len(buffer) > 65536:
                raise Failure('The display request is too large.')
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                try:
                    request = json.loads(line)
                    emit(controller.request(request))
                except (ValueError, Failure, OSError) as error:
                    emit(error_response(error if not isinstance(error, ValueError) else Failure('The display request is invalid.'), controller))
    except BrokenPipeError:
        pass
    except (Failure, OSError) as error:
        try:
            emit(error_response(error, controller))
        except BrokenPipeError:
            pass
    finally:
        if controller:
            if controller.pending:
                try:
                    controller.revert()
                except (Failure, OSError):
                    pass  # Journal is retained for recovery by the next client.
            controller.release()


if __name__ == '__main__':
    main()
