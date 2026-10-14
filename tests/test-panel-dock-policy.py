#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Production panel/dock policies, real timers and rendered slide evidence."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / '.cache/evidence/panel-dock'
EVIDENCE.mkdir(parents=True, exist_ok=True)
TMP = ROOT / '.cache/tmp'
TMP.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='p-', dir=ROOT / '.cache') as temporary:
    base = Path(temporary)
    qml = base / 'qml'
    shutil.copytree(ROOT / 'shell', qml)
    fixture = (ROOT / 'tests/fixtures/PanelDockPolicyTest.qml').read_text()
    surfaces = (ROOT / 'shell/Surfaces.qml').read_text()
    for part in ('bar', 'dock'):
        expression = re.search(r'WlrLayershell.layer: (surfaces.controller.' + part + r'Policy[^\n]+)', surfaces)[1]
        fixture = fixture.replace('property int ' + part + 'Layer: 0',
                                  'property int ' + part + 'Layer: ' + expression.replace('surfaces.controller', 'scene'))
    # The fullscreen inset marker's production mapping and height expressions.
    marker = re.search(r'PanelWindow \{\n        id: fullscreenInset\n(.*?)\n    \}\n', surfaces, re.S)[1]
    assert 'WlrLayershell.namespace: "emaki-fullscreen-inset"' in marker and 'mask: Region {}' in marker, marker
    assert 'exclusionMode: ExclusionMode.Ignore' in marker and 'exclusiveZone' not in marker, marker
    for name, key in (('insetMarkerVisible: false', 'visible'), ('insetMarkerHeight: 0', 'implicitHeight')):
        expression = re.search(r'\n        ' + key + r': ([^\n]+)', marker)[1]
        if key == 'visible':
            # The headless scene has no output; keep the policy part of the production expression.
            guard = 'surfaces.controller.enabled && surfaces.controller.output !== null && '
            assert expression.startswith(guard), expression
            expression = expression[len(guard):]
        kind = name.split(':')[0]
        fixture = fixture.replace('property ' + ('bool ' if 'Visible' in kind else 'int ') + name,
                                  'property ' + ('bool ' if 'Visible' in kind else 'int ') + kind + ': '
                                  + expression.replace('surfaces.controller', 'scene'))
    assert 'insetMarkerVisible: scene.' in fixture and 'insetMarkerHeight: Math.max(1, scene.' in fixture
    (qml / 'shell.qml').write_text(fixture)
    for name in ('home', 'cache', 'config', 'state', 'data', 'r'):
        (base / name).mkdir(mode=0o700)
    apps = base / 'data/applications'
    apps.mkdir()
    for app, title in [('fixture-editor', 'Editor'), ('fixture-files', 'Files')]:
        (apps / (app + '.desktop')).write_text(
            '[Desktop Entry]\nType=Application\nName=' + title + '\nExec=true\nTerminal=false\n')
    env = dict(os.environ, HOME=str(base / 'home'), QT_QPA_PLATFORM='offscreen',
               QT_QUICK_BACKEND='software', QT_SCALE_FACTOR='1', QML_DISABLE_DISK_CACHE='1',
               EMAKI_BIN='', EMAKI_SETTINGS_PROFILE='', EMAKI_TEST_SYSTEM='0', EMAKI_TEST_MPRIS='0',
               EMAKI_SHELL_NOTIFICATIONS='0', EMAKI_SHELL_TRAY='0', NIRI_SOCKET='',
               EMAKI_SHELL_BAR_AUTOHIDE='0', EMAKI_SHELL_OVERVIEW_WORKSPACES='1',
               XDG_RUNTIME_DIR=str(base / 'r'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_CONFIG_HOME=str(base / 'config'), XDG_STATE_HOME=str(base / 'state'),
               XDG_DATA_HOME=str(base / 'data'), XDG_DATA_DIRS=str(base / 'data'), TMPDIR=str(TMP),
               PANEL_DOCK_SHOTS=str(EVIDENCE), DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system'))
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_SCREEN_SCALE_FACTORS', 'QT_PLUGIN_PATH',
                 'QML_IMPORT_PATH', 'QML2_IMPORT_PATH', 'QT_LOGGING_RULES', 'QT_QPA_PLATFORMTHEME'):
        env.pop(name, None)

    for label, size in [('EMPTY', None), ('NORMAL', [800, 600]), ('FULLSCREEN', [1280, 800])]:
        snapshot = dict(focused_output='Fixture', overview_open=False, links_pending=False,
                        keyboard_layouts=dict(names=['English (US)'], current_idx=0),
                        outputs={'Fixture': dict(logical=dict(width=1280, height=800))},
                        workspaces={'1': dict(id=1, idx=1, is_active=True, is_focused=True, output='Fixture')},
                        windows={} if size is None else {'8': dict(id=8, workspace_id=1, layout=dict(tile_size=size))})
        env['PANEL_DOCK_' + label] = json.dumps(dict(schema_version=1, ipc_release='26.04', generation=1,
                                                    connection=dict(status='connected', reason='synchronized'), model=snapshot))

    def run(label):
        result = subprocess.run(['qs', '-p', str(qml), '--no-color'], env=env,
                                capture_output=True, text=True, timeout=60)
        output = result.stdout + result.stderr
        (EVIDENCE / (label + '.log')).write_text(output)
        found = re.search(r'PANEL_DOCK_RESULT (\d+) (\d+)', output)
        return output, result.returncode == 0 and found and int(found[2]) == 0

    output, passed = run('current')
    assert passed, output
    for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign'):
        assert error not in output, output
    diagnostics = [line for line in output.splitlines() if ' WARN ' in line or ' ERROR ' in line]
    unexpected = [line for line in diagnostics if 'Failed to start IPC server on path' not in line]
    assert not unexpected, '\n'.join(unexpected)
    if diagnostics:
        print('NOTE: sandbox denies the optional IPC server; all tests run in process')
    traces = [line.split('TRACE ', 1)[1] for line in output.splitlines() if 'TRACE ' in line]
    (EVIDENCE / 'timing.jsonl').write_text('\n'.join(traces) + '\n')
    for name in ('empty-desktop-visible', 'hide-middle', 'hidden', 'fullscreen-reveal-middle', 'fullscreen-revealed'):
        assert (EVIDENCE / (name + '.png')).stat().st_size > 1000, name
    current_panel = (qml / 'BarPolicy.qml').read_text()
    regression_shots = base / 'regression-shots'
    regression_shots.mkdir()
    env['PANEL_DOCK_SHOTS'] = str(regression_shots)
    for label, original, replacement, failure in (
        ('fullscreen-override', 'fullscreen ? autoHideFullscreen : autoHide',
         'fullscreen || autoHide', 'desktop/FULLSCREEN status visible'),
        ('fullscreen-inset-missing', 'fullscreen && !autoHideFullscreen ? configuredReserve : 0',
         '0', 'fullscreen inset/covered'),
        ('fullscreen-reserve-shift', 'autoHide ? Math.max(0, configuredReserve - 42) : configuredReserve',
         'fullscreen ? (autoHideFullscreen ? 0 : configuredReserve) : (autoHide ? Math.max(0, configuredReserve - 42) : configuredReserve)',
         'reserve/covered'),
        ('unknown-edge-disabled', 'hidesHere && !overview',
         'hidesHere && presentation !== "unknown" && !overview', 'unknown keeps reveal reachable'),
    ):
        assert original in current_panel, label
        (qml / 'BarPolicy.qml').write_text(current_panel.replace(original, replacement, 1))
        output, passed = run(label)
        assert not passed and failure in output and 'PANEL_DOCK_RESULT' in output, output
        assert 'ReferenceError' not in output and 'TypeError' not in output, output
    (qml / 'BarPolicy.qml').write_text(current_panel)
    current_service = (qml / 'NiriService.qml').read_text()
    inset_condition = 'if (!w.is_floating && scrolling && position &&'
    assert inset_condition in current_service
    (qml / 'NiriService.qml').write_text(current_service.replace(inset_condition, 'if (false &&', 1))
    output, passed = run('inset-fullscreen-detection')
    assert not passed and 'inset fullscreen geometry' in output and 'PANEL_DOCK_RESULT' in output, output
    assert 'ReferenceError' not in output and 'TypeError' not in output, output
    (qml / 'NiriService.qml').write_text(current_service)
    # Keep the same callable interface so failures establish changed decisions, not parse errors.
    for name, additions in {
        'BarPolicy.qml': 'property bool autoHideFullscreen: true\n'
                        'readonly property bool hidesHere: fullscreen || autoHide\n'
                        'readonly property string hideMode: autoHide ? (autoHideFullscreen ? "always" : "desktop") : (autoHideFullscreen ? "fullscreen" : "never")\n'
                        'property bool keyboardActive: false\nproperty bool dragging: false\n'
                        'property bool pointerRevealed: false\n'
                        'property int revealDelayMs: 150\nproperty int hideDelayMs: 500\n'
                        'property int slideDurationMs: 280\nproperty int fullscreenInset: 0\n',
        'DockPolicy.qml': 'property bool edgeDragging: false\nproperty int revealDelayMs: 100\n'
                         'property int hideDelayMs: 100\nproperty int slideDurationMs: 320\n',
    }.items():
        old = (ROOT / 'tests/fixtures/baseline' / name.replace('.qml', '-0.8.0.qml')).read_text()
        (qml / name).write_text(old.replace('id: policy', 'id: policy\n' + additions, 1))
    baseline_shots = base / 'baseline-shots'
    baseline_shots.mkdir()
    env['PANEL_DOCK_SHOTS'] = str(baseline_shots)
    output, passed = run('before')
    assert not passed and 'PANEL_DOCK_RESULT' in output, output
    assert int(re.search(r'PANEL_DOCK_RESULT (\d+) (\d+)', output)[2]) > 0, output
    assert 'ReferenceError' not in output and 'TypeError' not in output, output
print('PASS: 12 mode/state rows and status, unknown fallback, real timers, holds, reserve and slides; regressions and baseline fail')
print('Evidence:', EVIDENCE)
