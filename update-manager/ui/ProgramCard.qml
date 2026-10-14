// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
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
    padding: featured ? 18 : 12
    focusPolicy: Qt.StrongFocus
    Keys.onReturnPressed: clicked()
    Keys.onEnterPressed: clicked()
    Accessible.name: program.name || ""
    background: Rectangle {
        radius: card.featured ? 16 : 14
        color: card.hovered ? Qt.rgba(1, .98, .96, .6) : Qt.rgba(1, .98, .96, .42)
        gradient: card.featured ? featureGradient : null
        Gradient {
            id: featureGradient
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: Qt.rgba(226/255, 115/255, 63/255, card.hovered ? .34 : .26) }
            GradientStop { position: 1; color: Qt.rgba(1, .98, .96, card.hovered ? .6 : .42) }
        }
        border.color: card.activeFocus ? "#c2481f" : Qt.rgba(36/255,16/255,24/255,.14)
        border.width: card.activeFocus ? 2 : 1
    }
    contentItem: RowLayout {
        spacing: card.featured ? 16 : 12
        ProgramIcon {
            program: card.program
            Layout.preferredWidth: card.featured ? 68 : 46
            Layout.preferredHeight: card.featured ? 68 : 46
            Layout.alignment: Qt.AlignVCenter
        }
        ColumnLayout {
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
                color: "#866c70"
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
                Stars { objectName: "card-rating"; anchors.verticalCenter: parent.verticalCenter; rating: card.hint ? null : card.program.rating || null; showShortCount: card.featured }
            }
        }
        ManagerButton {
            objectName: "featured-install"
            visible: card.featured
            text: card.program.installed ? "Installed" : "Install"
            primary: !card.program.installed
            enabled: !card.program.installed && card.installEnabled
            onClicked: card.installRequested()
        }
    }
}
