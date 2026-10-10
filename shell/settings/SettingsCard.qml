// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick

Rectangle {
    id: root

    antialiasing: true
    property string title: ""
    default property alias content: body.data
    property Component headerAction
    property Component footer
    readonly property alias headerActionItem: headerEditor.item
    readonly property alias footerItem: footerEditor.item
    readonly property real headerHeight: Math.max(heading.visible ? heading.implicitHeight : 0, headerEditor.implicitHeight)
    implicitHeight: footerEditor.active ? footerEditor.y + footerEditor.implicitHeight + 16 : body.implicitHeight + body.y + (body.rowContent ? 0 : 16)
    color: "transparent"
    Rectangle {
        antialiasing: true
        x: 0
        y: root.headerHeight > 0 ? root.headerHeight + 9 : 0
        width: parent.width
        height: parent.height - y
        radius: SettingsTheme.radius
        color: SettingsTheme.card
        border.color: SettingsTheme.rim
    }
    Text {
        id: heading
        x: 6
        y: (root.headerHeight - height) / 2
        width: parent.width - 12 - (headerEditor.active ? headerEditor.width + 12 : 0)
        elide: Text.ElideRight
        visible: root.title !== ""
        textFormat: Text.PlainText
        text: root.title.toUpperCase()
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 12
        font.weight: Font.DemiBold
        font.letterSpacing: .84
        color: SettingsTheme.dim
    }
    Loader {
        id: headerEditor
        anchors.right: parent.right
        anchors.rightMargin: 6
        y: (root.headerHeight - height) / 2
        active: root.headerAction !== null
        sourceComponent: root.headerAction
    }
    Loader {
        id: footerEditor
        x: body.x
        y: body.y + body.implicitHeight + (body.rowContent ? 8 : 12)
        width: body.width
        active: root.footer !== null
        sourceComponent: root.footer
    }
    Column {
        id: body
        readonly property bool rowContent: children.length > 0 && children[0] instanceof SettingsRow
        x: 18
        y: (root.headerHeight > 0 ? root.headerHeight + 9 : 0) + (rowContent ? 0 : 16)
        width: parent.width - 34
        spacing: 0
    }
}
