// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell.Io
import "settings"

Column {
    id: root
    required property SystemService service
    property string page: "battery"
    readonly property bool lockOnly: page === "lock"
    property var snapshot: ({
            values: {},
            defaults: {},
            batteries: []
        })
    property string error: ""
    property var command: ["emaki-settings-power", "status", "--json"]
    property bool received: false
    property bool readingStatus: true
    readonly property var delays: [
        {
            value: 0,
            label: qsTranslate("SettingsPowerPage", "Never")
        },
        {
            value: 60,
            label: qsTranslate("SettingsPowerPage", "1 minute")
        },
        {
            value: 120,
            label: qsTranslate("SettingsPowerPage", "2 minutes")
        },
        {
            value: 300,
            label: qsTranslate("SettingsPowerPage", "5 minutes")
        },
        {
            value: 330,
            label: qsTranslate("SettingsPowerPage", "5½ minutes")
        },
        {
            value: 600,
            label: qsTranslate("SettingsPowerPage", "10 minutes")
        },
        {
            value: 900,
            label: qsTranslate("SettingsPowerPage", "15 minutes")
        },
        {
            value: 1800,
            label: qsTranslate("SettingsPowerPage", "30 minutes")
        },
        {
            value: 3600,
            label: qsTranslate("SettingsPowerPage", "1 hour")
        }
    ]
    readonly property bool pending: process.running
    property string actionKind: ""
    property int actionSerial: -1
    property string actionResult: "idle"
    property bool awaitingAction: false
    Connections {
        target: root.Window.window
        function onVisibleChanged(): void {
            if (!root.Window.window.visible)
                root.clearAction();
        }
    }
    function clearAction() {
        awaitingAction = false;
        actionKind = "";
        actionResult = "idle";
    }
    function request(kind, value) {
        awaitingAction = false;
        actionKind = kind;
        const accepted = service.act(kind, value);
        actionSerial = service.actionSerial;
        actionResult = service.actionState;
        awaitingAction = accepted && ["pending", "busy"].includes(actionResult);
        return accepted;
    }
    Connections {
        target: root.service
        function onActionStateChanged(): void {
            if (!root.awaitingAction)
                return;
            if (root.actionSerial !== root.service.actionSerial) {
                root.clearAction();
                return;
            }
            root.actionResult = root.service.actionState;
            root.awaitingAction = ["pending", "busy"].includes(root.actionResult);
        }
    }
    spacing: 16

    function delayOptions(current) {
        if (current === undefined || current === null || delays.some(option => option.value === current))
            return delays;
        return delays.concat([
            {
                value: current,
                label: Translation.englishPlural(qsTranslate("SettingsPowerPage", "%n second(s)", "", current), current)
            }
        ]);
    }
    function chargeOptions(current) {
        const values = [50, 60, 70, 80, 90, 100];
        const options = values.map(value => ({
                    value: value,
                    label: value + "%"
                }));
        if (current !== null && current !== undefined && !values.includes(current))
            options.push({
                value: current,
                label: qsTranslate("SettingsPowerPage", "%1% (current)").arg(current.toFixed(0))
            });
        return options;
    }
    function send(args) {
        if (process.running)
            return;
        error = "";
        readingStatus = args[0] === "status";
        received = false;
        command = ["emaki-settings-power"].concat(args, ["--json"]);
        process.running = true;
    }
    function set(key, value) {
        send(["set", key, String(value)]);
    }
    function reset(key) {
        send(["reset", key]);
    }
    onVisibleChanged: if (!visible)
        clearAction()
    onPageChanged: clearAction()
    Component.onCompleted: send(["status"])
    Process {
        id: process
        command: root.command
        onRunningChanged: {
            if (!running)
                Qt.callLater(function () {
                    if (!root.received && !root.error)
                        root.error = qsTranslate("SettingsPowerPage", "Power settings are unavailable. Try again when the system service is ready.");
                });
        }
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    const result = JSON.parse(text);
                    if (result.schema_version !== 1 || !result.state)
                        throw new Error("protocol");
                    root.received = true;
                    if (result.state === "ready") {
                        root.snapshot = result;
                        root.error = result.message || "";
                    } else
                        root.error = result.message || qsTranslate("SettingsPowerPage", "Could not apply the power setting.");
                } catch (_) {
                    root.error = qsTranslate("SettingsPowerPage", "Could not read the power settings.");
                }
            }
        }
        onExited: (code, status) => {
            if (code !== 0 && !root.error)
                root.error = qsTranslate("SettingsPowerPage", "Could not apply the power setting.");
        }
    }
    Timer {
        interval: 10000
        running: root.visible && !root.pending
        repeat: true
        onTriggered: {
            // Keep a failed change visible until the next deliberate action.
            if (!root.error || root.readingStatus)
                root.send(["status"]);
        }
    }
    Text {
        visible: text.length > 0
        width: parent.width
        text: root.error
        textFormat: Text.PlainText
        color: SettingsTheme.error
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 12
        wrapMode: Text.WordWrap
    }
    SettingsButton {
        objectName: "settings-power-retry"
        visible: root.error !== ""
        enabled: !root.pending
        text: qsTranslate("SettingsPowerPage", "Retry")
        onClicked: root.send(["status"])
    }
    Text {
        objectName: "settings-power-action-status"
        visible: !root.lockOnly && !["idle", "confirmed", "applied", "ready"].includes(root.actionResult)
        width: parent.width
        text: root.awaitingAction ? qsTranslate("SettingsPowerPage", "Working…") : qsTranslate("SettingsPowerPage", "Could not confirm the power mode change.")
        textFormat: Text.PlainText
        color: SettingsTheme.dim
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 12
        wrapMode: Text.WordWrap
    }
    Text {
        objectName: "settings-no-battery"
        visible: !root.lockOnly && root.snapshot.state === "ready" && root.snapshot.batteries?.length === 0
        width: parent.width
        text: qsTranslate("SettingsPowerPage", "No battery")
        textFormat: Text.PlainText
        color: SettingsTheme.dim
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 13
        wrapMode: Text.WordWrap
    }
    SettingsCard {
        width: parent.width
        visible: !root.lockOnly
        title: qsTranslate("SettingsPowerPage", "Screen and lid")
        SettingsChoiceRow {
            width: parent.width
            objectName: "settings-blank-battery"
            visible: root.snapshot.idle_available === true
            title: qsTranslate("SettingsPowerPage", "Blank the screen on battery")
            explanation: qsTranslate("SettingsPowerPage", "Turn off the screen after this much inactivity on battery power.")
            options: root.delayOptions(root.snapshot.values.blank_battery)
            value: root.snapshot.values.blank_battery ?? 0
            defaultValue: (root.snapshot.defaults || {}).blank_battery ?? value
            busy: root.pending
            onValueRequested: value => root.set("blank_battery", value)
            onResetRequested: root.reset("blank_battery")
        }
        SettingsChoiceRow {
            width: parent.width
            objectName: "settings-blank-ac"
            visible: root.snapshot.idle_available === true
            title: qsTranslate("SettingsPowerPage", "Blank the screen when plugged in")
            explanation: qsTranslate("SettingsPowerPage", "Turn off the screen after this much inactivity on external power.")
            options: root.delayOptions(root.snapshot.values.blank_ac)
            value: root.snapshot.values.blank_ac ?? 0
            defaultValue: (root.snapshot.defaults || {}).blank_ac ?? value
            busy: root.pending
            onValueRequested: value => root.set("blank_ac", value)
            onResetRequested: root.reset("blank_ac")
        }
        SettingsRow {
            width: parent.width
            title: qsTranslate("SettingsPowerPage", "Lid behavior")
            explanation: qsTranslate("SettingsPowerPage", "Lid behavior is managed by the system.")
            defaultValue: value
        }
    }
    SettingsCard {
        width: parent.width
        visible: root.lockOnly
        title: qsTranslate("SettingsPowerPage", "Automatic lock")
        SettingsChoiceRow {
            width: parent.width
            objectName: "settings-lock-delay"
            visible: root.snapshot.idle_available === true
            title: qsTranslate("SettingsPowerPage", "Lock after inactivity")
            explanation: qsTranslate("SettingsPowerPage", "Require your password after this much time without input.")
            options: root.delayOptions(root.snapshot.values.lock_delay)
            value: root.snapshot.values.lock_delay ?? 0
            defaultValue: (root.snapshot.defaults || {}).lock_delay ?? value
            busy: root.pending
            onValueRequested: value => root.set("lock_delay", value)
            onResetRequested: root.reset("lock_delay")
        }
    }
    SettingsCard {
        width: parent.width
        visible: !root.lockOnly && root.service.profiles.state === "ready" && root.service.profiles.profiles.length > 0
        title: qsTranslate("SettingsPowerPage", "Power mode")
        SettingsChoiceRow {
            width: parent.width
            objectName: "settings-power-profile"
            title: qsTranslate("SettingsPowerPage", "Power mode")
            explanation: qsTranslate("SettingsPowerPage", "Balance energy use and performance.")
            options: Array.from(root.service.profiles.profiles ?? []).map(profile => ({
                        value: profile,
                        label: ({
                                "power-saver": qsTranslate("SettingsPowerPage", "Power saver"),
                                balanced: qsTranslate("SettingsPowerPage", "Balanced"),
                                performance: qsTranslate("SettingsPowerPage", "Performance")
                            })[profile] || profile
                    }))
            value: root.service.profiles.current
            defaultValue: "balanced"
            busy: ["pending", "busy"].includes(root.service.actionState)
            onValueRequested: value => root.request("profile", value)
            onResetRequested: root.request("profile", "balanced")
        }
    }
    Repeater {
        model: root.lockOnly ? [] : root.snapshot.batteries
        delegate: SettingsCard {
            id: batteryCard
            required property var modelData
            width: root.width
            title: qsTranslate("SettingsPowerPage", "Battery")
            SettingsRow {
                width: parent.width
                title: qsTranslate("SettingsPowerPage", "Battery level")
                explanation: batteryCard.modelData.percent === null || batteryCard.modelData.percent === undefined ? qsTranslate("SettingsPowerPage", "Battery level is not reported by this hardware.") : (batteryCard.modelData.charging ? qsTranslate("SettingsPowerPage", "%1% remaining, charging.") : qsTranslate("SettingsPowerPage", "%1% remaining.")).arg(batteryCard.modelData.percent.toFixed(0))
                defaultValue: value
            }
            SettingsRow {
                width: parent.width
                objectName: "settings-battery-health"
                title: qsTranslate("SettingsPowerPage", "Battery health")
                explanation: batteryCard.modelData.health === null ? qsTranslate("SettingsPowerPage", "Battery health is not reported by this hardware.") : qsTranslate("SettingsPowerPage", "%1% of the original full-charge capacity.").arg(batteryCard.modelData.health.toFixed(0))
                defaultValue: value
            }
            SettingsChoiceRow {
                width: parent.width
                visible: batteryCard.modelData.charge_limit !== null
                objectName: "settings-charge-limit"
                title: qsTranslate("SettingsPowerPage", "Charge limit")
                explanation: qsTranslate("SettingsPowerPage", "Stop charging at this percentage. The limit is restored at startup.")
                options: root.chargeOptions(batteryCard.modelData.charge_limit)
                value: batteryCard.modelData.charge_limit
                defaultValue: 100
                busy: root.pending
                onValueRequested: value => root.send(["charge", batteryCard.modelData.name, String(value)])
                onResetRequested: root.send(["charge", batteryCard.modelData.name, "100"])
            }
        }
    }
}
