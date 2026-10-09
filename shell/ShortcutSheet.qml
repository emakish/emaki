// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

FocusScope {
    id: sheet
    property var rows: []
    property string loadState: "loading"
    readonly property var filtered: rows.filter(row => (row.keys + " " + row.description).toLowerCase().includes(search.text.toLowerCase()))
    signal closeRequested
    function takeFocus(): void {
        search.text = "";
        search.forceActiveFocus();
        loader.running = true;
    }
    function select(delta: int): void {
        if (!list.count)
            return;
        list.currentIndex = (Math.max(0, list.currentIndex) + delta + list.count) % list.count;
        list.positionViewAtIndex(list.currentIndex, ListView.Contain);
    }
    Keys.onEscapePressed: closeRequested()
    Keys.onTabPressed: {
        if (search.activeFocus)
            list.forceActiveFocus();
        else if (list.activeFocus)
            close.forceActiveFocus();
        else
            search.forceActiveFocus();
    }
    Keys.onBacktabPressed: {
        if (search.activeFocus)
            close.forceActiveFocus();
        else if (close.activeFocus)
            list.forceActiveFocus();
        else
            search.forceActiveFocus();
    }
    Rectangle {
        anchors.fill: parent
        radius: 20
        color: LiquidPalette.flatPanel
    }
    Text {
        id: heading
        x: 24
        y: 20
        text: "Keyboard shortcuts"
        font.family: ShellPalette.uiFont
        font.pixelSize: 22
        color: LiquidPalette.inkOnLight
    }
    Text {
        id: source
        x: 24
        y: heading.y + heading.height + 8
        width: parent.width - 48
        text: "Emaki defaults · Personal changes may differ."
        font.family: ShellPalette.uiFont
        font.pixelSize: 13
        color: LiquidPalette.dimOnLight
        wrapMode: Text.Wrap
    }
    Rectangle {
        id: searchBox
        x: 24
        y: source.y + source.height + 16
        width: parent.width - 48
        height: 44
        radius: 10
        color: LiquidPalette.flatDrop
        TextInput {
            id: search
            objectName: "shortcutSearch"
            anchors.fill: parent
            anchors.margins: 12
            activeFocusOnTab: true
            font.family: ShellPalette.uiFont
            font.pixelSize: 15
            color: LiquidPalette.inkOnLight
            clip: true
            Accessible.name: "Search shortcuts"
            Keys.onDownPressed: {
                list.forceActiveFocus();
                sheet.select(0);
            }
            onTextChanged: list.currentIndex = 0
            Text {
                visible: !search.text
                text: "Search shortcuts"
                color: LiquidPalette.dimOnLight
                font: search.font
            }
        }
        FocusRing {
            anchors.fill: parent
            shown: search.activeFocus
            radius: parent.radius
        }
    }
    ListView {
        id: list
        objectName: "shortcutList"
        x: 24
        y: searchBox.y + searchBox.height + 16
        width: parent.width - 48
        height: Math.max(0, close.y - y - 16)
        clip: true
        activeFocusOnTab: true
        model: sheet.filtered
        currentIndex: 0
        Keys.onDownPressed: sheet.select(1)
        Keys.onUpPressed: sheet.select(-1)
        Keys.onPressed: event => {
            if (event.key === Qt.Key_PageDown || event.key === Qt.Key_PageUp) {
                sheet.select(event.key === Qt.Key_PageDown ? 5 : -5);
                event.accepted = true;
            }
        }
        delegate: Item {
            id: row
            required property var modelData
            required property int index
            width: list.width
            height: Math.max(46, description.implicitHeight + 20, keys.implicitHeight + 20)
            Rectangle {
                anchors.fill: parent
                radius: 8
                color: list.activeFocus && list.currentIndex === row.index ? LiquidPalette.flatDrop : "transparent"
            }
            Text {
                id: keys
                x: 10
                y: 10
                width: Math.min(220, parent.width * .4)
                text: row.modelData.keys
                wrapMode: Text.WrapAnywhere
                font.family: ShellPalette.uiFont
                font.pixelSize: 13
                color: LiquidPalette.accentOnLight
            }
            Text {
                id: description
                x: Math.min(220, parent.width * .4) + 20
                y: 10
                width: parent.width - x - 10
                text: row.modelData.description
                wrapMode: Text.WrapAnywhere
                font.family: ShellPalette.uiFont
                font.pixelSize: 13
                color: LiquidPalette.inkOnLight
            }
            FocusRing {
                anchors.fill: parent
                shown: list.activeFocus && list.currentIndex === row.index
                radius: 8
            }
            MouseArea {
                anchors.fill: parent
                onClicked: {
                    list.currentIndex = row.index;
                    list.forceActiveFocus();
                }
            }
        }
        Text {
            anchors.centerIn: parent
            visible: !list.count
            text: sheet.loadState === "unavailable" ? "Shortcuts could not be read." : sheet.loadState === "loading" ? "Loading shortcuts…" : "No matching shortcuts."
            color: LiquidPalette.dimOnLight
            font.family: ShellPalette.uiFont
            font.pixelSize: 14
        }
    }
    WelcomeButton {
        id: close
        objectName: "shortcutClose"
        x: sheet.width - width - 24
        y: sheet.height - height - 20
        text: "Close"
        onClicked: sheet.closeRequested()
    }
    Process {
        id: loader
        onRunningChanged: {
            if (running)
                deadline.restart();
            else
                deadline.stop();
        }
        command: [Quickshell.env("EMAKI_PYTHON") || "python3", "-B", Quickshell.shellPath("helpers/shortcuts.py")]
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    const result = JSON.parse(text);
                    sheet.rows = result.rows;
                    sheet.loadState = result.state;
                } catch (_) {
                    sheet.rows = [];
                    sheet.loadState = "unavailable";
                }
            }
        }
    }
    Timer {
        id: deadline
        interval: 3000
        onTriggered: {
            loader.signal(9);
            sheet.rows = [];
            sheet.loadState = "unavailable";
        }
    }
}
