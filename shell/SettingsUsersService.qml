// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma Singleton
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

// Session-owned commands and receipts survive authentication closing Settings.
Scope {
    id: root
    property var accounts: []
    property int administrators: 0
    property bool received: false
    property bool pending: false
    property string reply: ""
    property string message: ""
    property bool problem: true
    property string operation: ""
    readonly property bool passwordPending: passwordProcess.running
    signal editorsClosed
    readonly property var reasons: ({
            authorization_refused: qsTranslate("SettingsUsersService", "Nothing was changed: authentication was cancelled or refused."),
            users_busy: qsTranslate("SettingsUsersService", "Another account change is in progress. Wait a moment, then try again."),
            last_administrator: qsTranslate("SettingsUsersService", "This computer needs at least one administrator. Make another account an administrator first."),
            current_user: qsTranslate("SettingsUsersService", "You cannot remove the account you are using."),
            user_signed_in: qsTranslate("SettingsUsersService", "This person is signed in or still has programs running. They need to sign out first."),
            login_taken: qsTranslate("SettingsUsersService", "This login is already used by an account or group on this computer."),
            invalid_login: qsTranslate("SettingsUsersService", "Use 1–32 lowercase letters, digits, _ or - for the login, starting with a letter or _."),
            invalid_name: qsTranslate("SettingsUsersService", "The full name must be at most 128 characters and contain no comma, colon or control characters."),
            invalid_password: qsTranslate("SettingsUsersService", "Use only letters, digits and symbols of the English (US) keyboard."),
            user_not_found: qsTranslate("SettingsUsersService", "This account is no longer on this computer. Refresh the list."),
            accounts_service_failed: qsTranslate("SettingsUsersService", "The system could not change this account. Nothing was changed."),
            password_unavailable: qsTranslate("SettingsUsersService", "The password could not be prepared on this computer. Nothing was changed."),
            change_unconfirmed: qsTranslate("SettingsUsersService", "The change could not be confirmed. Check the current value before trying again."),
            home_retention_failed: qsTranslate("SettingsUsersService", "The change could not be confirmed. Check the current value before trying again."),
            home_removal_failed: qsTranslate("SettingsUsersService", "The change could not be confirmed. Check the current value before trying again."),
            account_without_password: qsTranslate("SettingsUsersService", "The account was created, but its password was not set. Set a password or remove the account."),
            account_unavailable: qsTranslate("SettingsUsersService", "The account service cannot change this account until the computer restarts."),
            password_not_entered: qsTranslate("SettingsUsersService", "No password was entered. Nothing was changed."),
            password_window_unconfirmed: qsTranslate("SettingsUsersService", "The password window closed without an answer. Check the list before trying again."),
            picture_too_large: qsTranslate("SettingsUsersService", "This picture is too large. Choose a smaller picture."),
            picture_unreadable: qsTranslate("SettingsUsersService", "This picture could not be read. Choose a PNG, JPEG or WebP picture."),
            picture_unavailable: qsTranslate("SettingsUsersService", "The picture could not be prepared on this computer. Your picture has not changed.")
        })
    readonly property string unavailableText: qsTranslate("SettingsUsersService", "Accounts are unavailable. Try again when the system service is ready.")
    function validUser(user): bool {
        return !!user && typeof user.login === "string" && typeof user.name === "string" && typeof user.uid === "number" && typeof user.administrator === "boolean" && typeof user.locked === "boolean" && typeof user.current === "boolean" && typeof user.signed_in === "boolean" && typeof user.home === "string" && (user.available === undefined || typeof user.available === "boolean") && (user.picture === undefined || typeof user.picture === "boolean");
    }
    // Passwords are typed in the managed terminal that --terminal opens, never in this window.
    function run(args): void {
        if (pending || passwordPending)
            return;
        problem = false;
        message = (args[0] === "add" || args[0] === "set-password") && args[args.length - 1] === "--terminal" ? qsTranslate("SettingsUsersService", "Enter the new password in the terminal window.") : "";
        reply = "";
        operation = args[0];
        worker.command = ["emaki-settings-users"].concat(args, ["--json"]);
        worker.completed = false;
        pending = true;
        worker.running = true;
    }
    readonly property alias pictureChooser: pictureChooser
    property string pictureError: ""
    readonly property string pictureChooserFailed: qsTranslate("SettingsUsersService", "Could not choose a picture. Your picture has not changed.")
    function choosePicture(): void {
        if (pending || passwordPending || pictureChooser.busy || !accounts.some(user => user.current))
            return;
        pictureError = "";
        pictureChooser.start({
            op: "account-picture-choose"
        });
    }
    // The portal hands over a path; emaki-settings-users decodes it and gives AccountsService a copy.
    PrivateJob {
        id: pictureChooser
        timeoutMs: 130000
        onCompleted: result => {
            if (result.state === "ready" && typeof result.path === "string" && result.path.startsWith("/")) {
                root.pictureError = "";
                root.run(["picture", "--set", result.path]);
            } else if (result.state !== "cancelled") {
                root.pictureError = root.pictureChooserFailed;
            }
        }
        onStateChanged: {
            if (!["idle", "pending", "ready", "cancelled"].includes(state))
                root.pictureError = root.pictureChooserFailed;
        }
    }
    function refresh(): void {
        run(["status"]);
    }
    function acceptReply(code): void {
        pending = false;
        try {
            const result = JSON.parse(reply);
            if (result.schema_version !== 1 || typeof result.status !== "string")
                throw new Error();
            if (Array.isArray(result.users)) {
                if (!result.users.every(user => validUser(user)) || typeof result.administrators !== "number")
                    throw new Error();
                accounts = result.users;
                administrators = result.administrators;
                received = true;
            }
            if ((result.status === "read" || result.status === "applied") && code === 0) {
                if (!received)
                    throw new Error();
                if (operation === "add" || operation === "remove")
                    editorsClosed();
                problem = false;
                message = operation === "set-password" ? qsTranslate("SettingsUsersService", "Password changed.") : "";
                return;
            }
            problem = true;
            if (result.reason === "accounts_service_unavailable" || result.reason === "account_lookup_failed")
                message = unavailableText;
            else
                message = reasons[result.reason] ?? qsTranslate("SettingsUsersService", "Accounts could not be read.");
        } catch (_) {
            problem = true;
            message = operation === "status" ? unavailableText : reasons.change_unconfirmed;
        }
    }
    function changeOwnPassword(): void {
        if (pending || passwordPending)
            return;
        problem = false;
        message = qsTranslate("SettingsUsersService", "Enter your current and new password in the terminal.");
        passwordProcess.completed = false;
        passwordProcess.result = "";
        passwordProcess.running = true;
    }
    Process {
        id: worker
        property bool completed: false
        stdout: StdioCollector {
            onStreamFinished: root.reply = text
        }
        stderr: StdioCollector {}
        onRunningChanged: {
            if (!running)
                Qt.callLater(function () {
                    if (!worker.completed) {
                        root.pending = false;
                        root.problem = true;
                        root.message = root.unavailableText;
                    }
                });
        }
        onExited: code => {
            completed = true;
            root.acceptReply(code);
        }
    }
    Process {
        id: passwordProcess
        property bool completed: false
        onRunningChanged: {
            if (!running)
                Qt.callLater(function () {
                    if (!passwordProcess.completed) {
                        root.problem = true;
                        root.message = qsTranslate("SettingsUsersService", "The password window could not be opened.");
                    }
                });
        }
        property string result: ""
        command: ["emaki-terminal", "--change-password", "--json"]
        stdout: StdioCollector {
            onStreamFinished: passwordProcess.result = text
        }
        onExited: code => {
            completed = true;
            try {
                const reply = JSON.parse(result);
                if (code !== 0 || reply.schema_version !== 1 || !["changed", "unchanged", "unconfirmed"].includes(reply.status))
                    throw new Error();
                root.problem = false;
                root.message = reply.message;
            } catch (_) {
                root.problem = true;
                root.message = qsTranslate("SettingsUsersService", "The password change could not be confirmed. Check the password window.");
            }
        }
    }
}
