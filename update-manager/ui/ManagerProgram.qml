// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import "file:@EMAKI_DATADIR@/shell" as Shell
import QtQuick.Layouts
import QtQuick.Controls as C
import "ManagerLogic.js" as Logic

ColumnLayout {
    id: page
    required property var controller
    readonly property var program: controller.selectedEdition || ({})
    spacing: 24
    property bool showLog: false
    function size(value: var): string { return value === null || value === undefined ? qsTranslate("ManagerProgram", "Size unknown") : value < 1048576 ? qsTranslate("ManagerProgram", "Under 1 MB") : qsTranslate("ManagerProgram", "%1 MB").arg(Math.round(value / 1048576)); }
    function allowed(action: string): bool { return (program.actions || []).includes(action); }
    readonly property string unknown: qsTranslate("ManagerProgram", "Unknown")
    function known(field: string): bool { return program[field] !== undefined && program[field] !== null; }
    // Decision of 2026-10-13: without system snapshots (ext4) AUR installs are refused; the engine refuses them too.
    readonly property bool aurRefused: program.kind === "aur" && controller.aurKnown && !controller.aurSnapshots && allowed("install")
    function day(value: var): string { return value ? Logic.day(value) : ""; }
    function working(): string { return controller.phase === "removing" ? qsTranslate("ManagerProgram", "Removing…") : controller.phase === "undoing" ? qsTranslate("ManagerProgram", "Undoing the install…") : program.kind === "aur" ? qsTranslate("ManagerProgram", "Building and installing…") : qsTranslate("ManagerProgram", "Installing…"); }
    function undoText(receipt: var): string {
        if (!receipt) return "";
        const count = (receipt.added || []).length;
        return count > 1 ? Shell.Translation.englishPlural(qsTranslate("ManagerProgram", "Installed %1 with %n package(s) it needed", "", count - 1), count - 1).arg(day(receipt.time)) : qsTranslate("ManagerProgram", "Installed %1").arg(day(receipt.time));
    }
    // A narrow page (a niri third or half) puts the install column under the name and
    // Details under About.
    readonly property bool narrow: width < 640
    // Too narrow for the name beside the icon: the name goes under it.
    readonly property bool tight: width < 330
    GridLayout {
        columns: page.narrow ? 2 : 3
        columnSpacing: page.narrow ? 16 : 24
        rowSpacing: 20
        ProgramIcon { program: page.program; Layout.preferredWidth: page.narrow ? 64 : 104; Layout.preferredHeight: page.narrow ? 64 : 104; Layout.row: 0; Layout.column: 0 }
        ColumnLayout {
            Layout.row: page.tight ? 1 : 0; Layout.column: page.tight ? 0 : 1; Layout.columnSpan: page.tight ? 2 : 1
            Layout.fillWidth: true
            spacing: 7
            ManagerLabel { text: page.program.name || ""; font.pixelSize: page.narrow ? 24 : 30; font.weight: Font.DemiBold }
            ManagerLabel { text: page.program.summary || ""; color: Shell.ShellPalette.managerDim; font.pixelSize: 15 }
            Stars { objectName: "program-rating"; rating: programReviews.rating; showCount: !page.tight; showShortCount: page.tight; size: 14 }
            Flow {
                Layout.fillWidth: true
                spacing: 2
                Repeater {
                    model: page.controller.selectedProgram?.editions || []
                    ManagerButton {
                        id: editionButton
                        required property var modelData
                        text: modelData.sourceLabel || (modelData.kind === "flatpak" ? "Flathub" : modelData.kind === "aur" ? "AUR" : "Arch")
                        implicitHeight: 28
                        font.pixelSize: 12
                        background: Rectangle { radius: 8; color: page.program.key === editionButton.modelData.key ? Qt.alpha(Shell.ShellPalette.managerCream, .8) : Qt.alpha(Shell.ShellPalette.managerInk, .065); border.color: editionButton.activeFocus ? Shell.ShellPalette.accent : Qt.alpha(Shell.ShellPalette.managerInk, .14) }
                        enabled: !page.controller.busy
                        onClicked: page.controller.selectEdition(modelData.key)
                    }
                }
            }
        }
        ColumnLayout {
            Layout.row: page.tight ? 2 : page.narrow ? 1 : 0; Layout.column: page.narrow ? 0 : 2; Layout.columnSpan: page.narrow ? 2 : 1
            Layout.preferredWidth: page.narrow ? -1 : 280
            Layout.maximumWidth: page.narrow ? Number.POSITIVE_INFINITY : 280
            Layout.fillWidth: true
            spacing: 8
            ManagerButton {
                objectName: "program-install"
                text: page.program.kind === "aur" ? qsTranslate("ManagerProgram", "Build and install") : qsTranslate("ManagerProgram", "Install")
                primary: true
                implicitHeight: 44
                Layout.fillWidth: true
                visible: !page.program.installed && !page.controller.mutating
                enabled: page.allowed("install") && !page.aurRefused && (!page.controller.busy || page.controller.backgroundBusy) && !page.controller.externalBusy
                onClicked: page.controller.prepareInstall()
            }
            ManagerLabel {
                // By Install on every AUR page; it cannot be hidden.
                objectName: "aur-unchecked"
                visible: page.program.kind === "aur"
                text: qsTranslate("ManagerProgram", "From the AUR, nobody checked it.")
                color: Shell.ShellPalette.accentDeepForeground
                font.pixelSize: 12
                font.weight: Font.DemiBold
                horizontalAlignment: Text.AlignHCenter
            }
            ManagerLabel {
                objectName: "aur-no-snapshots-program"
                visible: page.aurRefused && !page.controller.mutating
                text: qsTranslate("ManagerProgram", "Installing from the AUR is not available here: this computer keeps no system snapshots.")
                color: Shell.ShellPalette.managerError
                font.pixelSize: 12
                horizontalAlignment: Text.AlignHCenter
            }
            RowLayout {
                visible: !!page.program.installed && !page.controller.mutating
                ManagerButton {
                    text: qsTranslate("ManagerProgram", "Open")
                    primary: true
                    Layout.fillWidth: true
                    visible: !page.program.packageOnly && page.program.kind !== "aur" || !!page.controller.selectedLauncher
                    onClicked: page.controller.openInstalled()
                }
                ManagerButton { text: qsTranslate("ManagerProgram", "Remove"); danger: true; visible: page.allowed("remove"); enabled: !page.controller.busy && !page.controller.externalBusy; onClicked: page.controller.prepareRemove() }
            }
            ManagerLabel {
                text: Logic.removalStatus(page.program)
                visible: !!page.program.installed && text !== ""
                color: Shell.ShellPalette.managerDim
                font.pixelSize: 12
                horizontalAlignment: Text.AlignHCenter
            }
            ManagerButton {
                objectName: "program-aur-update"
                text: qsTranslate("ManagerProgram", "Build the update · %1").arg(page.program.version || "")
                Layout.fillWidth: true
                squeezed: parent.width < implicitWidth
                visible: !!page.program.installed && page.program.kind === "aur" && page.allowed("install") && !page.controller.mutating
                enabled: !page.aurRefused && (!page.controller.busy || page.controller.backgroundBusy) && !page.controller.externalBusy
                onClicked: page.controller.prepareInstall()
            }
            ManagerButton {
                objectName: "program-undo"
                text: qsTranslate("ManagerProgram", "Undo this install")
                Layout.fillWidth: true; Layout.maximumWidth: implicitWidth
                squeezed: parent.width < implicitWidth
                quiet: true
                Layout.alignment: Qt.AlignHCenter
                visible: !!page.program.installed && page.allowed("undo") && !page.controller.mutating
                enabled: !page.controller.busy && !page.controller.externalBusy
                onClicked: page.controller.prepareUndo()
            }
            ManagerLabel {
                visible: !!page.program.installed && page.allowed("undo") && !page.controller.mutating && !!page.program.undo
                text: page.undoText(page.program.undo)
                color: Shell.ShellPalette.managerDim
                font.pixelSize: 12
                horizontalAlignment: Text.AlignHCenter
            }
            ManagerLabel {
                visible: !page.controller.mutating
                text: page.program.installed ? qsTranslate("ManagerProgram", "Installed · version %1").arg(page.program.installedVersion || page.program.version || "—")
                    : page.program.kind === "aur" ? qsTranslate("ManagerProgram", "Version %1 · size known after the build").arg(page.program.version || "—")
                    : qsTranslate("ManagerProgram", "This program: %1 on disk · %2 to download").arg(page.size(page.program.installedSize)).arg(page.size(page.program.downloadSize))
                color: Shell.ShellPalette.managerDim
                font.pixelSize: 12
                horizontalAlignment: Text.AlignHCenter
            }
            ManagerLabel {
                visible: !page.program.installed && (page.program.kind === "arch" || page.program.kind === "aur") && !page.controller.mutating
                text: page.program.kind === "aur" ? qsTranslate("ManagerProgram", "Building also brings the system up to date.") : qsTranslate("ManagerProgram", "Installing also brings the system up to date.")
                color: Shell.ShellPalette.managerDim
                font.pixelSize: 12
                horizontalAlignment: Text.AlignHCenter
            }
            ManagerButton {
                text: page.program.kind === "aur" ? qsTranslate("ManagerProgram", "show what will be built") : qsTranslate("ManagerProgram", "show what changes")
                // A page narrower than the label shortens it.
                Layout.fillWidth: true; Layout.maximumWidth: implicitWidth
                squeezed: parent.width < implicitWidth
                quiet: true
                Layout.alignment: Qt.AlignHCenter
                visible: !page.program.installed && (page.program.kind === "arch" || page.program.kind === "aur") && !page.controller.mutating
                enabled: page.allowed("install") && !page.aurRefused && (!page.controller.busy || page.controller.backgroundBusy) && !page.controller.externalBusy
                onClicked: page.controller.prepareInstall()
            }
            ManagerLabel { visible: page.controller.mutating; text: page.working(); font.weight: Font.DemiBold }
            Rectangle {
                id: progressTrack
                visible: page.controller.mutating
                Layout.fillWidth: true
                implicitHeight: 8
                radius: 4
                color: Qt.alpha(Shell.ShellPalette.managerInk, .14)
                clip: true
                Rectangle {
                    width: progressTrack.width * (page.controller.percent < 0 ? .35 : Math.min(100, Math.max(0, page.controller.percent)) / 100)
                    height: 8
                    radius: 4
                    color: Shell.ShellPalette.accent
                    objectName: "progress-fill"
                    x: page.controller.percent < 0 ? pulse.position : 0
                    QtObject { id: pulse; property real position: 0 }
                    NumberAnimation { target: pulse; property: "position"; from: -progressTrack.width * .35; to: progressTrack.width; duration: 1300; loops: Animation.Infinite; running: page.controller.mutating && page.controller.percent < 0 }
                }
            }
            ManagerLabel { visible: page.controller.mutating && page.controller.percent >= 0; text: qsTranslate("ManagerProgram", "%1%").arg(Math.round(page.controller.percent)); color: Shell.ShellPalette.managerDim; font.pixelSize: 12 }
            ManagerLabel {
                visible: page.controller.mutating
                text: page.controller.phase === "installing" ? qsTranslate("ManagerProgram", "You can close this window. Installing goes on.") : page.working()
                font.pixelSize: 12
                color: Shell.ShellPalette.managerDim
            }
        }
    }
    Rectangle {
        objectName: "aur-facts"
        visible: page.program.kind === "aur"
        Layout.fillWidth: true
        implicitHeight: aurFacts.implicitHeight + 32
        color: Qt.alpha(Shell.ShellPalette.managerCream, .42)
        border.color: Qt.alpha(Shell.ShellPalette.managerInk, .14)
        radius: 16
        ColumnLayout {
            id: aurFacts
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
            anchors.margins: 16
            spacing: 10
            ManagerLabel { text: qsTranslate("ManagerProgram", "WHAT IS KNOWN ABOUT IT"); font.pixelSize: 12; font.weight: Font.DemiBold; color: Shell.ShellPalette.managerDim }
            ManagerLabel {
                objectName: "aur-facts-unknown"
                visible: !page.known("votes")
                text: qsTranslate("ManagerProgram", "These AUR details are unavailable. Facts that could not be checked are shown as unknown.")
                color: Shell.ShellPalette.managerDim; font.pixelSize: 12
            }
            GridLayout {
                Layout.fillWidth: true
                columns: width >= 520 ? 3 : 2
                columnSpacing: 18
                rowSpacing: 10
                Repeater {
                    // A fact the AUR did not give (an installed program read without it) is unknown, never 0 or No.
                    model: [
                        [qsTranslate("ManagerProgram", "Votes"), page.known("votes") ? String(page.program.votes) : page.unknown, false],
                        [qsTranslate("ManagerProgram", "Popularity"), page.known("popularity") ? String(page.program.popularity) : page.unknown, false],
                        [qsTranslate("ManagerProgram", "Last update"), page.day(page.program.lastModified) || page.unknown, false],
                        [qsTranslate("ManagerProgram", "Out of date"), !page.known("outOfDate") ? page.unknown : page.program.outOfDate ? qsTranslate("ManagerProgram", "Yes, marked %1").arg(page.day(page.program.outOfDate)) : qsTranslate("ManagerProgram", "No"), !!page.program.outOfDate],
                        [qsTranslate("ManagerProgram", "Maintainer"), page.program.orphan === true ? qsTranslate("ManagerProgram", "Orphan — nobody maintains it") : page.program.maintainer || page.unknown, page.program.orphan === true],
                        [qsTranslate("ManagerProgram", "First published"), page.day(page.program.firstSubmitted) || page.unknown, false]
                    ]
                    ColumnLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        spacing: 2
                        ManagerLabel { text: parent.modelData[0]; color: Shell.ShellPalette.managerDim; font.pixelSize: 12 }
                        ManagerLabel { text: parent.modelData[1]; color: parent.modelData[2] ? Shell.ShellPalette.managerError : Shell.ShellPalette.managerInk; font.pixelSize: 14; font.weight: Font.DemiBold }
                    }
                }
            }
            Flow {
                Layout.fillWidth: true
                spacing: 4
                ManagerButton {
                    visible: /^https:\/\/aur\.archlinux\.org\//.test(page.program.recipe || "")
                    text: qsTranslate("ManagerProgram", "See how the program is built")
                    width: Math.min(implicitWidth, parent.width)
                    squeezed: parent.width > 0 && implicitWidth > parent.width
                    quiet: true; leftPadding: 0; font.pixelSize: 12
                    onClicked: Qt.openUrlExternally(page.program.recipe)
                }
                ManagerButton {
                    visible: /^https:\/\/aur\.archlinux\.org\//.test(page.program.page || "")
                    text: qsTranslate("ManagerProgram", "Its AUR page")
                    width: Math.min(implicitWidth, parent.width)
                    squeezed: parent.width > 0 && implicitWidth > parent.width
                    quiet: true; font.pixelSize: 12
                    onClicked: Qt.openUrlExternally(page.program.page)
                }
            }
        }
    }
    ProgramReviews {
        // Stars and reviews from ODRS, read in its own process: a slow network never holds the store.
        id: programReviews
        objectName: "program-reviews"
        program: page.controller.selectedProgram && page.program.kind !== "aur"
            ? Object.assign({}, page.program, {editions: page.controller.selectedProgram.editions || []}) : page.program
        // "What is known about it" above already shows an AUR package's votes.
        aurVotes: false
        Layout.fillWidth: true
    }
    ManagerButton {
        // The raw log stays behind Details.
        objectName: "program-details"
        visible: page.controller.mutating && page.controller.output !== ""
        text: page.showLog ? qsTranslate("ManagerProgram", "Hide details") : qsTranslate("ManagerProgram", "Details")
        onClicked: page.showLog = !page.showLog
    }
    ManagerLabel {
        objectName: "program-log"
        visible: page.showLog && page.controller.mutating && page.controller.output !== ""
        text: page.controller.output.split("\n").slice(-12).join("\n")
        font.family: "Iosevka Nerd Font Mono"
        font.pixelSize: 12
    }
    ManagerLabel { visible: (page.program.screenshots || []).length > 0; text: qsTranslate("ManagerProgram", "SCREENSHOTS"); font.pixelSize: 12; font.weight: Font.DemiBold; color: Shell.ShellPalette.managerDim }
    C.ScrollView {
        id: shotStrip
        // A narrower page shows each screenshot smaller, whole; the strip scrolls sideways, the page does not.
        objectName: "screenshots"
        readonly property real shotWidth: Math.min(400, width)
        readonly property real shotHeight: Math.round(shotWidth * 236 / 400)
        visible: (page.program.screenshots || []).length > 0
        Layout.fillWidth: true
        Layout.preferredHeight: shotHeight + 14
        contentWidth: shots.width
        contentHeight: shotHeight
        clip: true
        C.ScrollBar.vertical.policy: C.ScrollBar.AlwaysOff
        Row {
            id: shots
            spacing: 14
            Repeater {
                model: page.program.screenshots || []
                Rectangle {
                    required property var modelData
                    width: shotStrip.shotWidth; height: shotStrip.shotHeight; radius: 12
                    color: Qt.alpha(Shell.ShellPalette.managerInk, .06)
                    Image {
                        anchors.fill: parent
                        anchors.margins: 1
                        source: parent.modelData
                        asynchronous: true
                        fillMode: Image.PreserveAspectFit
                    }
                }
            }
        }
    }
    GridLayout {
        Layout.fillWidth: true
        columns: page.narrow ? 1 : 2
        columnSpacing: 24
        rowSpacing: 24
        ColumnLayout {
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignTop
            spacing: 16
            ManagerLabel { text: qsTranslate("ManagerProgram", "ABOUT"); font.pixelSize: 12; font.weight: Font.DemiBold; color: Shell.ShellPalette.managerDim }
            ManagerLabel { text: page.program.description || page.program.summary || ""; lineHeight: 1.5 }
        }
        ColumnLayout {
            Layout.preferredWidth: page.narrow ? -1 : 320
            Layout.maximumWidth: page.narrow ? Number.POSITIVE_INFINITY : 320
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignTop
            spacing: 12
            ManagerLabel { text: qsTranslate("ManagerProgram", "DETAILS"); font.pixelSize: 12; font.weight: Font.DemiBold; color: Shell.ShellPalette.managerDim }
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: details.implicitHeight + 8
                color: Qt.alpha(Shell.ShellPalette.managerCream, .42)
                radius: 16
                ColumnLayout {
                    id: details
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                    anchors.margins: 4
                    spacing: 0
                    Repeater {
                        model: [
                            [qsTranslate("ManagerProgram", "Source"), (page.program.sourceLabel || (page.program.kind === "flatpak" ? "Flathub" : page.program.kind === "aur" ? "AUR" : "Arch")) + (page.program.repository ? " · " + page.program.repository : "")],
                            [page.program.kind === "flatpak" ? qsTranslate("ManagerProgram", "App ID") : qsTranslate("ManagerProgram", "Package"), page.program.package || page.program.id || "—"],
                            [qsTranslate("ManagerProgram", "Version"), page.program.version || "—"], [qsTranslate("ManagerProgram", "Download"), page.size(page.program.downloadSize)],
                            [qsTranslate("ManagerProgram", "On disk"), page.size(page.program.installedSize)], [qsTranslate("ManagerProgram", "Licence"), page.program.licence || "—"],
                            [qsTranslate("ManagerProgram", "Category"), (page.program.categories || []).map(Logic.categoryLabel).join(", ") || "—"]
                        ]
                        RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            Layout.margins: 10
                            spacing: 14
                            ManagerLabel { text: parent.modelData[0]; color: Shell.ShellPalette.managerDim; Layout.preferredWidth: Math.min(140, Math.max(70, implicitWidth)); Layout.fillWidth: false; font.pixelSize: 13 }
                            ManagerLabel { text: parent.modelData[1]; horizontalAlignment: Text.AlignRight; font.pixelSize: 13 }
                        }
                    }
                }
            }
        }
    }
}
