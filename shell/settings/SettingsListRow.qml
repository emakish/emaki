// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick

SettingsRow {
    id: root
    // Entries may provide subtitle, icon, busy, unavailable, error and actions.
    // Slot contents read parent.entry and call parent.requestAction(action).
    property var entries: []
    property Component itemLeading
    property Component itemTrailing
    signal activated(var entry)
    signal actionRequested(var entry, string action)
    function requestAction(entry, action) {
        if (editable && !entry.busy && !entry.unavailable)
            actionRequested(entry.value, action);
    }
    controlWidth: 180
    control: Component {
        Column {
            spacing: 4
            Repeater {
                model: root.entries
                Item {
                    id: entryRow
                    required property var modelData
                    width: root.controlWidth
                    height: Math.max(primary.implicitHeight, leading.implicitHeight, trailing.implicitHeight, actions.implicitHeight)
                    enabled: !modelData.busy && !modelData.unavailable
                    opacity: enabled ? 1 : .45
                    Loader {
                        id: leading
                        property var entry: entryRow.modelData
                        function requestAction(action) {
                            root.requestAction(entry, action);
                        }
                        anchors.verticalCenter: parent.verticalCenter
                        sourceComponent: root.itemLeading
                        active: root.itemLeading !== null
                    }
                    Image {
                        id: icon
                        anchors.verticalCenter: parent.verticalCenter
                        width: visible ? 24 : 0
                        height: 24
                        visible: !leading.active && !!entryRow.modelData.icon
                        source: entryRow.modelData.icon || ""
                        fillMode: Image.PreserveAspectFit
                    }
                    SettingsButton {
                        id: primary
                        objectName: "list-item"
                        x: leading.active ? leading.width + 8 : (icon.visible ? icon.width + 8 : 0)
                        width: Math.max(0, parent.width - x - (trailing.active ? trailing.width + 8 : (actions.visible ? actions.width + 8 : 0)))
                        implicitHeight: Math.max(34, labels.implicitHeight + 12)
                        text: entryRow.modelData.label
                        padding: 6
                        Accessible.name: root.title + ": " + text
                        onClicked: if (root.editable)
                            root.activated(entryRow.modelData.value)
                        contentItem: Column {
                            id: labels
                            Text {
                                width: parent.width
                                text: primary.text
                                textFormat: Text.PlainText
                                font: primary.font
                                color: SettingsTheme.ink
                                wrapMode: Text.WordWrap
                            }
                            Text {
                                width: parent.width
                                visible: text !== ""
                                text: entryRow.modelData.subtitle || ""
                                textFormat: Text.PlainText
                                font.family: SettingsTheme.fontFamily
                                font.pixelSize: 12
                                color: SettingsTheme.dim
                                wrapMode: Text.WordWrap
                            }
                            Text {
                                width: parent.width
                                visible: text !== ""
                                text: entryRow.modelData.error || ""
                                textFormat: Text.PlainText
                                font.family: SettingsTheme.fontFamily
                                font.pixelSize: 12
                                color: SettingsTheme.ink
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                    Loader {
                        id: trailing
                        property var entry: entryRow.modelData
                        function requestAction(action) {
                            root.requestAction(entry, action);
                        }
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        sourceComponent: root.itemTrailing
                        active: root.itemTrailing !== null
                    }
                    Column {
                        id: actions
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        visible: !trailing.active && (entryRow.modelData.actions || []).length > 0
                        spacing: 4
                        Repeater {
                            model: entryRow.modelData.actions || []
                            SettingsButton {
                                required property var modelData
                                objectName: "list-action-" + modelData.id
                                text: modelData.label
                                enabled: modelData.enabled !== false
                                onClicked: root.requestAction(entryRow.modelData, modelData.id)
                            }
                        }
                    }
                }
            }
        }
    }
}
