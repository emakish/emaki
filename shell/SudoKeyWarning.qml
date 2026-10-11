// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import "settings"
import "SudoText.js" as Copy

Rectangle {
    id: warning
    property var backend: SudoKeySupport.backend
    signal dismissed
    signal accepted
    focus: true
    Keys.onEscapePressed: dismissed()
    color: "#fff7f2"
    radius: 20
    implicitHeight: content.implicitHeight + 48
    Flickable {
        anchors.fill: parent
        anchors.margins: 24
        contentHeight: content.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        Column {
            id: content
            width: parent.width
            spacing: 18
            Repeater {
                model: [Copy.keyWarningTitle, Copy.keyWarningBody, Copy.keyWarningLifetime, Copy.keyTimestampPolicy, Copy.keyWarningRisk]
                Text {
                    required property string modelData
                    required property int index
                    width: content.width
                    text: modelData
                    textFormat: Text.PlainText
                    wrapMode: Text.WordWrap
                    font.family: ShellPalette.uiFont
                    font.pixelSize: index === 0 ? 25 : 20
                    font.weight: index === 0 ? Font.DemiBold : Font.Normal
                    color: "#352522"
                }
            }
            Text {
                width: parent.width
                visible: !!warning.backend && warning.backend.btrfs
                text: warning.backend && warning.backend.snapguardAvailable ? Copy.keySnapguardOffer : Copy.keySnapguardUnavailable
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                font.family: ShellPalette.uiFont
                font.pixelSize: 20
                color: "#352522"
            }
            Flow {
                width: parent.width
                spacing: 12
                SettingsButton {
                    objectName: "sudo-key-warning-dismiss"
                    text: Copy.keyNotNow
                    onClicked: warning.dismissed()
                }
                SettingsButton {
                    objectName: "sudo-key-warning-accept"
                    text: Copy.keyAcknowledge
                    enabled: !!warning.backend && warning.backend.connected && warning.backend.installed
                    onClicked: {
                        warning.backend.acknowledgeWarning();
                        warning.accepted();
                    }
                }
            }
        }
    }
}
