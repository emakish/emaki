// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls

FocusScope {
    id: prompt
    property string snapshot: ""
    property string automaticMessage: ""
    property bool busy: false
    property bool succeeded: false
    property string error: ""
    signal keepRequested
    signal dismissed
    implicitWidth: 460
    implicitHeight: content.implicitHeight + actions.implicitHeight + 72
    function takeFocus(): void {
        later.forceActiveFocus();
    }
    onBusyChanged: if (!busy)
        Qt.callLater(takeFocus)
    Keys.onLeftPressed: later.forceActiveFocus()
    Keys.onUpPressed: later.forceActiveFocus()
    Keys.onRightPressed: (keep.visible && keep.enabled ? keep : later).forceActiveFocus()
    Keys.onDownPressed: (keep.visible && keep.enabled ? keep : later).forceActiveFocus()
    Keys.onEscapePressed: event => {
        if (!busy)
            dismissed();
        event.accepted = true;
    }
    Rectangle {
        anchors.fill: parent
        color: LiquidPalette.flatPanel
        radius: Metrics.panelRadius
        border.color: LiquidPalette.flatDropRim
    }
    Flickable {
        id: scroll
        objectName: "snapshotScroll"
        anchors {
            left: parent.left
            right: parent.right
            top: parent.top
            bottom: actions.top
            margins: 24
        }
        clip: true
        contentWidth: width
        contentHeight: content.implicitHeight
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar {
            policy: ScrollBar.AsNeeded
        }
        Column {
            id: content
            width: scroll.width - 12
            spacing: 16
            Text {
                width: parent.width
                text: prompt.succeeded ? "This state is ready" : "You are using a recovery snapshot"
                wrapMode: Text.WordWrap
                font.family: ShellPalette.uiFont
                font.pixelSize: 20
                font.weight: Font.DemiBold
                color: LiquidPalette.ink
            }
            Text {
                width: parent.width
                text: prompt.succeeded ? "Restart to use the restored system. Your previous system has been kept for undo." : (prompt.automaticMessage ? prompt.automaticMessage + "\n\n" : "") + "Snapshot " + prompt.snapshot + " is temporary until you keep it. Keeping it restores the original snapshot, including apps and system settings. Changes made during this recovery session are discarded. Your home files stay as they are."
                wrapMode: Text.WordWrap
                font.family: ShellPalette.uiFont
                font.pixelSize: 14
                color: LiquidPalette.ink
            }
            Text {
                width: parent.width
                visible: prompt.busy || prompt.error !== ""
                text: prompt.busy ? "Keeping this state…" : prompt.error
                wrapMode: Text.WrapAnywhere
                font.family: ShellPalette.uiFont
                font.pixelSize: 14
                color: prompt.error ? LiquidPalette.danger : LiquidPalette.plateDim
            }
        }
    }
    Flow {
        id: actions
        width: Math.min(parent.width - 48, later.width + (keep.visible ? keep.width + spacing : 0))
        anchors {
            right: parent.right
            bottom: parent.bottom
            margins: 24
        }
        spacing: 12
        PromptAction {
            id: later
            objectName: "snapshotLater"
            label: prompt.succeeded ? "Done" : "Not now"
            enabled: !prompt.busy
            KeyNavigation.tab: keep.visible ? keep : later
            KeyNavigation.backtab: keep.visible ? keep : later
            onClicked: prompt.dismissed()
        }
        PromptAction {
            id: keep
            objectName: "snapshotKeep"
            label: "Keep this state"
            visible: !prompt.succeeded
            enabled: !prompt.busy
            accent: true
            KeyNavigation.tab: later
            KeyNavigation.backtab: later
            onClicked: prompt.keepRequested()
        }
    }
    component PromptAction: Rectangle {
        id: action
        required property string label
        property bool accent: false
        signal clicked
        implicitWidth: labelText.implicitWidth + 28
        implicitHeight: 40
        radius: 11
        activeFocusOnTab: true
        FocusRing {
            shown: action.activeFocus
            strong: action.accent
            // The accent button sits on the plate: the rim's outer row faces the plate.
            outerInk: LiquidPalette.focusOuterOnAccent
            radius: action.radius
        }
        opacity: enabled ? 1 : .5
        color: accent ? ShellPalette.accent : LiquidPalette.flatDrop
        border.color: LiquidPalette.flatDropRim
        border.width: 1
        Accessible.role: Accessible.Button
        Accessible.name: label
        Accessible.onPressAction: if (enabled)
            clicked()
        Keys.onSpacePressed: event => {
            if (enabled && !event.isAutoRepeat)
                clicked();
            event.accepted = true;
        }
        Keys.onReturnPressed: event => {
            if (enabled && !event.isAutoRepeat)
                clicked();
            event.accepted = true;
        }
        Keys.onEnterPressed: event => {
            if (enabled && !event.isAutoRepeat)
                clicked();
            event.accepted = true;
        }
        Text {
            id: labelText
            anchors.centerIn: parent
            text: action.label
            color: LiquidPalette.ink
            font.family: ShellPalette.uiFont
            font.pixelSize: 14
            font.weight: Font.Medium
        }
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: action.clicked()
        }
    }
}
