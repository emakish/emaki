// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as C

ColumnLayout {
    id: page
    required property var controller
    readonly property var program: controller.selectedEdition || ({})
    signal backRequested
    spacing: 24
    function size(value: var): string { return value === null || value === undefined ? "Size unknown" : Math.round(value / 1048576) + " MB"; }
    function allowed(action: string): bool { return (program.actions || []).includes(action); }
    ManagerButton { text: "‹  Browse"; quiet: true; onClicked: page.backRequested() }
    RowLayout {
        spacing: 24
        ProgramIcon { program: page.program; Layout.preferredWidth: 104; Layout.preferredHeight: 104 }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 7
            ManagerLabel { text: page.program.name || ""; font.pixelSize: 30; font.weight: Font.DemiBold }
            ManagerLabel { text: page.program.summary || ""; color: "#866c70"; font.pixelSize: 15 }
            RowLayout {
                spacing: 2
                Repeater {
                    model: page.controller.selectedProgram?.editions || []
                    ManagerButton {
                        id: editionButton
                        required property var modelData
                        text: modelData.kind === "flatpak" ? "Flathub" : modelData.kind === "aur" ? "AUR" : "Arch"
                        implicitHeight: 28
                        font.pixelSize: 12
                        background: Rectangle { radius: 8; color: page.program.key === editionButton.modelData.key ? Qt.rgba(1, .98, .96, .8) : Qt.rgba(36/255,16/255,24/255,.065); border.color: editionButton.activeFocus ? "#e2733f" : Qt.rgba(36/255,16/255,24/255,.14) }
                        enabled: !page.controller.busy
                        onClicked: page.controller.selectEdition(modelData.key)
                    }
                }
            }
        }
        ColumnLayout {
            Layout.preferredWidth: 280
            Layout.maximumWidth: 280
            spacing: 8
            visible: page.program.kind !== "aur" || !!page.program.installed
            ManagerButton {
                objectName: "program-install"
                text: "Install"
                primary: true
                implicitHeight: 44
                Layout.fillWidth: true
                visible: !page.program.installed && !page.controller.mutating
                enabled: page.allowed("install") && !page.controller.busy && !page.controller.externalBusy
                onClicked: page.controller.prepareInstall()
            }
            RowLayout {
                visible: !!page.program.installed && !page.controller.mutating
                ManagerButton {
                    text: "Open"
                    primary: true
                    Layout.fillWidth: true
                    visible: !page.program.packageOnly || !!page.controller.selectedLauncher
                    onClicked: page.controller.openInstalled()
                }
                ManagerButton { text: "Remove"; danger: true; enabled: page.allowed("remove") && !page.controller.busy && !page.controller.externalBusy; onClicked: page.controller.prepareRemove() }
            }
            ManagerLabel {
                visible: !page.controller.mutating
                text: page.program.installed ? "Installed · version " + (page.program.installedVersion || page.program.version || "—") : "This program: " + page.size(page.program.installedSize) + " on disk · " + page.size(page.program.downloadSize) + " to download"
                color: "#866c70"
                font.pixelSize: 12
                horizontalAlignment: Text.AlignHCenter
            }
            ManagerLabel {
                visible: !page.program.installed && page.program.kind === "arch" && !page.controller.mutating
                text: "Installing also brings the system up to date."
                color: "#866c70"
                font.pixelSize: 12
                horizontalAlignment: Text.AlignHCenter
            }
            ManagerButton {
                text: "show what changes"
                quiet: true
                Layout.alignment: Qt.AlignHCenter
                visible: !page.program.installed && page.program.kind === "arch" && !page.controller.mutating
                enabled: !page.controller.busy && !page.controller.externalBusy
                onClicked: page.controller.prepareInstall()
            }
            ManagerLabel { visible: page.controller.mutating; text: page.controller.phase === "removing" ? "Removing…" : "Installing…"; font.weight: Font.DemiBold }
            Rectangle {
                id: progressTrack
                visible: page.controller.mutating
                Layout.fillWidth: true
                implicitHeight: 8
                radius: 4
                color: Qt.rgba(36/255,16/255,24/255,.14)
                clip: true
                Rectangle {
                    width: progressTrack.width * (page.controller.percent < 0 ? .35 : Math.min(100, Math.max(0, page.controller.percent)) / 100)
                    height: 8
                    radius: 4
                    color: "#e2733f"
                    x: page.controller.percent < 0 ? pulse.position : 0
                    QtObject { id: pulse; property real position: 0 }
                    NumberAnimation { target: pulse; property: "position"; from: -progressTrack.width * .35; to: progressTrack.width; duration: 1300; loops: Animation.Infinite; running: page.controller.mutating && page.controller.percent < 0 }
                }
            }
            ManagerLabel { visible: page.controller.mutating && page.controller.percent >= 0; text: Math.round(page.controller.percent) + "%"; color: "#866c70"; font.pixelSize: 12 }
            ManagerLabel {
                visible: page.controller.mutating
                text: page.controller.phase === "removing" ? "Removing…" : "You can close this window. Installing goes on."
                font.pixelSize: 12
                color: "#866c70"
            }
        }
    }
    Rectangle {
        visible: page.program.kind === "aur"
        Layout.fillWidth: true
        implicitHeight: aurNotice.implicitHeight + 32
        color: Qt.rgba(1,.98,.96,.42)
        border.color: Qt.rgba(36/255,16/255,24/255,.14)
        radius: 16
        ColumnLayout {
            id: aurNotice
            anchors.fill: parent
            anchors.margins: 16
            spacing: 8
            ManagerLabel { text: "Build in a terminal"; font.weight: Font.DemiBold }
            ManagerLabel { text: "AUR programs are made by other people and are not checked by Arch or Emaki. This window only shows them; it does not build or install them." }
        }
    }
    ManagerLabel {
        visible: page.controller.mutating && page.controller.output !== ""
        text: page.controller.output.split("\n").slice(-6).join("\n")
        font.family: "Iosevka Nerd Font Mono"
        font.pixelSize: 12
    }
    ManagerLabel { visible: (page.program.screenshots || []).length > 0; text: "SCREENSHOTS"; font.pixelSize: 12; font.weight: Font.DemiBold; color: "#866c70" }
    C.ScrollView {
        visible: (page.program.screenshots || []).length > 0
        Layout.fillWidth: true
        Layout.preferredHeight: 250
        contentWidth: shots.width
        contentHeight: 236
        clip: true
        C.ScrollBar.vertical.policy: C.ScrollBar.AlwaysOff
        Row {
            id: shots
            spacing: 14
            Repeater {
                model: page.program.screenshots || []
                Rectangle {
                    required property var modelData
                    width: 400; height: 236; radius: 12
                    color: Qt.rgba(36/255,16/255,24/255,.06)
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
    RowLayout {
        Layout.fillWidth: true
        spacing: 24
        ColumnLayout {
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignTop
            spacing: 16
            ManagerLabel { text: "ABOUT"; font.pixelSize: 12; font.weight: Font.DemiBold; color: "#866c70" }
            ManagerLabel { text: page.program.description || page.program.summary || ""; lineHeight: 1.5 }
        }
        ColumnLayout {
            Layout.preferredWidth: 320
            Layout.maximumWidth: 320
            Layout.alignment: Qt.AlignTop
            spacing: 12
            ManagerLabel { text: "DETAILS"; font.pixelSize: 12; font.weight: Font.DemiBold; color: "#866c70" }
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: details.implicitHeight + 8
                color: Qt.rgba(1,.98,.96,.42)
                radius: 16
                ColumnLayout {
                    id: details
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                    anchors.margins: 4
                    spacing: 0
                    Repeater {
                        model: [
                            ["Source", (page.program.sourceLabel || (page.program.kind === "flatpak" ? "Flathub" : page.program.kind === "aur" ? "AUR (shown only)" : "Arch")) + (page.program.repository ? " · " + page.program.repository : "")],
                            [page.program.kind === "flatpak" ? "App ID" : "Package", page.program.package || page.program.id || "—"],
                            ["Version", page.program.version || "—"], ["Download", page.size(page.program.downloadSize)],
                            ["On disk", page.size(page.program.installedSize)], ["Licence", page.program.licence || "—"],
                            ["Category", (page.program.categories || []).join(", ") || "—"]
                        ]
                        RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            Layout.margins: 10
                            spacing: 14
                            ManagerLabel { text: parent.modelData[0]; color: "#866c70"; Layout.preferredWidth: 70; Layout.fillWidth: false; font.pixelSize: 13 }
                            ManagerLabel { text: parent.modelData[1]; horizontalAlignment: Text.AlignRight; font.pixelSize: 13 }
                        }
                    }
                }
            }
        }
    }
}
