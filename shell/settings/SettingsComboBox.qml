// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window as WindowTypes
import QtQuick.Controls

ComboBox {
    id: choice
    textRole: "label"
    valueRole: "value"
    focusPolicy: Qt.StrongFocus
    hoverEnabled: true
    font.family: SettingsTheme.fontFamily
    font.pixelSize: 13
    contentItem: Text {
        textFormat: Text.PlainText
        leftPadding: 13
        rightPadding: 26
        text: choice.displayText
        font: choice.font
        color: SettingsTheme.ink
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    indicator: Canvas {
        id: arrow
        x: choice.width - width - 10
        y: (choice.height - height) / 2
        width: 16
        height: 16
        readonly property url source: Qt.resolvedUrl("icons/down.svg")
        readonly property color ink: SettingsTheme.dim
        Component.onCompleted: loadImage(source)
        onImageLoaded: requestPaint()
        onInkChanged: requestPaint()
        onPaint: {
            if (!isImageLoaded(source))
                return;
            const context = getContext("2d");
            context.reset();
            context.drawImage(source, 0, 0, width, height);
            context.globalCompositeOperation = "source-in";
            context.fillStyle = ink;
            context.fillRect(0, 0, width, height);
        }
    }
    ToolTip.visible: hovered || activeFocus
    ToolTip.text: displayText
    ToolTip.delay: 600
    background: Rectangle {
        antialiasing: true
        implicitHeight: 34
        radius: 10
        color: SettingsTheme.field
        border.width: choice.activeFocus ? 2 : 1
        border.color: choice.activeFocus ? SettingsTheme.accent : SettingsTheme.rim
    }
    delegate: ItemDelegate {
        id: optionDelegate
        required property var modelData
        required property int index
        highlighted: choice.highlightedIndex === index
        width: choice.width
        text: modelData[choice.textRole]
        font: choice.font
        palette.text: SettingsTheme.ink
        contentItem: Text {
            text: optionDelegate.text
            textFormat: Text.PlainText
            font: optionDelegate.font
            color: SettingsTheme.ink
            wrapMode: Text.Wrap
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            antialiasing: true
            color: optionDelegate.highlighted ? SettingsTheme.rim : "#fffaf6"
        }
    }
    popup: Popup {
        y: choice.height
        width: choice.width
        margins: 8
        padding: 1
        implicitHeight: Math.min(contentItem.implicitHeight + 2, Math.max(0, (choice.WindowTypes.Window.window?.height ?? 480) - 16))
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: choice.popup.visible ? choice.delegateModel : null
            currentIndex: choice.highlightedIndex
            highlightRangeMode: ListView.ApplyRange
            highlightMoveDuration: 0
            ScrollBar.vertical: ScrollBar {}
            onCurrentIndexChanged: positionViewAtIndex(currentIndex, ListView.Contain)
            onCountChanged: positionViewAtIndex(currentIndex, ListView.Contain)
        }
        background: Rectangle {
            antialiasing: true
            radius: 10
            color: "#fffaf6"
            border.color: SettingsTheme.rim
        }
    }
}
