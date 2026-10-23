// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import "CaptureText.js" as CaptureText
import "Keyboard.js" as Keyboard

Item {
    id: panel
    required property CaptureController controller
    readonly property bool keyboardBoundary: true
    property bool keyboardMode: true
    readonly property rect selection: controller.area
    readonly property bool selectingArea: controller.target === "area"
    readonly property color shade: Qt.rgba(LiquidPalette.inkOnLight.r, LiquidPalette.inkOnLight.g, LiquidPalette.inkOnLight.b, .48)
    focus: true

    function moveSelection(key, resize) {
        const a = selection;
        const dx = key === Qt.Key_Left ? -1 : key === Qt.Key_Right ? 1 : 0;
        const dy = key === Qt.Key_Up ? -1 : key === Qt.Key_Down ? 1 : 0;
        controller.setArea(a.x + (resize ? 0 : dx), a.y + (resize ? 0 : dy), a.width + (resize ? dx : 0), a.height + (resize ? dy : 0));
    }
    Keys.onEscapePressed: {
        if (selectingArea && !activeFocus)
            forceActiveFocus();
        else
            controller.close();
    }
    Keys.onReturnPressed: if (controller.canCapture)
        controller.capture()
    Keys.onEnterPressed: if (controller.canCapture)
        controller.capture()
    Keys.onPressed: event => {
        if (activeFocus && selectingArea && [Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down].indexOf(event.key) >= 0) {
            moveSelection(event.key, !!(event.modifiers & Qt.ShiftModifier));
            event.accepted = true;
        } else {
            Keyboard.handle(panel, event);
        }
    }
    // Four dimmed pieces leave the selected area transparent.
    Rectangle {
        width: panel.width
        height: panel.selectingArea ? Math.max(0, panel.selection.y) : panel.height
        color: panel.shade
    }
    Rectangle {
        visible: panel.selectingArea
        y: panel.selection.y
        width: Math.max(0, panel.selection.x)
        height: panel.selection.height
        color: panel.shade
    }
    Rectangle {
        visible: panel.selectingArea
        x: panel.selection.x + panel.selection.width
        y: panel.selection.y
        width: Math.max(0, panel.width - x)
        height: panel.selection.height
        color: panel.shade
    }
    Rectangle {
        visible: panel.selectingArea
        y: panel.selection.y + panel.selection.height
        width: panel.width
        height: Math.max(0, panel.height - y)
        color: panel.shade
    }
    MouseArea {
        id: areaPointer
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
        hoverEnabled: true
        cursorShape: panel.selectingArea ? Qt.CrossCursor : Qt.ArrowCursor
        property point origin
        onWheel: event => event.accepted = true
        onPressed: event => {
            panel.forceActiveFocus();
            if (!panel.selectingArea || event.button !== Qt.LeftButton)
                return;
            origin = Qt.point(event.x, event.y);
            panel.controller.setArea(origin.x, origin.y, 1, 1);
        }
        onPositionChanged: event => {
            if (pressed && panel.selectingArea && (pressedButtons & Qt.LeftButton))
                panel.controller.setArea(Math.min(origin.x, event.x), Math.min(origin.y, event.y), Math.abs(event.x - origin.x), Math.abs(event.y - origin.y));
        }
    }
    Rectangle {
        id: areaBorder
        visible: panel.selectingArea
        x: panel.selection.x
        y: panel.selection.y
        width: panel.selection.width
        height: panel.selection.height
        color: "transparent"
        border.width: 2
        border.color: LiquidPalette.inkOnDark
        Accessible.role: Accessible.Pane
        Accessible.name: CaptureText.selection()
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.SizeAllCursor
            property point origin
            property rect before
            onPressed: event => {
                panel.forceActiveFocus();
                origin = mapToItem(panel, event.x, event.y);
                before = panel.selection;
            }
            onPositionChanged: event => {
                if (!pressed)
                    return;
                const point = mapToItem(panel, event.x, event.y);
                panel.controller.setArea(before.x + point.x - origin.x, before.y + point.y - origin.y, before.width, before.height);
            }
        }
        Repeater {
            model: 4
            Rectangle {
                id: handle
                required property int index
                readonly property bool rightEdge: index % 2 === 1
                readonly property bool bottomEdge: index >= 2
                x: (rightEdge ? areaBorder.width : 0) - width / 2
                y: (bottomEdge ? areaBorder.height : 0) - height / 2
                width: 14
                height: 14
                radius: 7
                color: LiquidPalette.inkOnDark
                border.width: 1
                border.color: LiquidPalette.inkOnLight
                MouseArea {
                    anchors.fill: parent
                    anchors.margins: -7
                    cursorShape: handle.rightEdge === handle.bottomEdge ? Qt.SizeFDiagCursor : Qt.SizeBDiagCursor
                    property rect before
                    onPressed: {
                        panel.forceActiveFocus();
                        before = panel.selection;
                    }
                    onPositionChanged: event => {
                        if (!pressed)
                            return;
                        const p = mapToItem(panel, event.x, event.y);
                        const x = handle.rightEdge ? before.x : Math.min(p.x, before.x + before.width - 1);
                        const y = handle.bottomEdge ? before.y : Math.min(p.y, before.y + before.height - 1);
                        const right = handle.rightEdge ? Math.max(p.x, before.x + 1) : before.x + before.width;
                        const bottom = handle.bottomEdge ? Math.max(p.y, before.y + 1) : before.y + before.height;
                        panel.controller.setArea(x, y, right - x, bottom - y);
                    }
                }
            }
        }
    }
    Text {
        id: hint
        anchors.top: parent.top
        anchors.topMargin: Metrics.reservedSpace + 16
        anchors.horizontalCenter: parent.horizontalCenter
        width: Math.min(parent.width - 40, 760)
        text: !panel.controller.ready ? CaptureText.preparing() : panel.selectingArea ? CaptureText.areaHint() : panel.controller.target === "window" ? CaptureText.selectWindow() : CaptureText.screenHint()
        color: LiquidPalette.inkOnDark
        font.family: ShellPalette.uiFont
        font.pixelSize: 15
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
    }
    Item {
        id: windowsCard
        visible: panel.controller.target === "window"
        anchors.top: hint.bottom
        anchors.topMargin: 16
        anchors.bottom: toolbar.top
        anchors.bottomMargin: 16
        anchors.horizontalCenter: parent.horizontalCenter
        width: Math.min(parent.width - 48, 620)
        WelcomeGlass {
            anchors.fill: parent
            wallpaperTexture: panel.controller.wallpaperTexture
        }
        MouseArea {
            anchors.fill: parent
            onWheel: event => event.accepted = true
        }
        ListView {
            id: windowList
            anchors.fill: parent
            anchors.margins: 12
            clip: true
            spacing: 6
            model: panel.controller.windows
            currentIndex: panel.controller.windows.findIndex(window => window.id === panel.controller.windowId)
            onCurrentIndexChanged: if (currentIndex >= 0)
                positionViewAtIndex(currentIndex, ListView.Contain)
            delegate: WelcomeButton {
                required property var modelData
                width: windowList.width
                height: 48
                // Window titles are untrusted application text, never rich text.
                text: ""
                selected: panel.controller.windowId === modelData.id
                Accessible.name: modelData.title || modelData.app_id || CaptureText.untitledWindow()
                onClicked: panel.controller.selectWindow(modelData.id)
                Text {
                    anchors.fill: parent
                    anchors.margins: 14
                    text: parent.modelData.title || parent.modelData.app_id || CaptureText.untitledWindow()
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    color: LiquidPalette.ink
                    font.family: ShellPalette.uiFont
                    font.pixelSize: 14
                }
            }
            Text {
                anchors.centerIn: parent
                visible: windowList.count === 0
                text: CaptureText.noWindows()
                color: LiquidPalette.ink
                font.family: ShellPalette.uiFont
                font.pixelSize: 14
            }
        }
    }
    Item {
        id: toolbar
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 20
        width: Math.min(parent.width - 32, 760)
        height: controls.height + 28
        WelcomeGlass {
            anchors.fill: parent
            wallpaperTexture: panel.controller.wallpaperTexture
        }
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.AllButtons
            onWheel: event => event.accepted = true
        }
        Column {
            id: controls
            width: parent.width - 28
            anchors.centerIn: parent
            spacing: 10
            Flickable {
                visible: panel.controller.outputs.length > 1
                width: parent.width
                height: visible ? 34 : 0
                contentWidth: outputRow.width
                contentHeight: height
                clip: true
                Row {
                    id: outputRow
                    spacing: 6
                    Repeater {
                        model: panel.controller.outputs
                        WelcomeButton {
                            required property var modelData
                            height: 34
                            text: modelData.name
                            selected: panel.controller.outputName === modelData.name
                            onClicked: panel.controller.selectOutput(modelData.name)
                        }
                    }
                }
            }
            Row {
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: 10
                Repeater {
                    model: [
                        {
                            value: "area",
                            label: CaptureText.area(),
                            icon: "edit-select-all-symbolic"
                        },
                        {
                            value: "window",
                            label: CaptureText.window(),
                            icon: "focus-windows-symbolic"
                        },
                        {
                            value: "screen",
                            label: CaptureText.screen(),
                            icon: "video-display-symbolic"
                        }
                    ]
                    Column {
                        required property var modelData
                        spacing: 6
                        SymbolIcon {
                            anchors.horizontalCenter: parent.horizontalCenter
                            name: parent.modelData.icon
                            ink: LiquidPalette.ink
                            width: 24
                            height: 24
                        }
                        WelcomeButton {
                            text: parent.modelData.label
                            selected: panel.controller.target === parent.modelData.value
                            enabled: panel.controller.ready && !panel.controller.busy && (!panel.controller.recognizing || parent.modelData.value === "area")
                            onClicked: panel.controller.selectTarget(parent.modelData.value)
                        }
                    }
                }
            }
            Row {
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: 8
                WelcomeButton {
                    text: CaptureText.picture()
                    selected: panel.controller.mode === "picture"
                    onClicked: panel.controller.selectMode("picture")
                }
                WelcomeButton {
                    text: CaptureText.recording()
                    selected: panel.controller.mode === "recording"
                    onClicked: panel.controller.selectMode("recording")
                }
                WelcomeButton {
                    text: CaptureText.text()
                    selected: panel.controller.mode === "text"
                    onClicked: panel.controller.selectMode("text")
                }
                WelcomeButton {
                    text: CaptureText.qr()
                    selected: panel.controller.mode === "qr"
                    onClicked: panel.controller.selectMode("qr")
                }
            }
            Text {
                width: parent.width
                visible: text !== ""
                text: panel.controller.error
                textFormat: Text.PlainText
                color: LiquidPalette.danger
                font.family: ShellPalette.uiFont
                font.pixelSize: 13
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
            }
            Row {
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: 10
                WelcomeButton {
                    text: CaptureText.cancel()
                    onClicked: panel.controller.close()
                }
                WelcomeButton {
                    text: panel.controller.mode === "recording" ? CaptureText.record() : CaptureText.capture()
                    primary: true
                    enabled: panel.controller.canCapture
                    onClicked: panel.controller.capture()
                }
            }
        }
    }
}
