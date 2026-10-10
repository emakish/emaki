// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell.Io
import "settings"

Column {
    id: page
    spacing: 20
    property var details: ({})
    property string reportPath: ""
    property string message: ""
    property bool received: false
    signal updatesRequested
    function start(action) {
        if (backend.running)
            return;
        received = false;
        message = "";
        backend.command = ["emaki-settings-about", action, "--json"];
        backend.running = true;
    }
    Component.onCompleted: start("status")
    Process {
        id: backend
        onRunningChanged: {
            if (!running)
                Qt.callLater(function () {
                    if (!page.received)
                        page.message = "System details or the report could not be read. Try again.";
                });
        }
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    const reply = JSON.parse(text);
                    if (reply.schema_version !== 1 || !["ready", "saved"].includes(reply.status) || !reply.details)
                        throw new Error();
                    page.received = true;
                    page.details = reply.details;
                    if (reply.status === "saved" && typeof reply.path === "string") {
                        page.reportPath = reply.path;
                        page.message = "Report saved. Review it before sharing; it may contain private information. Nothing has been uploaded.";
                    }
                } catch (error) {
                    page.message = "System details or the report could not be read. Try again.";
                }
            }
        }
        stderr: StdioCollector {}
        onExited: _code => {
            if (!page.received)
                page.message = "System details or the report could not be read. Try again.";
        }
    }
    Process {
        id: viewer
        property bool completed: false
        onRunningChanged: {
            if (!running)
                Qt.callLater(function () {
                    if (!viewer.completed)
                        page.message = "The report could not be opened. You can find it at the path below.";
                });
        }
        stderr: StdioCollector {}
        onExited: code => {
            completed = true;
            if (code !== 0)
                page.message = "The report could not be opened. You can find it at the path below.";
        }
    }
    SettingsCard {
        width: parent.width
        Column {
            width: parent.width
            spacing: 12
            topPadding: 18
            bottomPadding: 18
            Image {
                anchors.horizontalCenter: parent.horizontalCenter
                width: 200
                height: 48
                source: "file://" + Platform.dataDir + "/fetch/wordmark.png"
                fillMode: Image.PreserveAspectFit
                Accessible.name: "Emaki"
            }
            Text {
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                objectName: "settings-about-version"
                text: "Version " + (page.details.version || "Unavailable") + (page.details.label ? " " + page.details.label : "") + " · " + (page.details.channel || "Unavailable") + " channel"
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                color: SettingsTheme.dim
                font.family: SettingsTheme.fontFamily
                font.pixelSize: 14
            }
            SettingsButton {
                anchors.horizontalCenter: parent.horizontalCenter
                text: "Update channel"
                onClicked: page.updatesRequested()
            }
        }
    }
    SettingsCard {
        width: parent.width
        title: "This computer"
        objectName: "settings-about-hardware"
        Repeater {
            model: [
                {
                    key: "computer",
                    title: "Computer",
                    explanation: "The manufacturer and model of this computer."
                },
                {
                    key: "processor",
                    title: "Processor",
                    explanation: "The processor reported by the system."
                },
                {
                    key: "graphics",
                    title: "Graphics",
                    explanation: "The graphics devices reported by the system."
                },
                {
                    key: "memory",
                    title: "Memory",
                    explanation: "The memory available to the system."
                },
                {
                    key: "disk",
                    title: "Disk",
                    explanation: "Total and free space on the system disk."
                },
                {
                    key: "firmware",
                    title: "Firmware",
                    explanation: "The firmware vendor and version."
                }
            ]
            delegate: Column {
                id: detailRow
                required property var modelData
                width: parent.width
                spacing: 3
                topPadding: 12
                bottomPadding: 12
                Row {
                    width: parent.width
                    spacing: 16
                    Text {
                        width: (parent.width - 16) * .3
                        text: detailRow.modelData.title
                        color: SettingsTheme.ink
                        font.family: SettingsTheme.fontFamily
                        font.pixelSize: 14
                        font.weight: Font.Medium
                        wrapMode: Text.WordWrap
                    }
                    Text {
                        width: (parent.width - 16) * .7
                        text: page.details[detailRow.modelData.key] || "Unavailable"
                        textFormat: Text.PlainText
                        horizontalAlignment: Text.AlignRight
                        wrapMode: Text.WordWrap
                        color: SettingsTheme.ink
                        font.family: SettingsTheme.fontFamily
                        font.pixelSize: 14
                    }
                }
                Text {
                    width: parent.width
                    text: parent.modelData.explanation
                    wrapMode: Text.WordWrap
                    color: SettingsTheme.dim
                    font.family: SettingsTheme.fontFamily
                    font.pixelSize: 13
                }
            }
        }
    }
    SettingsCard {
        width: parent.width
        title: "Software"
        Repeater {
            model: [
                {
                    key: "kernel",
                    title: "Linux kernel"
                },
                {
                    key: "window_manager",
                    title: "Window manager"
                }
            ]
            delegate: Row {
                id: softwareRow
                required property var modelData
                width: parent.width
                topPadding: 12
                bottomPadding: 12
                spacing: 16
                Text {
                    width: (parent.width - 16) * .3
                    text: softwareRow.modelData.title
                    color: SettingsTheme.ink
                    font.family: SettingsTheme.fontFamily
                    font.pixelSize: 14
                    font.weight: Font.Medium
                    wrapMode: Text.WordWrap
                }
                Text {
                    width: (parent.width - 16) * .7
                    text: page.details[softwareRow.modelData.key] || "Unavailable"
                    textFormat: Text.PlainText
                    horizontalAlignment: Text.AlignRight
                    wrapMode: Text.WordWrap
                    color: SettingsTheme.ink
                    font.family: SettingsTheme.fontFamily
                    font.pixelSize: 14
                }
            }
        }
    }
    SettingsCard {
        width: parent.width
        title: "Help"
        SettingsRow {
            width: parent.width
            objectName: "settings-report-problem"
            title: "Report a problem"
            explanation: "Collect logs and a short system summary into a file you can review before sharing."
            controlWidth: 160
            busy: backend.running
            control: Component {
                SettingsButton {
                    text: "Report a problem…"
                    onClicked: page.start("report")
                }
            }
        }
        Text {
            width: parent.width
            visible: text !== ""
            text: page.message
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            color: SettingsTheme.dim
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 13
        }
        Text {
            width: parent.width
            visible: text !== ""
            text: page.reportPath
            textFormat: Text.PlainText
            wrapMode: Text.WrapAnywhere
            color: SettingsTheme.dim
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 12
        }
        SettingsButton {
            visible: page.reportPath !== ""
            objectName: "review-report"
            text: "Review report"
            enabled: !viewer.running
            onClicked: {
                viewer.completed = false;
                viewer.command = ["xdg-open", page.reportPath];
                viewer.running = true;
            }
        }
    }
}
