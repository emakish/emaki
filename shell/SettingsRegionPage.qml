// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell.Io
import "settings"

Column {
    id: page
    spacing: 20
    property var catalog: null
    property var rows: []
    property var installedLocales: []
    property var timeZones: []
    function options(key) {
        let values = (key === "time.zone" ? timeZones : installedLocales).slice();
        const current = page.row(key).value ?? "";
        if (current && !values.includes(current))
            values.unshift(current);
        let result = values.map(value => ({
                    label: choiceLabel(key, value),
                    value: value
                }));
        if (key === "locale.formats")
            result.unshift({
                label: "Follow language",
                value: ""
            });
        return result;
    }
    property string message: ""
    property bool received: false
    function choiceLabel(key, value) {
        if (key === "time.zone")
            return value.replace(/_/g, " ");
        const locale = Qt.locale(value.replace(/\.UTF-8$/, ""));
        return locale.nativeLanguageName + " — " + locale.nativeTerritoryName + " (" + value + ")";
    }
    function row(key) {
        return rows.find(item => item.key === key) || {};
    }
    function start(args) {
        if (provider.running)
            return;
        received = false;
        message = "";
        provider.command = ["emaki-machine-settings"].concat(args, ["--json"]);
        provider.running = true;
    }
    function apply(key, value) {
        start(["set", key, String(value)]);
    }
    Component.onCompleted: start(["get", "locale.language", "locale.formats", "time.zone", "time.automatic"])
    Process {
        id: choicesProvider
        command: ["emaki-machine-settings", "choices", "--json"]
        running: true
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    const reply = JSON.parse(text);
                    if (reply.schema_version === 1 && Array.isArray(reply.locales) && Array.isArray(reply.time_zones)) {
                        page.installedLocales = reply.locales;
                        page.timeZones = reply.time_zones;
                    }
                } catch (error) {}
            }
        }
        stderr: StdioCollector {}
    }
    Process {
        id: provider
        onRunningChanged: {
            if (!running)
                Qt.callLater(function () {
                    if (!page.received)
                        page.message = "Machine settings are unavailable. Try again when the system service is ready.";
                });
        }
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    const reply = JSON.parse(text);
                    if (reply.schema_version !== 1)
                        throw new Error();
                    page.received = true;
                    if (reply.status === "rejected" && reply.reason === "machine_settings_busy") {
                        page.message = "Another machine setting is being changed. Wait a moment, then try again.";
                        return;
                    }
                    if (!Array.isArray(reply.settings)) {
                        page.message = "The system could not apply this setting. Check your permission and try again.";
                        return;
                    }
                    let next = page.rows.slice();
                    for (const item of reply.settings) {
                        next = next.filter(old => old.key !== item.key);
                        next.push(item);
                    }
                    page.rows = next;
                    if (reply.status === "uncertain")
                        page.message = "The change could not be confirmed. Check the current value before trying again.";
                    else if (!["read", "applied", "unchanged"].includes(reply.status))
                        page.message = "The system could not apply this setting. Check the value and your permission to change it.";
                } catch (error) {
                    page.message = "Machine settings could not be read.";
                }
            }
        }
        stderr: StdioCollector {}
        onExited: _code => {
            if (!page.received)
                page.message = "Machine settings are unavailable. Try again when the system service is ready.";
        }
    }
    Text {
        width: parent.width
        visible: text !== ""
        text: page.message
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        color: "#a01b45"
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 13
    }
    SettingsCard {
        width: parent.width
        title: "Language and region"
        Repeater {
            model: [
                {
                    key: "locale.language",
                    title: "Language",
                    explanation: "Choose an installed language. Sign out to use it in all apps."
                },
                {
                    key: "locale.formats",
                    title: "Formats",
                    explanation: "Choose an installed locale for dates and numbers."
                },
                {
                    key: "time.zone",
                    title: "Time zone",
                    explanation: "Choose a time zone installed on this computer."
                }
            ]
            delegate: SettingsRow {
                id: setting
                objectName: ({
                        "locale.language": "settings-language",
                        "locale.formats": "settings-formats",
                        "time.zone": "settings-time-zone"
                    })[modelData.key]
                visible: page.row(modelData.key).source !== "unavailable" && page.row(modelData.key).key !== undefined
                required property var modelData
                width: parent.width
                title: modelData.title
                explanation: modelData.explanation
                value: page.row(modelData.key).value ?? ""
                defaultValue: value
                busy: provider.running
                managed: page.row(modelData.key).source === "declared"
                unavailable: !page.row(modelData.key).editable && !managed
                controlWidth: Math.min(400, page.width * .5)
                control: Component {
                    SettingsComboBox {
                        model: page.options(setting.modelData.key)
                        textRole: "label"
                        valueRole: "value"
                        currentIndex: model.findIndex(option => option.value === String(setting.value))
                        font.family: SettingsTheme.fontFamily
                        font.pixelSize: 13
                        Accessible.name: setting.title
                        popup.height: Math.min(300, popup.implicitHeight)
                        onActivated: page.apply(setting.modelData.key, currentValue)
                    }
                }
            }
        }
        SettingsToggleRow {
            width: parent.width
            objectName: "settings-24-hour-clock"
            title: "24-hour clock"
            explanation: "Use 24-hour time in the panel clock."
            value: page.catalog?.value("bar.clock_24_hour") ?? true
            defaultValue: true
            busy: page.catalog?.busy ?? false
            unavailable: !page.catalog
            onValueRequested: value => page.catalog?.set("bar.clock_24_hour", String(value))
        }
        SettingsToggleRow {
            width: parent.width
            objectName: "settings-automatic-time"
            visible: page.row("time.automatic").source !== "unavailable" && page.row("time.automatic").key !== undefined
            title: "Automatic time"
            explanation: "Keep the clock in sync with an internet time service."
            value: page.row("time.automatic").value ?? false
            defaultValue: value
            busy: provider.running
            managed: page.row("time.automatic").source === "declared"
            unavailable: !page.row("time.automatic").editable && !managed
            onValueRequested: value => page.apply("time.automatic", value)
        }
    }
    SettingsButton {
        text: "Refresh"
        enabled: !provider.running
        onClicked: {
            page.start(["get", "locale.language", "locale.formats", "time.zone", "time.automatic"]);
            choicesProvider.running = true;
        }
    }
}
