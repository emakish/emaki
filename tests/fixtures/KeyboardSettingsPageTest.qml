// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    id: root
    property list<string> machineLayouts: ["us", "ru"]
    SettingsCatalog {
        id: nativeCatalog
        active: false
    }
    PrivateJob {
        id: transport
        onCompleted: response => SettingsBridge.values = response.settings
    }
    QtObject {
        id: catalog
        property bool busy: false
        readonly property bool writing: busy
        property string lastStatus: ""
        property string lastAction: ""
        property var calls: []
        property var values: []
        property var xkbLayouts: [
            {
                code: "us",
                name: "English (US)"
            },
            {
                code: "gb",
                name: "English (UK)"
            },
            {
                code: "de",
                name: "German"
            },
            {
                code: "fr",
                name: "French"
            },
            {
                code: "ua",
                name: "Ukrainian"
            }
        ]
        function row(key: string): var {
            return values.find(entry => entry.key === key);
        }
        function set(key, value) {
            calls = calls.concat([
                {
                    kind: "set",
                    key: key,
                    value: value
                }
            ]);
        }
        function reset(key) {
            calls = calls.concat([
                {
                    kind: "reset",
                    key: key
                }
            ]);
        }
        function replace(key, changes) {
            values = values.map(entry => entry.key === key ? Object.assign({}, entry, changes) : entry);
        }
    }
    FloatingWindow {
        id: window
        visible: true
        implicitWidth: 760
        implicitHeight: 1000
        color: "#eee4db"
        Flickable {
            property int escaped: 0
            Keys.onEscapePressed: escaped++
            anchors.fill: parent
            contentHeight: page.height + 40
            clip: true
            KeyboardSettingsPage {
                id: page
                x: 20
                y: 20
                width: parent.width - 40
                catalog: catalog
            }
        }
    }
    TestCase {
        name: "KeyboardSettingsPage"
        when: window.backingWindowVisible
        onCompletedChanged: if (completed)
            console.log("KEYBOARD_SETTINGS_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
        function find(item, name) {
            if (item.objectName === name)
                return item;
            for (const child of item.children ?? []) {
                const result = find(child, name);
                if (result)
                    return result;
            }
            return null;
        }
        function row(key) {
            return find(page, "setting-" + key);
        }
        function last(kind, key, value) {
            verify(catalog.calls.length > 0);
            const call = catalog.calls[catalog.calls.length - 1];
            compare(call.kind, kind);
            compare(call.key, key);
            if (kind === "set")
                compare(call.value, value);
        }
        function init() {
            page.catalog = catalog;
            catalog.busy = false;
            catalog.lastStatus = "";
            catalog.lastAction = "";
            catalog.calls = [];
            catalog.values = [
                {
                    key: "keyboard.layouts",
                    value: ["us", "de"],
                    default: null,
                    editable: true,
                    source: "machine"
                },
                {
                    key: "keyboard.switch_key",
                    value: "Super+Space",
                    default: "Super+Space",
                    editable: true
                },
                {
                    key: "keyboard.repeat_delay",
                    value: 600,
                    default: 600,
                    editable: true
                },
                {
                    key: "keyboard.repeat_rate",
                    value: 25,
                    default: 25,
                    editable: true
                },
                {
                    key: "keybindings.toggle_window_floating",
                    value: null,
                    default: null,
                    editable: true
                },
                {
                    key: "shortcuts.launcher",
                    title: "Open applications",
                    value: "Super+D",
                    default: "Super+D",
                    editable: true
                },
                {
                    key: "shortcuts.terminal",
                    title: "Open terminal",
                    value: "Super+T",
                    default: "Super+T",
                    editable: true
                },
                {
                    key: "shortcuts.close",
                    title: "Close window",
                    value: "Super+Q",
                    default: "Super+W",
                    editable: true
                }
            ];
            find(page, "keyboard-layout-search").text = "";
            find(page, "keyboard-shortcut-search").text = "";
            wait(0);
        }
        function test_layout_changes() {
            page.addLayout("fr");
            last("set", "keyboard.layouts", "us,de,fr");
            page.moveLayout(0, 1);
            last("set", "keyboard.layouts", "de,us");
            page.moveLayout(1, -1);
            last("set", "keyboard.layouts", "de,us");
            page.removeLayout(0);
            last("set", "keyboard.layouts", "de");
            const before = catalog.calls.length;
            page.moveLayout(-1, 1);
            page.moveLayout(0, -1);
            page.moveLayout(1, 1);
            page.removeLayout(9);
            page.addLayout("us");
            page.addLayout("invalid");
            compare(catalog.calls.length, before);
            catalog.replace("keyboard.layouts", {
                value: ["us"]
            });
            page.removeLayout(0);
            compare(catalog.calls.length, before, "Keep at least one layout");
            catalog.replace("keyboard.layouts", {
                value: ["us", "de", "fr", "gb"]
            });
            page.addLayout("ua");
            compare(catalog.calls.length, before, "At most four layouts");
            verify(!find(page, "keyboard-add-layout").enabled);
        }
        function test_catalog_layout_sequence() {
            // Native sequences can arrive through the helper's var signal and
            // the catalog's typed return without becoming JavaScript arrays.
            transport.completed({
                settings: [
                    {
                        key: "keyboard.layouts",
                        value: root.machineLayouts,
                        default: null,
                        editable: true,
                        source: "machine"
                    }
                ]
            });
            page.catalog = nativeCatalog;
            const source = nativeCatalog.row("keyboard.layouts").value;
            verify(!Array.isArray(source), "The native sequence remains a sequence through the catalog");
            compare(source.length, 2);
            compare(page.layouts.length, 2, "Available machine layouts survive the catalog boundary");
            wait(0);
            verify(find(page, "keyboard-layout-us").visible);
            verify(find(page, "keyboard-layout-ru").visible);
            verify(!find(page, "keyboard-layout-unavailable").visible);
            for (const invalid of [null, undefined, "us,de", 2,
                {}
            ])
                compare(page.layoutList(invalid).length, 0, "Malformed values are not layout lists");
        }
        function test_layout_guards_and_search() {
            find(page, "keyboard-layout-search").text = "FREN";
            compare(page.availableLayouts.length, 1);
            compare(page.availableLayouts[0].code, "fr");
            find(page, "keyboard-layout-search").text = "ua";
            compare(page.availableLayouts[0].code, "ua");
            for (const state of ["busy", "declared", "unavailable"]) {
                catalog.busy = state === "busy";
                catalog.replace("keyboard.layouts", {
                    editable: state === "busy",
                    source: state
                });
                page.addLayout("fr");
                page.removeLayout(0);
                page.moveLayout(1, -1);
                compare(catalog.calls.length, 0, state + " forbids mutations");
                verify(!find(page, "keyboard-add-layout").enabled);
            }
            catalog.values = catalog.values.filter(entry => entry.key !== "keyboard.layouts");
            page.addLayout("fr");
            compare(catalog.calls.length, 0);
            verify(!page.layoutsEditable);
        }
        function test_repeat_and_switch() {
            for (const spec of [["keyboard.repeat_delay", 750], ["keyboard.repeat_rate", 32]]) {
                const control = row(spec[0]);
                control.requestValue(spec[1]);
                last("set", spec[0], String(spec[1]));
                catalog.replace(spec[0], {
                    value: spec[1]
                });
                control.requestReset();
                last("reset", spec[0]);
                catalog.busy = true;
                const before = catalog.calls.length;
                control.requestValue(50);
                control.requestReset();
                compare(catalog.calls.length, before);
                catalog.busy = false;
            }
            row("keyboard.switch_key").requestValue("Alt+Shift");
            last("set", "keyboard.switch_key", "Alt+Shift");
            catalog.replace("keyboard.switch_key", {
                value: "Alt+Shift"
            });
            row("keyboard.switch_key").requestReset();
            last("reset", "keyboard.switch_key");
            catalog.values = catalog.values.concat([
                {
                    key: "shortcuts.Mod+Space",
                    value: "Mod+Shift+Space",
                    default: "Mod+Space",
                    title: "Switch keyboard layout"
                }
            ]);
            compare(row("keyboard.switch_key").options[0].label, "Super+Shift+Space", "The layout choice shows the remapped Emaki shortcut");
            catalog.replace("keyboard.switch_key", {
                editable: false,
                source: "declared"
            });
            const before = catalog.calls.length;
            row("keyboard.switch_key").requestValue("Caps Lock");
            compare(catalog.calls.length, before);
        }
        function test_shortcuts_search_edit_reset() {
            compare(page.shortcuts.length, 3);
            compare(page.chord("Mod+Ctrl+T"), "Super+Ctrl+T");
            catalog.replace("shortcuts.terminal", {
                value: "Mod+T",
                default: "Mod+T"
            });
            find(page, "keyboard-shortcut-search").text = "Super+T";
            compare(page.shortcuts.length, 1, "Search accepts the printed Super key name");
            compare(row("shortcuts.terminal").value, "Super+T");
            find(page, "keyboard-shortcut-search").text = "TERMINAL";
            compare(page.shortcuts.length, 1);
            compare(page.shortcuts[0].key, "shortcuts.terminal");
            find(page, "keyboard-shortcut-search").text = "Super+W";
            compare(page.shortcuts.length, 1, "Search also finds the default combination");
            find(page, "keyboard-shortcut-search").text = "";
            wait(0);
            const control = row("shortcuts.terminal");
            const editor = control.controlItem;
            editor.text = "  Super+Shift+T  ";
            editor.editingFinished();
            last("set", "shortcuts.terminal", "Super+Shift+T");
            catalog.lastStatus = "rejected";
            catalog.lastAction = "That shortcut is already in use. Choose another key combination.";
            compare(control.explanation, catalog.lastAction, "Conflict stays beside the edited shortcut");
            catalog.lastStatus = "";
            catalog.replace("shortcuts.terminal", {
                value: "Super+Shift+T"
            });
            row("shortcuts.terminal").requestReset();
            last("reset", "shortcuts.terminal");
            catalog.busy = true;
            const before = catalog.calls.length;
            row("shortcuts.terminal").requestValue("Super+X");
            row("shortcuts.terminal").requestReset();
            compare(catalog.calls.length, before);
            catalog.busy = false;
            catalog.replace("shortcuts.terminal", {
                editable: false
            });
            row("shortcuts.terminal").requestValue("Super+X");
            compare(catalog.calls.length, before);
        }
        function test_record_shortcuts_with_keys_and_cancel() {
            const editor = row("shortcuts.terminal").controlItem;
            editor.startRecording();
            verify(editor.recording);
            verify(editor.activeFocus);
            const before = catalog.calls.length;
            keyPress(Qt.Key_Control);
            compare(catalog.calls.length, before, "Modifiers alone do not submit");
            keyRelease(Qt.Key_Control);
            keyClick(Qt.Key_T, Qt.ShiftModifier);
            compare(catalog.calls.length, before, "Shift plus a letter cannot capture ordinary typing");
            verify(editor.recording);
            keyClick(Qt.Key_T, Qt.MetaModifier | Qt.ControlModifier | Qt.ShiftModifier);
            last("set", "shortcuts.terminal", "Super+Ctrl+Shift+T");
            verify(!page.shortcutRecording);
            editor.startRecording();
            const original = editor.previousText;
            const calls = catalog.calls.length;
            const escaped = page.parent.escaped;
            keyClick(Qt.Key_Escape);
            compare(editor.text, original);
            compare(catalog.calls.length, calls, "Escape does not save a shortcut");
            verify(!page.shortcutRecording);
            compare(page.parent.escaped, escaped, "Escape does not reach the window");
            for (const binding of [[Qt.Key_Left, "Left"], [Qt.Key_F7, "F7"], [Qt.Key_Return, "Return"], [Qt.Key_Tab, "Tab"]]) {
                editor.startRecording();
                keyClick(binding[0], Qt.MetaModifier | Qt.AltModifier);
                last("set", "shortcuts.terminal", "Super+Alt+" + binding[1]);
                verify(!page.shortcutRecording);
            }
            for (const binding of [[Qt.Key_F7, "Shift+F7"], [Qt.Key_Print, "Shift+Print"]]) {
                editor.startRecording();
                keyClick(binding[0], Qt.ShiftModifier);
                last("set", "shortcuts.terminal", binding[1]);
                verify(!page.shortcutRecording);
            }
            editor.startRecording();
            find(page, "keyboard-shortcut-search").forceActiveFocus();
            tryCompare(page, "shortcutRecording", false);
        }
        function test_record_cancel_mouse_click() {
            verify(waitForPolish(page.Window.window), "Settle the restored layout before locating the recorder");
            const editor = row("shortcuts.terminal").controlItem;
            const button = find(editor, "shortcut-record-button");
            const original = editor.text;
            const before = catalog.calls.length;
            page.parent.contentY = editor.mapToItem(page, 0, 0).y;
            verify(waitForPolish(page.Window.window), "Settle the scroll position before clicking the recorder");
            mouseClick(button, button.width / 2, button.height / 2);
            verify(editor.recording);
            compare(button.text, "Cancel");
            mousePress(button, button.width / 2, button.height / 2);
            wait(40);
            mouseRelease(button, button.width / 2, button.height / 2);
            verify(!editor.recording, "Cancel must stay cancelled after release");
            compare(button.text, "Record");
            compare(editor.text, original);
            compare(catalog.calls.length, before, "Cancel does not submit a shortcut");
            page.parent.contentY = 0;
        }
        function test_floating_and_unavailable_repeat() {
            const floating = row("keybindings.toggle_window_floating");
            floating.controlItem.text = "Super+Shift+F";
            floating.controlItem.editingFinished();
            last("set", "keybindings.toggle_window_floating", "Super+Shift+F");
            catalog.replace("keybindings.toggle_window_floating", {
                value: "Super+Shift+F"
            });
            floating.requestReset();
            last("reset", "keybindings.toggle_window_floating");
            find(page, "keyboard-shortcut-search").text = "floating";
            verify(floating.visible);
            find(page, "keyboard-shortcut-search").text = "no matching action";
            verify(!floating.visible);
            const before = catalog.calls.length;
            catalog.values = catalog.values.filter(entry => !entry.key.startsWith("keyboard.repeat_"));
            for (const key of ["keyboard.repeat_delay", "keyboard.repeat_rate"]) {
                verify(row(key).unavailable);
                row(key).requestValue(50);
            }
            compare(catalog.calls.length, before);
        }
        function test_failure_lines() {
            const error = find(page, "keyboard-error");
            verify(!error.visible);
            catalog.lastStatus = "rejected";
            for (const spec of [["machine_settings_unavailable", "machine settings service"], ["keyboard_requires_machine_settings", "machine settings service"], ["authorization_refused", "Administrator approval"], ["invalid_keybinding", "key combination"], ["machine_service_timeout", "too long"], ["machine_settings_busy", "Try again shortly"], ["declared_by_system_configuration", "system configuration"], ["unknown_layout", "not available"]]) {
                catalog.lastAction = spec[0];
                verify(error.visible);
                verify(error.text.includes(spec[1]), spec[0]);
                verify(!error.text.includes(spec[0]), "Present a plain failure line");
            }
            catalog.lastAction = "That shortcut is already used by Open applications.";
            compare(error.text, catalog.lastAction);
            catalog.lastStatus = "unconfirmed";
            verify(error.text.includes("Could not confirm"));
            catalog.lastStatus = "uncertain";
            verify(error.visible);
            verify(error.text.includes("Could not confirm"));
        }
        function test_screenshot() {
            wait(100);
            let saved = false;
            page.grabToImage(result => saved = result.saveToFile(Quickshell.env("KEYBOARD_SETTINGS_SHOT")));
            tryVerify(() => saved, 5000);
        }
    }
}
