// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

// "Reset Emaki settings" (Settings > Updates & Recovery). Lives with the settings controller, not
// the page, so the Undo action of its notification still works after Settings is closed. The
// helper backs up Emaki's own preference files, resets them (the managed settings through the
// core), and restores the backup on Undo; the shell's stores then read their files again.
Scope {
    id: reset
    property var notificationStore: null
    property string helper: Quickshell.shellPath("helpers/settings-reset.py")
    // idle, working, done (backupId names the copy), undoing, undone, failed
    property string state: "idle"
    property string backupId: ""
    // The backup the running Undo restores.
    property string undoingId: ""
    property string message: ""
    property int noticeId: 0
    readonly property bool busy: state === "working" || state === "undoing"
    readonly property bool undoable: state === "done" && backupId !== ""
    // Each notice's action names its own backup, so an older notice never restores a newer one.
    readonly property string undoAction: "settings-reset-undo:"
    function retireNotice(): void {
        if (noticeId !== 0 && notificationStore)
            notificationStore.dismiss([noticeId]);
        noticeId = 0;
    }
    function offerUndo(id: string): void {
        retireNotice();
        if (notificationStore)
            noticeId = notificationStore.offer("Settings", message, qsTranslate("SettingsReset", "Your files and app settings were not changed."), undoAction + id, qsTranslate("SettingsReset", "Undo"));
    }
    function start(): bool {
        if (busy)
            return false;
        // A new reset replaces the previous one: its notice no longer offers Undo.
        retireNotice();
        state = "working";
        backupId = "";
        message = "";
        SettingsBridge.storesHoldRequested();
        const started = job.start({
            op: "settings-reset"
        });
        if (!started) {
            SettingsBridge.storesReloadRequested();
            state = "failed";
            message = qsTranslate("SettingsReset", "Emaki settings could not be reset. Try again.");
        }
        return started;
    }
    // Restores the backup id (a notice's own), or the current one.
    function undo(id: string): bool {
        const target = id || (undoable ? backupId : "");
        if (busy || !target)
            return false;
        state = "undoing";
        undoingId = target;
        message = "";
        if (target === backupId)
            retireNotice();
        SettingsBridge.storesHoldRequested();
        const started = job.start({
            op: "settings-reset-restore",
            id: target
        });
        if (!started) {
            SettingsBridge.storesReloadRequested();
            retryUndo();
        }
        return started;
    }
    // Possibly partly back: Undo stays, on the page and in a new notice.
    function retryUndo(): void {
        const older = backupId !== "" && undoingId !== backupId;
        if (!older)
            backupId = undoingId;
        state = "done";
        message = qsTranslate("SettingsReset", "Your previous Emaki settings could not be restored. Try Undo again.");
        if (!older)
            offerUndo(undoingId);
        else if (notificationStore)
            // An older reset's notice: offered again, the current reset's notice stays.
            notificationStore.offer("Settings", message, qsTranslate("SettingsReset", "Your files and app settings were not changed."), undoAction + undoingId, qsTranslate("SettingsReset", "Undo"));
    }
    // An Undo that ended: the current reset is no longer undoable when it was the one restored.
    function undoEnded(text: string): void {
        if (undoingId === backupId)
            backupId = "";
        state = backupId !== "" ? "done" : "undone";
        message = text;
    }
    function finish(value: var): void {
        // One reply per job: a helper "timeout" reply also changes the job state.
        if (!busy)
            return;
        const working = state === "working";
        SettingsBridge.storesReloadRequested();
        if (working && ["reset", "reset_incomplete"].includes(value.state) && value.id) {
            backupId = String(value.id);
            state = "done";
            message = value.state === "reset" ? qsTranslate("SettingsReset", "Emaki settings are back to their defaults.") : value.settings === "live_pending" ? qsTranslate("SettingsReset", "Emaki settings could not be reset, but some may show their defaults until you change a setting or log in again. Try the reset again, or Undo.") : value.settings === "uncertain" ? qsTranslate("SettingsReset", "The reset could not be confirmed. Undo puts back what it can; History shows the rest.") : qsTranslate("SettingsReset", "Some Emaki settings could not be reset. Undo puts everything back.");
            offerUndo(backupId);
            return;
        }
        if (working) {
            state = "failed";
            // The helper answers without a backup only when nothing was changed; a missing or
            // broken answer may hide a change.
            message = ["timeout", "helper_failed", "invalid_response", "reset_incomplete"].includes(value.state) ? qsTranslate("SettingsReset", "The reset could not be confirmed. Reopen Settings to check your settings.") : qsTranslate("SettingsReset", "Emaki settings could not be reset. Nothing was changed.");
            return;
        }
        if (value.state === "restored") {
            undoEnded(qsTranslate("SettingsReset", "Your previous Emaki settings are restored."));
            return;
        }
        if (value.state === "settings_conflict") {
            // The core refuses the whole undo when a reset key changed again; the files are back.
            undoEnded(qsTranslate("SettingsReset", "Dock pins, Night Light and screen blanking are restored. Other settings changed after the reset, so they stay as they are; see History."));
            return;
        }
        if (value.state === "settings_uncertain") {
            // The reset's core part is unknown: the files are back, History tells the rest.
            undoEnded(qsTranslate("SettingsReset", "Dock pins, Night Light and screen blanking are restored. Whether other settings were reset is unknown; see History."));
            return;
        }
        if (value.state === "settings_pending") {
            // The core refused that reset and kept its file; the session catches up with it later.
            undoEnded(qsTranslate("SettingsReset", "Your Emaki settings were kept. Some may show their defaults until you change a setting or log in again."));
            return;
        }
        retryUndo();
    }
    PrivateJob {
        id: job
        helper: reset.helper
        timeoutMs: 60000
        onCompleted: value => reset.finish(value)
        onStateChanged: {
            if (["timeout", "helper_failed", "invalid_response"].includes(state))
                reset.finish({
                    state: state
                });
        }
    }
    Connections {
        target: reset.notificationStore
        function onLocalActionRequested(action: string): void {
            if (action.startsWith(reset.undoAction)) {
                // Choosing the action already closed its notice (an older notice's choice leaves
                // the current reset's notice to be retired later).
                const id = action.slice(reset.undoAction.length);
                if (id === reset.backupId)
                    reset.noticeId = 0;
                reset.undo(id);
            }
        }
    }
}
