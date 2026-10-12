// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as C
import "ManagerLogic.js" as Logic

ColumnLayout {
    id: root
    required property var controller
    property var programs: []
    property var launchEntries: []
    property bool transactionBusy: false
    property string restoreId: ""
    property bool restoreChosen: false
    property var offeredRestorePoints: []
    readonly property int restoreIndex: offeredRestorePoints.findIndex(point => String(point.id) === restoreId)
    function refreshRestoreDates(): void {
        if (restorePicker.popup.visible) return;
        offeredRestorePoints = controller.restorePoints.slice();
        if (!restoreChosen || !offeredRestorePoints.some(point => String(point.id) === restoreId)) {
            restoreChosen = false;
            restoreId = offeredRestorePoints.length ? String(offeredRestorePoints[0].id) : "";
        }
    }
    Component.onCompleted: refreshRestoreDates()
    Connections {
        target: root.controller
        function onRestorePointsChanged() { root.refreshRestoreDates(); }
    }
    readonly property bool locked: controller.busy || transactionBusy
    readonly property var point: offeredRestorePoints[restoreIndex] || null
    readonly property bool running: controller.phase === "applying"
    readonly property bool finished: controller.phase === "finished" && controller.succeeded
    readonly property var groups: [
        {name: "Emaki", subtitle: "Made for Emaki"},
        {name: "Arch", subtitle: "The system and programs from Arch"},
        {name: "Flatpak", subtitle: "Programs from Flathub"},
        {name: "AUR", subtitle: "Updated in the terminal"}
    ]
    spacing: 14
    function group(row: var): string {
        if (row.aur) return "AUR";
        if (row.kind === "flatpak" || row.source === "Flatpak") return "Flatpak";
        return row.source === "Emaki" ? "Emaki" : "Arch";
    }
    function rows(name: string): var { const pending = controller.data.updates || [];
        const displayed = finished ? (controller.completedUpdates || []).concat(pending.filter(row => row.aur)) : pending;
        return displayed.filter(row => group(row) === name); }
    function completedSummary(): string {
        const count = (controller.completedUpdates || []).length;
        const stamp = new Date(controller.completedAt);
        const today = stamp.toDateString() === new Date().toDateString();
        return count + (count === 1 ? " update installed " : " updates installed ") + (today ? "today at " + Qt.formatDateTime(stamp, "HH:mm") : "on " + date(controller.completedAt)) + ".";
    }
    function catalogProgram(row: var): var {
        const kind = row.aur ? "aur" : row.kind === "flatpak" || row.source === "Flatpak" ? "flatpak" : "arch";
        return Logic.catalogProgram(programs, row.id || row.name, launchEntries, kind);
    }
    function plainName(row: var): string {
        const names = {linux: "Linux kernel", "linux-lts": "Linux kernel (long-term support)", systemd: "System services", "linux-firmware": "Device firmware", mesa: "Graphics drivers", emaki: "Emaki", "emaki-config": "Emaki desktop settings"};
        return row.displayName || row.appName || names[row.name] || catalogProgram(row).name || row.name;
    }
    function bytes(value: var): string {
        if (value === null || value === undefined) return "—";
        return value >= 1000000000 ? (value / 1000000000).toFixed(1) + " GB" : (value / 1000000).toFixed(1) + " MB";
    }
    function date(value: var): string {
        if (!value) return "";
        const parsed = new Date(value);
        return isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString(Qt.locale("en_GB"), "ddd d MMM yyyy, HH:mm");
    }
    function restoreTitle(selected: var): string { return selected ? "Restore the system as it was before the update of " + date(selected.timestamp || selected.date) : "Nothing to restore yet. Emaki saves the system before every update."; }
    component Card: Rectangle {
        Layout.fillWidth: true
        color: Qt.rgba(1, 250 / 255, 246 / 255, 0.42)
        border.color: Qt.rgba(36 / 255, 16 / 255, 24 / 255, 0.14)
        radius: 16
    }
    component Muted: ManagerLabel { color: Qt.rgba(36 / 255, 16 / 255, 24 / 255, 0.58); font.pixelSize: 13 }
    component Line: Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Qt.rgba(36 / 255, 16 / 255, 24 / 255, 0.14) }
    Card {
        visible: root.controller.restart && (root.controller.reminder || root.controller.restorePending)
        implicitHeight: restartRow.implicitHeight + 32
        color: "#29e2733f"
        border.color: "#8ce2733f"
        RowLayout {
            id: restartRow
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 18 }
            spacing: 16
            ManagerLabel { Layout.fillWidth: false; text: "↻"; font.pixelSize: 30; color: "#c2481f" }
            ColumnLayout {
                Layout.fillWidth: true
                ManagerLabel { text: root.controller.restorePending ? (root.controller.restoredPoint ? "The system from before " + root.date(root.controller.restoredPoint.timestamp || root.controller.restoredPoint.date) + " is ready" : "The restored system is ready") : "Restart to finish the update"; font.bold: true; font.pixelSize: 15 }
                Muted { text: root.controller.restorePending ? "Restart to use it. Your files in /home were not touched. The current system is kept, so this can be undone." : "The new kernel and system services start after a restart. Save your work first." }
            }
            ManagerButton { text: "Later"; visible: !root.controller.restorePending; enabled: !root.locked; onClicked: root.controller.dismissReminder() }
            ManagerButton { text: "Restart now"; primary: true; enabled: !root.locked; onClicked: restartDialog.open() }
        }
    }
    Card {
        implicitHeight: summary.implicitHeight + 36
        ColumnLayout {
            id: summary
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 18 }
            spacing: 12
            RowLayout {
                spacing: 18
                Rectangle {
                    implicitWidth: 52; implicitHeight: 52; radius: 15; color: root.finished ? "#a8b86a" : "#e2733f"
                    ManagerLabel { anchors.centerIn: parent; text: root.finished ? "✓" : "↻"; color: "#fffaf5"; font.pixelSize: 30 }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    ManagerLabel { text: root.running ? "Updating…" : root.controller.phase === "checking" ? "Checking for updates…" : root.finished ? "Updated" : (root.controller.data.updates || []).length + ((root.controller.data.updates || []).length === 1 ? " update" : " updates"); font.pixelSize: 18; font.bold: true }
                    Muted {
                        text: root.running ? "You can close this window. The update goes on, and Emaki tells you when it is done." : root.finished ? root.completedSummary() : root.bytes(root.controller.data.downloadSize) + " to download" + (root.controller.data.checkedAt ? " · checked " + root.date(root.controller.data.checkedAt) : "")
                    }
                }
                ManagerButton { text: "Check again"; visible: !root.running; enabled: !root.locked; onClicked: root.controller.check() }
                ManagerButton { text: "Update all"; primary: true; visible: !root.finished && !root.running; enabled: !root.locked && root.controller.managedCount > 0 && !root.controller.data.error && !root.controller.restorePending; onClicked: root.controller.apply() }
            }
            Rectangle {
                id: progressTrack
                visible: root.running
                Layout.fillWidth: true
                implicitHeight: 8
                radius: 4
                color: "#24241018"
                clip: true
                Rectangle {
                    width: progressTrack.width * 0.35
                    height: 8
                    radius: 4
                    color: "#e2733f"
                    NumberAnimation on x { from: -progressTrack.width * 0.35; to: progressTrack.width; duration: 1300; loops: Animation.Infinite; running: root.running }
                }
            }
            Muted { visible: root.running; text: root.controller.output.trim().split("\n").slice(-1)[0] || "Updating…" }
            Rectangle {
                visible: root.running && root.controller.output.length > 0
                Layout.fillWidth: true
                implicitHeight: log.implicitHeight + 20
                color: "#0b241018"; radius: 10; border.color: "#24241018"
                Muted { id: log; anchors { left: parent.left; right: parent.right; top: parent.top; margins: 10 } font.family: "Iosevka Nerd Font Mono"; font.pixelSize: 12; text: root.controller.output.trim().split("\n").slice(-8).join("\n") }
            }
            Repeater {
                model: root.running || root.finished ? [] : root.controller.data.warnings || []
                delegate: ColumnLayout {
                    id: notice
                    required property string modelData
                    Layout.fillWidth: true
                    spacing: 10
                    Line {}
                    ManagerLabel { text: notice.modelData; font.pixelSize: 13 }
                }
            }
            ManagerLabel { visible: root.controller.message.length > 0 && !root.finished; text: root.controller.message; color: root.controller.data.error ? "#a01b45" : "#241018" }
            Muted { visible: !!root.controller.data.errorDetails; text: root.controller.data.errorDetails || "" }
            Muted { visible: !!root.controller.countError; text: root.controller.countError || "" }
            Muted { visible: root.controller.signOut; text: "Save your work, sign out and sign in again after updating." }
        }
    }
    Repeater {
        model: root.controller.data.news || []
        delegate: ManagerButton {
            required property var modelData
            Layout.fillWidth: true
            text: modelData.title
            onClicked: if (/^https:\/\/archlinux\.org\/news\//.test(modelData.link)) Qt.openUrlExternally(modelData.link)
        }
    }
    Repeater {
        model: root.groups
        delegate: Card {
            id: groupCard
            required property var modelData
            readonly property var updates: root.rows(modelData.name)
            visible: updates.length > 0
            opacity: modelData.name === "AUR" ? 0.78 : 1
            implicitHeight: groupContents.implicitHeight
            ColumnLayout {
                id: groupContents
                width: parent.width
                spacing: 0
                RowLayout {
                    Layout.fillWidth: true
                    Layout.margins: 16
                    ManagerLabel { Layout.fillWidth: false; text: groupCard.modelData.name; font.bold: true }
                    Muted { text: String(groupCard.updates.length) }
                    Muted { Layout.fillWidth: false; text: groupCard.modelData.subtitle }
                }
                Repeater {
                    model: groupCard.updates
                    delegate: ColumnLayout {
                        id: updateRow
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: 0
                        Line {}
                        RowLayout {
                            Layout.fillWidth: true
                            Layout.leftMargin: 16; Layout.rightMargin: 16; Layout.topMargin: 10; Layout.bottomMargin: 10
                            spacing: 12
                            ProgramIcon { program: root.catalogProgram(updateRow.modelData); Layout.preferredWidth: 22; Layout.preferredHeight: 22 }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                ManagerLabel { Layout.fillWidth: false; text: root.plainName(updateRow.modelData); font.pixelSize: 13; font.bold: true }
                                Muted { text: root.plainName(updateRow.modelData) !== updateRow.modelData.name ? updateRow.modelData.name : ""; font.pixelSize: 12; color: "#52241018"; elide: Text.ElideRight; wrapMode: Text.NoWrap }
                            }
                            Muted { Layout.fillWidth: false; visible: updateRow.modelData.restart === true; text: "Restart after"; color: "#c2481f"; font.pixelSize: 11 }
                            Muted { Layout.fillWidth: false; text: (updateRow.modelData.old || "") + " → " + (updateRow.modelData.new || ""); font.family: "Iosevka Nerd Font Mono"; font.pixelSize: 12 }
                            Muted { Layout.fillWidth: false; Layout.preferredWidth: 70; horizontalAlignment: Text.AlignRight; text: updateRow.modelData.aur ? "" : root.finished ? "✓" : root.bytes(updateRow.modelData.downloadSize); font.pixelSize: 12 }
                        }
                    }
                }
                Muted { visible: groupCard.modelData.name === "AUR"; Layout.margins: 16; text: "Emaki does not build these. Update them in a terminal with your AUR tool." }
            }
        }
    }
    Muted { text: "IF SOMETHING BROKE"; font.pixelSize: 12; font.bold: true; font.letterSpacing: 0.8; Layout.topMargin: 12 }
    Muted { visible: !!root.controller.restoreError; text: root.controller.restoreError || "" }
    Card {
        visible: !root.controller.restoreError || !!root.point
        implicitHeight: restoreRow.implicitHeight + 32
        RowLayout {
            id: restoreRow
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16 }
            spacing: 16
            ManagerLabel { Layout.fillWidth: false; text: "↶"; font.pixelSize: 30 }
            ColumnLayout {
                Layout.fillWidth: true
                ManagerLabel { text: root.restoreTitle(root.point); font.bold: true }
                Muted { text: root.point ? "Your files in /home stay as they are." : ""; visible: !!root.point }
                Muted { text: root.point ? root.point.notice || "" : ""; visible: text.length > 0 }
            }
            C.ComboBox {
                id: restorePicker
                implicitWidth: 108
                implicitHeight: 34
                font.family: "Noto Sans"
                font.pixelSize: 13
                leftPadding: 12
                rightPadding: 28
                opacity: enabled ? 1 : 0.45
                contentItem: Text {
                    text: restorePicker.displayText
                    font: restorePicker.font
                    color: "#241018"
                    verticalAlignment: Text.AlignVCenter
                }
                indicator: Text {
                    x: restorePicker.width - width - 10
                    anchors.verticalCenter: parent.verticalCenter
                    text: "⌄"; color: "#866c70"
                }
                background: Rectangle {
                    radius: 11; color: "#fff9f5"
                    border.width: restorePicker.activeFocus ? 2 : 1
                    border.color: restorePicker.activeFocus ? "#c2481f" : "#24241018"
                }
                delegate: C.ItemDelegate {
                    required property int index
                    required property string modelData
                    width: restorePicker.popup.width - 12
                    padding: 10
                    highlighted: restorePicker.highlightedIndex === index
                    contentItem: Text {
                        text: modelData; font: restorePicker.font; color: "#241018"
                        elide: Text.ElideRight
                    }
                    background: Rectangle { radius: 8; color: parent.highlighted ? "#1ce2733f" : "transparent" }
                }
                popup: C.Popup {
                    onClosed: root.refreshRestoreDates()
                    y: restorePicker.height + 4
                    width: 250
                    padding: 6
                    implicitHeight: Math.min(contentItem.implicitHeight + 12, 250)
                    background: Rectangle { radius: 12; color: "#fff9f5"; border.color: "#24241018" }
                    contentItem: ListView {
                        clip: true
                        implicitHeight: contentHeight
                        model: restorePicker.popup.visible ? restorePicker.delegateModel : null
                        currentIndex: restorePicker.highlightedIndex
                        C.ScrollIndicator.vertical: C.ScrollIndicator {}
                    }
                }
                visible: root.offeredRestorePoints.length > 1
                model: root.offeredRestorePoints.map(point => root.date(point.timestamp || point.date))
                currentIndex: root.restoreIndex
                displayText: root.offeredRestorePoints.length + (root.offeredRestorePoints.length === 1 ? " date" : " dates")
                enabled: !root.locked && !root.controller.restorePending
                onActivated: index => {
                    if (root.offeredRestorePoints[index]) {
                        root.restoreChosen = true;
                        root.restoreId = String(root.offeredRestorePoints[index].id);
                    }
                }
            }
            ManagerButton {
                text: "Restore…"
                enabled: !!root.point && !root.locked && !root.controller.restorePending
                onClicked: { root.controller.selectRestore(root.point); restoreDialog.open(); }
            }
        }
    }
    C.Dialog {
        id: restartDialog
        parent: C.Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(470, parent ? parent.width - 32 : 470)
        modal: true
        padding: 24
        closePolicy: root.locked ? C.Popup.NoAutoClose : C.Popup.CloseOnEscape
        background: Rectangle { radius: 18; color: "#fff9f5"; border.width: 2; border.color: "#9ee2733f" }
        contentItem: ColumnLayout {
            spacing: 14
            ManagerLabel { text: "Restart now?"; font.pixelSize: 19; font.bold: true }
            ManagerLabel { text: "The computer needs a restart. Save your work first." }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                ManagerButton { text: "Cancel"; onClicked: restartDialog.close() }
                ManagerButton { text: "Restart now"; primary: true; enabled: !root.locked && root.controller.restart; onClicked: { restartDialog.close(); root.controller.reboot(); } }
            }
        }
    }
    C.Dialog {
        id: restoreDialog
        parent: C.Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(470, parent ? parent.width - 32 : 470)
        modal: true
        padding: 24
        closePolicy: root.locked ? C.Popup.NoAutoClose : C.Popup.CloseOnEscape
        onRejected: root.controller.cancelRestore()
        background: Rectangle { radius: 18; color: "#fff9f5"; border.width: 2; border.color: "#9ee2733f" }
        contentItem: ColumnLayout {
            spacing: 14
            ManagerLabel { text: root.restoreTitle(root.controller.selectedRestore) + "?"; font.pixelSize: 19; font.bold: true }
            ManagerLabel { text: "Your files in /home stay as they are." }
            ManagerLabel { text: "The current system is kept, so you can undo this." }
            ManagerLabel { text: "The computer needs a restart. Save your work first." }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                ManagerButton { text: "Cancel"; onClicked: { root.controller.cancelRestore(); restoreDialog.close(); } }
                ManagerButton { text: "Restore"; primary: true; enabled: !root.locked && !!root.controller.selectedRestore; onClicked: { root.controller.restore(); restoreDialog.close(); } }
            }
        }
    }
}
