#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise real Qt/gettext catalogues and the untranslated-copy gate in isolation."""
import gettext
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


catalogues = module('catalogues', ROOT / 'i18n/catalogues.py')
scanner = module('scanner', ROOT / 'tests/check-i18n.py')


class Translations(unittest.TestCase):
    def test_pseudo_keeps_fields_markup_and_newline_contract(self):
        source = '\nHello <b>%(name)s</b>: %L1, %n, {count}\n'
        target = catalogues.pseudo(source)
        self.assertTrue(target.startswith('\n[') and target.endswith(']\n'))
        for token in ('<b>', '</b>', '%(name)s', '%L1', '%n', '{count}'):
            self.assertIn(token, target)
        self.assertGreaterEqual(len(target), len(source) * 1.3)

    def test_scanner_rejects_presentation_literals(self):
        for text in ('Text { text: "Untranslated" }', 'Button { Accessible.name: "Open" }',
                     'TextField { placeholderText: `Enter ${name}` }'):
            self.assertTrue(list(scanner.qml_findings(text)), text)
        self.assertFalse(list(scanner.qml_findings('Text { text: qsTranslate("Test", "Hello") }')))
        self.assertTrue(list(scanner.python_findings('return_value = dict(error="Try again.")')))
        self.assertFalse(list(scanner.python_findings('return_value = dict(error=_("Try again."))')))
        self.assertTrue(list(scanner.rust_findings('eprintln!("Try again.");')))
        self.assertFalse(list(scanner.rust_findings('eprintln!("{}", tr("Try again."));')))
        self.assertTrue(list(scanner.wrapper_findings("printf 'Try again.\\n'")))
        self.assertFalse(list(scanner.wrapper_findings("translate 'Try again.'")))

    def test_scanner_checks_dictionaries_and_stable_identifiers(self):
        self.assertTrue(list(scanner.qml_findings('property var words: ({start: "Start"})')))
        self.assertFalse(list(scanner.qml_findings('property var words: ({start: qsTr("Start")}); objectName: "clock"')))
        self.assertTrue(list(scanner.python_findings('ERRORS = {"invalid_duration": "Invalid duration"}')))
        for source in ('parser.get(_("VPN Connection"), "service")',
                       'rules[_("Programs & Updates")] = True',
                       'reply = {"phase": _("flatpak")}',
                       'if state == _("Ready"): pass'):
            self.assertTrue(list(scanner.identifier_findings(source, 'python')), source)
        for source in ('store.local(qsTr("Launcher"), message, "")',
                       'id: qsTr("name:Battery low")',
                       'text: qsTr("Type ERASE")',
                       'line.startsWith("Keyboard: ")',
                       '/^Updating Flatpak applications/.test(message)',
                       'message.includes("hold the power button")',
                       'function status(): var {\n return {sound: qsTr("Muted")};\n    }'):
            self.assertTrue(list(scanner.identifier_findings(source, 'qml')), source)
        self.assertTrue(list(scanner.invariant_findings('installer/emaki_installer/boot.py',
                                                        'title = _("Emaki, with Linux {value1}")')))
        self.assertTrue(list(scanner.invariant_findings('niri/default.kdl',
                                                        'match title="^Welcome to Emaki$"')))
        self.assertTrue(list(scanner.invariant_findings('tests/vm/check.py',
                                                        "window.get('title') == 'Recovery snapshot'")))
        self.assertFalse(list(scanner.identifier_findings('store.local("Launcher", qsTr("Ready"), "")', 'qml')))

    def test_welcome_title_fallback_requires_stable_identity_in_the_same_rule(self):
        stable = '    match app-id=r#"^org\\.emaki\\.welcome$"#\n'
        legacy = '    match app-id=r#"^org\\.quickshell$"# title="^Welcome to Emaki$"\n'
        rule = 'window-rule {\n' + stable + legacy + '    open-floating true\n}\n'
        self.assertFalse(list(scanner.invariant_findings('niri/default.kdl', rule)))
        for invalid in (rule.replace(stable, ''), rule.replace(stable, stable + '}\nwindow-rule {\n'),
                        rule.replace('^org\\.quickshell$', '.*')):
            self.assertTrue(list(scanner.invariant_findings('niri/default.kdl', invalid)), invalid)

    def test_entrypoints_can_load_without_the_optional_plugin(self):
        for path in ('shell/shell.qml', 'shell/lock.qml', 'shell/greeter.qml', 'shell/session-cover.qml',
                     'notes/ui/shell.qml', 'update-manager/ui/shell.qml', 'installer/ui/shell.qml',
                     'installer/ui/InstallerView.qml', 'update-manager/ui/ManagerView.qml',
                     'update-manager/ui/ProgramReviews.qml'):
            source = (ROOT / path).read_text()
            self.assertNotIn('import Emaki.I18n', source, path)
            self.assertIn('Translation.initialize()', source, path)
        with tempfile.TemporaryDirectory(prefix='i18n-fallback-') as directory:
            base = Path(directory)
            original = (ROOT / 'shell/Translation.qml').read_text()
            source = original.replace('import Emaki.I18n', 'import Emaki.UnavailableCatalogue')
            self.assertNotEqual(source, original)
            (base / 'Translation.qml').write_text(source)
            (base / 'qmldir').write_text('singleton Translation 1.0 Translation.qml\n')
            (base / 'ManagerLogic.js').write_text((ROOT / 'update-manager/ui/ManagerLogic.js').read_text().replace('@EMAKI_DATADIR@', str(ROOT)))
            plurals = []
            for domain in catalogues.DOMAINS:
                path = ROOT / 'i18n' / domain / 'en.ts'
                if not path.exists():
                    continue
                for context in ET.parse(path).findall('context'):
                    for message in context.findall("message[@numerus='yes']"):
                        forms = message.findall('translation/numerusform')
                        for count in (0, 1, 2):
                            expected = forms[0 if count == 1 else 1].text.replace('%n', str(count))
                            plurals.append([context.findtext('name'), message.findtext('source'), count, expected])
            (base / 'test.qml').write_text('''import QtQuick
import Quickshell
import "PROTOCOL_URI" as Protocol
import "LOGIC_URI" as Logic
import "SUDO_URI" as Sudo
ShellRoot {
    property bool ready: Translation.initialize()
    property var plurals: PLURAL_CASES
    FloatingWindow { Text { id: label; text: qsTranslate("Test", "English fallback") } }
    Timer { interval: 10; running: true; onTriggered: {
        if (ready || label.text !== "English fallback") { Qt.exit(1); return; }
        if (Translation.timeZoneLabel("America/New_York", "ru") !== "America/New York"
                || Translation.searchTimeZones("Москва", "ru").length !== 0) { Qt.exit(7); return; }
        for (const row of plurals) {
            const actual = Translation.englishPlural(qsTranslate(row[0], row[1], "", row[2]), row[2]);
            if (actual !== row[3]) { console.error(JSON.stringify(row), actual); Qt.exit(2); return; }
        }
        for (const count of [1, 2]) {
            const state = {forcedDeadline: count * 1000};
            Protocol.forcedTick(state, 0);
            const unit = count === 1 ? "second" : "seconds";
            if (state.rebootMessage.indexOf("Restarting in " + count + " " + unit + "…") < 0) { Qt.exit(3); return; }
            const blockers = count === 1 ? ["One"] : ["One", "Two"];
            const expected = "This install can no longer be taken back as it was. " + blockers.join(", ") + (count === 1 ? " needs" : " need") + " what it added.";
            if (Logic.undoRefusal({code: "cannot-undo", requiredBy: blockers}) !== expected) { Qt.exit(4); return; }
            if (Sudo.keyTurns(count) !== count + (count === 1 ? " turn" : " turns")) { Qt.exit(5); return; }
            if (Sudo.keyTurnsRemaining(count) !== count + (count === 1 ? " turn left" : " turns left")) { Qt.exit(6); return; }
        }
        console.log("FALLBACK_OK"); Qt.quit();
    } }
}
'''.replace('PLURAL_CASES', json.dumps(plurals))
                .replace('PROTOCOL_URI', (ROOT / 'installer/ui/Protocol.js').as_uri())
                .replace('LOGIC_URI', (base / 'ManagerLogic.js').as_uri())
                .replace('SUDO_URI', (ROOT / 'shell/SudoText.js').as_uri()))
            env = os.environ.copy()
            for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME',
                        'QML_IMPORT_PATH', 'QML2_IMPORT_PATH', 'EMAKI_LANGUAGE', 'LANGUAGE'):
                env.pop(key, None)
            for key in ('HOME', 'XDG_RUNTIME_DIR', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME'):
                path = base / key
                path.mkdir(mode=0o700)
                env[key] = str(path)
            env.update(QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QS_DISABLE_CRASH_HANDLER='1',
                       QML_DISABLE_DISK_CACHE='1', DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
                       DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
            result = subprocess.run(['qs', '-p', str(base / 'test.qml')], env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('FALLBACK_OK', result.stdout + result.stderr)

    def test_numerus_callers_keep_english_fallback_and_notice_identity(self):
        for root in ('shell', 'installer/ui', 'update-manager/ui'):
            for path in (ROOT / root).glob('*.qml'):
                for line in path.read_text().splitlines():
                    if 'qsTranslate(' in line and any(marker in line for marker in ('(s)', '(es)', '(ies)')):
                        self.assertIn('Translation.englishPlural(qsTranslate(', line, str(path))
        notice = (ROOT / 'shell/SessionUpdateNotice.qml').read_text()
        self.assertIn('store.localNotice("Desktop update", qsTranslate(', notice)

    def test_gettext_catalogues_and_placeholders(self):
        for domain in catalogues.DOMAINS:
            source = catalogues.python_messages(domain)
            translated = gettext.translation(domain, localedir=ROOT / 'i18n/locale', languages=['en_XA'])
            for message, plural in source.items():
                value = translated.gettext(message) if plural is None else translated.ngettext(message, plural, 2)
                self.assertIn('[', value, (domain, message))
                expected = plural if plural is not None else message
                self.assertEqual(catalogues.PROTECTED.findall(expected), catalogues.PROTECTED.findall(value))

    def test_every_qt_message_uses_real_catalogue(self):
        entries = []
        for domain in catalogues.DOMAINS:
            path = ROOT / 'i18n' / domain / 'en.ts'
            if not path.exists():
                continue
            for context in ET.parse(path).findall('context'):
                for message in context.findall('message'):
                    entries.append([context.findtext('name'), message.findtext('source'),
                                    2 if message.get('numerus') == 'yes' else -1])
        self.assertGreater(len(entries), 1000)
        qml = '''import QtQuick
import Quickshell
import Emaki.I18n
ShellRoot {
    property var entries: ENTRIES
    Timer { interval: 10; running: true; onTriggered: {
        for (const entry of entries) {
            const value = qsTranslate(entry[0], entry[1], "", entry[2]);
            if (!value.includes("[")) { console.error("UNTRANSLATED", JSON.stringify(entry), value); Qt.exit(1); return; }
        }
        console.log("I18N_QT_PASS", entries.length);
        Qt.quit();
    } }
}
'''.replace('ENTRIES', json.dumps(entries, ensure_ascii=False))
        with tempfile.TemporaryDirectory(prefix='ei18n-') as tmp:
            base = Path(tmp)
            env = os.environ.copy()
            for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME'):
                env.pop(key, None)
            for key, child in [('HOME', 'home'), ('XDG_RUNTIME_DIR', 'run'), ('XDG_CONFIG_HOME', 'config'),
                               ('XDG_CACHE_HOME', 'cache'), ('XDG_DATA_HOME', 'data'), ('XDG_STATE_HOME', 'state')]:
                path = base / child
                path.mkdir(mode=0o700)
                env[key] = str(path)
            env.update(QML_IMPORT_PATH=str(ROOT / '.cache/i18n-native/qml'),
                       EMAKI_LOCALE_DIR=str(ROOT / 'i18n/locale'), EMAKI_LANGUAGE='en_XA',
                       QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                       QS_DISABLE_CRASH_HANDLER='1', QML_DISABLE_DISK_CACHE='1',
                       DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
                       DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
            path = base / 'test.qml'
            path.write_text(qml)
            result = subprocess.run(['qs', '-p', str(path), '--no-color'], env=env,
                                    capture_output=True, text=True, timeout=20)
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, output)
            self.assertIn('I18N_QT_PASS', output)
            self.assertNotIn('UNTRANSLATED', output)


if __name__ == '__main__':
    unittest.main()
