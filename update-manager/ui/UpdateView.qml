// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as C
import "@EMAKI_DATADIR@/shell" as Shell

Rectangle {
    id: view
    required property var controller
    signal hideRequested
    signal openNews(string link)
    color: Shell.LiquidPalette.flatPanel
    radius: 18
    readonly property color ink: Shell.LiquidPalette.inkOnLight
    function size(bytes: var): string {
        return bytes === null || bytes === undefined ? "Size unknown" : (bytes / 1048576).toFixed(1) + " MiB";
    }
    property bool followTail: true
    property bool positioning: false
    property bool detailsOpen: false
    Timer {
        id: initialPosition
        interval: 0
        running: true
        onTriggered: if (view.controller.phase === "applying") {
            view.followTail = true;
            view.scrollTo((body.contentItem as Flickable).contentHeight);
        }
    }
    Timer {
        id: tailPosition
        interval: 0
        onTriggered: if (view.followTail) view.scrollTo((body.contentItem as Flickable).contentHeight)
    }
    Timer {
        id: topPosition
        interval: 0
        onTriggered: view.scrollTo(0)
    }
    function present(): void {
        if (controller.phase === "finished") topPosition.restart();
        else if (controller.phase === "applying" && followTail) tailPosition.restart();
    }
    function localTime(value: string): string {
        const date = new Date(value);
        return isNaN(date.getTime()) ? value : date.toLocaleString(Qt.locale(), Locale.ShortFormat);
    }
    function scrollTo(position: real): void {
        const flick = body.contentItem as Flickable;
        positioning = true;
        flick.contentY = Math.max(0, Math.min(Math.max(0, flick.contentHeight - flick.height), position));
        positioning = false;
    }
    function scrollKey(event: var): void {
        const flick = body.contentItem as Flickable;
        let position = flick.contentY;
        if (event.key === Qt.Key_Up) position -= 40;
        else if (event.key === Qt.Key_Down) position += 40;
        else if (event.key === Qt.Key_PageUp) position -= flick.height * 0.9;
        else if (event.key === Qt.Key_PageDown) position += flick.height * 0.9;
        else if (event.key === Qt.Key_Home) position = 0;
        else if (event.key === Qt.Key_End) position = flick.contentHeight;
        else return;
        scrollTo(position);
        followTail = flick.contentY >= Math.max(0, flick.contentHeight - flick.height) - 2;
        event.accepted = true;
    }
    Keys.onPressed: event => {
        if (event.key === Qt.Key_Escape) {
            if (controller.phase !== "applying") hideRequested();
            event.accepted = true;
        } else scrollKey(event);
    }
    function reveal(item: Item): void {
        const flick = body.contentItem as Flickable;
        let ancestor = item.parent;
        while (ancestor && ancestor !== flick.contentItem) ancestor = ancestor.parent;
        if (!ancestor) return;
        const rect = item.mapToItem(flick.contentItem, 0, 0, item.width, item.height);
        if (rect.y < flick.contentY) scrollTo(rect.y - 8);
        else if (rect.y + rect.height > flick.contentY + flick.height)
            scrollTo(rect.y + rect.height - flick.height + 8);
    }
    component Label: Text {
        color: view.ink
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        font.pixelSize: 15
        Layout.fillWidth: true
    }
    component Action: C.Button {
        id: action
        focusPolicy: Qt.StrongFocus
        Keys.onReturnPressed: if (enabled) clicked()
        Keys.onEnterPressed: if (enabled) clicked()
        onActiveFocusChanged: if (activeFocus) view.reveal(action)
        contentItem: Text {
            text: action.text
            textFormat: Text.PlainText
            color: view.ink
            wrapMode: Text.Wrap
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        padding: 12
        background: Rectangle {
            radius: 10
            color: action.down ? Shell.ShellPalette.accent : Shell.LiquidPalette.flatDrop
            border.color: action.activeFocus ? Shell.LiquidPalette.accentOnLight : "transparent"
            border.width: 2
            opacity: action.enabled ? 1 : 0.4
        }
    }
    Connections {
        target: view.controller
        function onPhaseChanged(): void {
            view.followTail = true;
            view.detailsOpen = false;
            topPosition.restart();
            body.forceActiveFocus();
        }
    }
    Connections {
        target: body.contentItem
        function onContentYChanged(): void {
            const flick = body.contentItem as Flickable;
            if (!view.positioning)
                view.followTail = flick.contentY >= Math.max(0, flick.contentHeight - flick.height) - 2;
        }
        function onContentHeightChanged(): void {
            if (view.controller.phase === "applying" && view.followTail)
                tailPosition.restart();
        }
    }
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 14
        Label { text: "Emaki updates"; font.pixelSize: 28; font.bold: true }
        Label { text: "Review the changes before updating." }
        C.ScrollView {
            id: body
            objectName: "updateBody"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            focusPolicy: Qt.StrongFocus
            Keys.priority: Keys.BeforeItem
            Keys.onPressed: event => view.scrollKey(event)
            contentWidth: availableWidth
            ColumnLayout {
                width: body.availableWidth
                spacing: 12
                Label { visible: view.controller.busy; text: view.controller.phase === "applying" ? "Updating… You can hide this window and return to it later." : "Working…" }
                C.ProgressBar {
                    id: progress
                    Layout.fillWidth: true
                    implicitHeight: 8
                    visible: view.controller.busy
                    indeterminate: true
                    padding: 0
                    background: Rectangle { color: Shell.LiquidPalette.flatDrop; radius: 4 }
                    contentItem: Item {
                        clip: true
                        Rectangle {
                            id: band
                            width: progress.availableWidth / 4
                            height: progress.availableHeight
                            radius: 4
                            color: Shell.ShellPalette.accent
                            NumberAnimation on x {
                                from: -band.width
                                to: progress.availableWidth
                                duration: 1200
                                loops: Animation.Infinite
                                running: progress.visible
                            }
                        }
                    }
                }
                Label { objectName: "resultMessage"; visible: text.length > 0; text: view.controller.message }
                Label { visible: view.controller.signOut; text: "Sign out and sign back in to use the updated Emaki session." }
                Label { visible: view.controller.restart; text: "A kernel update needs a restart." }
                ColumnLayout {
                    objectName: "updateCatalog"
                    Layout.fillWidth: true
                    visible: !view.controller.busy && view.controller.phase !== "finished"
                    spacing: 12
                    Label { text: view.controller.data.checkedAt ? "Last checked: " + view.localTime(view.controller.data.checkedAt) : "No update check yet." }
                    Label { text: "Repository download: " + view.size(view.controller.data.downloadSize) }
                    Repeater {
                        model: (view.controller.data.warnings || []).filter((warning, index, warnings) => warnings.indexOf(warning) === index)
                        Label { required property string modelData; text: modelData; font.bold: true }
                    }
                    Repeater {
                        model: view.controller.data.groups || []
                        ColumnLayout {
                            id: group
                            required property var modelData
                            Layout.fillWidth: true
                            Label { text: group.modelData.name + " (" + group.modelData.count + ")"; font.bold: true; font.pixelSize: 18 }
                            Repeater {
                                model: (view.controller.data.updates || []).filter(row => row.source === group.modelData.name)
                                Label {
                                    required property var modelData
                                    text: modelData.name + "   " + modelData.old + " → " + modelData.new + "   ·   " + view.size(modelData.downloadSize)
                                }
                            }
                        }
                    }
                    Label { visible: (view.controller.data.news || []).length > 0; text: "Arch news — manual intervention"; font.bold: true }
                    Repeater {
                        model: view.controller.data.news || []
                        Action {
                            required property var modelData
                            required property int index
                            objectName: "news" + index
                            Layout.fillWidth: true
                            text: modelData.title
                            onClicked: if (/^https:\/\/archlinux\.org\/news\//.test(modelData.link)) view.openNews(modelData.link)
                        }
                    }
                }
                Action {
                    objectName: "detailsToggle"
                    visible: !!view.controller.data.errorDetails
                    text: view.detailsOpen ? "Hide details" : "Show details"
                    onClicked: view.detailsOpen = !view.detailsOpen
                }
                Label { objectName: "errorDetails"; visible: view.detailsOpen && text.length > 0; text: view.controller.data.errorDetails || "" }
                Label { objectName: "progressOutput"; visible: text.length > 0; text: view.controller.output; font.family: "monospace"; font.pixelSize: 12 }
            }
        }
        GridLayout {
            columns: view.width < 780 ? 2 : 4
            Layout.fillWidth: true
            Action { objectName: "check"; text: "Check again"; enabled: !view.controller.busy; onClicked: view.controller.check() }
            Action { objectName: "apply"; text: "Update repositories"; enabled: !view.controller.busy && view.controller.repositoryCount > 0 && !view.controller.data.error && view.controller.phase !== "finished"; onClicked: view.controller.apply() }
            Action { objectName: "restart"; text: "Restart now"; visible: view.controller.restart; enabled: !view.controller.busy; onClicked: view.controller.reboot() }
            Action { objectName: "hide"; text: view.controller.restart ? "Later" : "Hide"; onClicked: view.hideRequested() }
        }
    }
}
