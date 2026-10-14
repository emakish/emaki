// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import "file:@EMAKI_DATADIR@/shell" as Shell
import QtQuick.Layouts
import QtQuick.Controls as C

C.AbstractButton {
    id: card
    required property var program
    // The first pick on Browse: a larger card with its own Install button.
    property bool featured: false
    // Replaces the summary line (the "All N" card of a shelf).
    property string hint: ""
    property bool installEnabled: true
    signal installRequested
    // Never shorter than its content, so the stars row stays inside the card.
    implicitHeight: Math.max(featured ? 104 : 74, topPadding + implicitContentHeight + bottomPadding)
    // Set by a narrow window: the large card keeps the small card's icon and puts Install under its text.
    property bool compact: false
    padding: featured && !compact ? 18 : 12
    focusPolicy: Qt.StrongFocus
    Keys.onReturnPressed: clicked()
    Keys.onEnterPressed: clicked()
    Accessible.name: program.name || ""
    background: Rectangle {
        radius: card.featured ? 16 : 14
        color: card.hovered ? Qt.alpha(Shell.ShellPalette.managerCream, .6) : Qt.alpha(Shell.ShellPalette.managerCream, .42)
        gradient: card.featured ? featureGradient : null
        Gradient {
            id: featureGradient
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: Qt.alpha(Shell.ShellPalette.accent, card.hovered ? .34 : .26) }
            GradientStop { position: 1; color: Qt.alpha(Shell.ShellPalette.managerCream, card.hovered ? .6 : .42) }
        }
        border.color: card.activeFocus ? Shell.ShellPalette.accentForeground : Qt.alpha(Shell.ShellPalette.managerInk, .14)
        border.width: card.activeFocus ? 2 : 1
    }
    contentItem: GridLayout {
        columns: card.featured && !card.compact ? 3 : 2
        columnSpacing: card.featured && !card.compact ? 16 : 12
        rowSpacing: 10
        ProgramIcon {
            program: card.program
            Layout.preferredWidth: card.featured && !card.compact ? 68 : 46
            Layout.preferredHeight: card.featured && !card.compact ? 68 : 46
            Layout.alignment: Qt.AlignVCenter
            Layout.row: 0; Layout.column: 0
        }
        ColumnLayout {
            Layout.row: 0; Layout.column: 1
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignVCenter
            spacing: 2
            ManagerLabel {
                text: card.program.name || ""
                font.pixelSize: card.featured ? 18 : 14
                font.weight: Font.DemiBold
                maximumLineCount: 1
                elide: Text.ElideRight
                wrapMode: Text.NoWrap
            }
            ManagerLabel {
                text: card.hint || card.program.summary || ""
                visible: text !== ""
                color: Shell.ShellPalette.managerDim
                font.pixelSize: 12
                maximumLineCount: 1
                elide: Text.ElideRight
                wrapMode: Text.NoWrap
            }
            // The engine puts {average, count} on catalog rows from the cached ODRS index.
            // The row keeps its height without a rating, so rated and unrated cards line up.
            Item {
                Layout.fillWidth: true
                implicitHeight: Math.max(glyphProbe.implicitHeight, textProbe.implicitHeight)
                Text { id: glyphProbe; visible: false; text: "★"; font.family: "Noto Sans Symbols 2, Noto Sans"; font.pixelSize: 13 }
                Text { id: textProbe; visible: false; text: "4.6"; font.family: "Noto Sans"; font.pixelSize: 12 }
                Stars { objectName: "card-rating"; anchors.verticalCenter: parent.verticalCenter; rating: card.hint ? null : card.program.rating || null; showShortCount: card.featured; maximumWidth: parent.width }
            }
        }
        ManagerButton {
            objectName: "featured-install"
            visible: card.featured
            Layout.row: card.featured && !card.compact ? 0 : 1; Layout.column: card.featured && !card.compact ? 2 : 1
            text: card.program.installed ? "Installed" : "Install"
            primary: !card.program.installed
            enabled: !card.program.installed && card.installEnabled
            onClicked: card.installRequested()
        }
    }
}
