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
                label: "System default: " + (defaults[role]?.name || "unavailable"),
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
    Component.onCompleted: run(["status"])
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
                        page.error = "Apps could not be read. Try again when the settings helper is available.";
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
                    throw new Error(reply.error || "Could not read apps.");
                page.applications = reply.applications;
                page.startup = reply.startup;
                page.defaults = reply.defaults || {};
            } catch (failure) {
                page.error = String(failure.message || failure);
            }
            output = "";
        }
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
        title: "Default apps"
        Repeater {
            model: [
                {
                    key: "browser",
                    title: "Web browser",
                    explanation: "Open web links with this app."
                },
                {
                    key: "mail",
                    title: "Mail",
                    explanation: "Open email links with this app."
                },
                {
                    key: "files",
                    title: "Files",
                    explanation: "Open folders with this app."
                },
                {
                    key: "terminal",
                    title: "Terminal",
                    explanation: "Open a terminal with this app."
                },
                {
                    key: "editor",
                    title: "Text editor",
                    explanation: "Open plain text files with this app."
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
        title: "Startup apps"
        Repeater {
            model: page.startup
            SettingsToggleRow {
                required property var modelData
                width: parent.width
                title: modelData.name
                explanation: "Start this app when you sign in."
                value: modelData.enabled
                defaultValue: true
                busy: request.running
                onValueRequested: value => page.run(["set", modelData.id, String(value)])
                onResetRequested: page.run(["reset", modelData.id])
            }
        }
        SettingsChoiceRow {
            width: parent.width
            title: "Add a startup app"
            explanation: "Choose an installed app to start when you sign in."
            value: ""
            defaultValue: ""
            busy: request.running
            options: [
                {
                    label: "Choose an app",
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
}
