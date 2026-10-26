#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""A glass must not draw over a backdrop's first, unsettled frame (0.8.1).

On the GPU path a click that opened a panel showed one bad frame: the fresh screen capture
reported content before its textures had settled (a doubled, streaked drop). The software
renderer draws the flat stand-in, so this checks the contract in
shell/DockBackdrop.qml that the live fix depends on:
  - in capture mode `ready` waits for captureSettled, at least two frames after content;
  - the frame count restarts with new content and with every activation;
"""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
source = (ROOT / 'shell/DockBackdrop.qml').read_text()
failures = []


def need(ok, message):
    if not ok:
        failures.append(message)


ready = re.search(r'readonly property bool ready: (.*)', source)
need(ready and 'capturing ? captureSettled :' in ready.group(1),
     'ready trusts capture.hasContent alone: the first captured frame reaches the glass')
settle = re.search(r'readonly property int settleFrames: (\d+)', source)
need(settle and int(settle.group(1)) >= 2, 'settleFrames missing or below 2')
need(re.search(r'captureSettled: capture\.hasContent && contentFrames >= settleFrames', source),
     'captureSettled does not wait for settleFrames after content')
need(re.search(r'onHasContentChanged:\s*(?:\{\s*)?backdrop\.contentFrames = 0', source),
     'the frame count does not restart with new capture content')
need(re.search(r'onActiveChanged:\s*(?:\{\s*)?contentFrames = 0', source),
     'the frame count does not restart when the backdrop is activated again')
counter = re.search(r'FrameAnimation \{\s*running: ([^\n]*)\n\s*onTriggered: \+\+backdrop\.contentFrames', source)
need(counter and 'capture.hasContent' in counter.group(1) and 'contentFrames < backdrop.settleFrames' in counter.group(1),
     'no FrameAnimation counts the settle frames (or it runs forever)')

if failures:
    print('FAIL backdrop first frame:\n  ' + '\n  '.join(failures))
    sys.exit(1)
print('OK backdrop first frame: a fresh capture reaches the glass only after 2 settled frames')
