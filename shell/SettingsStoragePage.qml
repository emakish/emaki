// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import Quickshell.Io
import "settings"

Column {
    id: root
    spacing: 16
    property var inventory: ({
            filesystems: [],
            categories: {}
        })
    property var preview: null
    property bool pending: false
    property string message: ""
    property string reply: ""
    property string operationMessage: ""
    property string operation: ""
    property string operationKind: ""
    property bool operationAuthenticated: false
    property var personalTrash: null
    readonly property var kinds: ["snapshots", "cache", "logs", "trash"]
    readonly property var labels: ({
            snapshots: "Snapshots",
            cache: "Package cache",
            logs: "Logs",
            trash: "Trash"
        })
    function bytes(value): string {
        return value === null || value === undefined ? "Unknown" : value.toLocaleString(Qt.locale("en_US"), "f", 0) + " B";
    }
    function unsharedBytes(details): string {
        if (details.reclaimable_bytes === null && number(details.reclaimable_lower_bound_bytes) && number(details.reclaimable_unknown_files))
            return "At least " + bytes(details.reclaimable_lower_bound_bytes) + "; " + details.reclaimable_unknown_files + " files unknown";
        return bytes(details.reclaimable_bytes);
    }
    function number(value): bool {
        return typeof value === "number" && isFinite(value) && value >= 0;
    }
    function optionalNumber(value): bool {
        return value === null || number(value);
    }
    function cancelPreview(): void {
        if (!pending)
            preview = null;
    }
    function refresh(authenticated): void {
        run("status", "", "", authenticated === true);
    }
    function requestPreview(kind): void {
        if (["cache", "logs", "trash"].includes(kind))
            run("preview", kind, "", kind !== "trash");
    }
    function confirmPreview(): void {
        if (preview && !pending)
            run("cleanup", preview.kind, preview.token, preview.kind !== "trash");
    }
    function run(action, kind, token, authenticated): void {
        if (pending)
            return;
        preview = null;
        if (action === "status")
            inventory = {
                filesystems: [],
                categories: {}
            };
        operationMessage = "";
        operation = action;
        operationKind = kind;
        operationAuthenticated = authenticated;
        if ((action === "status" && !authenticated) || (action === "cleanup" && kind === "trash"))
            personalTrash = null;
        message = "";
        reply = "";
        let args = [action];
        if (kind)
            args.push(kind);
        if (token)
            args.push(token);
        args.push("--json");
        worker.command = authenticated ? ["pkexec", Platform.binDir + "/emaki-settings-storage"].concat(args) : ["emaki-settings-storage"].concat(args);
        worker.completed = false;
        pending = true;
        worker.running = true;
    }
    function acceptReply(code): void {
        pending = false;
        try {
            const result = JSON.parse(reply);
            if (code !== 126 && result.schema_version === 1 && result.status === "error" && typeof result.message === "string" && result.message.length > 0) {
                preview = null;
                message = operationMessage + result.message;
                return;
            }
            if (result.schema_version !== 1 || code !== 0 || result.status === "error")
                throw new Error();
            if (operation === "status") {
                if (result.status !== "ready" || !Array.isArray(result.filesystems) || !result.categories || !result.filesystems.every(row => typeof row.path === "string" && typeof row.filesystem === "string" && optionalNumber(row.total_bytes) && optionalNumber(row.used_bytes) && optionalNumber(row.available_bytes)) || !kinds.every(kind => result.categories[kind] && optionalNumber(result.categories[kind].bytes) && typeof result.categories[kind].reason === "string"))
                    throw new Error();
                if (operationAuthenticated)
                    result.categories.trash = personalTrash || {
                        bytes: null,
                        reason: ""
                    };
                else
                    personalTrash = result.categories.trash;
                inventory = result;
            } else if (operation === "preview") {
                if (result.status !== "preview" || result.kind !== operationKind || !/^[a-f0-9]{64}$/.test(result.token) || !Array.isArray(result.entries) || !result.entries.every(row => typeof row.path === "string" && row.path.startsWith("/") && number(row.bytes)) || !number(result.allocated_bytes) || !optionalNumber(result.reclaimable_bytes) || typeof result.reason !== "string" || ((result.reclaimable_lower_bound_bytes !== undefined || result.reclaimable_unknown_files !== undefined) && (!number(result.reclaimable_lower_bound_bytes) || !Number.isInteger(result.reclaimable_unknown_files) || result.reclaimable_unknown_files < 0)))
                    throw new Error();
                preview = result;
            } else {
                if (result.status !== "cleaned" || result.kind !== operationKind || !number(result.removed_count) || !optionalNumber(result.reclaimed_bytes))
                    throw new Error();
                inventory = {
                    filesystems: [],
                    categories: {}
                };
            }
            message = operationMessage + (typeof result.message === "string" ? result.message : "");
            if (operation === "cleanup") {
                message += "\nRemoved entries: " + result.removed_count + "\nSpace returned: " + bytes(result.reclaimed_bytes);
                const completionMessage = message;
                pending = true;
                Qt.callLater(function () {
                    root.pending = false;
                    root.refresh(false);
                    root.operationMessage = completionMessage + "\n";
                });
            }
        } catch (_) {
            preview = null;
            message = operationMessage + (code === 126 ? "Authentication was cancelled. Preview again to continue." : "Storage could not be confirmed. Refresh and preview again.");
        }
    }
    Component.onCompleted: refresh(false)
    Process {
        id: worker
        property bool completed: false
        stdout: StdioCollector {
            onStreamFinished: root.reply = text
        }
        onRunningChanged: {
            if (!running)
                Qt.callLater(function () {
                    if (!worker.completed) {
                        root.pending = false;
                        root.preview = null;
                        root.message = "The storage helper could not be started.";
                    }
                });
        }
        onExited: code => {
            completed = true;
            root.acceptReply(code);
        }
    }
    Text {
        width: parent.width
        visible: root.pending || text !== ""
        text: root.pending ? "Reading storage…" : root.message
        textFormat: Text.PlainText
        wrapMode: Text.WrapAnywhere
        color: SettingsTheme.dim
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 13
    }
    SettingsCard {
        width: parent.width
        title: "Disk use"
        SettingsRow {
            objectName: "settings-storage-disks"
            width: parent.width
            title: "System and home"
            explanation: "Shared filesystem totals describe the same pool; do not add them together."
            busy: root.pending
            control: Component {
                SettingsButton {
                    text: "Refresh"
                    onClicked: root.refresh(false)
                }
            }
            controlWidth: 180
        }
        Repeater {
            model: root.inventory.filesystems
            delegate: SettingsRow {
                required property var modelData
                width: parent.width
                title: modelData.path + " (" + modelData.filesystem + ")"
                explanation: "Used: " + root.bytes(modelData.used_bytes) + "\nAvailable: " + root.bytes(modelData.available_bytes) + "\nTotal: " + root.bytes(modelData.total_bytes) + (modelData.shared_with ? "\nShared pool with: " + modelData.shared_with : "")
                controlWidth: 0
            }
        }
        SettingsRow {
            width: parent.width
            title: "Protected storage details"
            explanation: "Authenticate to inspect system snapshots, cache and logs."
            busy: root.pending
            controlWidth: 180
            control: Component {
                SettingsButton {
                    text: "Inspect"
                    onClicked: root.refresh(true)
                }
            }
        }
    }
    SettingsCard {
        width: parent.width
        title: "Space by category"
        Repeater {
            model: root.kinds
            delegate: SettingsRow {
                id: categoryRow
                required property string modelData
                readonly property var details: root.inventory.categories[modelData] || {
                    bytes: null,
                    reason: ""
                }
                objectName: "settings-storage-" + modelData
                width: parent.width
                title: root.labels[modelData]
                explanation: root.bytes(details.bytes) + (details.reason ? "\n" + details.reason : "") + (modelData === "snapshots" ? "\nRead only. Cleanup never deletes snapshots." : "")
                busy: root.pending
                controlWidth: modelData === "snapshots" ? 0 : 180
                control: modelData === "snapshots" ? null : previewButton
                Component {
                    id: previewButton
                    SettingsButton {
                        text: "Preview"
                        onClicked: root.requestPreview(categoryRow.modelData)
                    }
                }
            }
        }
    }
    SettingsCard {
        objectName: "settings-storage-preview"
        width: parent.width
        visible: root.preview !== null
        title: "Review cleanup"
        Text {
            width: parent.width
            text: root.preview ? root.labels[root.preview.kind] + "\nAllocated bytes in selected files: " + root.bytes(root.preview.allocated_bytes) + "\nUnshared file data: " + root.unsharedBytes(root.preview) + "\n" + root.preview.reason + "\nShared or compressed extents can make returned space smaller. Only these exact entries are selected." : ""
            textFormat: Text.PlainText
            wrapMode: Text.WrapAnywhere
            color: SettingsTheme.dim
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 13
        }
        ListView {
            id: previewEntries
            objectName: "settings-storage-preview-entries"
            width: parent.width
            height: Math.min(320, contentHeight)
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            cacheBuffer: 0
            reuseItems: true
            ScrollBar.vertical: ScrollBar {
                policy: ScrollBar.AsNeeded
            }
            model: root.preview ? root.preview.entries : []
            delegate: Text {
                objectName: "settings-storage-preview-entry"
                required property var modelData
                width: previewEntries.width
                text: modelData.path + "\n" + root.bytes(modelData.bytes)
                textFormat: Text.PlainText
                wrapMode: Text.WrapAnywhere
                color: SettingsTheme.ink
                font.family: SettingsTheme.fontFamily
                font.pixelSize: 13
                topPadding: 12
                bottomPadding: 12
            }
        }
        footer: Component {
            Row {
                spacing: 8
                SettingsButton {
                    objectName: "storage-cancel"
                    text: "Cancel"
                    enabled: !root.pending
                    onClicked: root.cancelPreview()
                }
                SettingsButton {
                    objectName: "storage-confirm"
                    text: "Delete listed entries"
                    enabled: !root.pending && root.preview !== null && root.preview.entries.length > 0
                    onClicked: root.confirmPreview()
                }
            }
        }
    }
}
