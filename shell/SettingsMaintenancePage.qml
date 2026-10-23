// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
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
    // Reset Emaki settings: the controller's SettingsReset (it keeps Undo after the page closes).
    property var resetter: SettingsBridge.controller?.settingsReset ?? null
    property bool resetReview: false
    // A reset started on this page: its outcome shows here. Undo shows whenever the controller
    // holds an undoable reset, also on a page opened later (after a failed Undo, for example).
    property bool resetHere: false
    // What the reset returns to defaults, and what it never touches (docs: Reset Emaki settings).
    readonly property var resetItems: [qsTranslate("SettingsMaintenancePage", "Theme, accent, wallpaper choice and window gaps"), qsTranslate("SettingsMaintenancePage", "Top bar, dock and when they hide"), qsTranslate("SettingsMaintenancePage", "Apps pinned to the dock"), qsTranslate("SettingsMaintenancePage", "Mouse, touchpad, key repeat and shortcuts"), qsTranslate("SettingsMaintenancePage", "Default apps"), qsTranslate("SettingsMaintenancePage", "Notification rules and Do Not Disturb"), qsTranslate("SettingsMaintenancePage", "System sounds and Night Light"), qsTranslate("SettingsMaintenancePage", "Screen blanking and lock delay")]
    readonly property var keptItems: [qsTranslate("SettingsMaintenancePage", "Your files, notes, pictures and wallpapers"), qsTranslate("SettingsMaintenancePage", "Settings inside your apps"), qsTranslate("SettingsMaintenancePage", "Your own niri configuration"), qsTranslate("SettingsMaintenancePage", "Displays, keyboard layouts, language and time zone"), qsTranslate("SettingsMaintenancePage", "Clipboard and notification history")]
    function reviewReset(): void {
        if (!resetter || resetter.busy)
            return;
        resetReview = true;
        Qt.callLater(() => {
            const cancel = root.findNamed("settings-reset-cancel");
            if (cancel)
                cancel.forceActiveFocus(Qt.TabFocusReason);
        });
    }
    function findNamed(name: string, from: var): var {
        const stack = [from || resetReviewCard];
        while (stack.length) {
            const item = stack.pop();
            if (item.objectName === name)
                return item;
            for (const child of item.children || [])
                stack.push(child);
        }
        return null;
    }
    function confirmReset(): void {
        if (!resetter || resetter.busy)
            return;
        resetReview = false;
        resetHere = resetter.start();
    }
    function cancelReset(): void {
        resetReview = false;
        focusResetButton();
    }
    // The row's button (Reset… or Undo) takes the focus back from the closed review.
    function focusResetButton(): void {
        Qt.callLater(() => {
            const row = root.findNamed("settings-reset-emaki", resetCard);
            if (row?.controlItem?.enabled)
                row.controlItem.forceActiveFocus(Qt.TabFocusReason);
        });
    }
    Connections {
        target: root.resetter
        function onBusyChanged(): void {
            if (!root.resetter.busy && root.resetHere)
                root.focusResetButton();
        }
    }
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
        resetReview = false;
        resetHere = false;
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
                        root.message = qsTranslate("SettingsMaintenancePage", "Update channel settings could not be opened. Try again.");
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
                    throw new Error(result.message || qsTranslate("SettingsMaintenancePage", "The update channel could not be changed."));
                root.channel = result;
                root.message = result.message || "";
            } catch (error) {
                root.message = code === 126 ? qsTranslate("SettingsMaintenancePage", "Authentication was cancelled.") : qsTranslate("SettingsMaintenancePage", "The update channel could not be confirmed. Reopen Settings to check.");
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
                        root.message = qsTranslate("SettingsMaintenancePage", "The password window could not be opened.");
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
                root.message = qsTranslate("SettingsMaintenancePage", "The password change could not be confirmed. Check the password window.");
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
        title: qsTranslate("SettingsMaintenancePage", "Update channel")
        SettingsChoiceRow {
            objectName: "settings-update-channel"
            width: parent.width
            title: qsTranslate("SettingsMaintenancePage", "Channel")
            explanation: qsTranslate("SettingsMaintenancePage", "Choose which releases your next update uses.")
            value: root.channel.channel || ""
            defaultValue: "stable"
            busy: root.pending
            options: [
                {
                    label: qsTranslate("SettingsMaintenancePage", "Stable"),
                    value: "stable"
                },
                {
                    label: qsTranslate("SettingsMaintenancePage", "Testing"),
                    value: "testing"
                }
            ]
            onValueRequested: value => root.changeChannel(value)
            onResetRequested: root.changeChannel("stable")
        }
    }
    SettingsCard {
        objectName: "settings-snapshot-recovery"
        width: parent.width
        visible: root.page === "updates" && !!root.service.recovery?.snapshot
        title: qsTranslate("SettingsMaintenancePage", "Recovery snapshot")
        SettingsRow {
            width: parent.width
            title: qsTranslate("SettingsMaintenancePage", "Review snapshot")
            explanation: qsTranslate("SettingsMaintenancePage", "Return to the recovery prompt to keep this state.")
            controlWidth: 150
            control: Component {
                SettingsButton {
                    objectName: "settings-review-snapshot"
                    text: qsTranslate("SettingsMaintenancePage", "Review snapshot")
                    onClicked: root.service.recovery.opened = true
                }
            }
        }
    }
    SettingsCard {
        id: resetCard
        objectName: "settings-reset"
        width: parent.width
        visible: root.page === "updates" && root.resetter !== null
        title: qsTranslate("SettingsMaintenancePage", "Reset")
        SettingsRow {
            objectName: "settings-reset-emaki"
            width: parent.width
            title: qsTranslate("SettingsMaintenancePage", "Reset Emaki settings")
            explanation: ((root.resetHere || root.resetter?.undoable) && root.resetter?.message) || qsTranslate("SettingsMaintenancePage", "Return Emaki's own settings to their defaults. Your files and app settings are not touched.")
            busy: root.resetter?.busy ?? false
            controlWidth: 150
            control: Component {
                SettingsButton {
                    objectName: root.resetter?.undoable ? "settings-reset-undo" : "settings-reset-start"
                    text: root.resetter?.undoable ? qsTranslate("SettingsMaintenancePage", "Undo") : qsTranslate("SettingsMaintenancePage", "Reset…")
                    enabled: !(root.resetter?.busy ?? true) && !root.resetReview
                    onClicked: {
                        if (root.resetter.undoable)
                            root.resetter.undo("");
                        else
                            root.reviewReset();
                    }
                }
            }
        }
    }
    SettingsCard {
        id: resetReviewCard
        objectName: "settings-reset-review"
        width: parent.width
        visible: root.page === "updates" && root.resetReview
        title: qsTranslate("SettingsMaintenancePage", "Review reset")
        Text {
            width: parent.width
            text: qsTranslate("SettingsMaintenancePage", "These return to their defaults:")
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            color: SettingsTheme.ink
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 13
            font.weight: Font.Medium
        }
        Repeater {
            model: root.resetItems
            delegate: Text {
                objectName: "settings-reset-item"
                required property string modelData
                width: parent.width
                text: "• " + modelData
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                color: SettingsTheme.dim
                font.family: SettingsTheme.fontFamily
                font.pixelSize: 13
                topPadding: 4
            }
        }
        Text {
            width: parent.width
            text: qsTranslate("SettingsMaintenancePage", "These are not touched:")
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            color: SettingsTheme.ink
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 13
            font.weight: Font.Medium
            topPadding: 12
        }
        Repeater {
            model: root.keptItems
            delegate: Text {
                objectName: "settings-reset-kept"
                required property string modelData
                width: parent.width
                text: "• " + modelData
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                color: SettingsTheme.dim
                font.family: SettingsTheme.fontFamily
                font.pixelSize: 13
                topPadding: 4
            }
        }
        Text {
            width: parent.width
            text: qsTranslate("SettingsMaintenancePage", "A copy of your current Emaki settings is kept, so you can undo the reset.")
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            color: SettingsTheme.dim
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 13
            topPadding: 12
        }
        footer: Component {
            Row {
                spacing: 8
                SettingsButton {
                    objectName: "settings-reset-cancel"
                    text: qsTranslate("SettingsMaintenancePage", "Cancel")
                    onClicked: root.cancelReset()
                }
                SettingsButton {
                    objectName: "settings-reset-confirm"
                    text: qsTranslate("SettingsMaintenancePage", "Reset settings")
                    enabled: !(root.resetter?.busy ?? true)
                    onClicked: root.confirmReset()
                }
            }
        }
    }
    SettingsCard {
        width: parent.width
        visible: root.page === "lock"
        title: qsTranslate("SettingsMaintenancePage", "Password")
        SettingsRow {
            objectName: "settings-password"
            width: parent.width
            title: qsTranslate("SettingsMaintenancePage", "Change password")
            explanation: qsTranslate("SettingsMaintenancePage", "Open the system password prompt in your terminal. Your login wallet may still use your previous password; update its password separately.")
            busy: passwordProcess.running
            controlWidth: 150
            control: Component {
                SettingsButton {
                    text: qsTranslate("SettingsMaintenancePage", "Change…")
                    Accessible.name: qsTranslate("SettingsMaintenancePage", "Change password")
                    ToolTip.text: qsTranslate("SettingsMaintenancePage", "Change password")
                    ToolTip.visible: hovered || activeFocus
                    onClicked: {
                        root.message = qsTranslate("SettingsMaintenancePage", "Enter your current and new password in the terminal.");
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
