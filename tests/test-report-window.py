#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Report a problem: the real controller and window offscreen, with a stub emaki-report and
browser. Nothing leaves before Send; the preview is exactly what Send hands to the browser."""
import configparser
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / '.cache/evidence'
EVIDENCE.mkdir(parents=True, exist_ok=True)
SHOTS = EVIDENCE / 'report-shots'
SHOTS.mkdir(exist_ok=True)
if not (ROOT / 'shell/Platform.qml').exists():
    sys.exit('shell/Platform.qml is generated: run `make paths` first')


def entries():
    """The launcher entry and every offer call reach the shell's `report` IPC target."""
    desktop = configparser.ConfigParser(interpolation=None)
    desktop.read(ROOT / 'shell/emaki-report.desktop')
    entry = desktop['Desktop Entry']
    assert entry['Exec'] == 'emaki-shell call report open other', entry['Exec']
    assert entry['Type'] == 'Application' and entry.get('NoDisplay', 'false') == 'false'
    controller = (ROOT / 'shell/ReportController.qml').read_text()
    assert 'target: "report"' in controller
    for function in ('function open(part: string): string', 'function offer(part: string): string'):
        assert function in controller, function
    main = (ROOT / 'shell/shell.qml').read_text()
    assert 'ReportController {' in main and 'ReportBridge.controller = report' in main
    about = (ROOT / 'shell/SettingsAboutPage.qml').read_text()
    assert 'ReportBridge.open("other")' in about and 'xdg-open' not in about.split('id: viewer')[0]
    makefile = (ROOT / 'Makefile').read_text()
    assert 'shell/emaki-report.desktop $(DESTDIR)$(PREFIX)/share/applications/emaki-report.desktop' in makefile
    assert "['emaki-shell', 'call', 'report', 'offer', part]" in (ROOT / 'update-manager/reminder.py').read_text()
    assert '["emaki-shell", "call", "report", "offer", "install"]' in (ROOT / 'update-manager/ui/ProgramsController.qml').read_text()
    assert '"report:shell"' in (ROOT / 'shell/ShellRecoveryNotice.qml').read_text()


def run(mode, mutation=None):
    with tempfile.TemporaryDirectory(prefix='report-window-', dir=EVIDENCE) as directory:
        base = Path(directory)
        shutil.copytree(ROOT / 'shell', base / 'shell')
        shutil.copyfile(ROOT / 'tests/fixtures/ReportTest.qml', base / 'shell/ReportTest.qml')
        if mutation:
            path = base / 'shell' / mutation[0]
            text = path.read_text()
            assert mutation[1] in text, f'mutation not present in source: {mutation}'
            path.write_text(text.replace(mutation[1], mutation[2]))
        for name in ('home', 'config', 'data', 'cache', 'state', 'runtime', 'bin', 'profile'):
            (base / name).mkdir(mode=0o700)
        (base / 'profile/mode').write_text(mode)
        helper = base / 'bin/emaki-report'
        shutil.copyfile(ROOT / 'tests/fixtures/report-helper-stub.py', helper)
        # noexec: the helper is there but cannot start.
        helper.chmod(0o600 if mode == 'noexec' else 0o700)
        browser = base / 'bin/xdg-open'
        browser.write_text('#!/bin/sh\nprintf "%s\\n" "$1" > "$REPORT_PROFILE/opened"\n'
                           '[ "$(cat "$REPORT_PROFILE/mode")" != nobrowser ]\n')
        browser.chmod(0o700)
        env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
                   XDG_DATA_HOME=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
                   XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(base / 'runtime'),
                   QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1',
                   QS_DISABLE_CRASH_HANDLER='1', REPORT_MODE=mode, REPORT_PROFILE=str(base / 'profile'),
                   REPORT_SHOTS=str(SHOTS) if mutation is None else '',
                   PATH=os.pathsep.join([str(base / 'bin')] + [d for d in os.environ['PATH'].split(os.pathsep)
                                                               if d and not (Path(d) / 'emaki-report').exists()]),
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_LOGGING_RULES'):
            env.pop(key, None)
        result = subprocess.run(['qs', '-p', str(base / 'shell/ReportTest.qml'), '--no-color'], env=env,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        output = result.stdout
        passed = result.returncode == 0 and 'REPORT_PASS' in output
        for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign',
                      'is not a type', 'Failed to load'):
            passed = passed and error not in output
        if mutation is None:
            assert passed, f'{mode}:\n{output}'
        else:
            assert not passed, f'Surviving mutation: {mutation}'


# ok/big/badurl/nobrowser: preview, untick, Send (fresh, so it sends the shown reply at once);
# fail/bad/noexec: compose fails, the helper answers nonsense, or it cannot start; huge: the
# document on stdin, too_big, the shown preview copied; limit: 2000 characters in the field;
# unopened/stale: Send with no or a stale preview refreshes it and asks to send again.
MODES = ('ok', 'big', 'fail', 'bad', 'badurl', 'nobrowser', 'huge', 'noexec', 'limit', 'unopened', 'stale')
entries()
for mode in sys.argv[1:] or MODES:  # Naming modes runs only them, without the mutants.
    run(mode)
if sys.argv[1:]:
    sys.exit(print('PASS (modes only):', ' '.join(sys.argv[1:])))
# (mode that must catch it, file, source text, replacement). A source text that is not in the file
# fails loudly ("mutation not present in source"), so a stale entry cannot pass silently.
MUTANTS = [
    # Ignoring the person's choices.
    ('ok', 'ReportController.qml', 'return items.filter(i => chosen[i.id] === true).map(i => i.id);', 'return items.map(i => i.id);'),
    # Ticking everything regardless of the helper's defaults.
    ('ok', 'ReportController.qml', 'next[item.id] = item.default;', 'next[item.id] = true;'),
    # Accepting a non-web address.
    ('badurl', 'ReportController.qml', 'reply.url.startsWith("https://")', 'true'),
    # A notice action that does not open the window.
    ('ok', 'ReportController.qml', 'controller.open(action.slice(7));', ''),
    # Send runs `emaki-report url` again instead of handing over the reply of the shown preview.
    ('ok', 'ReportController.qml', 'browser.command = ["xdg-open", shown.url];',
     'previewer.again = false; refreshPreview(); browser.command = ["xdg-open", shown.url];'),
    # Send ignores staleness: it sends while the preview is stale or still refreshing.
    ('stale', 'ReportController.qml', 'if (!previewOpen || previewStale) {', 'if (!previewOpen) {'),
    # Send ignores a preview that was never opened.
    ('unopened', 'ReportController.qml', 'if (!previewOpen || previewStale) {', 'if (previewOpen && previewStale) {'),
    # The preview step opens the browser itself: sending before Send.
    ('ok', 'ReportController.qml', 'controller.preview = controller.outgoing(reply);',
     'controller.preview = controller.outgoing(reply); browser.command = ["xdg-open", reply.url]; browser.running = true;'),
    # Too big: Send copies nothing.
    ('big', 'ReportController.qml', 'Quickshell.clipboardText = shown.body;', ''),
    # The composed document back in argv through a `sh -c` wrapper.
    ('huge', 'ReportController.qml', 'previewer.command = ["emaki-report", "url", "--what", what, "--items", ids.join(",")];',
     'previewer.command = ["sh", "-c", \'printf "%s" "$1" | exec emaki-report url --what "$2" --items "$3"\', "emaki-report", '
     'previewer.document, what, ids.join(",")];'),
    # An own size ceiling in the controller again.
    ('huge', 'ReportController.qml', 'const ids = chosenIds();\n        previewer.key',
     'if (JSON.stringify(report).length > 120000) return;\n        const ids = chosenIds();\n        previewer.key'),
    # No input limit on the visible field.
    ('limit', 'ReportView.qml', 'if (length > view.controller.whatLimit)', 'if (false)'),
    # Only exit is handled: a helper that cannot start leaves the window "composing".
    ('noexec', 'ReportController.qml', 'onRunningChanged: {\n            if (!running)\n                Qt.callLater(() => {\n                    if (!composer.running',
     'onExited: {\n            if (true)\n                Qt.callLater(() => {\n                    if (!composer.running'),
]
for mutant in MUTANTS:
    run(mutant[0], mutant[1:])
print(f'PASS: launcher entry, About, offers wired to the report IPC; report window modes {", ".join(MODES)}; '
      f'Send sends the shown preview, only when fresh; document on stdin; input limit; helper that cannot start; '
      f'notice offer and action; {len(MUTANTS)} mutants killed')
