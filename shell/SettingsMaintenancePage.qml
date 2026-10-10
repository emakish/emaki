// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell.Io
import "settings"

Column {
    id: root
    required property string page
    required property SystemService service
    property var channel: ({})
    property string message: ""
    property bool pending: false
    property string reply: ""
    spacing: 16
    function readChannel(): void {
        if (pending)
            return;
        channelProcess.command = ["emaki-update-channel", "status", "--json"];
        reply = "";
        pending = true;
        channelProcess.completed = false;
        channelProcess.running = true;
    }
    function changeChannel(value: string): void {
        if (pending || !["stable", "testing"].includes(value))
            return;
        message = "";
        reply = "";
        pending = true;
        channelProcess.completed = false;
        channelProcess.command = ["pkexec", Platform.binDir + "/emaki-update-channel", "set", value, "--json"];
        channelProcess.running = true;
    }
    Component.onCompleted: {
        if (page === "updates")
            readChannel();
    }
    onPageChanged: {
        message = "";
        if (page === "updates")
            readChannel();
    }
    Process {
        id: channelProcess
        property bool completed: false
        onRunningChanged: {
            if (!running)
                Qt.callLater(function () {
                    if (!channelProcess.completed) {
                        root.pending = false;
                        root.message = "The update channel helper could not be started.";
                    }
                });
        }
        stdout: StdioCollector {
            onStreamFinished: root.reply = text
        }
        onExited: code => {
            completed = true;
            root.pending = false;
            try {
                const result = JSON.parse(root.reply);
                if (code !== 0 || result.schema_version !== 1 || !["read", "applied", "unchanged"].includes(result.status))
                    throw new Error(result.message || "The update channel could not be changed.");
                root.channel = result;
                root.message = result.message || "";
            } catch (error) {
                root.message = code === 126 ? "Authentication was cancelled." : "The update channel could not be confirmed. Reopen Settings to check.";
                try {
                    const result = JSON.parse(root.reply);
                    if (result.message)
                        root.message = result.message;
                } catch (_) {}
            }
        }
    }
    Process {
        id: passwordProcess
        property bool completed: false
        onRunningChanged: {
            if (!running)
                Qt.callLater(function () {
                    if (!passwordProcess.completed)
                        root.message = "The password window could not be opened.";
                });
        }
        property string result: ""
        command: ["emaki-terminal", "--change-password", "--json"]
        stdout: StdioCollector {
            onStreamFinished: passwordProcess.result = text
        }
        onExited: code => {
            completed = true;
            try {
                const reply = JSON.parse(result);
                if (code !== 0 || reply.schema_version !== 1 || !["changed", "unchanged", "unconfirmed"].includes(reply.status))
                    throw new Error();
                root.message = reply.message;
            } catch (_) {
                root.message = "The password change could not be confirmed. Check the password window.";
            }
        }
    }
    Text {
        width: parent.width
        visible: text !== ""
        text: root.message
        textFormat: Text.PlainText
        color: SettingsTheme.dim
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 13
        wrapMode: Text.WordWrap
    }
    SettingsCard {
        width: parent.width
        visible: root.page === "updates" && root.channel.editable === true
        title: "Update channel"
        SettingsChoiceRow {
            objectName: "settings-update-channel"
            width: parent.width
            title: "Channel"
            explanation: "Choose which releases your next update uses."
            value: root.channel.channel || ""
            defaultValue: "stable"
            busy: root.pending
            options: [
                {
                    label: "Stable",
                    value: "stable"
                },
                {
                    label: "Testing",
                    value: "testing"
                }
            ]
            onValueRequested: value => root.changeChannel(value)
            onResetRequested: root.changeChannel("stable")
        }
    }
    SettingsCard {
        width: parent.width
        visible: root.page === "lock"
        title: "Password"
        SettingsRow {
            objectName: "settings-password"
            width: parent.width
            title: "Change password"
            explanation: "Open the system password prompt in your terminal. Your login wallet may still use your previous password; update its password separately."
            busy: passwordProcess.running
            controlWidth: 150
            control: Component {
                SettingsButton {
                    text: "Change password"
                    onClicked: {
                        root.message = "Enter your current and new password in the terminal.";
                        passwordProcess.completed = false;
                        passwordProcess.result = "";
                        passwordProcess.running = true;
                    }
                }
            }
        }
    }
    Loader {
        width: parent.width
        active: root.page === "lock"
        sourceComponent: SettingsPowerPage {
            service: root.service
            page: "lock"
        }
    }
}
