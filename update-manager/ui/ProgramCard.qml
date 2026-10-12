// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as C

C.AbstractButton {
    id: card
    required property var program
    property bool featured: false
    property color featureColor: "#a8456e"
    implicitHeight: featured ? 176 : 150
    padding: featured ? 18 : 15
    focusPolicy: Qt.StrongFocus
    Keys.onReturnPressed: clicked()
    Keys.onEnterPressed: clicked()
    background: Rectangle {
        radius: 16
        color: card.featured ? card.featureColor : card.hovered ? Qt.rgba(1, .98, .96, .6) : Qt.rgba(1, .98, .96, .42)
        gradient: card.featured ? featureGradient : null
        Gradient {
            id: featureGradient
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: Qt.lighter(card.featureColor, 1.22) }
            GradientStop { position: 1; color: card.featureColor }
        }
        border.color: card.activeFocus ? "#c2481f" : Qt.rgba(36/255,16/255,24/255,.14)
        border.width: card.activeFocus ? 2 : 1
    }
    contentItem: ColumnLayout {
        spacing: 9
        RowLayout {
            spacing: 12
            ProgramIcon { program: card.program; Layout.preferredWidth: card.featured ? 64 : 48; Layout.preferredHeight: width }
            ColumnLayout {
                spacing: 4
                ManagerLabel {
                    visible: !card.featured
                    text: card.program.name || ""
                    font.weight: Font.DemiBold
                    maximumLineCount: 1
                    elide: Text.ElideRight
                }
                ManagerLabel {
                    visible: card.featured
                    text: (card.program.editions || [card.program]).map(e => e.kind === "flatpak" ? "Flathub" : e.kind === "aur" ? "AUR" : "Arch").filter((s,i,a) => a.indexOf(s) === i).join(" · ")
                    color: card.featured ? "#fff8f3" : "#2f5f86"
                    font.pixelSize: 11
                    font.weight: Font.DemiBold
                    horizontalAlignment: card.featured ? Text.AlignRight : Text.AlignLeft
                }
                RowLayout {
                    visible: !card.featured
                    spacing: 4
                    Repeater {
                        model: (card.program.editions || [card.program]).map(e => e.kind).filter((kind, i, kinds) => kinds.indexOf(kind) === i)
                        Rectangle {
                            required property string modelData
                            implicitWidth: sourceName.implicitWidth + 12
                            implicitHeight: 20
                            radius: 6
                            color: modelData === "flatpak" ? Qt.rgba(168/255,184/255,106/255,.18) : Qt.rgba(125/255,154/255,184/255,.16)
                            border.color: modelData === "flatpak" ? Qt.rgba(76/255,107/255,46/255,.35) : Qt.rgba(47/255,95/255,134/255,.35)
                            Text { id: sourceName; anchors.centerIn: parent; text: parent.modelData === "flatpak" ? "Flathub" : parent.modelData === "aur" ? "AUR" : "Arch"; color: parent.modelData === "flatpak" ? "#4c6b2e" : "#2f5f86"; font.family: "Noto Sans"; font.pixelSize: 11; font.weight: Font.DemiBold }
                        }
                    }
                }
            }
        }
        Item { visible: card.featured; Layout.fillHeight: true }
        ManagerLabel {
            visible: card.featured
            text: card.program.name || ""
            font.pixelSize: 17
            font.weight: Font.DemiBold
            color: "#fff8f3"
        }
        ManagerLabel {
            text: card.program.summary || ""
            color: card.featured ? "#fff8f3" : Qt.rgba(36/255,16/255,24/255,.58)
            font.pixelSize: 12
            maximumLineCount: 2
            elide: Text.ElideRight
            Layout.fillHeight: !card.featured
        }
        ManagerLabel {
            visible: !card.featured
            text: card.program.installed ? "✓ Installed" : card.program.installedSize ? Math.round(card.program.installedSize / 1048576) + " MB" : "Size unknown"
            color: card.program.installed ? "#c2481f" : Qt.rgba(36/255,16/255,24/255,.4)
            font.pixelSize: 12
        }
    }
}
