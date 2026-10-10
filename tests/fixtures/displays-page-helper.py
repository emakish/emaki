#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Line protocol fixture: never contacts a compositor or writes settings."""
import json
import sys

state = dict(schema_version=1, state='ready', outputs=[dict(name='PANEL')],
             main='PANEL', pending=False, seconds=0, message='')

def emit(value):
    print(json.dumps(value), flush=True)

emit(state)
for line in sys.stdin:
    request = json.loads(line)
    if request['op'] == 'set' and request['key'] == 'enabled':
        emit(dict(schema_version=1, state='error', message='The last display must stay on.'))
        continue
    if request['op'] == 'set':
        state.update(state='pending', pending=True, seconds=15)
    elif request['op'] in ('keep', 'revert'):
        state.update(state='ready', pending=False, seconds=0)
    emit(state)
