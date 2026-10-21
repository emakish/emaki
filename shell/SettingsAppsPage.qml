// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell.Io
import "settings"

Column {
    id: page
    required property var catalog
    property var applications: []
    property var startup: []
    property var defaults: ({})
    property string error: ""
    spacing: 20
    function run(args) {
        if (request.running)
            return;
        error = "";
        request.completed = false;
        request.command = ["emaki-settings-apps"].concat(args).concat(["--json"]);
        request.running = true;
    }
    function options(role, current) {
        let result = [
            {
                label: qsTranslate("SettingsAppsPage", "System default: %1").arg(defaults[role]?.name || qsTranslate("SettingsAppsPage", "unavailable")),
                value: null
            }
        ];
        for (const app of applications) {
            if (app.roles.indexOf(role) >= 0)
                result.push({
                    label: app.name,
                    value: app.id
                });
        }
        if (current && !result.some(option => option.value === current))
            result.push({
                label: current,
                value: current
            });
        return result;
    }
    // Installed programs come from the package backend; this page never names a package manager.
    property var programs: []
    property var preview: ({})
    property string programsMessage: ""
    property bool programsFailed: false
    property bool refreshQueued: false
    property bool initialized: false
    function refreshPrograms() {
        if (programsBusy) {
            refreshQueued = true;
            return;
        }
        refreshQueued = false;
        programCommand(["list"]);
    }
    onVisibleChanged: if (visible && initialized) {
        run(["status"]);
        refreshPrograms();
    }
    Timer {
        interval: 10000
        repeat: true
        running: page.visible && !page.programsBusy
        onTriggered: page.refreshPrograms()
    }
    readonly property bool programsBusy: programRequest.running
    function programCommand(args) {
        if (programRequest.running)
            return;
        programRequest.completed = false;
        programRequest.serial += 1;
        programRequest.output = "";
        programRequest.operation = args[0];
        programRequest.command = ["emaki-programs"].concat(args).concat(["--json"]);
        programRequest.running = true;
    }
    function previewProgram(name) {
        programsMessage = "";
        preview = {
            target: name
        };
        programCommand(["preview", name]);
    }
    function removeProgram() {
        if (!preview.ok || !preview.token)
            return;
        programCommand(["remove", preview.target, preview.token]);
    }
    Component.onCompleted: {
        initialized = true;
        run(["status"]);
        programCommand(["list"]);
    }
    Connections {
        target: page.catalog
        function onWritingChanged() {
            if (!page.catalog.writing)
                page.run(["status"]);
        }
    }
    Process {
        id: request
        property bool completed: false
        property string output: ""
        onRunningChanged: {
            if (!running)
                Qt.callLater(function () {
                    if (!request.completed)
                        page.error = qsTranslate("SettingsAppsPage", "Apps could not be read. Try again.");
                });
        }
        stdout: StdioCollector {
            onStreamFinished: request.output = text
        }
        onExited: _code => {
            completed = true;
            try {
                const reply = JSON.parse(output);
                if (_code !== 0 || !reply.ok)
                    throw new Error(reply.error || qsTranslate("SettingsAppsPage", "Could not read apps."));
                page.applications = reply.applications;
                page.startup = reply.startup;
                page.defaults = reply.defaults || {};
            } catch (failure) {
                page.error = String(failure.message || failure);
            }
            output = "";
        }
    }
    Process {
        id: programRequest
        property bool completed: false
        property string output: ""
        property string operation: ""
        // A finished removal starts the list request at once: the start check below
        // belongs to one request only.
        property int serial: 0
        onRunningChanged: {
            const serial = programRequest.serial;
            if (!running)
                Qt.callLater(function () {
                    if (programRequest.serial === serial && !programRequest.completed) {
                        page.preview = {};
                        page.programsMessage = qsTranslate("SettingsAppsPage", "Installed programs could not be read. Try again later.");
                        page.programsFailed = true;
                    }
                });
        }
        stdout: StdioCollector {
            onStreamFinished: programRequest.output = text
        }
        onExited: _code => {
            completed = true;
            let reply = null;
            try {
                reply = JSON.parse(output);
            } catch (failure) {
                reply = null;
            }
            output = "";
            if (operation === "list") {
                if (_code === 0 && reply && reply.ok && Array.isArray(reply.programs)) {
                    page.programs = reply.programs;
                    if (page.programsFailed)
                        page.programsMessage = "";
                    page.programsFailed = false;
                } else {
                    page.programsFailed = true;
                    page.programsMessage = qsTranslate("SettingsAppsPage", "Installed programs could not be read. Try again later.");
                }
            } else if (operation === "preview") {
                page.preview = reply ? Object.assign({
                    target: page.preview.target
                }, reply) : {};
                if (!reply)
                    page.programsMessage = qsTranslate("SettingsAppsPage", "The removal could not be checked. Nothing was removed.");
            } else if (operation === "remove") {
                page.programsMessage = reply && reply.message ? reply.message : qsTranslate("SettingsAppsPage", "The removal did not finish as expected. Check the installed programs again.");
                // The helper passed the new list through every refusal rule; the person
                // reviews it and confirms again.
                if (reply && reply.code === "changed" && reply.token)
                    page.preview = Object.assign({
                        target: page.preview.target
                    }, reply, {
                        ok: true
                    });
                else
                    page.preview = {};
                // The list changes after any finished removal attempt.
                Qt.callLater(() => page.refreshPrograms());
            }
            if (page.refreshQueued && operation !== "remove")
                Qt.callLater(() => page.refreshPrograms());
        }
    }
    SettingsButton {
        objectName: "settings-apps-retry"
        visible: page.error !== ""
        enabled: !request.running
        text: qsTranslate("SettingsAppsPage", "Retry")
        onClicked: page.run(["status"])
    }
    Text {
        width: parent.width
        visible: page.error !== ""
        text: page.error
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        color: SettingsTheme.ink
    }
    SettingsCard {
        width: parent.width
        title: qsTranslate("SettingsAppsPage", "Default apps")
        Repeater {
            model: [
                {
                    key: "browser",
                    title: qsTranslate("SettingsAppsPage", "Web browser"),
                    explanation: qsTranslate("SettingsAppsPage", "Open web links with this app.")
                },
                {
                    key: "mail",
                    title: qsTranslate("SettingsAppsPage", "Mail"),
                    explanation: qsTranslate("SettingsAppsPage", "Open email links with this app.")
                },
                {
                    key: "files",
                    title: qsTranslate("SettingsAppsPage", "Files"),
                    explanation: qsTranslate("SettingsAppsPage", "Open folders with this app.")
                },
                {
                    key: "terminal",
                    title: qsTranslate("SettingsAppsPage", "Terminal"),
                    explanation: qsTranslate("SettingsAppsPage", "Open a terminal with this app.")
                },
                {
                    key: "editor",
                    title: qsTranslate("SettingsAppsPage", "Text editor"),
                    explanation: qsTranslate("SettingsAppsPage", "Open plain text files with this app.")
                }
            ]
            SettingsChoiceRow {
                required property var modelData
                readonly property var row: page.catalog.row("defaults." + modelData.key)
                objectName: "setting-defaults." + modelData.key
                width: parent.width
                title: modelData.title
                explanation: modelData.explanation
                value: row ? row.value : null
                defaultValue: null
                options: page.options(modelData.key, value)
                controlWidth: Math.min(280, width * .48)
                busy: page.catalog.writing || request.running
                unavailable: !row
                onValueRequested: value => value === null ? page.catalog.reset("defaults." + modelData.key) : page.catalog.set("defaults." + modelData.key, value)
                onResetRequested: page.catalog.reset("defaults." + modelData.key)
            }
        }
    }
    SettingsCard {
        width: parent.width
        objectName: "settings-startup-apps"
        title: qsTranslate("SettingsAppsPage", "Startup apps")
        Repeater {
            model: page.startup
            SettingsToggleRow {
                required property var modelData
                width: parent.width
                title: modelData.name
                explanation: qsTranslate("SettingsAppsPage", "Start this app when you sign in.")
                value: modelData.enabled
                defaultValue: true
                busy: request.running
                onValueRequested: value => page.run(["set", modelData.id, String(value)])
                onResetRequested: page.run(["reset", modelData.id])
            }
        }
        SettingsChoiceRow {
            width: parent.width
            title: qsTranslate("SettingsAppsPage", "Add a startup app")
            explanation: qsTranslate("SettingsAppsPage", "Choose an installed app to start when you sign in.")
            value: ""
            defaultValue: ""
            busy: request.running
            options: [
                {
                    label: qsTranslate("SettingsAppsPage", "Choose an app"),
                    value: ""
                }
            ].concat(page.applications.filter(app => !page.startup.some(entry => entry.id === app.id)).map(app => ({
                        label: app.name,
                        value: app.id
                    })))
            onValueRequested: value => {
                if (value)
                    page.run(["add", value]);
            }
        }
    }
    SettingsCard {
        width: parent.width
        objectName: "settings-installed-programs"
        title: qsTranslate("SettingsAppsPage", "Installed programs")
        SettingsButton {
            objectName: "settings-programs-retry"
            visible: page.programsFailed
            enabled: !page.programsBusy
            text: qsTranslate("SettingsAppsPage", "Retry")
            onClicked: page.refreshPrograms()
        }
        Text {
            width: parent.width
            topPadding: 14
            bottomPadding: 14
            visible: text !== ""
            text: page.programsMessage || (page.programs.length === 0 && !page.programsBusy ? qsTranslate("SettingsAppsPage", "No programs can be removed here.") : "")
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            color: SettingsTheme.dim
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 13
        }
        Repeater {
            model: page.programs
            Column {
                id: program
                required property var modelData
                required property int index
                readonly property bool previewing: page.preview.target === modelData.package
                width: parent.width
                SettingsRow {
                    objectName: "program-" + program.modelData.package
                    width: parent.width
                    separator: false
                    title: program.modelData.names.join(", ")
                    explanation: program.modelData.package
                    busy: page.programsBusy
                    controlWidth: 200
                    control: Component {
                        // The button keeps its text width at the row's right edge.
                        Item {
                            implicitHeight: remove.implicitHeight
                            SettingsButton {
                                id: remove
                                objectName: "program-remove"
                                anchors.right: parent.right
                                text: qsTranslate("SettingsAppsPage", "Remove…")
                                Accessible.name: text + " " + program.modelData.names.join(", ")
                                onClicked: page.previewProgram(program.modelData.package)
                            }
                        }
                    }
                }
                Column {
                    objectName: "program-preview"
                    width: parent.width
                    visible: program.previewing && page.preview.code !== undefined
                    spacing: 8
                    bottomPadding: 14
                    Text {
                        width: parent.width
                        text: page.preview.message || ""
                        textFormat: Text.PlainText
                        wrapMode: Text.WordWrap
                        color: SettingsTheme.ink
                        font.family: SettingsTheme.fontFamily
                        font.pixelSize: 13
                    }
                    Text {
                        // The full list, read before anything is removed.
                        objectName: "program-preview-packages"
                        width: parent.width
                        visible: text !== ""
                        text: (page.preview.packages || []).join("\n")
                        textFormat: Text.PlainText
                        wrapMode: Text.WrapAnywhere
                        color: SettingsTheme.ink
                        font.family: "monospace"
                        font.pixelSize: 12
                    }
                    Text {
                        width: parent.width
                        visible: text !== ""
                        text: (page.preview.requiredBy || []).length ? qsTranslate("SettingsAppsPage", "Needed by: %1").arg(page.preview.requiredBy.join(", ")) : (page.preview.blocked || []).length ? qsTranslate("SettingsAppsPage", "Kept: %1").arg(page.preview.blocked.join(", ")) : ""
                        textFormat: Text.PlainText
                        wrapMode: Text.WordWrap
                        color: SettingsTheme.dim
                        font.family: SettingsTheme.fontFamily
                        font.pixelSize: 12
                    }
                    Row {
                        spacing: 8
                        SettingsButton {
                            objectName: "program-cancel"
                            text: qsTranslate("SettingsAppsPage", "Cancel")
                            enabled: !page.programsBusy
                            onClicked: page.preview = {}
                        }
                        SettingsButton {
                            objectName: "program-confirm"
                            visible: page.preview.ok === true
                            enabled: !page.programsBusy
                            text: Translation.englishPlural(qsTranslate("SettingsAppsPage", "Remove %n package(s)", "", (page.preview.packages || []).length), (page.preview.packages || []).length)
                            onClicked: page.removeProgram()
                        }
                    }
                }
                Rectangle {
                    antialiasing: true
                    x: -18
                    width: parent.width + 34
                    height: 1
                    color: SettingsTheme.rim
                    visible: program.index < page.programs.length - 1
                }
            }
        }
    }
}
