// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell.Io
import "settings"
import "SecureBoot.js" as Logic

// Shown only on machines with the Secure Boot machine key. The status comes from root's copy
// (emaki-secureboot status); a new confirmation goes through pkexec and its one-time password
// lives only in this page's memory. Startup files an update could not sign are signed again
// through pkexec as well.
SettingsCard {
    id: root
    property var status: ({})
    property string password: ""
    property string message: ""
    property string reply: ""
    property string enrollReply: ""
    property string signMessage: ""
    readonly property bool busy: statusProcess.running || enrollProcess.running || signProcess.running
    objectName: "settings-privacy-secure-boot"
    title: Logic.title
    visible: Logic.shown(status)
    function refresh(): void {
        if (statusProcess.running)
            return;
        reply = "";
        statusProcess.running = true;
    }
    function enroll(): void {
        if (busy || !Logic.offer(status))
            return;
        message = "";
        password = "";
        enrollReply = "";
        enrollProcess.completed = false;
        enrollProcess.running = true;
    }
    function sign(): void {
        if (busy || Logic.unsigned(status).length === 0)
            return;
        signMessage = "";
        signProcess.completed = false;
        signProcess.running = true;
    }
    Component.onCompleted: refresh()
    Timer {
        interval: 5000
        repeat: true
        // The card itself is hidden until the key status is known.
        running: (!root.parent || root.parent.visible) && !root.busy && (!Logic.valid(root.status) || Logic.unpublished(root.status))
        onTriggered: root.refresh()
    }
    Process {
        id: statusProcess
        command: ["emaki-secureboot", "status", "--json"]
        stdout: StdioCollector {
            onStreamFinished: root.reply = text
        }
        onExited: code => {
            try {
                const result = JSON.parse(root.reply);
                root.status = code === 0 && Logic.valid(result) ? result : ({});
            } catch (_) {
                root.status = ({});
            }
        }
    }
    Process {
        id: enrollProcess
        property bool completed: false
        command: ["pkexec", Platform.binDir + "/emaki-secureboot", "enroll", "--json"]
        stdout: StdioCollector {
            onStreamFinished: root.enrollReply = text
        }
        onRunningChanged: {
            if (!running)
                Qt.callLater(function () {
                    if (!enrollProcess.completed)
                        root.message = Logic.failed;
                });
        }
        onExited: code => {
            completed = true;
            let answer = null;
            try {
                answer = JSON.parse(root.enrollReply);
            } catch (_) {}
            root.enrollReply = "";
            if (code === 0 && Logic.validPassword(answer))
                root.password = answer.password;
            else
                root.message = code === 126 ? Logic.cancelled : Logic.failed;
            root.refresh();
        }
    }
    Process {
        id: signProcess
        property bool completed: false
        command: ["pkexec", Platform.binDir + "/emaki-secureboot", "sign"]
        onRunningChanged: {
            if (!running)
                Qt.callLater(function () {
                    if (!signProcess.completed)
                        root.signMessage = Logic.signFailed;
                });
        }
        onExited: code => {
            completed = true;
            if (code !== 0)
                root.signMessage = code === 126 ? Logic.cancelled : Logic.signFailed;
            root.refresh();
        }
    }
    SettingsRow {
        objectName: "settings-secure-boot-state"
        width: parent.width
        controlWidth: 0
        title: Logic.statusTitle
        explanation: Logic.stateText(root.status)
    }
    SettingsRow {
        objectName: "settings-secure-boot-sign"
        width: parent.width
        visible: Logic.unsigned(root.status).length > 0
        title: Logic.signTitle
        explanation: Logic.signExplanation
        errorText: root.signMessage
        busy: root.busy
        controlWidth: 150
        control: Component {
            SettingsButton {
                objectName: "settings-secure-boot-sign-button"
                text: Logic.signButton
                onClicked: root.sign()
            }
        }
    }
    SettingsRow {
        objectName: "settings-secure-boot-confirm"
        width: parent.width
        visible: Logic.offer(root.status) && root.password === ""
        title: Logic.confirmTitle
        explanation: Logic.confirmExplanation
        errorText: root.message
        busy: root.busy
        controlWidth: 150
        control: Component {
            SettingsButton {
                objectName: "settings-secure-boot-enroll"
                text: Logic.confirmButton
                onClicked: root.enroll()
            }
        }
    }
    Column {
        objectName: "settings-secure-boot-password"
        width: parent.width
        visible: root.password !== ""
        topPadding: 14
        bottomPadding: 16
        spacing: 6
        Text {
            width: parent.width
            textFormat: Text.PlainText
            text: Logic.passwordTitle
            color: SettingsTheme.ink
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 14
            font.weight: Font.Medium
        }
        Text {
            objectName: "settings-secure-boot-password-digits"
            width: parent.width
            textFormat: Text.PlainText
            // Digits only, ungrouped: MokManager takes exactly these keys.
            text: root.password
            color: SettingsTheme.ink
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 28
            font.weight: Font.DemiBold
            font.letterSpacing: 2
        }
        Text {
            width: parent.width
            textFormat: Text.PlainText
            text: Logic.steps + " " + Logic.afterSteps
            color: SettingsTheme.dim
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 13
            wrapMode: Text.WordWrap
        }
    }
}
