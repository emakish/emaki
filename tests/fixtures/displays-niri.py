#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Private file-backed output simulator; only validation reaches the real compositor binary."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

root = Path(os.environ['DISPLAY_TEST_ROOT'])
args = sys.argv[1:]
with (root / 'calls').open('a') as stream:
    stream.write(json.dumps(args) + '\n')
flags = json.loads((root / 'flags').read_text())
if args == ['msg', '--json', 'event-stream']:
    event_path = root / 'reload-events'
    previous = event_path.read_text() if event_path.exists() else ''
    print(json.dumps({'ConfigLoaded': {'failed': False}}), flush=True)
    while True:
        current = event_path.read_text() if event_path.exists() else ''
        if current != previous:
            previous = current
            print(json.dumps({'ConfigLoaded': {'failed': False}}), flush=True)
        time.sleep(.01)
if args[0] == 'validate':
    if flags.get('invalid'):
        sys.exit(1)
    sys.exit(subprocess.run([os.environ['DISPLAY_TEST_VALIDATOR'], *args],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL).returncode)
state = json.loads((root / 'outputs').read_text())
if args == ['msg', '--json', 'outputs']:
    print(json.dumps(state)); sys.exit(0)
if args == ['msg', '--json', 'focused-output']:
    print(json.dumps({'name': (root / 'focus').read_text()})); sys.exit(0)
if flags.get('fail'):
    sys.exit(1)
if flags.get('ignore'):
    sys.exit(0)
if args[:3] == ['msg', 'action', 'focus-monitor']:
    (root / 'focus').write_text(args[3]); sys.exit(0)
if args == ['msg', 'action', 'load-config-file']:
    state = json.loads((root / 'inherited').read_text())
    managed = Path(os.environ['XDG_CONFIG_HOME']) / 'emaki' / 'displays.kdl'
    document = {'outputs': {}}
    if managed.exists():
        marker = '// Display preferences: '
        document = json.loads(next(line[len(marker):] for line in managed.read_text().splitlines()
                                   if line.startswith(marker)))
    for name, values in document['outputs'].items():
        if name not in state:
            continue
        item = state[name]
        item['current_mode'] = next((i for i, mode in enumerate(item['modes'])
                                    if f"{mode['width']}x{mode['height']}@{mode['refresh_rate']/1000:.3f}" == values.get('mode')), 0)
        mode = item['modes'][item['current_mode']]
        scale = values.get('scale', 1)
        rotation = values.get('rotation', 'Normal')
        width, height = mode['width'], mode['height']
        if rotation in ('90', '270', 'flipped-90', 'flipped-270'):
            width, height = height, width
        position = values.get('position', {axis: (item['logical'] or {}).get(axis, 0) for axis in ('x', 'y')})
        item['logical'] = dict(position, width=round(width / scale), height=round(height / scale),
                               scale=scale, transform=rotation)
    (root / 'outputs').write_text(json.dumps(state))
    (root / 'reload-events').write_text(str(time.monotonic_ns()))
    sys.exit(0)
assert args[:2] == ['msg', 'output'], args
name, action, *values = args[2:]
item = state[name]
if action == 'off':
    item['current_mode'] = None; item['logical'] = None
elif action == 'on':
    if item['current_mode'] is None:
        item['current_mode'] = 0
        right = max((other['logical']['x'] + other['logical']['width']
                     for other in state.values() if other['logical']), default=0)
        item['logical'] = dict(x=right, y=0, width=1920, height=1080, scale=1.0, transform='Normal')
elif action == 'mode':
    item['current_mode'] = 0 if values[0] == 'auto' else next(i for i, mode in enumerate(item['modes']) if f"{mode['width']}x{mode['height']}@{mode['refresh_rate']/1000:.3f}" == values[0])
elif action == 'scale':
    item['logical']['scale'] = 1.0 if values[0] == 'auto' else float(values[0])
elif action == 'transform':
    item['logical']['transform'] = 'Normal' if values[0] == 'normal' else values[0]
elif action == 'position':
    coords = [0, 0] if values == ['auto'] else [int(v) for v in values[1:] if v != '--']
    item['logical'].update(zip(('x', 'y'), coords))
else:
    raise AssertionError(args)
if item['logical'] is not None and action in ('mode', 'scale', 'transform', 'on'):
    logical = item['logical']
    mode = item['modes'][item['current_mode']]
    width, height = mode['width'], mode['height']
    if logical['transform'] in ('90', '270', 'Flipped90', 'Flipped270', 'flipped-90', 'flipped-270'):
        width, height = height, width
    logical.update(width=round(width / logical['scale']), height=round(height / logical['scale']))
(root / 'outputs').write_text(json.dumps(state))
