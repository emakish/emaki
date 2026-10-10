// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import "settings"

Column {
    id: page
    required property var catalog
    spacing: 20
    property string lastShortcutKey: ""
    property var shortcutRecorder: null
    readonly property bool shortcutRecording: shortcutRecorder !== null
    readonly property var layoutsRow: catalog.row("keyboard.layouts")
    readonly property var layouts: layoutList(layoutsRow?.value)
    readonly property bool layoutsEditable: !!layoutsRow && layoutsRow.editable !== false && !catalog.writing
    readonly property var availableLayouts: (catalog.xkbLayouts || []).filter(entry => !layouts.includes(entry.code) && (entry.name + " " + entry.code).toLowerCase().includes(layoutSearch.text.trim().toLowerCase()))
    readonly property var shortcuts: Array.from(catalog.values || []).filter(row => row.key.startsWith("shortcuts.") && (row.title + " " + row.value + " " + row.default + " " + chord(row.value) + " " + chord(row.default)).toLowerCase().includes(shortcutSearch.text.trim().toLowerCase()))
    function layoutList(value) {
        return value && typeof value === "object" && typeof value.length === "number" ? Array.from(value) : [];
    }
    function chord(value) {
        return value ? String(value).replace(/(^|\+)Mod(?=\+|$)/g, "$1Super") : "";
    }
    function value(key, fallback) {
        const row = catalog.row(key);
        return row ? row.value : fallback;
    }
    function defaultValue(key, fallback) {
        const row = catalog.row(key);
        return row ? row.default : fallback;
    }
    function layoutName(code) {
        return (catalog.xkbLayouts || []).find(entry => entry.code === code)?.name || code;
    }
    function saveLayouts(next) {
        if (layoutsEditable && next.length > 0 && next.length <= 4)
            catalog.set("keyboard.layouts", next.join(","));
    }
    function moveLayout(index, offset) {
        const next = layouts.slice();
        const destination = index + offset;
        if (index < 0 || index >= next.length || destination < 0 || destination >= next.length)
            return;
        [next[index], next[destination]] = [next[destination], next[index]];
        saveLayouts(next);
    }
    function removeLayout(index) {
        if (index >= 0 && index < layouts.length)
            saveLayouts(layouts.filter((_, i) => i !== index));
    }
    function addLayout(code) {
        if (!layouts.includes(code) && (catalog.xkbLayouts || []).some(entry => entry.code === code))
            saveLayouts(layouts.concat([code]));
    }
    function failureLine(reason) {
        if (reason && reason.includes(" "))
            return reason;
        if (reason === "keyboard_requires_machine_settings" || reason === "machine_settings_unavailable" || reason === "machine_service_unavailable")
            return "Keyboard layout changes need the machine settings service.";
        if (reason === "machine_setting_has_no_default")
            return "Keyboard layouts have no universal default. Choose the languages you use.";
        if (reason === "authorization_refused" || reason === "authorization_required")
            return "Administrator approval is needed to change keyboard layouts.";
        if (reason === "machine_service_timeout")
            return "The machine settings service took too long to respond. Try again.";
        if (reason === "machine_settings_busy")
            return "Another machine setting is being changed. Try again shortly.";
        if (reason === "declared_by_system_configuration")
            return "Keyboard layouts are set by the system configuration.";
        if (reason === "unknown_layout")
            return "That keyboard layout is not available. Choose another layout.";
        if (reason === "invalid_keybinding")
            return "Enter a key combination such as Super+Shift+T.";
        return "Could not change the setting. Your previous value is kept.";
    }
    function shortcutExplanation(key, normal) {
        return lastShortcutKey === key && catalog.lastStatus === "rejected" ? failureLine(catalog.lastAction) : normal;
    }
    component Entry: TextField {
        leftPadding: 13
        rightPadding: 13
        topPadding: 8
        bottomPadding: 8
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 13
        color: SettingsTheme.ink
        placeholderTextColor: SettingsTheme.dim
        selectByMouse: true
        implicitHeight: 36
        background: Rectangle {
            radius: 10
            color: SettingsTheme.field
            border.width: parent.activeFocus ? 2 : 1
            border.color: parent.activeFocus ? SettingsTheme.accent : SettingsTheme.rim
        }
    }
    component ShortcutEntry: Entry {
        id: editor
        property bool recording: page.shortcutRecorder === editor
        property string previousText: ""
        rightPadding: 76
        readOnly: recording
        function cancelRecording() {
            if (recording) {
                text = previousText;
                page.shortcutRecorder = null;
            }
        }
        function startRecording() {
            if (page.shortcutRecorder)
                page.shortcutRecorder.cancelRecording();
            previousText = text;
            page.shortcutRecorder = editor;
            forceActiveFocus();
        }
        onActiveFocusChanged: if (!activeFocus)
            Qt.callLater(() => {
                if (!editor.activeFocus)
                    editor.cancelRecording();
            })
        onEnabledChanged: if (!enabled)
            cancelRecording()
        Component.onDestruction: if (recording)
            page.shortcutRecorder = null
        Keys.priority: Keys.BeforeItem
        Keys.onPressed: event => {
            if (!recording)
                return;
            event.accepted = true;
            if (event.key === Qt.Key_Escape) {
                cancelRecording();
                return;
            }
            if (event.isAutoRepeat || [Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_AltGr].includes(event.key))
                return;
            const named = {
                [Qt.Key_Return]: "Return",
                [Qt.Key_Enter]: "KP_Enter",
                [Qt.Key_Space]: "space",
                [Qt.Key_Tab]: "Tab",
                [Qt.Key_Backtab]: "Tab",
                [Qt.Key_Backspace]: "BackSpace",
                [Qt.Key_Print]: "Print",
                [Qt.Key_Delete]: "Delete",
                [Qt.Key_Insert]: "Insert",
                [Qt.Key_Home]: "Home",
                [Qt.Key_End]: "End",
                [Qt.Key_PageUp]: "Page_Up",
                [Qt.Key_PageDown]: "Page_Down",
                [Qt.Key_Left]: "Left",
                [Qt.Key_Right]: "Right",
                [Qt.Key_Up]: "Up",
                [Qt.Key_Down]: "Down",
                [Qt.Key_Minus]: "minus",
                [Qt.Key_Equal]: "equal",
                [Qt.Key_Plus]: "plus",
                [Qt.Key_Comma]: "comma",
                [Qt.Key_Period]: "period",
                [Qt.Key_Slash]: "slash",
                [Qt.Key_Semicolon]: "semicolon",
                [Qt.Key_Apostrophe]: "apostrophe",
                [Qt.Key_BracketLeft]: "bracketleft",
                [Qt.Key_BracketRight]: "bracketright",
                [Qt.Key_Backslash]: "backslash",
                [Qt.Key_QuoteLeft]: "grave"
            };
            let key = named[event.key] || "";
            if (event.key >= Qt.Key_A && event.key <= Qt.Key_Z || event.key >= Qt.Key_0 && event.key <= Qt.Key_9)
                key = String.fromCharCode(event.key);
            else if (event.key >= Qt.Key_F1 && event.key <= Qt.Key_F35)
                key = "F" + (event.key - Qt.Key_F1 + 1);
            if (!key)
                return;
            const standalone = event.key >= Qt.Key_F1 && event.key <= Qt.Key_F24 || key.startsWith("XF86") || key === "Print";
            if (!(event.modifiers & (Qt.MetaModifier | Qt.ControlModifier | Qt.AltModifier)) && !standalone)
                return;
            const parts = [];
            if (event.modifiers & Qt.MetaModifier)
                parts.push("Super");
            if (event.modifiers & Qt.ControlModifier)
                parts.push("Ctrl");
            if (event.modifiers & Qt.AltModifier)
                parts.push("Alt");
            if (event.modifiers & Qt.ShiftModifier)
                parts.push("Shift");
            text = parts.concat([key]).join("+");
            page.shortcutRecorder = null;
            editingFinished();
        }
        Keys.onReleased: event => {
            if (recording || event.key === Qt.Key_Escape)
                event.accepted = true;
        }
        SettingsButton {
            objectName: "shortcut-record-button"
            focusPolicy: Qt.TabFocus
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            width: 72
            text: editor.recording ? "Cancel" : "Record"
            Accessible.name: editor.recording ? "Cancel shortcut recording" : "Record shortcut"
            onClicked: editor.recording ? editor.cancelRecording() : editor.startRecording()
        }
        ToolTip.visible: recording
        ToolTip.text: "Press a key combination. Escape cancels."
    }
    Text {
        objectName: "keyboard-error"
        width: parent.width
        visible: ["rejected", "unconfirmed", "uncertain"].includes(page.catalog.lastStatus)
        text: ["unconfirmed", "uncertain"].includes(page.catalog.lastStatus) ? "Could not confirm the change. Reopen Settings to check." : page.failureLine(page.catalog.lastAction)
        textFormat: Text.PlainText
        color: "#a01b45"
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 13
        wrapMode: Text.WordWrap
    }
    SettingsCard {
        objectName: "setting-keyboard.layouts"
        width: parent.width
        title: "Layouts"
        Text {
            width: parent.width
            text: "The languages you type in, in the order the switch shortcut goes through them."
            color: SettingsTheme.dim
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 13
            wrapMode: Text.WordWrap
        }
        Repeater {
            model: page.layouts
            delegate: SettingsRow {
                id: layout
                required property string modelData
                required property int index
                objectName: "keyboard-layout-" + modelData
                width: parent.width
                title: page.layoutName(modelData)
                explanation: index === 0 ? "First in the switching order." : "Layout " + (index + 1) + " in the switching order."
                value: page.layouts
                defaultValue: page.layouts
                managed: page.layoutsRow?.editable === false
                busy: page.catalog.writing
                controlWidth: 132
                control: Component {
                    Row {
                        spacing: 4
                        SettingsButton {
                            text: "↑"
                            width: 38
                            enabled: layout.index > 0
                            Accessible.name: "Move " + layout.title + " up"
                            onClicked: page.moveLayout(layout.index, -1)
                        }
                        SettingsButton {
                            text: "↓"
                            width: 38
                            enabled: layout.index + 1 < page.layouts.length
                            Accessible.name: "Move " + layout.title + " down"
                            onClicked: page.moveLayout(layout.index, 1)
                        }
                        SettingsButton {
                            text: "−"
                            width: 38
                            enabled: page.layouts.length > 1
                            Accessible.name: "Remove " + layout.title
                            onClicked: page.removeLayout(layout.index)
                        }
                    }
                }
            }
        }
        Text {
            objectName: "keyboard-layout-unavailable"
            width: parent.width
            visible: !page.layoutsRow || page.layoutsRow.editable === false || page.layouts.length === 0
            text: page.layoutsRow?.source === "declared" ? "Keyboard layouts are set by the system configuration." : "Keyboard layouts are unavailable. Check the machine settings service."
            color: SettingsTheme.dim
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 13
            wrapMode: Text.WordWrap
        }
        SettingsButton {
            text: "Reset layouts to default"
            visible: page.layoutList(page.layoutsRow?.default).length > 0 && JSON.stringify(page.layouts) !== JSON.stringify(page.layoutList(page.layoutsRow.default))
            enabled: page.layoutsEditable
            onClicked: page.catalog.reset("keyboard.layouts")
        }
        Entry {
            id: layoutSearch
            objectName: "keyboard-layout-search"
            width: parent.width
            placeholderText: "Search languages to add"
            Accessible.name: placeholderText
            enabled: page.layoutsEditable && page.layouts.length < 4
        }
        Row {
            width: parent.width
            spacing: 8
            SettingsComboBox {
                id: layoutChoice
                objectName: "keyboard-layout-choice"
                width: parent.width - addLayout.width - 8
                model: page.availableLayouts
                textRole: "name"
                valueRole: "code"
                enabled: page.layoutsEditable && page.layouts.length < 4
                font.family: SettingsTheme.fontFamily
                font.pixelSize: 13
                Accessible.name: "Language to add"
            }
            SettingsButton {
                id: addLayout
                objectName: "keyboard-add-layout"
                text: "Add layout"
                enabled: page.layoutsEditable && page.layouts.length < 4 && layoutChoice.currentIndex >= 0
                onClicked: page.addLayout(layoutChoice.currentValue)
            }
        }
    }
    SettingsCard {
        width: parent.width
        SettingsChoiceRow {
            objectName: "setting-keyboard.switch_key"
            width: parent.width
            title: "Switch layouts with"
            explanation: "Go to the next language in your layout list."
            value: page.value("keyboard.switch_key", "Super+Space")
            defaultValue: page.defaultValue("keyboard.switch_key", "Super+Space")
            options: ["Super+Space", "Alt+Shift", "Caps Lock"].map(value => ({
                        label: value === "Super+Space" ? page.chord(page.value("shortcuts.Mod+Space", "Mod+Space")) : value,
                        value: value
                    }))
            managed: page.catalog.row("keyboard.switch_key")?.editable === false
            unavailable: !page.catalog.row("keyboard.switch_key")
            busy: page.catalog.writing
            onValueRequested: value => page.catalog.set("keyboard.switch_key", value)
            onResetRequested: page.catalog.reset("keyboard.switch_key")
        }
    }
    SettingsCard {
        width: parent.width
        title: "Key repeat"
        SettingsSliderRow {
            objectName: "setting-keyboard.repeat_delay"
            width: parent.width
            title: "Delay before repeat"
            explanation: value + " ms before a held key starts repeating."
            value: page.value("keyboard.repeat_delay", 600)
            defaultValue: page.defaultValue("keyboard.repeat_delay", 600)
            from: 100
            to: 2000
            stepSize: 50
            unavailable: !page.catalog.row("keyboard.repeat_delay")
            busy: page.catalog.writing
            onValueRequested: value => page.catalog.set("keyboard.repeat_delay", String(Math.round(value)))
            onResetRequested: page.catalog.reset("keyboard.repeat_delay")
        }
        SettingsSliderRow {
            objectName: "setting-keyboard.repeat_rate"
            width: parent.width
            title: "Repeat speed"
            explanation: value + " characters per second while a key is held."
            value: page.value("keyboard.repeat_rate", 25)
            defaultValue: page.defaultValue("keyboard.repeat_rate", 25)
            from: 1
            to: 100
            stepSize: 1
            unavailable: !page.catalog.row("keyboard.repeat_rate")
            busy: page.catalog.writing
            onValueRequested: value => page.catalog.set("keyboard.repeat_rate", String(Math.round(value)))
            onResetRequested: page.catalog.reset("keyboard.repeat_rate")
        }
        Item {
            width: parent.width
            implicitHeight: repeatTrial.implicitHeight + 16
            Entry {
                id: repeatTrial
                width: parent.width
                placeholderText: "Hold a key here to try the delay and speed"
                Accessible.name: placeholderText
            }
        }
    }
    SettingsCard {
        objectName: "settings-keyboard-shortcuts"
        width: parent.width
        title: "Shortcuts"
        Entry {
            id: shortcutSearch
            objectName: "keyboard-shortcut-search"
            width: parent.width
            placeholderText: "Search shortcuts — a word or a key"
            Accessible.name: placeholderText
        }
        Text {
            width: parent.width
            text: "Enter a combination and press Enter. Super+Ctrl+Esc is reserved for revoking administrator access."
            color: SettingsTheme.dim
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 13
            wrapMode: Text.WordWrap
        }
        SettingsRow {
            id: floating
            objectName: "setting-keybindings.toggle_window_floating"
            width: parent.width
            visible: (title + " " + value).toLowerCase().includes(shortcutSearch.text.trim().toLowerCase())
            title: "Toggle floating window"
            explanation: page.shortcutExplanation("keybindings.toggle_window_floating", page.catalog.row("keybindings.toggle_window_floating")?.explanation || "Switch the focused window between floating and tiled.")
            value: page.chord(page.value("keybindings.toggle_window_floating", null)) || null
            defaultValue: page.chord(page.defaultValue("keybindings.toggle_window_floating", null)) || null
            unavailable: !page.catalog.row("keybindings.toggle_window_floating") || page.catalog.row("keybindings.toggle_window_floating")?.editable === false
            busy: page.catalog.writing
            controlWidth: 270
            onValueRequested: value => {
                page.lastShortcutKey = "keybindings.toggle_window_floating";
                page.catalog.set("keybindings.toggle_window_floating", value);
            }
            onResetRequested: page.catalog.reset("keybindings.toggle_window_floating")
            control: Component {
                ShortcutEntry {
                    text: floating.value || ""
                    placeholderText: "No shortcut set"
                    Accessible.name: floating.title + " shortcut"
                    onEditingFinished: {
                        if (text.trim() !== (floating.value || ""))
                            floating.requestValue(text.trim());
                        text = Qt.binding(() => floating.value || "");
                    }
                }
            }
        }
        Repeater {
            model: page.shortcuts
            delegate: SettingsRow {
                id: shortcut
                required property var modelData
                objectName: "setting-" + modelData.key
                width: parent.width
                title: modelData.title || modelData.key.slice(10)
                explanation: page.shortcutExplanation(modelData.key, modelData.explanation || "Choose the keys that perform this action.")
                value: page.chord(modelData.value)
                defaultValue: page.chord(modelData.default)
                busy: page.catalog.writing
                managed: modelData.editable === false
                controlWidth: 270
                onValueRequested: value => {
                    page.lastShortcutKey = modelData.key;
                    page.catalog.set(modelData.key, value);
                }
                onResetRequested: page.catalog.reset(modelData.key)
                control: Component {
                    ShortcutEntry {
                        objectName: "shortcut-editor"
                        text: String(shortcut.value)
                        Accessible.name: shortcut.title + " shortcut"
                        onEditingFinished: {
                            if (text.trim() !== String(shortcut.value))
                                shortcut.requestValue(text.trim());
                            text = Qt.binding(() => String(shortcut.value));
                        }
                    }
                }
            }
        }
        Text {
            visible: page.shortcuts.length === 0 && !floating.visible
            text: "No shortcuts found. Try another word."
            color: SettingsTheme.dim
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 13
        }
    }
}
