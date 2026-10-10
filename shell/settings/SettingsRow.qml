// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls

Item {
    id: root
    property string title: ""
    property string explanation: ""
    // A core-backed row opts in with a catalog and key; other rows keep their own values.
    property var catalog: null
    property string settingKey: ""
    property var fallbackValue
    readonly property var catalogRow: catalog && settingKey ? catalog.row(settingKey) : null
    property var value: catalogRow ? catalogRow.value : fallbackValue
    property var defaultValue: catalogRow ? catalogRow.default : fallbackValue
    objectName: settingKey ? "setting-" + settingKey : ""
    property var formatValue: value => String(value)
    property bool managed: false
    property bool unavailable: !!catalog && !!settingKey && (!catalogRow || catalogRow.editable === false)
    property bool busy: !!catalog && !!catalog.writing
    property string errorText: ""
    property bool separator: true
    readonly property bool editable: !managed && !unavailable && !busy
    readonly property bool changed: !unavailable && JSON.stringify(value) !== JSON.stringify(defaultValue)
    property Component control
    property real controlWidth: 150
    readonly property alias controlItem: editor.item
    readonly property alias resetItem: reset
    signal valueRequested(var value)
    onValueRequested: next => {
        if (catalog && settingKey)
            catalog.set(settingKey, String(next));
    }
    onResetRequested: {
        if (catalog && settingKey)
            catalog.reset(settingKey);
    }
    signal resetRequested
    function requestValue(next) {
        if (editable)
            valueRequested(next);
    }
    function requestReset() {
        if (editable && changed)
            resetRequested();
    }
    implicitHeight: Math.max(62, labels.implicitHeight + 26, editor.implicitHeight + 26)
    Rectangle {
        antialiasing: true
        anchors.left: parent.left
        anchors.leftMargin: -18
        anchors.right: parent.right
        anchors.rightMargin: -16
        anchors.bottom: parent.bottom
        height: 1
        color: SettingsTheme.rim
        visible: root.separator && root.y + root.height < root.parent.height - .5
    }
    Column {
        id: labels
        x: 0
        opacity: root.managed || root.unavailable ? .4 : 1
        anchors.verticalCenter: parent.verticalCenter
        width: Math.max(80, parent.width - root.controlWidth - (reset.width + 8) - 18)
        spacing: 2
        Text {
            textFormat: Text.PlainText
            width: parent.width
            text: root.title
            color: SettingsTheme.ink
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 14
            font.weight: Font.Medium
            wrapMode: Text.WordWrap
        }
        Text {
            textFormat: Text.PlainText
            width: parent.width
            text: root.explanation
            color: SettingsTheme.dim
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 13
            visible: text !== ""
            wrapMode: Text.WordWrap
        }
        Text {
            textFormat: Text.PlainText
            width: parent.width
            wrapMode: Text.WordWrap
            visible: root.errorText !== "" || root.managed || root.unavailable
            text: root.errorText || (root.managed ? "Set by the system" : "Unavailable")
            color: SettingsTheme.dim
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 11
        }
    }
    Loader {
        id: editor
        anchors.right: reset.left
        anchors.rightMargin: 8
        anchors.verticalCenter: parent.verticalCenter
        width: root.controlWidth
        sourceComponent: root.control
        enabled: root.editable
        opacity: enabled ? 1 : .45
    }
    SettingsButton {
        id: reset
        objectName: "reset"
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        visible: root.changed && !root.managed && !root.unavailable
        enabled: root.editable
        text: "↺"
        width: 26
        height: 26
        background: Rectangle {
            antialiasing: true
            radius: 8
            color: reset.hovered ? SettingsTheme.dropHover : "transparent"
            border.width: reset.activeFocus ? 2 : 0
            border.color: SettingsTheme.accent
        }
        font.pixelSize: 20
        ToolTip {
            id: resetTip
            visible: reset.hovered || reset.activeFocus
            text: "Reset to default: " + root.formatValue(root.defaultValue)
            delay: 400
            contentItem: Text {
                text: resetTip.text
                textFormat: Text.PlainText
                font.family: SettingsTheme.fontFamily
                font.pixelSize: 12
                color: "#fbf3ea"
            }
            background: Rectangle {
                antialiasing: true
                color: "#241018"
                radius: 8
                border.color: SettingsTheme.rim
            }
        }
        Accessible.name: "Reset " + root.title + " to default: " + root.formatValue(root.defaultValue)
        onClicked: root.requestReset()
    }
}
