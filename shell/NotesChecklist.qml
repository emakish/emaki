// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
Column {
    id: root
    property string body: ""
    property bool interactive: true
    property bool tasksOnly: false
    signal toggled(int line)
    spacing: 7
    Repeater {
        model: root.body.split("\n")
        delegate: Item {
            id: row
            required property string modelData
            required property int index
            readonly property bool task: /^\s*- \[[ xX]\] /.test(modelData)
            width: root.width
            visible: !root.tasksOnly || task
            height: visible ? Math.max(task ? 44 : 10, label.implicitHeight) : 0
            CheckBox {
                id: box
                visible: row.task
                enabled: root.interactive
                anchors.verticalCenter: parent.verticalCenter
                width: 44; height: 44
                checked: /^\s*- \[[xX]\] /.test(row.modelData)
                onClicked: root.toggled(row.index)
                Accessible.name: label.text
                indicator: Rectangle {
                    x: 10; y: 10; width: 24; height: 24; radius: 5
                    color: box.checked ? ShellPalette.accent : "transparent"
                    border.width: box.activeFocus ? 2 : 1
                    border.color: box.checked || box.activeFocus ? LiquidPalette.accent : LiquidPalette.faint
                    Text { anchors.centerIn: parent; text: box.checked ? "✓" : ""; color: ShellPalette.accentText; font.pixelSize: 13 }
                }
            }
            Text {
                id: label
                x: row.task ? 48 : 0
                anchors.verticalCenter: parent.verticalCenter
                width: parent.width - x
                text: row.task ? row.modelData.replace(/^\s*- \[[ xX]\] /, "") : row.modelData
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                font.family: "Noto Sans"
                font.pixelSize: row.index === 0 ? 20 : 15
                lineHeight: 1.5
                font.weight: row.index === 0 ? Font.DemiBold : Font.Normal
                font.strikeout: row.task && box.checked
                color: row.task && box.checked ? LiquidPalette.plateDim : LiquidPalette.ink
            }
        }
    }
}
