// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import "settings"
import "PolkitText.js" as Copy

FocusScope {
    id: card
    required property PolkitPromptController controller
    property bool reducedMotion: false
    property bool sudoKeyInstalled: SudoKeySupport.installed
    // Only the authenticated sudo adapter may opt into key grants.
    property string requestKind: controller.backend.requestKind || "polkit"
    readonly property bool keyAvailable: sudoKeyInstalled && requestKind === "sudo" && controller.backend.keyOffered !== false
    readonly property bool keyGrantAllowed: keyAvailable && controller.backend.keyEligible === true
    readonly property bool agentRequest: requestKind === "sudo" && controller.backend.agent === true
    property bool detailsOpen: false
    property bool keyOpen: false
    property real shakeOffset: 0
    property real viewportWidth: 1280
    property real viewportHeight: 720
    property real islandX: (viewportWidth - islandWidth) / 2
    property real islandWidth: 148
    property real islandY: Metrics.top
    property real expansion: controller.opened ? 1 : 0
    readonly property real targetWidth: Math.min(520, viewportWidth - 20)
    readonly property real targetHeight: Math.min(content.implicitHeight + 56, viewportHeight - 72)
    x: islandX + ((viewportWidth - targetWidth) / 2 - islandX) * expansion
    y: islandY + (52 - islandY) * expansion
    width: islandWidth + (targetWidth - islandWidth) * expansion
    height: Metrics.islandHeight + (targetHeight - Metrics.islandHeight) * expansion
    opacity: expansion
    clip: true
    visible: expansion > 0
    focus: controller.opened
    Behavior on expansion {
        NumberAnimation {
            duration: card.reducedMotion || card.controller.locked ? 0 : card.controller.opened ? 460 : 320
            easing.type: Easing.OutCubic
        }
    }
    onKeyAvailableChanged: if (!keyAvailable)
        keyOpen = false
    Keys.onShortcutOverride: event => {
        if (controller.inputBlocked)
            event.accepted = true;
    }
    Keys.onPressed: event => {
        if (controller.inputBlocked)
            event.accepted = true;
    }
    Keys.onReleased: event => {
        if (controller.inputBlocked)
            event.accepted = true;
    }
    Keys.onEscapePressed: event => {
        if (!controller.inputBlocked)
            controller.cancel("cancel");
        event.accepted = true;
    }
    function focusPassword(): void {
        if (controller.accepting && visible)
            password.forceActiveFocus();
    }
    function clearPassword(): void {
        password.clear();
        password.text = "";
        reveal.revealed = false;
    }
    function submit(): void {
        if (!controller.accepting)
            return;
        if (keyOpen) {
            if (keyGrantAllowed && keyChoices.amountValid)
                controller.submitKey(password.text, keyChoices.mode, keyChoices.seconds);
        } else {
            controller.submit(password.text);
        }
    }
    Connections {
        target: card.controller
        function onClearInput(): void {
            card.clearPassword();
        }
        function onFocusInput(): void {
            Qt.callLater(card.focusPassword);
        }
        function onStarted(): void {
            card.forceActiveFocus();
            card.detailsOpen = false;
            card.keyOpen = false;
            keyChoices.way = "time";
            keyChoices.minutesText = "";
            keyChoices.turnsText = "";
            Qt.callLater(card.focusPassword);
        }
        function onShake(): void {
            if (!card.reducedMotion)
                wrongShake.restart();
        }
    }
    Component.onDestruction: clearPassword()
    SequentialAnimation {
        id: wrongShake
        NumberAnimation {
            target: card
            property: "shakeOffset"
            to: -7
            duration: 50
        }
        NumberAnimation {
            target: card
            property: "shakeOffset"
            to: 7
            duration: 70
        }
        NumberAnimation {
            target: card
            property: "shakeOffset"
            to: -5
            duration: 70
        }
        NumberAnimation {
            target: card
            property: "shakeOffset"
            to: 5
            duration: 70
        }
        NumberAnimation {
            target: card
            property: "shakeOffset"
            to: 0
            duration: 70
        }
    }
    PolkitHazardFrame {
        anchors.fill: parent
        reducedMotion: card.reducedMotion
    }
    Rectangle {
        anchors.fill: parent
        anchors.margins: 10
        radius: 20
        color: "#fff7f2"
    }
    Flickable {
        anchors.fill: parent
        anchors.margins: 10
        clip: true
        contentWidth: width
        contentHeight: content.implicitHeight + 36
        boundsBehavior: Flickable.StopAtBounds
        Column {
            id: content
            enabled: !card.controller.inputBlocked
            x: 22
            y: 20
            width: card.targetWidth - 64
            spacing: 14
            Row {
                width: parent.width
                spacing: 14
                Rectangle {
                    width: 44
                    height: 44
                    radius: 14
                    color: "#f18c50"
                    Icon {
                        anchors.centerIn: parent
                        visible: !requestIcon.found
                        kind: "lock"
                        ink: "white"
                        width: 24
                        height: 24
                    }
                    SymbolIcon {
                        id: requestIcon
                        anchors.centerIn: parent
                        name: card.controller.backend.iconName || ""
                        ink: "white"
                        width: 24
                        height: 24
                    }
                }
                Column {
                    width: parent.width - 58
                    spacing: 5
                    Text {
                        text: card.agentRequest ? "AI agent" : "Administrator rights"
                        textFormat: Text.PlainText
                        font.family: ShellPalette.uiFont
                        font.pixelSize: 11
                        font.capitalization: Font.AllUppercase
                        font.letterSpacing: 0.6
                        font.weight: Font.DemiBold
                        color: "#b9273f"
                    }
                    Text {
                        width: content.width - 58
                        text: card.controller.backend.requestLine
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                        font.family: ShellPalette.uiFont
                        font.pixelSize: 17
                        font.weight: Font.DemiBold
                        color: "#352522"
                    }
                }
            }
            Rectangle {
                id: field
                x: card.shakeOffset
                width: parent.width
                height: 42
                radius: 12
                color: "#fffdfb"
                border.width: password.activeFocus || card.controller.wrong ? 2 : 1
                border.color: card.controller.wrong ? ShellPalette.critical : password.activeFocus ? "#a67058" : "#d5bfb4"
                TextInput {
                    id: password
                    objectName: "polkit-password"
                    anchors.left: parent.left
                    anchors.leftMargin: 12
                    anchors.right: reveal.left
                    anchors.rightMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                    enabled: card.controller.accepting
                    echoMode: reveal.revealed ? TextInput.Normal : TextInput.Password
                    inputMethodHints: Qt.ImhHiddenText | Qt.ImhSensitiveData | Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase
                    maximumLength: 1024
                    selectByMouse: false
                    // Revealing a password must not put it in either clipboard.
                    onSelectedTextChanged: if (selectedText.length)
                        deselect()
                    Keys.onShortcutOverride: event => {
                        if (event.matches(StandardKey.Copy) || event.matches(StandardKey.Cut) || event.matches(StandardKey.SelectAll))
                            event.accepted = true;
                    }
                    Keys.onPressed: event => {
                        if (event.matches(StandardKey.Copy) || event.matches(StandardKey.Cut) || event.matches(StandardKey.SelectAll))
                            event.accepted = true;
                        if ((event.modifiers & Qt.ShiftModifier) && [Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down, Qt.Key_Home, Qt.Key_End, Qt.Key_PageUp, Qt.Key_PageDown].includes(event.key))
                            event.accepted = true;
                    }
                    clip: true
                    font.family: ShellPalette.uiFont
                    font.pixelSize: 14
                    color: "#352522"
                    onAccepted: card.submit()
                    Text {
                        anchors.fill: parent
                        visible: !password.text.length
                        text: "Password for " + (card.controller.backend.accountName || Copy.unavailable)
                        textFormat: Text.PlainText
                        font: password.font
                        color: "#806c62"
                        elide: Text.ElideRight
                    }
                }
                PasswordToggle {
                    id: reveal
                    objectName: "polkit-reveal"
                    anchors.right: parent.right
                    anchors.rightMargin: 5
                    anchors.verticalCenter: parent.verticalCenter
                    ink: "#806c62"
                    Accessible.name: revealed ? "Hide" : "Show"
                    enabled: card.controller.accepting
                    onToggled: {
                        password.deselect();
                        revealed = !revealed;
                        card.focusPassword();
                    }
                }
            }
            Text {
                width: parent.width
                visible: card.controller.wrong || !card.controller.backend.passwordRequest
                text: card.controller.wrong ? "That password is not right. Try again." : Copy.unsupported
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                font.family: ShellPalette.uiFont
                font.pixelSize: 12
                color: "#b9273f"
            }
            Column {
                width: parent.width
                spacing: 9
                visible: card.detailsOpen
                Repeater {
                    model: card.controller.backend.detailRows
                    Column {
                        id: detail
                        required property var modelData
                        width: content.width
                        spacing: 3
                        Text {
                            width: parent.width
                            text: detail.modelData.label
                            textFormat: Text.PlainText
                            wrapMode: Text.WrapAnywhere
                            font.family: ShellPalette.uiFont
                            font.pixelSize: 11
                            color: "#806c62"
                        }
                        Text {
                            width: parent.width
                            text: detail.modelData.value
                            textFormat: Text.PlainText
                            wrapMode: Text.WrapAnywhere
                            font.family: ShellPalette.uiFont
                            font.pixelSize: 12
                            color: "#352522"
                        }
                    }
                }
            }
            SudoKeyChoices {
                id: keyChoices
                objectName: "sudo-key-choices"
                grantAllowed: card.keyGrantAllowed
                turnEligible: card.keyGrantAllowed && card.controller.backend.turnEligible === true
                width: parent.width
                visible: card.keyAvailable && card.keyOpen
            }
            Row {
                width: parent.width
                height: 36
                spacing: 4
                PolkitExpander {
                    id: detailsButton
                    text: "Details"
                    checked: card.detailsOpen
                    onClicked: card.detailsOpen = !card.detailsOpen
                }
                PolkitExpander {
                    id: keyButton
                    objectName: "polkit-sudo-key"
                    visible: card.keyAvailable
                    text: "Sudo key"
                    checked: card.keyOpen
                    onClicked: card.keyOpen = !card.keyOpen
                }
                Item {
                    width: Math.max(0, content.width - detailsButton.width - (keyButton.visible ? keyButton.width : 0) - cancelButton.width - allowButton.width - (keyButton.visible ? 16 : 12))
                    height: 1
                }
                SettingsButton {
                    id: cancelButton
                    text: "Cancel"
                    onClicked: card.controller.cancel("cancel")
                }
                SettingsButton {
                    id: allowButton
                    text: card.keyOpen ? "Allow with sudo key" : "Allow"
                    font.weight: Font.DemiBold
                    enabled: card.controller.accepting && (!card.keyOpen || (card.keyGrantAllowed && keyChoices.amountValid))
                    background: Rectangle {
                        radius: 12
                        color: card.keyOpen ? "#9b1730" : "#120506"
                        opacity: allowButton.enabled ? 1 : 0.5
                        border.width: allowButton.activeFocus ? 2 : 0
                        border.color: ShellPalette.critical
                    }
                    contentItem: Text {
                        text: allowButton.text
                        textFormat: Text.PlainText
                        font: allowButton.font
                        color: "white"
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    onClicked: card.submit()
                }
            }
        }
    }
}
