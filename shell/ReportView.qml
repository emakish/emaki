// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import "settings"
import "Keyboard.js" as Keyboard

// The report window's content: the person's words, the attached items, what was removed
// for privacy, the exact preview, and Send. Only Send hands anything to the browser.
FocusScope {
    id: view
    readonly property bool keyboardBoundary: true
    Keys.onPressed: event => Keyboard.handle(view, event)
    function editorKey(editor, event): void {
        if (event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab || (event.key === Qt.Key_Up && editor.cursorPosition === 0) || (event.key === Qt.Key_Down && editor.cursorPosition === editor.length))
            Keyboard.handle(view, event);
    }
    required property var controller
    property string wallpaperTexture: ""
    property real dpr: 1
    signal closeRequested
    implicitWidth: 720
    implicitHeight: 700
    focus: true
    readonly property string subject: controller.part === "shell" ? "About the panel that stopped." : controller.part === "update" ? "About the system update that did not finish." : controller.part === "install" ? "About the program that could not be installed." : "Describe the problem; the details below help to find its cause."
    function takeFocus(): void {
        whatField.forceActiveFocus();
    }
    // Keep the focused control visible while moving through the window with the keyboard.
    function reveal(item: Item): void {
        const top = item.mapToItem(content, 0, 0).y;
        if (top < scroller.contentY)
            scroller.contentY = Math.max(0, top - 16);
        else if (top + item.height > scroller.contentY + scroller.height)
            scroller.contentY = Math.min(scroller.contentHeight - scroller.height, top + item.height - scroller.height + 16);
    }
    function removedText(removed: var): string {
        const names = {
            user: ["user name", "user names"],
            host: ["computer name", "computer names"],
            email: ["e-mail address", "e-mail addresses"],
            ip: ["IP address", "IP addresses"],
            mac: ["MAC address", "MAC addresses"],
            serial: ["serial number", "serial numbers"],
            secret: ["password or token", "passwords or tokens"]
        };
        const parts = [];
        for (const key of Object.keys(names)) {
            const count = Number(removed?.[key] ?? 0);
            if (count > 0)
                parts.push(count + " " + names[key][count === 1 ? 0 : 1]);
        }
        return parts.length ? "Removed for privacy before you see it: " + parts.join(", ") + "." : "Your user name, computer name, e-mail and network addresses, serial numbers, passwords and tokens are removed before you see the report. None were found.";
    }
    Keys.onEscapePressed: event => {
        if (view.controller.previewOpen)
            view.controller.togglePreview();
        else
            view.closeRequested();
        event.accepted = true;
    }
    Shortcut {
        sequences: ["Ctrl+Return", "Ctrl+Enter"]
        onActivated: view.controller.send()
    }
    SettingsGlass {
        anchors.fill: parent
        wallpaperTexture: view.wallpaperTexture
        dpr: view.dpr
    }
    Flickable {
        id: scroller
        anchors.fill: parent
        anchors.margins: 28
        anchors.bottomMargin: footer.height + 40
        clip: true
        contentWidth: width
        contentHeight: content.implicitHeight
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar {}
        Column {
            id: content
            width: scroller.width
            spacing: 18
            SettingsPageHeader {
                width: parent.width
                title: "Report a problem"
                explanation: view.subject
            }
            SettingsCard {
                width: parent.width
                title: "What happened?"
                ScrollView {
                    width: parent.width
                    height: 112
                    TextArea {
                        id: whatField
                        objectName: "report-what"
                        text: view.controller.what
                        placeholderText: "What you were doing, and what went wrong."
                        placeholderTextColor: SettingsTheme.faint
                        wrapMode: TextArea.Wrap
                        selectByMouse: true
                        color: SettingsTheme.ink
                        font.family: SettingsTheme.fontFamily
                        font.pixelSize: 14
                        Accessible.name: "What happened?"
                        // The field itself holds no more than the report takes, so
                        // nothing shown here is silently left out.
                        onTextChanged: {
                            if (length > view.controller.whatLimit)
                                remove(view.controller.whatLimit, length);
                            else
                                view.controller.setWhat(text);
                        }
                        onActiveFocusChanged: if (activeFocus)
                            view.reveal(parent)
                        // Tab moves on through the window; it does not type a tab.
                        Keys.onPressed: event => view.editorKey(whatField, event)
                        background: Rectangle {
                            radius: 10
                            color: SettingsTheme.field
                            border.width: whatField.activeFocus ? 2 : 1
                            border.color: whatField.activeFocus ? SettingsTheme.accent : SettingsTheme.rim
                        }
                    }
                }
                Text {
                    objectName: "report-what-limit"
                    width: parent.width
                    horizontalAlignment: Text.AlignRight
                    text: view.controller.what.length + " / " + view.controller.whatLimit
                    textFormat: Text.PlainText
                    color: view.controller.what.length >= view.controller.whatLimit ? SettingsTheme.ink : SettingsTheme.faint
                    font.family: SettingsTheme.fontFamily
                    font.pixelSize: 12
                    Accessible.name: "Characters used: " + text
                }
            }
            SettingsCard {
                width: parent.width
                title: "Attached"
                Text {
                    width: parent.width
                    visible: view.controller.state !== "ready"
                    text: view.controller.state === "composing" ? "Collecting the details…" : view.controller.message
                    textFormat: Text.PlainText
                    wrapMode: Text.WordWrap
                    color: SettingsTheme.dim
                    font.family: SettingsTheme.fontFamily
                    font.pixelSize: 13
                }
                SettingsButton {
                    visible: view.controller.state === "failed"
                    objectName: "report-retry"
                    text: "Try again"
                    onClicked: view.controller.compose(view.controller.part, false)
                }
                Repeater {
                    model: view.controller.items
                    delegate: AbstractButton {
                        id: itemRow
                        required property var modelData
                        readonly property bool ticked: view.controller.chosen[modelData.id] === true
                        objectName: "report-item-" + modelData.id
                        width: parent.width
                        implicitHeight: itemColumn.implicitHeight + 20
                        focusPolicy: Qt.StrongFocus
                        Accessible.role: Accessible.CheckBox
                        Accessible.name: modelData.label
                        Accessible.checked: ticked
                        onClicked: view.controller.setChosen(modelData.id, !ticked)
                        onActiveFocusChanged: if (activeFocus)
                            view.reveal(itemRow)
                        Keys.onReturnPressed: view.controller.setChosen(modelData.id, !ticked)
                        Keys.onEnterPressed: view.controller.setChosen(modelData.id, !ticked)
                        background: Rectangle {
                            radius: 10
                            color: itemRow.hovered ? SettingsTheme.drop : "transparent"
                            border.width: itemRow.activeFocus ? 2 : 0
                            border.color: SettingsTheme.accent
                        }
                        contentItem: Item {}
                        Rectangle {
                            id: box
                            x: 10
                            y: 12
                            width: 20
                            height: 20
                            radius: 6
                            color: itemRow.ticked ? SettingsTheme.accent : SettingsTheme.field
                            border.width: 1
                            border.color: itemRow.ticked ? SettingsTheme.accentInk : SettingsTheme.rim
                            Text {
                                anchors.centerIn: parent
                                visible: itemRow.ticked
                                text: "✓"
                                textFormat: Text.PlainText
                                color: SettingsTheme.accentText
                                font.pixelSize: 14
                                font.weight: Font.Bold
                            }
                        }
                        Column {
                            id: itemColumn
                            x: 42
                            y: 10
                            width: parent.width - 52
                            spacing: 2
                            Text {
                                width: parent.width
                                text: itemRow.modelData.label
                                textFormat: Text.PlainText
                                wrapMode: Text.WordWrap
                                color: SettingsTheme.ink
                                font.family: SettingsTheme.fontFamily
                                font.pixelSize: 14
                                font.weight: Font.Medium
                            }
                            Text {
                                width: parent.width
                                text: itemRow.modelData.text.split("\n")[0]
                                textFormat: Text.PlainText
                                elide: Text.ElideRight
                                color: SettingsTheme.dim
                                font.family: SettingsTheme.fontFamily
                                font.pixelSize: 12
                            }
                        }
                    }
                }
                Text {
                    width: parent.width
                    visible: view.controller.state === "ready"
                    objectName: "report-removed"
                    topPadding: 8
                    text: view.removedText(view.controller.report?.removed)
                    textFormat: Text.PlainText
                    wrapMode: Text.WordWrap
                    color: SettingsTheme.dim
                    font.family: SettingsTheme.fontFamily
                    font.pixelSize: 13
                }
            }
            SettingsButton {
                id: previewButton
                objectName: "report-preview"
                text: view.controller.previewOpen ? "Hide what will be sent" : "See what will be sent"
                enabled: view.controller.state === "ready"
                onClicked: view.controller.togglePreview()
                onActiveFocusChanged: if (activeFocus)
                    view.reveal(previewButton)
            }
            SettingsCard {
                width: parent.width
                visible: view.controller.previewOpen
                title: "What will be sent"
                // An older preview stays dimmed until the new one replaces it.
                Text {
                    width: parent.width
                    visible: view.controller.previewStale
                    objectName: "report-preview-updating"
                    text: "Updating the preview…"
                    textFormat: Text.PlainText
                    color: SettingsTheme.dim
                    font.family: SettingsTheme.fontFamily
                    font.pixelSize: 12
                }
                TextEdit {
                    id: previewText
                    opacity: view.controller.previewStale ? 0.4 : 1
                    Keys.onPressed: event => view.editorKey(previewText, event)
                    objectName: "report-preview-text"
                    width: parent.width
                    text: view.controller.preview
                    readOnly: true
                    selectByMouse: true
                    activeFocusOnTab: view.controller.previewOpen
                    textFormat: TextEdit.PlainText
                    wrapMode: TextEdit.WrapAnywhere
                    color: SettingsTheme.ink
                    font.family: "monospace"
                    font.pixelSize: 12
                    Accessible.name: "What will be sent"
                    onActiveFocusChanged: if (activeFocus)
                        view.reveal(previewText)
                }
            }
        }
    }
    Column {
        id: footer
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 28
        spacing: 10
        Text {
            width: parent.width
            visible: text !== ""
            objectName: "report-message"
            text: view.controller.state === "failed" ? "" : view.controller.message
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            color: SettingsTheme.ink
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 13
        }
        Row {
            anchors.right: parent.right
            spacing: 10
            Text {
                anchors.verticalCenter: parent.verticalCenter
                width: Math.max(0, footer.width - closeButton.width - sendButton.width - 20)
                text: "Nothing leaves this computer until you choose Send. Send opens the issue form on GitHub; it is posted only when you submit it there."
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                color: SettingsTheme.dim
                font.family: SettingsTheme.fontFamily
                font.pixelSize: 12
            }
            SettingsButton {
                id: closeButton
                objectName: "report-close"
                text: view.controller.sent ? "Close" : "Cancel"
                onClicked: view.closeRequested()
            }
            SettingsButton {
                id: sendButton
                objectName: "report-send"
                text: "Send"
                enabled: view.controller.state === "ready" && !view.controller.busy
                onClicked: view.controller.send()
                background: Rectangle {
                    antialiasing: true
                    radius: 11
                    color: sendButton.down || sendButton.hovered ? SettingsTheme.accentInk : SettingsTheme.accent
                    border.width: sendButton.activeFocus ? 2 : 0
                    border.color: SettingsTheme.ink
                }
            }
        }
    }
}
