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
    readonly property var delays: [
        {
            value: 0,
            label: "Never"
        },
        {
            value: 60,
            label: "1 minute"
        },
        {
            value: 120,
            label: "2 minutes"
        },
        {
            value: 300,
            label: "5 minutes"
        },
        {
            value: 330,
            label: "5½ minutes"
        },
        {
            value: 600,
            label: "10 minutes"
        },
        {
            value: 900,
            label: "15 minutes"
        },
        {
            value: 1800,
            label: "30 minutes"
        },
        {
            value: 3600,
            label: "1 hour"
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

    function chargeOptions(current) {
        const values = [50, 60, 70, 80, 90, 100];
        const options = values.map(value => ({
                    value: value,
                    label: value + "%"
                }));
        if (current !== null && current !== undefined && !values.includes(current))
            options.push({
                value: current,
                label: current + "% (current)"
            });
        return options;
    }
    function send(args) {
        if (process.running)
            return;
        error = "";
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
                        root.error = "Power settings are unavailable. Try again when the system service is ready.";
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
                        root.error = result.message || "Could not apply the power setting.";
                } catch (_) {
                    root.error = "Could not read the power settings.";
                }
            }
        }
        onExited: (code, status) => {
            if (code !== 0 && !root.error)
                root.error = "Could not apply the power setting.";
        }
    }
    Timer {
        interval: 10000
        running: root.visible && !root.pending
        repeat: true
        onTriggered: {
            // Keep a failed change visible until the next deliberate action.
            if (!root.error)
                root.send(["status"]);
        }
    }
    Text {
        visible: text.length > 0
        width: parent.width
        text: root.error
        textFormat: Text.PlainText
        color: "#a01b45"
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 12
        wrapMode: Text.WordWrap
    }
    Text {
        objectName: "settings-power-action-status"
        visible: !root.lockOnly && !["idle", "confirmed", "applied", "ready"].includes(root.actionResult)
        width: parent.width
        text: root.awaitingAction ? "Working…" : "Could not confirm the power mode change."
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
        text: "No battery"
        textFormat: Text.PlainText
        color: SettingsTheme.dim
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 13
        wrapMode: Text.WordWrap
    }
    SettingsCard {
        width: parent.width
        visible: !root.lockOnly
        title: "Screen and lid"
        SettingsChoiceRow {
            width: parent.width
            objectName: "settings-blank-battery"
            visible: root.snapshot.idle_available === true
            title: "Blank the screen on battery"
            explanation: "Turn off the screen after this much inactivity on battery power."
            options: root.delays
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
            title: "Blank the screen when plugged in"
            explanation: "Turn off the screen after this much inactivity on external power."
            options: root.delays
            value: root.snapshot.values.blank_ac ?? 0
            defaultValue: (root.snapshot.defaults || {}).blank_ac ?? value
            busy: root.pending
            onValueRequested: value => root.set("blank_ac", value)
            onResetRequested: root.reset("blank_ac")
        }
        SettingsRow {
            width: parent.width
            title: "Lid behavior"
            explanation: "Lid behavior is managed by the system."
            defaultValue: value
        }
    }
    SettingsCard {
        width: parent.width
        visible: root.lockOnly
        title: "Automatic lock"
        SettingsChoiceRow {
            width: parent.width
            objectName: "settings-lock-delay"
            visible: root.snapshot.idle_available === true
            title: "Lock after inactivity"
            explanation: "Require your password after this much time without input."
            options: root.delays
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
        title: "Power mode"
        SettingsChoiceRow {
            width: parent.width
            objectName: "settings-power-profile"
            title: "Power mode"
            explanation: "Balance energy use and performance."
            options: Array.from(root.service.profiles.profiles ?? []).map(profile => ({
                        value: profile,
                        label: ({
                                "power-saver": "Power saver",
                                balanced: "Balanced",
                                performance: "Performance"
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
            title: "Battery"
            SettingsRow {
                width: parent.width
                title: "Battery level"
                explanation: batteryCard.modelData.percent === null || batteryCard.modelData.percent === undefined ? "Battery level is not reported by this hardware." : batteryCard.modelData.percent + "% remaining" + (batteryCard.modelData.charging ? ", charging." : ".")
                defaultValue: value
            }
            SettingsRow {
                width: parent.width
                objectName: "settings-battery-health"
                title: "Battery health"
                explanation: batteryCard.modelData.health === null ? "Battery health is not reported by this hardware." : batteryCard.modelData.health + "% of the original full-charge capacity."
                defaultValue: value
            }
            SettingsChoiceRow {
                width: parent.width
                visible: batteryCard.modelData.charge_limit !== null
                objectName: "settings-charge-limit"
                title: "Charge limit"
                explanation: "Stop charging at this percentage. The limit is restored at startup."
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
