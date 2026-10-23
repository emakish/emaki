// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as C
import "file:@EMAKI_DATADIR@/shell" as Shell

ColumnLayout {
    id: page
    required property var controller
    spacing: 16
    ManagerLabel { text: qsTranslate("ManagerDevelopment", "Development kits"); font.pixelSize: 25; font.weight: Font.DemiBold }
    ManagerLabel { text: qsTranslate("ManagerDevelopment", "Tools for building software, installed together from Arch."); color: Shell.ShellPalette.managerDim }
    ManagerLabel { visible: !page.controller.developmentLoaded; text: qsTranslate("ManagerDevelopment", "Loading…"); color: Shell.ShellPalette.managerDim }
    Repeater {
        model: page.controller.developmentKits || []
        Rectangle {
            id: card
            required property var modelData
            Layout.fillWidth: true
            implicitHeight: contents.implicitHeight + 32
            radius: 16
            color: Qt.alpha(Shell.ShellPalette.managerCream, .42)
            border.color: Qt.alpha(Shell.ShellPalette.managerInk, .14)
            ColumnLayout {
                id: contents
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                anchors.margins: 16
                spacing: 10
                ManagerLabel { text: card.modelData.name; font.pixelSize: 19; font.weight: Font.DemiBold }
                ManagerLabel { text: (card.modelData.packages || []).join(", "); color: Shell.ShellPalette.managerDim; font.pixelSize: 12 }
                ManagerLabel { visible: !!card.modelData.installed; text: qsTranslate("ManagerDevelopment", "Installed"); color: Shell.ShellPalette.managerSuccess }
                ManagerLabel {
                    visible: card.modelData.id === "rust"
                    text: qsTranslate("ManagerDevelopment", "The stable Rust toolchain is downloaded for your account after the packages are installed. Toolchains in your home folder stay when you remove the kit.")
                    font.pixelSize: 13
                }
                ManagerLabel {
                    visible: card.modelData.id === "docker"
                    text: qsTranslate("ManagerDevelopment", "Installing enables the Docker socket. Removing restores the previous socket and group settings. Images and containers stay on this computer.")
                    font.pixelSize: 13
                }
                C.CheckBox {
                    id: access
                    visible: card.modelData.id === "docker" && !card.modelData.installed
                    Layout.fillWidth: true
                    text: qsTranslate("ManagerDevelopment", "Allow Docker without an administrator password")
                    font.family: "Noto Sans"; font.pixelSize: 13
                    focusPolicy: Qt.StrongFocus
                    indicator: Rectangle {
                        x: access.leftPadding
                        anchors.verticalCenter: parent.verticalCenter
                        implicitWidth: 18; implicitHeight: 18; radius: 5
                        color: access.checked ? Shell.ShellPalette.accent : Shell.ShellPalette.managerBackground
                        border.width: access.activeFocus ? 2 : 1
                        border.color: access.activeFocus ? Shell.ShellPalette.accentForeground : Qt.alpha(Shell.ShellPalette.managerInk, .35)
                        Text { anchors.centerIn: parent; visible: access.checked; text: "✓"; color: Shell.ShellPalette.accentText; font.pixelSize: 13; font.bold: true }
                    }
                    contentItem: Text {
                        text: access.text; font.family: "Noto Sans"; font.pixelSize: 13
                        color: Shell.ShellPalette.managerInk; wrapMode: Text.WordWrap
                        leftPadding: access.indicator.width + 10
                    }
                    enabled: !page.controller.busy && !page.controller.externalBusy
                }
                ManagerLabel {
                    visible: access.visible
                    text: qsTranslate("ManagerDevelopment", "Members of the Docker group can control this computer as root.")
                    color: Shell.ShellPalette.managerDim; font.pixelSize: 13
                }
                ManagerLabel { visible: !!card.modelData.conflict; text: card.modelData.conflict || ""; color: Shell.ShellPalette.managerError; font.pixelSize: 13 }
                Flow {
                    Layout.fillWidth: true
                    spacing: 10
                    ManagerButton {
                        visible: (card.modelData.actions || []).includes("install")
                        text: qsTranslate("ManagerDevelopment", "Install")
                        width: Math.min(implicitWidth, parent.width); squeezed: parent.width > 0 && parent.width < implicitWidth
                        primary: true
                        enabled: !page.controller.busy && !page.controller.externalBusy
                        onClicked: page.controller.prepareKit(card.modelData, "install", access.checked)
                    }
                    ManagerButton {
                        visible: (card.modelData.actions || []).includes("finish")
                        text: qsTranslate("ManagerDevelopment", "Finish setup")
                        width: Math.min(implicitWidth, parent.width); squeezed: parent.width > 0 && parent.width < implicitWidth
                        primary: true
                        enabled: !page.controller.busy && !page.controller.externalBusy
                        onClicked: page.controller.prepareKit(card.modelData, "finish", false)
                    }
                    ManagerButton {
                        visible: (card.modelData.actions || []).includes("remove")
                        text: qsTranslate("ManagerDevelopment", "Remove")
                        width: Math.min(implicitWidth, parent.width); squeezed: parent.width > 0 && parent.width < implicitWidth
                        enabled: !page.controller.busy && !page.controller.externalBusy
                        onClicked: page.controller.prepareKit(card.modelData, "remove", false)
                    }
                    ManagerButton {
                        visible: (card.modelData.actions || []).includes("undo")
                        text: qsTranslate("ManagerDevelopment", "Undo this install")
                        width: Math.min(implicitWidth, parent.width); squeezed: parent.width > 0 && parent.width < implicitWidth
                        enabled: !page.controller.busy && !page.controller.externalBusy
                        onClicked: page.controller.prepareKit(card.modelData, "undo", false)
                    }
                }
            }
        }
    }
    Rectangle {
        id: progressTrack
        objectName: "development-progress"
        visible: page.controller.mutating && !!page.controller.planTarget
        Layout.fillWidth: true
        implicitHeight: 8; radius: 4
        color: Qt.alpha(Shell.ShellPalette.managerInk, .14)
        clip: true
        Rectangle {
            objectName: "development-progress-fill"
            width: progressTrack.width * (page.controller.percent < 0 ? .35 : Math.min(100, Math.max(0, page.controller.percent)) / 100)
            height: 8; radius: 4
            color: Shell.ShellPalette.accent
            x: page.controller.percent < 0 ? pulse.position : 0
            QtObject { id: pulse; property real position: 0 }
            NumberAnimation { target: pulse; property: "position"; from: -progressTrack.width * .35; to: progressTrack.width; duration: 1300; loops: Animation.Infinite; running: progressTrack.visible && page.controller.percent < 0 }
        }
    }
    ManagerLabel {
        objectName: "development-output"
        visible: !!page.controller.planTarget && page.controller.output !== ""
        text: page.controller.output.split("\n").slice(-12).join("\n")
        font.pixelSize: 12
    }
}
