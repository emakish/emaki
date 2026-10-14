#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the capture controller against isolated process fixtures, without a desktop."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from runtime_fixture import short_runtime
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parent.parent
HELPER = r'''
import json, os, signal, sys, time
from pathlib import Path
scenario, operation = sys.argv[1:3]
log = Path(os.environ['CAPTURE_EVENTS'])
def emit(value):
    print(json.dumps(value), flush=True)
def event(value):
    with log.open('a') as stream:
        stream.write(json.dumps(value) + '\n')
if operation == '--inventory':
    if scenario == 'slow-inventory':
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(.8)
    emit({'event':'inventory', 'outputs':[
        {'name':'TEST-1','width':800,'height':600},
        {'name':'TEST-2','width':1280,'height':720}],
        'windows':[{'id':11,'is_focused':True},{'id':22,'is_focused':False}]})
else:
    event({'operation':operation, 'request':json.loads(sys.argv[3])})
    if scenario == 'error':
        emit({'event':'error','reason':'fixture_refused'})
        sys.exit(1)
    if operation == '--record':
        if scenario == 'slow-start':
            time.sleep(.6)
        emit({'event':'stream','node':57})
        emit({'event':'recording'})
        line = sys.stdin.readline()
        event({'control':line.strip()})
        if line.strip() != 'stop':
            sys.exit(2)
    emit({'event':'saved','path':str(Path.home() / 'capture-result'),
          'clipboard':scenario != 'clipboard-failed'})
'''


def main():
    qs = shutil.which('qs')
    assert qs, 'qs is required'
    with tempfile.TemporaryDirectory(prefix='capture-controller-', dir='/tmp') as folder, short_runtime() as runtime:
        profile = Path(folder)
        qml = profile / 'qml'
        qml.mkdir()
        for name in ('CaptureController.qml', 'CaptureText.js'):
            shutil.copyfile(ROOT / 'shell' / name, qml / name)
        shutil.copyfile(ROOT / 'tests/fixtures/capture-controller.qml', qml / 'shell.qml')
        (qml / 'qmldir').write_text('CaptureController 1.0 CaptureController.qml\nsingleton Platform 1.0 Platform.qml\n')
        (qml / 'Platform.qml').write_text('pragma Singleton\nimport QtQuick\nQtObject { readonly property string python: ' + json.dumps(sys.executable) + ' }\n')
        helper = profile / 'helper.py'
        helper.write_text(HELPER)
        events = profile / 'events'
        env = dict(os.environ, HOME=folder, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QML_DISABLE_DISK_CACHE='1', QS_DISABLE_CRASH_HANDLER='1', XDG_RUNTIME_DIR=runtime,
                   CAPTURE_HELPER=str(helper), CAPTURE_PYTHON=sys.executable,
                   CAPTURE_EVENTS=str(events), CAPTURE_MISSING=str(profile / 'missing'))
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'NIRI_SOCKET', 'QML_IMPORT_PATH', 'QML2_IMPORT_PATH'):
            env.pop(key, None)
        for key, name in (('XDG_CACHE_HOME','cache'), ('XDG_CONFIG_HOME','config'), ('XDG_STATE_HOME','state'), ('XDG_DATA_HOME','data')):
            path = profile / name
            path.mkdir()
            env[key] = str(path)
        env.update(XDG_DATA_DIRS=str(profile / 'data'), DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'no-bus'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'no-system'))
        with (profile / 'output.log').open('w') as log:
            process = subprocess.Popen([qs, '-p', str(qml), '--no-color'], env=env, stdout=log, stderr=subprocess.STDOUT)
            def call(method, *args):
                result = subprocess.run([qs, 'ipc', '--pid', str(process.pid), 'call', 'tests', method,
                                         *[str(arg).lower() if isinstance(arg, bool) else str(arg) for arg in args]],
                                        env=env, text=True, capture_output=True, timeout=5)
                assert result.returncode == 0, result.stdout + result.stderr + (profile / 'output.log').read_text()
                value = result.stdout.strip()
                return json.loads(value) if value else None
            def wait(predicate, timeout=4):
                deadline = time.monotonic() + timeout
                while True:
                    state = call('state')
                    if predicate(state):
                        return state
                    assert time.monotonic() < deadline, state
                    time.sleep(.04)
            def prepare(scenario='normal', mode='picture', target='screen'):
                assert call('configure', scenario)
                assert call('mode', mode)
                assert call('targetKind', target)
                assert call('present')
                return wait(lambda s: s['ready'])
            def begin():
                state = call('begin')
                assert state == {'accepted': True, 'opened': False, 'busy': True, 'duplicate': False}, state
            try:
                deadline = time.monotonic() + 8
                while True:
                    try:
                        call('state')
                        break
                    except AssertionError:
                        assert process.poll() is None and time.monotonic() < deadline, (profile / 'output.log').read_text()
                        time.sleep(.05)
                prepare(mode='recording', target='window')
                assert not call('windowId', 99)
                assert call('windowId', 22)
                assert call('output', 'TEST-2')
                begin()
                # The recording's own PipeWire node is known while it runs (the privacy pill
                # leaves that cast alone) and forgotten when the worker ends.
                assert wait(lambda s: s['recording'])['stream_node'] == 57
                assert not call('present')
                assert call('finish')
                state = wait(lambda s: not s['busy'])
                assert state['notices'][-1]['title'] == 'Recording saved', state
                assert state['stream_node'] == -1, state
                operations = [json.loads(line) for line in events.read_text().splitlines()]
                assert operations[-2]['request']['window_id'] == 22 and operations[-2]['request']['output'] == 'TEST-2'
                assert operations[-1] == {'control': 'stop'}
                prepare('clipboard-failed', target='area')
                call('area', -20, 599, 10000, 10000)
                assert call('state')['area'] == {'x':0,'y':598,'width':800,'height':2}
                begin()
                state = wait(lambda s: not s['busy'])
                assert state['notices'][-1]['title'] == 'Screenshot saved; clipboard unavailable', state
                prepare('normal')
                call('lock', True)
                assert not call('present') and not call('state')['canCapture']
                assert not call('begin')['accepted']
                call('lock', False)
                prepare('normal', mode='recording')
                count = len(events.read_text().splitlines())
                begin()
                call('lock', True)
                wait(lambda s: not s['busy'])
                time.sleep(.25)
                assert len(events.read_text().splitlines()) == count, 'Lock before unmap completion started a worker'
                call('lock', False)
                prepare('slow-start', mode='recording')
                begin()
                time.sleep(.25)
                call('lock', True)
                state = wait(lambda s: not s['busy'])
                assert not state['recording'] and not state['opened']
                call('lock', False)
                prepare('normal', mode='recording')
                begin()
                wait(lambda s: s['recording'])
                call('lock', True)
                wait(lambda s: not s['busy'])
                call('lock', False)
                prepare('error')
                begin()
                state = wait(lambda s: not s['busy'])
                assert state['reason'] == 'fixture_refused' and len(state['notices']) == 1, state
                prepare()
                begin()
                state = wait(lambda s: not s['busy'])
                assert state['notices'][-1]['title'] == 'Screenshot saved and copied', state
                assert call('configure', 'slow-inventory')
                assert call('present')
                time.sleep(.1)
                call('dismiss')
                time.sleep(1)
                state = call('state')
                assert not state['opened'] and not state['ready'], state
                assert call('configure', 'missing')
                assert call('present')
                state = wait(lambda s: bool(s['reason']), timeout=7)
                assert not state['ready'] and state['reason'] in ('inventory_failed', 'inventory_timeout'), state
                call('dismiss')
                prepare()
                assert call('configure', 'missing') is False
                call('loseHelper')
                begin()
                state = wait(lambda s: bool(s['reason']) and not s['busy'])
                assert state['reason'] == 'capture_failed' and len(state['notices']) == 1, state
                print('Capture controller: selection, recording stop, notifications, lock races, errors, missing helpers and inventory closure passed')
            finally:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        output = (profile / 'output.log').read_text()
        assert not any(word in output for word in ('ReferenceError', 'TypeError', 'Cannot assign', 'is not a type')), output


if __name__ == '__main__':
    main()
