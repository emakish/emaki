// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell.Io
import "settings"

Column {
    id: root
    property var sudoSupport: SudoKeySupport
    property var snapshot: ({})
    property bool pending: false
    property bool available: false
    property string message: ""
    property string sudoMessage: ""
    property string pendingKey: ""
    property string reply: ""
    property bool revoking: false
    readonly property var recovery: available ? snapshot.recovery : ({})
    readonly property var portals: available ? snapshot.portals : ({})
    readonly property var sudoBackend: sudoSupport ? sudoSupport.backend : null
    signal lockRequested
    spacing: 16

    function run(command, mutation) {
        if (pending)
            return;
        pending = true;
        revoking = mutation;
        reply = "";
        message = "";
        helper.completed = false;
        helper.command = command;
        helper.running = true;
    }
    function refresh() {
        run(["emaki-settings-privacy", "status", "--json"], false);
    }
    function revokePortal(row) {
        if (!available || pending || row.can_revoke !== true || !portals.rows.some(grant => grant.can_revoke === true && grant.table === row.table && grant.id === row.id && grant.app === row.app))
            return;
        run(["emaki-settings-privacy", "revoke-portal", row.table, row.id, row.app, "--json"], true);
    }
    function valid(result) {
        return result && result.schema_version === 1 && result.status === "ready" && result.recovery && result.recovery.snapshot && result.recovery.rollback && result.recovery.menu && result.encryption && Array.isArray(result.encryption.mounts) && result.portals && Array.isArray(result.portals.rows) && Array.isArray(result.portals.unavailable) && ["ready", "none", "unavailable"].includes(result.recovery.snapshot.state) && (result.recovery.snapshot.state !== "ready" || (typeof result.recovery.snapshot.number === "string" && /^[0-9]+$/.test(result.recovery.snapshot.number))) && ["normal", "snapshot", "pending", "unsupported", "unavailable"].includes(result.recovery.rollback.mode) && ["checked", "missing", "invalid", "unavailable"].includes(result.recovery.menu.state) && ["encrypted", "unencrypted", "mixed", "unavailable"].includes(result.encryption.state) && ["ready", "partial", "unavailable"].includes(result.portals.state) && result.lock && ["ready", "unavailable"].includes(result.lock.state) && (result.lock.state !== "ready" || (Number.isInteger(result.lock.delay) && result.lock.delay >= 0)) && result.reports && typeof result.reports.saved === "string" && result.portals.rows.every(row => typeof row.table === "string" && typeof row.id === "string" && typeof row.app === "string" && Array.isArray(row.permissions) && row.permissions.every(permission => typeof permission === "string") && typeof row.can_revoke === "boolean");
    }
    function failed() {
        available = false;
        snapshot = ({});
        pending = false;
        message = "[PLACEHOLDER] Privacy status could not be confirmed. Retry to read it again.";
    }
    function snapshotText() {
        const snapshot = recovery.snapshot;
        if (!snapshot || snapshot.state === "unavailable")
            return "Unavailable";
        if (snapshot.state === "none")
            return "[PLACEHOLDER] No pre-update snapshot found.";
        return "[PLACEHOLDER] Snapshot " + snapshot.number + (snapshot.date ? " · " + snapshot.date : "");
    }
    function rollbackText() {
        const rollback = recovery.rollback;
        if (!rollback)
            return "Unavailable";
        const labels = {
            normal: "[PLACEHOLDER] Running the normal system.",
            snapshot: "[PLACEHOLDER] Running a snapshot.",
            pending: "[PLACEHOLDER] Rollback is pending.",
            unsupported: "[PLACEHOLDER] Rollback is not supported on this installation."
        };
        return labels[rollback.mode] || "Unavailable";
    }
    function menuText() {
        const menu = recovery.menu;
        if (!menu)
            return "Unavailable";
        if (menu.state === "checked")
            return "[PLACEHOLDER] Menu structure checked; bootability has not been tested. " + (menu.latest_snapshot_present === true ? "Latest snapshot entry found." : menu.latest_snapshot_present === false ? "Latest snapshot entry missing." : "Latest snapshot entry could not be confirmed.");
        if (menu.state === "missing")
            return "[PLACEHOLDER] Snapshot boot menu is missing.";
        if (menu.state === "invalid")
            return "[PLACEHOLDER] Snapshot boot menu needs repair.";
        return "Unavailable";
    }
    function encryptionText(value) {
        return ({
                encrypted: "[PLACEHOLDER] Encrypted",
                unencrypted: "[PLACEHOLDER] Not encrypted",
                mixed: "[PLACEHOLDER] Partly encrypted"
            })[value] || "Unavailable";
    }
    function lockText() {
        if (!available || snapshot.lock.state !== "ready" || !Number.isInteger(snapshot.lock.delay) || snapshot.lock.delay < 0)
            return "Unavailable";
        return snapshot.lock.delay === 0 ? "[PLACEHOLDER] Automatic lock delay is disabled." : "[PLACEHOLDER] Automatic lock delay: " + snapshot.lock.delay + " seconds.";
    }
    Component.onCompleted: refresh()
    Process {
        id: helper
        property bool completed: false
        stdout: StdioCollector {
            onStreamFinished: root.reply = text
        }
        onRunningChanged: if (!running)
            Qt.callLater(function () {
                if (!helper.completed)
                    root.failed();
            })
        onExited: code => {
            completed = true;
            try {
                const result = JSON.parse(root.reply);
                if (code !== 0 || !root.valid(result))
                    throw new Error();
                root.snapshot = result;
                root.available = true;
                root.pending = false;
                if (root.revoking) {
                    root.message = result.operation === "revoked" ? "[PLACEHOLDER] Remembered permission revoked. Active sessions are not stopped." : "[PLACEHOLDER] Permission revocation could not be confirmed.";
                }
            } catch (_) {
                root.failed();
            }
        }
    }
    QtObject {
        id: keyActions
        readonly property bool connected: !!root.sudoBackend && root.sudoBackend.connected
        readonly property var keys: connected ? root.sudoBackend.keys : []
        function revoke(id) {
            if (!connected || root.pendingKey !== "")
                return;
            root.pendingKey = String(id);
            root.sudoMessage = "[PLACEHOLDER] Waiting for sudo key revocation.";
            keyTimer.restart();
            root.sudoBackend.revoke(id);
        }
        onKeysChanged: {
            if (root.pendingKey !== "" && !keys.some(key => String(key.id) === root.pendingKey)) {
                keyTimer.stop();
                root.pendingKey = "";
                root.sudoMessage = connected ? "[PLACEHOLDER] Sudo key revoked." : "[PLACEHOLDER] Sudo key revocation could not be confirmed.";
            }
        }
    }
    Timer {
        id: keyTimer
        interval: 5000
        onTriggered: {
            root.pendingKey = "";
            root.sudoMessage = "[PLACEHOLDER] Sudo key revocation could not be confirmed.";
        }
    }
    SettingsCard {
        width: parent.width
        title: "[PLACEHOLDER] Privacy status"
        SettingsRow {
            width: parent.width
            title: root.pending ? "[PLACEHOLDER] Reading privacy status…" : root.available ? "[PLACEHOLDER] Privacy status loaded" : "Unavailable"
            explanation: root.message
            busy: root.pending
            control: Component {
                SettingsButton {
                    objectName: "privacy-refresh"
                    text: root.available ? "[PLACEHOLDER] Refresh" : "Retry"
                    onClicked: root.refresh()
                }
            }
        }
    }
    SettingsCard {
        width: parent.width
        objectName: "settings-privacy-recovery"
        title: "[PLACEHOLDER] Snapshots and rollback"
        SettingsRow {
            width: parent.width
            controlWidth: 0
            title: "[PLACEHOLDER] Last pre-update snapshot"
            explanation: root.snapshotText()
        }
        SettingsRow {
            width: parent.width
            controlWidth: 0
            title: "[PLACEHOLDER] Rollback status"
            explanation: root.rollbackText()
        }
        SettingsRow {
            width: parent.width
            controlWidth: 0
            title: "[PLACEHOLDER] Snapshot boot menu"
            explanation: root.menuText()
        }
    }
    SettingsCard {
        width: parent.width
        objectName: "settings-privacy-encryption"
        title: "[PLACEHOLDER] Disk encryption"
        SettingsRow {
            width: parent.width
            controlWidth: 0
            title: "[PLACEHOLDER] System and home storage"
            explanation: root.encryptionText(root.available ? root.snapshot.encryption.state : "unavailable")
        }
        Repeater {
            model: root.available ? root.snapshot.encryption.mounts : []
            SettingsRow {
                required property var modelData
                width: parent.width
                controlWidth: 0
                title: modelData.mount
                explanation: root.encryptionText(modelData.state)
            }
        }
    }
    SettingsCard {
        width: parent.width
        objectName: "settings-privacy-sudo"
        title: "Sudo key"
        SettingsRow {
            width: parent.width
            controlWidth: 0
            title: root.sudoSupport && root.sudoSupport.installed ? "[PLACEHOLDER] Package installed" : "[PLACEHOLDER] Package not installed"
            explanation: !root.sudoSupport || !root.sudoSupport.installed ? "" : !keyActions.connected ? "[PLACEHOLDER] Sudo service unavailable." : keyActions.keys.length === 0 ? "[PLACEHOLDER] No active sudo keys." : "[PLACEHOLDER] Select a key to revoke it."
            errorText: root.sudoMessage
        }
        SudoKeyIndicator {
            width: parent.width
            backend: keyActions
            installed: !!root.sudoSupport && root.sudoSupport.installed
            enabled: root.pendingKey === ""
        }
    }
    SettingsCard {
        width: parent.width
        objectName: "settings-privacy-portals"
        title: "[PLACEHOLDER] Portal permissions"
        SettingsRow {
            width: parent.width
            controlWidth: 0
            title: "[PLACEHOLDER] Remembered application permissions"
            explanation: "[PLACEHOLDER] These are remembered portal grants, not a complete list of application access or active screen captures. Revoking a grant does not stop active sessions."
        }
        SettingsRow {
            width: parent.width
            controlWidth: 0
            title: root.portals.state === "ready" ? root.portals.rows.length === 0 ? "[PLACEHOLDER] No remembered portal decisions." : "[PLACEHOLDER] Remembered portal decisions" : root.portals.state === "partial" ? "[PLACEHOLDER] Some portal permissions are unavailable." : "Unavailable"
            explanation: root.portals.state === "partial" || root.portals.state === "unavailable" ? "[PLACEHOLDER] Unavailable permissions must not be treated as denied." : ""
        }
        Repeater {
            model: root.portals.rows || []
            SettingsRow {
                id: portalRow
                required property var modelData
                width: parent.width
                title: modelData.app || "[PLACEHOLDER] Unidentified application"
                explanation: "[PLACEHOLDER] " + modelData.table + " · " + modelData.permissions.join(", ")
                busy: root.pending
                control: Component {
                    SettingsButton {
                        objectName: "privacy-portal-revoke"
                        text: "[PLACEHOLDER] Revoke"
                        visible: portalRow.modelData.can_revoke === true
                        onClicked: root.revokePortal(portalRow.modelData)
                    }
                }
            }
        }
    }
    SettingsCard {
        width: parent.width
        objectName: "settings-privacy-lock"
        title: "Lock & Login"
        SettingsRow {
            width: parent.width
            title: "[PLACEHOLDER] Lock screen delay"
            explanation: root.lockText()
            control: Component {
                SettingsButton {
                    objectName: "privacy-lock"
                    text: "Lock & Login"
                    onClicked: root.lockRequested()
                }
            }
        }
    }
    SettingsCard {
        width: parent.width
        objectName: "settings-privacy-reports"
        title: "[PLACEHOLDER] Crash and report data"
        SettingsRow {
            width: parent.width
            controlWidth: 0
            title: "[PLACEHOLDER] Saved reports"
            explanation: root.available ? root.snapshot.reports.saved : "Unavailable"
        }
        SettingsRow {
            width: parent.width
            controlWidth: 0
            title: "[PLACEHOLDER] Temporary shell diagnostics"
            explanation: root.available && root.snapshot.reports.runtime ? root.snapshot.reports.runtime : "Unavailable"
        }
        SettingsRow {
            width: parent.width
            controlWidth: 0
            title: "[PLACEHOLDER] System journal"
            explanation: "[PLACEHOLDER] Shell and service errors are in the system journal (emaki-shell.service and niri-emaki.service). This page does not collect or upload reports."
        }
    }
}
