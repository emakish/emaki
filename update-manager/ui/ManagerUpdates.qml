// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import "file:@EMAKI_DATADIR@/shell" as Shell
import QtQuick.Layouts
import QtQuick.Controls as C
import Quickshell
import "ManagerLogic.js" as Logic

ColumnLayout {
    id: root
    required property var controller
    property var programs: []
    // Built once per catalog; every update row's name and icon look it up.
    readonly property var catalog: Logic.catalogIndex(programs)
    property var launchEntries: []
    property bool transactionBusy: false
    property bool aurKnown: false
    property bool aurEnabled: false
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
    property bool showLog: false
    readonly property var progress: controller.progress ?? Logic.updateProgress(controller.output)
    // A pulse until the restore point; downloading has no count to show.
    readonly property real fraction: progress.step < 2 ? -1
        : ((progress.step - 1) + (progress.step === 3 && progress.total ? progress.done / progress.total : progress.step === 4 ? 0.5 : 0)) / progress.steps
    function stepText(): string {
        const label = progress.step === 3 && progress.total ? qsTranslate("ManagerUpdates", "Installing %1 of %2").arg(progress.done).arg(progress.total) : progress.label;
        return progress.step < 1 ? qsTranslate("ManagerUpdates", "Getting ready…") : qsTranslate("ManagerUpdates", "Step %1 of %2 · %3").arg(progress.step).arg(progress.steps).arg(label);
    }
    readonly property var groups: [
        {name: "Emaki", subtitle: qsTranslate("ManagerUpdates", "Made for Emaki")},
        {name: "Arch", subtitle: qsTranslate("ManagerUpdates", "The system and programs from Arch")},
        {name: "Flatpak", subtitle: qsTranslate("ManagerUpdates", "Programs from Flathub")},
        {name: "AUR", subtitle: qsTranslate("ManagerUpdates", "Not part of Update all")}
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
        return today ? Shell.Translation.englishPlural(qsTranslate("ManagerUpdates", "%n update(s) installed today at %1.", "", count), count).arg(Qt.formatDateTime(stamp, "HH:mm")) : Shell.Translation.englishPlural(qsTranslate("ManagerUpdates", "%n update(s) installed on %1.", "", count), count).arg(date(controller.completedAt));
    }
    function catalogProgram(row: var): var {
        const kind = row.aur ? "aur" : row.kind === "flatpak" || row.source === "Flatpak" ? "flatpak" : "arch";
        return Logic.catalogProgram(catalog, row.id || row.name, launchEntries, kind);
    }
    function plainName(row: var): string {
        const names = {linux: qsTranslate("ManagerUpdates", "Linux kernel"), "linux-lts": qsTranslate("ManagerUpdates", "Linux kernel (long-term support)"), systemd: qsTranslate("ManagerUpdates", "System services"), "linux-firmware": qsTranslate("ManagerUpdates", "Device firmware"), mesa: qsTranslate("ManagerUpdates", "Graphics drivers"), emaki: "Emaki", "emaki-config": qsTranslate("ManagerUpdates", "Emaki desktop settings")};
        return row.displayName || row.appName || names[row.name] || catalogProgram(row).name || row.name;
    }
    function bytes(value: var): string { return Logic.downloadText(value); }
    function date(value: var): string {
        if (!value) return "";
        const parsed = new Date(value);
        //: Qt date/time pattern for a restore point. Keep the time in 24-hour form.
        return isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString(Qt.locale().name === "C" || Qt.locale().name.startsWith("en_") ? Qt.locale("en_GB") : Qt.locale(), qsTranslate("ManagerUpdates", "ddd d MMM yyyy, HH:mm"));
    }
    function confirmRestore(selected: var): void {
        if (locked || controller.restorePending || !selected) return;
        controller.selectRestore(selected);
        restoreDialog.open();
    }
    function restoreTitle(selected: var): string {
        if (selected?.operation === "remove")
            return qsTranslate("ManagerUpdates", "Restore the system as it was before this removal (snapshot %1)").arg(selected.snapshot);
        if (selected?.operation === "install")
            return selected.timestamp || selected.date
                ? qsTranslate("ManagerUpdates", "Restore the system as it was before the install of %1").arg(date(selected.timestamp || selected.date))
                : qsTranslate("ManagerUpdates", "Restore the system as it was before this install (snapshot %1)").arg(selected.snapshot);
        return selected ? qsTranslate("ManagerUpdates", "Restore the system as it was before the update of %1").arg(date(selected.timestamp || selected.date)) : qsTranslate("ManagerUpdates", "Nothing to restore yet. Emaki saves the system before every update."); }
    component Card: Rectangle {
        Layout.fillWidth: true
        color: Qt.alpha(Shell.ShellPalette.managerBackground, 0.42)
        border.color: Qt.alpha(Shell.ShellPalette.managerInk, 0.14)
        radius: 16
    }
    // A narrow page (a niri third) puts each card's buttons under its text.
    readonly property bool narrow: width < 560
    // Under 300 px the text goes under the card's symbol instead of beside it.
    readonly property bool tight: width < 300
    // A card's buttons after its symbol and text: in the same row, or wrapping under them.
    component Buttons: Flow {
        id: buttons
        // A button wider than the whole row shortens its label.
        function fit(): void {
            for (const child of children) {
                child.width = Qt.binding(() => Math.min(child.implicitWidth, buttons.width));
                if (child.squeezed !== undefined) child.squeezed = Qt.binding(() => buttons.width > 0 && child.implicitWidth > buttons.width);
            }
        }
        Component.onCompleted: fit()
        onChildrenChanged: fit()
        readonly property real rowWidth: {
            let total = 0, count = 0;
            for (const child of children) if (child.visible) { total += child.implicitWidth; count++; }
            return total + Math.max(0, count - 1) * spacing;
        }
        Layout.row: root.tight ? 2 : root.narrow ? 1 : 0
        Layout.column: root.narrow ? 0 : 2
        Layout.columnSpan: root.narrow ? 2 : 1
        Layout.fillWidth: root.narrow
        Layout.preferredWidth: rowWidth
        spacing: 16
    }
    component Muted: ManagerLabel { color: Shell.ShellPalette.managerDim; font.pixelSize: 13 }
    component Line: Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Qt.alpha(Shell.ShellPalette.managerInk, 0.14) }
    Muted {
        objectName: "aur-updates-disabled"
        visible: root.aurKnown && !root.aurEnabled && (root.controller.data.foreignPackageCount || 0) > 0
        text: qsTranslate("ManagerUpdates", "AUR updates are not checked while the AUR is off.")
    }
    Card {
        visible: root.controller.restart && (root.controller.reminder || root.controller.restorePending)
        implicitHeight: restartRow.implicitHeight + 32
        color: Qt.alpha(Shell.ShellPalette.accent, 0.1607843137254902)
        border.color: Qt.alpha(Shell.ShellPalette.accent, 0.5490196078431373)
        GridLayout {
            id: restartRow
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 18 }
            columns: root.narrow ? 2 : 3
            columnSpacing: 16
            rowSpacing: 12
            ManagerLabel { Layout.fillWidth: false; text: "↻"; font.pixelSize: 30; color: Shell.ShellPalette.accentForeground; Layout.row: 0; Layout.column: 0 }
            ColumnLayout {
                Layout.row: root.tight ? 1 : 0; Layout.column: root.tight ? 0 : 1; Layout.columnSpan: root.tight ? 2 : 1
                Layout.fillWidth: true
                ManagerLabel { text: root.controller.restorePending ? (root.controller.restoredPoint ? qsTranslate("ManagerUpdates", "The system from before %1 is ready").arg(root.date(root.controller.restoredPoint.timestamp || root.controller.restoredPoint.date)) : qsTranslate("ManagerUpdates", "The restored system is ready")) : qsTranslate("ManagerUpdates", "Restart to finish the update"); font.bold: true; font.pixelSize: 15 }
                Muted { text: root.controller.restorePending ? qsTranslate("ManagerUpdates", "Restart to use it. Your files in /home were not touched. The current system is kept, so this can be undone.") : qsTranslate("ManagerUpdates", "The new kernel and system services start after a restart. Save your work first.") }
            }
            Buttons {
                ManagerButton { text: qsTranslate("ManagerUpdates", "Later"); visible: !root.controller.restorePending; enabled: !root.locked; onClicked: root.controller.dismissReminder() }
                ManagerButton { text: qsTranslate("ManagerUpdates", "Restart now"); primary: true; enabled: !root.locked; onClicked: restartDialog.open() }
            }
        }
    }
    Card {
        id: releaseCard
        objectName: "whats-new"
        // The pending Emaki release, or the one the last Update all installed.
        readonly property var release: root.finished ? root.controller.completedRelease : root.controller.data.release || null
        readonly property bool linkSafe: /^https:\/\/github\.com\/emakish\/emaki\/releases(\/tag\/v[0-9.]+)?$/.test(release?.url || "")
        visible: !!release && !!release.version && !root.running
        implicitHeight: releaseColumn.implicitHeight + 36
        ColumnLayout {
            id: releaseColumn
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 18 }
            spacing: 10
            GridLayout {
                columns: root.narrow ? 2 : 3
                columnSpacing: 16
                rowSpacing: 8
                // The Emaki mark in its own Hot colours, as in the bar (0.9.0: not the flat demo).
                Shell.Icon {
                    id: releaseIcon
                    kind: "logo"
                    visible: ready
                    width: 44; height: 44
                    Layout.preferredWidth: 44; Layout.preferredHeight: 44
                    // A hidden icon leaves its cell to the text.
                    Layout.row: visible ? 0 : 3; Layout.column: 0
                }
                ColumnLayout {
                    // Without the icon the text starts at the card's edge.
                    Layout.row: root.tight ? 1 : 0; Layout.column: releaseIcon.visible && !root.tight ? 1 : 0; Layout.columnSpan: releaseIcon.visible && !root.tight ? 1 : 2
                    Layout.fillWidth: true
                    spacing: 2
                    ManagerLabel { text: qsTranslate("ManagerUpdates", "What’s new in Emaki %1").arg(releaseCard.release?.version || ""); font.pixelSize: 17; font.bold: true }
                    Muted { text: root.finished ? qsTranslate("ManagerUpdates", "Emaki %1 is installed.").arg(releaseCard.release?.version) : qsTranslate("ManagerUpdates", "Emaki %1 is available. Update all installs it.").arg(releaseCard.release?.version) }
                }
                Buttons {
                    visible: releaseCard.linkSafe
                    ManagerButton {
                        text: releaseCard.release?.published ? qsTranslate("ManagerUpdates", "Read the release notes") : qsTranslate("ManagerUpdates", "See Emaki releases")
                        quiet: true
                        onClicked: Qt.openUrlExternally(releaseCard.release.url)
                    }
                }
            }
            Repeater {
                model: releaseCard.release?.items || []
                RowLayout {
                    required property string modelData
                    Layout.fillWidth: true
                    spacing: 10
                    ManagerLabel { text: "•"; Layout.fillWidth: false; Layout.alignment: Qt.AlignTop; color: Shell.ShellPalette.accentForeground }
                    ManagerLabel { text: parent.modelData; font.pixelSize: 13; lineHeight: 1.3 }
                }
            }
            Muted { visible: !(releaseCard.release?.items || []).length; text: qsTranslate("ManagerUpdates", "The notes for this release are not published yet.") }
        }
    }
    Card {
        implicitHeight: summary.implicitHeight + 36
        ColumnLayout {
            id: summary
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 18 }
            spacing: 12
            GridLayout {
                columns: root.narrow ? 2 : 3
                columnSpacing: 18
                rowSpacing: 12
                Rectangle {
                    implicitWidth: 52; implicitHeight: 52; radius: 15; color: root.finished ? Shell.ShellPalette.managerComplete : Shell.ShellPalette.accent
                    Layout.row: 0; Layout.column: 0
                    ManagerLabel { anchors.centerIn: parent; text: root.finished ? "✓" : "↻"; color: Shell.ShellPalette.managerIconInk; font.pixelSize: 30 }
                }
                ColumnLayout {
                    Layout.row: root.tight ? 1 : 0; Layout.column: root.tight ? 0 : 1; Layout.columnSpan: root.tight ? 2 : 1
                    Layout.fillWidth: true
                    ManagerLabel { objectName: "update-title"; text: root.running ? qsTranslate("ManagerUpdates", "Updating Emaki") : root.controller.phase === "checking" ? qsTranslate("ManagerUpdates", "Checking for updates…") : root.finished ? qsTranslate("ManagerUpdates", "Updated") : Shell.Translation.englishPlural(qsTranslate("ManagerUpdates", "%n update(s)", "", (root.controller.data.updates || []).length), (root.controller.data.updates || []).length); font.pixelSize: 18; font.bold: true }
                    Muted {
                        objectName: "update-step"
                        text: root.running ? root.stepText() : root.finished ? root.completedSummary() : (root.controller.data.checkedAt ? qsTranslate("ManagerUpdates", "%1 to download · checked %2").arg(root.bytes(root.controller.data.downloadSize)).arg(root.date(root.controller.data.checkedAt)) : qsTranslate("ManagerUpdates", "%1 to download").arg(root.bytes(root.controller.data.downloadSize)))
                    }
                }
                Buttons {
                    visible: !root.running
                    spacing: 18
                    ManagerButton { text: qsTranslate("ManagerUpdates", "Check again"); visible: !root.running; enabled: !root.locked; onClicked: root.controller.check() }
                    ManagerButton { text: qsTranslate("ManagerUpdates", "Update all"); primary: true; visible: !root.finished && !root.running; enabled: !root.locked && root.controller.managedCount > 0 && !root.controller.data.error && !root.controller.restorePending; onClicked: root.controller.apply() }
                }
            }
            Rectangle {
                id: progressTrack
                objectName: "update-bar"
                visible: root.running
                Layout.fillWidth: true
                implicitHeight: 8
                radius: 4
                color: Qt.alpha(Shell.ShellPalette.managerInk, 0.1411764705882353)
                clip: true
                Rectangle {
                    // Determinate once the update has reached a step; a pulse while it gets ready.
                    width: root.fraction < 0 ? progressTrack.width * 0.35 : progressTrack.width * root.fraction
                    height: 8
                    radius: 4
                    color: Shell.ShellPalette.accent
                    objectName: "progress-fill"
                    x: root.fraction < 0 ? pulse.position : 0
                    QtObject { id: pulse; property real position: 0 }
                    NumberAnimation { target: pulse; property: "position"; from: -progressTrack.width * 0.35; to: progressTrack.width; duration: 1300; loops: Animation.Infinite; running: root.running && root.fraction < 0 }
                }
            }
            Muted { objectName: "update-restore-point"; visible: root.running && root.progress.restorePoint; text: qsTranslate("ManagerUpdates", "A restore point was made before the update. You can keep working.") }
            Muted { visible: root.running && !root.progress.restorePoint; text: qsTranslate("ManagerUpdates", "You can close this window. The update goes on, and Emaki tells you when it is done.") }
            ManagerButton {
                // The raw log stays behind Details.
                objectName: "update-details"
                visible: root.controller.output.trim().length > 0 && (root.running || root.controller.phase === "finished")
                text: root.showLog ? qsTranslate("ManagerUpdates", "Hide details") : qsTranslate("ManagerUpdates", "Details")
                onClicked: root.showLog = !root.showLog
            }
            Rectangle {
                objectName: "update-log"
                visible: root.showLog && root.controller.output.trim().length > 0 && (root.running || root.controller.phase === "finished")
                Layout.fillWidth: true
                implicitHeight: log.implicitHeight + 20
                color: Qt.alpha(Shell.ShellPalette.managerInk, 0.043137254901960784); radius: 10; border.color: Qt.alpha(Shell.ShellPalette.managerInk, 0.1411764705882353)
                Muted { id: log; anchors { left: parent.left; right: parent.right; top: parent.top; margins: 10 } font.family: "Iosevka Nerd Font Mono"; font.pixelSize: 12; text: root.controller.output.trim().split("\n").slice(-40).join("\n") }
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
            ManagerLabel { visible: root.controller.message.length > 0 && !root.finished; text: root.controller.message; color: root.controller.data.error ? Shell.ShellPalette.managerError : Shell.ShellPalette.managerInk }
            Muted { visible: !!root.controller.data.errorDetails; text: root.controller.data.errorDetails || "" }
            Muted { visible: !!root.controller.countError; text: root.controller.countError || "" }
            Muted { visible: root.controller.signOut; text: qsTranslate("ManagerUpdates", "Save your work, log out and log in again after updating.") }
        }
    }
    Repeater {
        model: root.controller.data.news || []
        delegate: ManagerButton {
            required property var modelData
            Layout.fillWidth: true
            // A long title in a narrow window is shortened.
            squeezed: !!parent && parent.width < implicitWidth
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
            implicitHeight: groupContents.implicitHeight
            ColumnLayout {
                id: groupContents
                width: parent.width
                spacing: 0
                RowLayout {
                    Layout.fillWidth: true
                    Layout.margins: 16
                    ManagerLabel { Layout.fillWidth: false; text: groupCard.modelData.name; font.bold: true }
                    Muted { text: String(groupCard.updates.length); Layout.minimumWidth: implicitWidth }
                    // Wraps in a narrow window.
                    Muted { Layout.fillWidth: root.narrow; Layout.maximumWidth: implicitWidth; text: groupCard.modelData.subtitle }
                }
                Repeater {
                    model: groupCard.updates
                    delegate: ColumnLayout {
                        id: updateRow
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: 0
                        Line {}
                        GridLayout {
                            // In a narrow window the versions and size go under the name.
                            Layout.fillWidth: true
                            Layout.leftMargin: 16; Layout.rightMargin: 16; Layout.topMargin: 10; Layout.bottomMargin: 10
                            columns: root.narrow ? 2 : 3
                            columnSpacing: 12
                            rowSpacing: 4
                            ProgramIcon { program: root.catalogProgram(updateRow.modelData); Layout.preferredWidth: 22; Layout.preferredHeight: 22; Layout.row: 0; Layout.column: 0 }
                            RowLayout {
                                Layout.row: 0; Layout.column: 1
                                Layout.fillWidth: true
                                spacing: 6
                                ManagerLabel { Layout.fillWidth: root.narrow; Layout.maximumWidth: implicitWidth; text: root.plainName(updateRow.modelData); font.pixelSize: 13; font.bold: true }
                                Muted { text: root.plainName(updateRow.modelData) !== updateRow.modelData.name ? updateRow.modelData.name : ""; font.pixelSize: 12; color: Shell.ShellPalette.managerDim; elide: Text.ElideRight; wrapMode: Text.NoWrap }
                            }
                            GridLayout {
                                Layout.row: root.narrow ? 1 : 0; Layout.column: root.narrow ? 1 : 2
                                Layout.fillWidth: root.narrow
                                columns: root.narrow ? 1 : 3
                                columnSpacing: 12
                                rowSpacing: 2
                                Muted { Layout.fillWidth: false; visible: updateRow.modelData.restart === true; text: qsTranslate("ManagerUpdates", "Restart after"); color: Shell.ShellPalette.accentForeground; font.pixelSize: 11 }
                                Muted { Layout.fillWidth: root.narrow; text: (updateRow.modelData.oldDisplay || updateRow.modelData.old || "") + " → " + (updateRow.modelData.new || ""); font.family: "Iosevka Nerd Font Mono"; font.pixelSize: 12 }
                                Muted { Layout.fillWidth: false; Layout.preferredWidth: root.narrow ? -1 : 70; horizontalAlignment: root.narrow ? Text.AlignLeft : Text.AlignRight; text: updateRow.modelData.aur ? "" : root.finished ? "✓" : root.bytes(updateRow.modelData.downloadSize); font.pixelSize: 12 }
                            }
                        }
                    }
                }
                Muted { visible: groupCard.modelData.name === "AUR"; Layout.margins: 16; text: qsTranslate("ManagerUpdates", "Update all does not build these. Build each update from its page in the AUR tab, or in a terminal with your AUR tool.") }
            }
        }
    }
    Muted { text: qsTranslate("ManagerUpdates", "IF SOMETHING BROKE"); font.pixelSize: 12; font.bold: true; font.letterSpacing: 0.8; Layout.topMargin: 12 }
    Muted { visible: !!root.controller.restoreError; text: root.controller.restoreError || "" }
    Card {
        visible: !root.controller.restoreError || !!root.point
        implicitHeight: restoreRow.implicitHeight + 32
        GridLayout {
            id: restoreRow
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16 }
            columns: root.narrow ? 2 : 3
            columnSpacing: 16
            rowSpacing: 12
            ManagerLabel { Layout.fillWidth: false; text: "↶"; font.pixelSize: 30; Layout.row: 0; Layout.column: 0 }
            ColumnLayout {
                Layout.row: root.tight ? 1 : 0; Layout.column: root.tight ? 0 : 1; Layout.columnSpan: root.tight ? 2 : 1
                Layout.fillWidth: true
                ManagerLabel { text: root.restoreTitle(root.point); font.bold: true }
                Muted { text: root.point ? qsTranslate("ManagerUpdates", "Your files in /home stay as they are.") : ""; visible: !!root.point }
                Muted { text: root.point ? root.point.notice || "" : ""; visible: text.length > 0 }
            }
            Buttons {
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
                        color: Shell.ShellPalette.managerInk
                        verticalAlignment: Text.AlignVCenter
                    }
                    indicator: Text {
                        x: restorePicker.width - width - 10
                        anchors.verticalCenter: parent.verticalCenter
                        text: "⌄"; color: Shell.ShellPalette.managerDim
                    }
                    background: Rectangle {
                        radius: 11; color: Shell.ShellPalette.managerPanel
                        border.width: restorePicker.activeFocus ? 2 : 1
                        border.color: restorePicker.activeFocus ? Shell.ShellPalette.accentForeground : Qt.alpha(Shell.ShellPalette.managerInk, 0.1411764705882353)
                    }
                    delegate: C.ItemDelegate {
                        required property int index
                        required property string modelData
                        width: restorePicker.popup.width - 12
                        padding: 10
                        highlighted: restorePicker.highlightedIndex === index
                        contentItem: Text {
                            text: modelData; font: restorePicker.font; color: Shell.ShellPalette.managerInk
                            elide: Text.ElideRight
                        }
                        background: Rectangle { radius: 8; color: parent.highlighted ? Qt.alpha(Shell.ShellPalette.accent, 0.10980392156862745) : "transparent" }
                    }
                    popup: C.Popup {
                        id: restoreDates
                        onClosed: root.refreshRestoreDates()
                        // In a narrow window the list moves left so it stays inside the window.
                        onAboutToShow: x = Math.min(0, (restorePicker.Window.width || width) - 8 - width - restorePicker.mapToItem(null, 0, 0).x)
                        y: restorePicker.height + 4
                        width: Math.min(250, (restorePicker.Window.width || 266) - 16)
                        padding: 6
                        implicitHeight: Math.min(contentItem.implicitHeight + 12, 250)
                        background: Rectangle { radius: 12; color: Shell.ShellPalette.managerPanel; border.color: Qt.alpha(Shell.ShellPalette.managerInk, 0.1411764705882353) }
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
                    displayText: Shell.Translation.englishPlural(qsTranslate("ManagerUpdates", "%n date(s)", "", root.offeredRestorePoints.length), root.offeredRestorePoints.length)
                    enabled: !root.locked && !root.controller.restorePending
                    onActivated: index => {
                        if (root.offeredRestorePoints[index]) {
                            root.restoreChosen = true;
                            root.restoreId = String(root.offeredRestorePoints[index].id);
                        }
                    }
                }
                ManagerButton {
                    text: qsTranslate("ManagerUpdates", "Restore…")
                    enabled: !!root.point && !root.locked && !root.controller.restorePending
                    onClicked: root.confirmRestore(root.point)
                }
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
        background: Rectangle { radius: 18; color: Shell.ShellPalette.managerPanel; border.width: 2; border.color: Qt.alpha(Shell.ShellPalette.accent, 0.6196078431372549) }
        contentItem: ColumnLayout {
            spacing: 14
            ManagerLabel { text: qsTranslate("ManagerUpdates", "Restart now?"); font.pixelSize: 19; font.bold: true }
            ManagerLabel { text: qsTranslate("ManagerUpdates", "The computer needs a restart. Save your work first.") }
            ManagerActions {
                spacing: 5
                ManagerButton { text: qsTranslate("ManagerUpdates", "Cancel"); onClicked: restartDialog.close() }
                ManagerButton { text: qsTranslate("ManagerUpdates", "Restart now"); primary: true; enabled: !root.locked && root.controller.restart; onClicked: { restartDialog.close(); root.controller.reboot(); } }
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
        // A window shorter than the dialog scrolls its text; the buttons stay in sight.
        height: Math.min(implicitHeight, parent ? parent.height - 32 : implicitHeight)
        background: Rectangle { radius: 18; color: Shell.ShellPalette.managerPanel; border.width: 2; border.color: Qt.alpha(Shell.ShellPalette.accent, 0.6196078431372549) }
        contentItem: ColumnLayout {
            spacing: 14
            C.ScrollView {
                id: restoreScroll
                objectName: "restore-text"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredHeight: restoreText.implicitHeight
                contentWidth: availableWidth
                clip: true
                ColumnLayout {
                    id: restoreText
                    width: restoreScroll.availableWidth
                    spacing: 14
                    ManagerLabel { text: root.restoreTitle(root.controller.selectedRestore) + "?"; font.pixelSize: 19; font.bold: true }
                    ManagerLabel { text: qsTranslate("ManagerUpdates", "Your files in /home stay as they are.") }
                    ManagerLabel { text: qsTranslate("ManagerUpdates", "The current system is kept, so you can undo this.") }
                    ManagerLabel { text: qsTranslate("ManagerUpdates", "The computer needs a restart. Save your work first.") }
                }
            }
            ManagerActions {
                spacing: 5
                ManagerButton { text: qsTranslate("ManagerUpdates", "Cancel"); onClicked: { root.controller.cancelRestore(); restoreDialog.close(); } }
                ManagerButton { text: qsTranslate("ManagerUpdates", "Restore"); primary: true; enabled: !root.locked && !!root.controller.selectedRestore; onClicked: { root.controller.restore(); restoreDialog.close(); } }
            }
        }
    }
}
