#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offscreen recovery, visible-page refresh, and arbitrary power delay regressions."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from runtime_fixture import runtime_path
import reaper

reaper.guard()
ROOT = Path(os.environ.get('SETTINGS_RECOVERY_SOURCE', Path(__file__).resolve().parents[1]))
CASES = {
    'battery-auto': ('SettingsPowerPage { id: page; width: 700; service: service }', '''
        tryVerify(() => page.error !== "");
        tryVerify(() => page.snapshot.state === "ready", 12000);
        compare(page.error, "");'''),
    'lock-auto': ('SettingsPowerPage { id: page; width: 700; service: service; page: "lock" }', '''
        tryVerify(() => page.error !== "");
        tryVerify(() => page.snapshot.state === "ready", 12000);'''),
    'power-retry': ('SettingsPowerPage { id: page; width: 700; service: service }', '''
        tryVerify(() => page.error !== "" && !page.pending);
        const retry = findChild(page, "settings-power-retry");
        verify(retry !== null && retry.visible && retry.enabled);
        retry.clicked();
        tryVerify(() => page.snapshot.state === "ready");'''),
    'delays': ('SettingsPowerPage { id: page; width: 700; service: service }', '''
        tryVerify(() => page.snapshot.state === "ready");
        for (const name of ["settings-blank-battery", "settings-blank-ac", "settings-lock-delay"]) {
            const row = findChild(page, name);
            verify(row.options.some(option => option.value === 150 && option.label === "150 seconds"));
        }'''),
    'secure-auto': ('SecureBootSettings { id: page; width: 700 }', '''
        tryCompare(page, "status", {schema_version: 1, secure_boot: false, key: null, enrollment: null, signed: {}});
        tryVerify(() => page.status.key === true, 7000);
        verify(page.visible);'''),
    'secure-refresh': ('SettingsPrivacyPage { id: page; width: 700; sudoSupport: null }', '''
        const card = findChild(page, "settings-privacy-secure-boot");
        tryVerify(() => card.status.key === true && !card.busy && !page.pending);
        compare(card.status.secure_boot, false);
        compare(findChild(page, "privacy-refresh").text, "Refresh");
        findChild(page, "privacy-refresh").clicked();
        tryCompare(card, "status", {schema_version: 1, secure_boot: true, key: true, enrollment: "enrolled", signed: {}});'''),
    'apps-auto': ('SettingsAppsPage { id: page; width: 700; catalog: catalog }', '''
        tryVerify(() => page.programs.length === 1);
        compare(page.programs[0].package, "old");
        tryVerify(() => page.programs.length === 1 && page.programs[0].package === "new", 12000);'''),
    'apps-shown': ('SettingsAppsPage { id: page; width: 700; catalog: catalog }', '''
        tryVerify(() => page.programs.length === 1 && !page.programsBusy);
        page.visible = false;
        wait(50);
        page.visible = true;
        tryVerify(() => page.programs.length === 1 && page.programs[0].package === "new", 2000);'''),
    'apps-status-retry': ('SettingsAppsPage { id: page; width: 700; catalog: catalog }', '''
        tryVerify(() => page.error !== "");
        const retry = findChild(page, "settings-apps-retry");
        verify(retry !== null && retry.visible);
        tryVerify(() => retry.enabled);
        retry.clicked();
        tryCompare(page, "error", "");
        tryVerify(() => page.applications.length === 1);'''),
    'apps-retry': ('SettingsAppsPage { id: page; width: 700; catalog: catalog }', '''
        tryVerify(() => page.programsMessage !== "" && !page.programsBusy);
        const retry = findChild(page, "settings-programs-retry");
        verify(retry !== null && retry.visible && retry.enabled);
        retry.clicked();
        tryVerify(() => page.programs.length === 1);
        compare(page.programsMessage, "");'''),
}
HELPER = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
name = Path(sys.argv[0]).name
mode = os.environ['RECOVERY_CASE']
log = Path(os.environ['XDG_STATE_HOME']) / name
count = int(log.read_text()) + 1 if log.exists() else 1
log.write_text(str(count))
if name == 'emaki-settings-power':
    if mode != 'delays' and count == 1:
        sys.exit(1)
    answer = dict(schema_version=1, state='ready', idle_available=True, values=dict(blank_battery=150, blank_ac=150, lock_delay=150), defaults={}, batteries=[])
elif name == 'emaki-secureboot':
    answer = dict(schema_version=1, secure_boot=count > 1, key=None if mode == 'secure-auto' and count == 1 else True, enrollment=None if mode == 'secure-auto' and count == 1 else 'enrolled', signed={})
elif name == 'emaki-programs':
    if mode == 'apps-retry' and count == 1:
        sys.exit(1)
    answer = dict(ok=True, programs=[dict(package='old' if count == 1 else 'new', names=['Editor'])])
elif name == 'emaki-settings-apps':
    if mode == 'apps-status-retry' and count == 1:
        sys.exit(1)
    answer = dict(ok=True, applications=[dict(id='editor.desktop', name='Editor', roles=[])], startup=[], defaults={})
elif name == 'emaki-settings-privacy':
    answer = dict(schema_version=1, status='ready', recovery=dict(snapshot=dict(state='none'), rollback=dict(mode='normal'), menu=dict(state='missing')), encryption=dict(state='unencrypted', mounts=[]), portals=dict(state='ready', rows=[], unavailable=[]), lock=dict(state='ready', delay=0), reports=dict(saved='None'))
else:
    sys.exit(1)
print(json.dumps(answer))
'''
failures = []
with tempfile.TemporaryDirectory(prefix='settings-read-recovery-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    for name in ('home', 'config', 'data', 'cache', 'state', 'bin'):
        (base / name).mkdir(mode=0o700)
    for name in ('emaki-settings-power', 'emaki-secureboot', 'emaki-programs', 'emaki-settings-apps', 'emaki-settings-privacy'):
        helper = base / 'bin' / name
        helper.write_text(HELPER)
        helper.chmod(0o700)
    (base / 'bin/python3').symlink_to(shutil.which('python3'))
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
               XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime_path(base)),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1',
               PATH=str(base / 'bin'),
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME'):
        env.pop(key, None)
    for mode, (page, assertions) in CASES.items():
        if os.environ.get('SETTINGS_RECOVERY_CASES') and mode not in os.environ['SETTINGS_RECOVERY_CASES'].split(','):
            continue
        for log in (base / 'state').iterdir():
            log.unlink()
        (base / 'shell/test.qml').write_text('''// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import QtTest
import Quickshell
ShellRoot {
    SystemService { id: service }
    QtObject { id: catalog; property bool writing: false; function row(key) { return {value: null}; } }
    Item {
        width: 700; height: 1000
        ''' + page + '''
        TestCase {
            name: "ReadRecovery"
            when: true
            function test_recovery() {''' + assertions + '''
            }
            onCompletedChanged: if (completed)
                console.log("RECOVERY_RESULT " + qtest_results.failCount)
        }
    }
}
''')
        result = subprocess.run([shutil.which('qs'), '-p', str(base / 'shell/test.qml'), '--no-color'],
                                env=dict(env, RECOVERY_CASE=mode), capture_output=True, text=True, timeout=25)
        output = result.stdout + result.stderr
        passed = result.returncode == 0 and 'RECOVERY_RESULT 0' in output
        passed = passed and not any(error in output for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign'))
        print(('PASS: ' if passed else 'FAIL: ') + mode, flush=True)
        if not passed:
            failures.append(mode)
            print(output, flush=True)
assert not failures, failures
