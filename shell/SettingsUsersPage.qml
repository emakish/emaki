// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import "settings"

Column {
    id: root
    spacing: 16
    readonly property var accounts: SettingsUsersService.accounts
    readonly property int administrators: SettingsUsersService.administrators
    readonly property bool received: SettingsUsersService.received
    readonly property bool pending: SettingsUsersService.pending || SettingsUsersService.passwordPending
    readonly property string message: SettingsUsersService.message
    readonly property bool problem: SettingsUsersService.problem
    property string editing: ""
    property string removing: ""
    property bool adding: false
    readonly property var me: accounts.find(user => user.current) ?? null
    readonly property var others: accounts.filter(user => !user.current)
    readonly property var edited: accounts.find(user => user.login === editing) ?? null
    readonly property var reasons: SettingsUsersService.reasons
    function label(user): string {
        return user.name || user.login;
    }
    function describe(user): string {
        if (user.available === false)
            return user.login + " · " + reasons.account_unavailable;
        let text = user.login + " · " + (user.administrator ? "[PLACEHOLDER] Administrator" : "[PLACEHOLDER] Standard user");
        if (user.signed_in)
            text += " · [PLACEHOLDER] Signed in";
        if (user.locked)
            text += " · [PLACEHOLDER] Cannot sign in";
        return text;
    }
    function lastAdministrator(user): bool {
        return !!user && user.administrator && !user.locked && administrators <= 1;
    }
    // The installer's account rules (installer/ui/Protocol.js accountErrors), for another person.
    function nameProblem(name): string {
        return Array.from(name).length > 128 || /[\x00-\x1f:,\x7f]/.test(name) ? reasons.invalid_name : "";
    }
    function loginProblem(login): string {
        if (!/^[a-z_][a-z0-9_-]{0,31}$/.test(login))
            return reasons.invalid_login;
        return accounts.some(user => user.login === login) ? reasons.login_taken : "";
    }
    function run(args): void {
        SettingsUsersService.run(args);
    }
    function refresh(): void {
        SettingsUsersService.refresh();
    }
    function closeEditors(): void {
        adding = false;
        editing = "";
        removing = "";
    }
    Component.onCompleted: {
        if (!received && !pending)
            refresh();
    }
    Connections {
        target: SettingsUsersService
        function onEditorsClosed(): void {
            root.closeEditors();
        }
    }
    Text {
        objectName: "settings-users-message"
        width: parent.width
        visible: text !== ""
        text: root.message
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        color: root.problem ? "#a01b45" : SettingsTheme.dim
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 13
    }
    SettingsCard {
        width: parent.width
        visible: root.me !== null
        title: "Your account"
        SettingsRow {
            objectName: "settings-users-account"
            width: parent.width
            title: root.me ? root.label(root.me) : ""
            explanation: root.me?.administrator ? "Administrator · this computer" : "This computer"
            busy: root.pending
            controlWidth: 180
            control: Component {
                SettingsButton {
                    objectName: "settings-users-change-" + (root.me?.login ?? "")
                    text: "[PLACEHOLDER] Change"
                    enabled: root.me?.available !== false
                    onClicked: {
                        root.closeEditors();
                        root.editing = root.me.login;
                    }
                }
            }
        }
        SettingsRow {
            objectName: "settings-users-password"
            width: parent.width
            title: "Password"
            explanation: "Open the system password prompt in your terminal. Your login wallet may still use your previous password; update its password separately."
            busy: SettingsUsersService.passwordPending
            controlWidth: 150
            control: Component {
                SettingsButton {
                    text: "Change password"
                    objectName: "settings-users-own-password"
                    onClicked: SettingsUsersService.changeOwnPassword()
                }
            }
        }
    }
    SettingsCard {
        objectName: "settings-users-others"
        width: parent.width
        visible: root.received
        title: "Other users"
        Repeater {
            model: root.others
            SettingsRow {
                id: otherRow
                required property var modelData
                objectName: "settings-users-row-" + modelData.login
                width: parent.width
                title: root.label(modelData)
                explanation: root.describe(modelData)
                busy: root.pending
                controlWidth: 180
                control: Component {
                    SettingsButton {
                        objectName: "settings-users-change-" + otherRow.modelData.login
                        text: "[PLACEHOLDER] Change"
                        enabled: otherRow.modelData.available !== false
                        onClicked: {
                            root.closeEditors();
                            root.editing = otherRow.modelData.login;
                        }
                    }
                }
            }
        }
        SettingsRow {
            width: parent.width
            visible: root.others.length === 0
            title: "[PLACEHOLDER] No other users"
            explanation: "[PLACEHOLDER] Add an account for another person who uses this computer."
            controlWidth: 0
        }
        SettingsRow {
            objectName: "settings-users-add"
            width: parent.width
            title: "[PLACEHOLDER] Add user"
            explanation: "[PLACEHOLDER] Create an account with its own files and settings."
            busy: root.pending
            controlWidth: 180
            control: Component {
                SettingsButton {
                    objectName: "settings-users-add-open"
                    text: "[PLACEHOLDER] Add"
                    onClicked: {
                        root.closeEditors();
                        addName.text = "";
                        addLogin.text = "";
                        addAdministrator.checked = false;
                        root.adding = true;
                        addName.forceActiveFocus();
                    }
                }
            }
        }
    }
    SettingsCard {
        objectName: "settings-users-add-card"
        width: parent.width
        visible: root.adding
        title: "[PLACEHOLDER] New user"
        Column {
            width: parent.width
            spacing: 10
            readonly property string problem: root.nameProblem(addName.text) || (addLogin.text === "" ? "" : root.loginProblem(addLogin.text))
            TextField {
                id: addName
                objectName: "settings-users-add-name"
                width: parent.width
                placeholderText: "Full name"
                Accessible.name: "Full name"
                maximumLength: 128
                font.family: SettingsTheme.fontFamily
            }
            TextField {
                id: addLogin
                objectName: "settings-users-add-login"
                width: parent.width
                placeholderText: "Login"
                Accessible.name: "Login"
                maximumLength: 32
                font.family: SettingsTheme.fontFamily
            }
            CheckBox {
                id: addAdministrator
                objectName: "settings-users-add-administrator"
                text: "[PLACEHOLDER] Administrator: may install software and change settings for everyone"
                font.family: SettingsTheme.fontFamily
            }
            Text {
                width: parent.width
                visible: text !== ""
                text: parent.problem
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                color: SettingsTheme.dim
                font.family: SettingsTheme.fontFamily
                font.pixelSize: 13
            }
            Text {
                width: parent.width
                text: "[PLACEHOLDER] A terminal window asks for the new person's password."
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                color: SettingsTheme.dim
                font.family: SettingsTheme.fontFamily
                font.pixelSize: 13
            }
            Row {
                spacing: 8
                SettingsButton {
                    objectName: "settings-users-add-confirm-button"
                    text: "[PLACEHOLDER] Add user…"
                    enabled: !root.pending && root.loginProblem(addLogin.text) === "" && root.nameProblem(addName.text) === ""
                    onClicked: root.run(["add", addLogin.text, addName.text, "--administrator", addAdministrator.checked ? "yes" : "no", "--terminal"])
                }
                SettingsButton {
                    text: "Cancel"
                    onClicked: root.closeEditors()
                }
            }
        }
    }
    SettingsCard {
        objectName: "settings-users-edit-card"
        width: parent.width
        visible: root.edited !== null
        title: root.edited ? root.label(root.edited) : ""
        Connections {
            target: root
            function onEditingChanged() {
                editName.text = root.accounts.find(user => user.login === root.editing)?.name ?? "";
            }
        }
        Column {
            width: parent.width
            spacing: 10
            Text {
                width: parent.width
                text: "Full name"
                textFormat: Text.PlainText
                color: SettingsTheme.ink
                font.family: SettingsTheme.fontFamily
                font.pixelSize: 14
                font.weight: Font.Medium
            }
            Row {
                width: parent.width
                spacing: 8
                TextField {
                    id: editName
                    objectName: "settings-users-edit-name"
                    width: parent.width - saveName.width - 8
                    placeholderText: "Full name"
                    Accessible.name: "Full name"
                    maximumLength: 128
                    font.family: SettingsTheme.fontFamily
                }
                SettingsButton {
                    id: saveName
                    objectName: "settings-users-save-name"
                    text: "[PLACEHOLDER] Save name"
                    enabled: !root.pending && !!root.edited && editName.text !== root.edited.name && root.nameProblem(editName.text) === ""
                    onClicked: root.run(["set-name", root.edited.login, editName.text])
                }
            }
            Text {
                width: parent.width
                visible: text !== ""
                text: root.nameProblem(editName.text)
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                color: SettingsTheme.dim
                font.family: SettingsTheme.fontFamily
                font.pixelSize: 13
            }
            Column {
                width: parent.width
                spacing: 10
                visible: !!root.edited && !root.edited.current
                Text {
                    width: parent.width
                    text: "Password"
                    textFormat: Text.PlainText
                    color: SettingsTheme.ink
                    font.family: SettingsTheme.fontFamily
                    font.pixelSize: 14
                    font.weight: Font.Medium
                }
                Text {
                    width: parent.width
                    text: "[PLACEHOLDER] A terminal window asks for this person's new password. Their login wallet keeps the previous password until they change it there."
                    textFormat: Text.PlainText
                    wrapMode: Text.WordWrap
                    color: SettingsTheme.dim
                    font.family: SettingsTheme.fontFamily
                    font.pixelSize: 13
                }
                SettingsButton {
                    objectName: "settings-users-set-password"
                    text: "[PLACEHOLDER] Set password…"
                    enabled: !root.pending
                    onClicked: root.run(["set-password", root.edited.login, "--terminal"])
                }
            }
            SettingsToggleRow {
                objectName: "settings-users-administrator"
                width: parent.width
                separator: false
                title: "[PLACEHOLDER] Administrator"
                explanation: "[PLACEHOLDER] Administrators may install software and change settings for everyone."
                value: root.edited?.administrator ?? false
                defaultValue: value
                busy: root.pending
                unavailable: root.lastAdministrator(root.edited)
                errorText: unavailable ? "[PLACEHOLDER] This computer needs at least one administrator." : ""
                onValueRequested: value => root.run(["set-administrator", root.edited.login, value ? "yes" : "no"])
            }
            Column {
                width: parent.width
                spacing: 10
                visible: !!root.edited && !root.edited.current
                Text {
                    width: parent.width
                    text: "[PLACEHOLDER] Remove user"
                    textFormat: Text.PlainText
                    color: SettingsTheme.ink
                    font.family: SettingsTheme.fontFamily
                    font.pixelSize: 14
                    font.weight: Font.Medium
                }
                Text {
                    objectName: "settings-users-remove-explanation"
                    width: parent.width
                    text: !root.edited ? "" : root.edited.signed_in ? "[PLACEHOLDER] This person is signed in or still has programs running. They need to sign out before the account can be removed." : root.lastAdministrator(root.edited) ? "[PLACEHOLDER] This computer needs at least one administrator. Make another account an administrator first." : root.removing === root.edited.login ? "[PLACEHOLDER] Keep the home folder " + root.edited.home + " with this person's files, or delete it with everything in it? Deleted files cannot be restored. A kept folder passes to the next account you add if it receives the same user number." : "[PLACEHOLDER] Removes the account. You choose what happens to the home folder next."
                    textFormat: Text.PlainText
                    wrapMode: Text.WordWrap
                    color: SettingsTheme.dim
                    font.family: SettingsTheme.fontFamily
                    font.pixelSize: 13
                }
                SettingsButton {
                    objectName: "settings-users-remove"
                    visible: !!root.edited && root.removing !== root.edited.login
                    text: "[PLACEHOLDER] Remove…"
                    enabled: !root.pending && !!root.edited && !root.edited.signed_in && !root.lastAdministrator(root.edited)
                    onClicked: root.removing = root.edited.login
                }
                Flow {
                    width: parent.width
                    spacing: 8
                    visible: !!root.edited && root.removing === root.edited.login
                    SettingsButton {
                        objectName: "settings-users-remove-keep"
                        text: "[PLACEHOLDER] Remove and keep the home folder"
                        enabled: !root.pending
                        onClicked: root.run(["remove", root.edited.login, "--home", "keep"])
                    }
                    SettingsButton {
                        objectName: "settings-users-remove-delete"
                        text: "[PLACEHOLDER] Remove and delete the home folder"
                        enabled: !root.pending
                        onClicked: root.run(["remove", root.edited.login, "--home", "delete"])
                    }
                    SettingsButton {
                        text: "Cancel"
                        onClicked: root.removing = ""
                    }
                }
            }
            SettingsButton {
                objectName: "settings-users-done"
                text: "[PLACEHOLDER] Done"
                onClicked: root.closeEditors()
            }
        }
    }
    SettingsButton {
        text: "Refresh"
        enabled: !root.pending
        onClicked: root.refresh()
    }
}
