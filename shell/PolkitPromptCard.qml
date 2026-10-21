// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import "settings"
import "Keyboard.js" as Keyboard
import "PolkitText.js" as Copy
import "Liquid.js" as Liquid

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
    property bool keyboardBoundary: true
    property bool keyboardMode: true
    property bool detailsOpen: false
    property bool keyOpen: false
    property real shakeOffset: 0
    // Used only with LiquidPalette.sharedCanvas.
    property string wallpaperTexture: ""
    property real dpr: 1
    property real viewportWidth: 1280
    property real viewportHeight: 720
    property real islandX: (viewportWidth - islandWidth) / 2
    property real islandWidth: 148
    property real islandY: Metrics.top
    property string time: ""
    property string date: ""
    property bool dnd: false
    property int notificationCount: 0
    property DockBackdrop backdrop: null
    property var islandBubble: null
    property real expansion: controller.opened ? 1 : 0
    readonly property real morph: Math.max(0, Math.min(1, expansion))
    readonly property real contentAlpha: Liquid.smooth((morph - .55) / .45)
    readonly property real lineAlpha: 1 - Liquid.smooth(morph * 2.5)
    readonly property real radius: Liquid.mix(Metrics.islandRadius, Metrics.panelRadius, morph)
    // Reject every foreign texture before it reaches a shader, even while hidden.
    readonly property DockBackdrop localBackdrop: backdrop !== null && card.Window.window !== null && backdrop.Window.window === card.Window.window && backdrop.sharp.Window.window === card.Window.window && backdrop.clear.Window.window === card.Window.window && backdrop.regular.Window.window === card.Window.window ? backdrop : null
    readonly property bool glassReady: localBackdrop !== null && localBackdrop.ready && GraphicsInfo.api !== GraphicsInfo.Software
    readonly property real islandAlpha: islandBubble ? Liquid.smooth(islandBubble.alpha.x) * lineAlpha : 0
    readonly property var islandDrop: {
        if (!islandBubble || lineAlpha <= .001)
            return null;
        const drop = Liquid.drop(islandBubble, islandX, islandY);
        if (drop)
            drop.params.w *= lineAlpha;
        return drop;
    }
    readonly property real targetWidth: Math.min(520, viewportWidth - 20)
    readonly property real targetHeight: Math.min(content.implicitHeight + 56, viewportHeight - 72)
    x: islandX + ((viewportWidth - targetWidth) / 2 - islandX) * morph
    y: islandY
    width: islandWidth + (targetWidth - islandWidth) * morph
    height: Metrics.islandHeight + (targetHeight - Metrics.islandHeight) * morph
    visible: controller.opened || expansion > 0
    focus: controller.opened
    Behavior on expansion {
        NumberAnimation {
            duration: card.reducedMotion || card.controller.locked ? 0 : Metrics.morphMs
            easing.type: Easing.BezierSpline
            easing.bezierCurve: Metrics.morphCurve
        }
    }
    onKeyAvailableChanged: if (!keyAvailable)
        keyOpen = false
    Keys.onShortcutOverride: event => {
        if (controller.inputBlocked)
            event.accepted = true;
    }
    Keys.onPressed: event => {
        if (controller.inputBlocked) {
            event.accepted = true;
        } else if ([Qt.Key_Return, Qt.Key_Enter].includes(event.key)) {
            const target = Keyboard.active(card);
            if (target && target.enabled && target.visible && target !== password && target.clicked !== undefined) {
                if (!event.isAutoRepeat)
                    target.clicked();
                event.accepted = true;
            }
        } else {
            Keyboard.handle(card, event);
        }
    }
    Keys.onReleased: event => {
        if (controller.inputBlocked)
            event.accepted = true;
    }
    Keys.onEscapePressed: event => {
        if (!controller.inputBlocked) {
            if (keyOpen) {
                keyOpen = false;
                keyButton.forceActiveFocus(Qt.BacktabFocusReason);
            } else if (detailsOpen) {
                detailsOpen = false;
                detailsButton.forceActiveFocus(Qt.BacktabFocusReason);
            } else {
                controller.cancel("cancel");
            }
        }
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
    // Carry the clock's plate and compact line through the same morph as its drawer.
    // The authorization controls retain their own established appearance once grown.
    Rectangle {
        anchors.fill: parent
        visible: !card.glassReady
        radius: card.radius
        color: LiquidPalette.flatPlate
        opacity: 1 - card.contentAlpha
    }
    Rectangle {
        readonly property var drop: card.islandDrop
        visible: !card.glassReady && drop !== null
        x: drop ? drop.rect.x - card.x : 0
        y: drop ? drop.rect.y - card.y : 0
        width: drop ? drop.rect.z : 0
        height: drop ? drop.rect.w : 0
        radius: drop ? drop.params.x : 0
        opacity: drop ? drop.params.w : 0
        color: LiquidPalette.flatDrop
        border.width: 1
        border.color: LiquidPalette.flatDropRim
    }
    Item {
        id: clockInk
        x: -card.x
        y: -card.y
        width: card.viewportWidth
        height: card.islandY + Metrics.islandHeight + 16
        ClockCompactRow {
            x: card.islandX
            y: card.islandY
            width: card.islandWidth
            time: card.time
            date: card.date
            dnd: card.dnd
            notificationCount: card.notificationCount
            ink: LiquidPalette.inkOnLight
            dim: LiquidPalette.dimOnLight
            accent: LiquidPalette.accentOnLight
            opacity: card.lineAlpha * (1 - card.islandAlpha)
        }
    }
    ShaderEffectSource {
        id: clockTexture
        width: clockInk.width
        height: clockInk.height
        sourceItem: clockInk
        hideSource: card.glassReady
        live: true
        visible: false
    }
    IslandGlass {
        visible: card.glassReady && card.contentAlpha < 1
        backdrop: card.localBackdrop
        sceneOffset: Qt.point(card.x, card.y)
        plate: Qt.rect(card.x, card.y, card.width, card.height)
        drops: card.islandDrop ? [card.islandDrop] : []
        uRadius: card.radius
        uIcons: clockTexture
        uSceneSize: Qt.point(clockInk.width, clockInk.height)
        uCover: card.localBackdrop ? card.localBackdrop.coverFor(Qt.point(0, 0)) : Qt.vector4d(0, 0, 1, 1)
        uOpacity: 1 - card.contentAlpha
    }
    ClockCompactRow {
        x: card.islandX - card.x
        width: card.islandWidth
        time: card.time
        date: card.date
        dnd: card.dnd
        notificationCount: card.notificationCount
        ink: LiquidPalette.inkOnLight
        dim: LiquidPalette.dimOnLight
        accent: LiquidPalette.accentOnLight
        opacity: card.lineAlpha * card.islandAlpha
    }
    PolkitHazardFrame {
        anchors.fill: parent
        opacity: card.contentAlpha
        reducedMotion: card.reducedMotion
    }
    Rectangle {
        visible: !LiquidPalette.sharedCanvas
        anchors.fill: parent
        anchors.margins: 10
        radius: 20
        color: LiquidPalette.polkitCard
        opacity: card.contentAlpha
    }
    // With the shared canvas the whole card is the Settings material (it has no top part).
    // It grows in with the card body, like the flat fill it replaces.
    Loader {
        anchors.fill: parent
        anchors.margins: 10
        opacity: card.contentAlpha
        active: LiquidPalette.sharedCanvas
        sourceComponent: Item {
            WelcomeGlass {
                anchors.fill: parent
                radius: 20
                wallpaperTexture: card.wallpaperTexture
                dpr: card.dpr
            }
            CanvasTint {
                objectName: "polkit-canvas"
                anchors.fill: parent
                radius: 20
            }
        }
    }
    Flickable {
        anchors.fill: parent
        anchors.margins: 10
        opacity: card.contentAlpha
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
                    color: LiquidPalette.polkitAccent
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
                        text: card.agentRequest ? qsTranslate("PolkitPromptCard", "AI agent") : qsTranslate("PolkitPromptCard", "Administrator rights")
                        textFormat: Text.PlainText
                        font.family: ShellPalette.uiFont
                        font.pixelSize: 11
                        font.capitalization: Font.AllUppercase
                        font.letterSpacing: 0.6
                        font.weight: Font.DemiBold
                        color: LiquidPalette.polkitDanger
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
                        color: LiquidPalette.polkitInk
                    }
                }
            }
            Rectangle {
                id: field
                x: card.shakeOffset
                width: parent.width
                height: 42
                radius: 12
                color: LiquidPalette.polkitField
                border.width: password.activeFocus || card.controller.wrong ? 2 : 1
                border.color: card.controller.wrong ? ShellPalette.critical : password.activeFocus ? LiquidPalette.polkitFocus : LiquidPalette.polkitBorder
                TextInput {
                    id: password
                    objectName: "polkit-password"
                    anchors.left: parent.left
                    anchors.leftMargin: 12
                    anchors.right: reveal.left
                    anchors.rightMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                    enabled: card.controller.accepting
                    activeFocusOnTab: true
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
                        if ([Qt.Key_Up, Qt.Key_Down].includes(event.key)) {
                            // Keep editing; Tab reaches the reveal control.
                            event.accepted = true;
                        } else if ([Qt.Key_Tab, Qt.Key_Backtab].includes(event.key)) {
                            Keyboard.handle(card, event);
                        }
                        if (event.matches(StandardKey.Copy) || event.matches(StandardKey.Cut) || event.matches(StandardKey.SelectAll))
                            event.accepted = true;
                        if ((event.modifiers & Qt.ShiftModifier) && [Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down, Qt.Key_Home, Qt.Key_End, Qt.Key_PageUp, Qt.Key_PageDown].includes(event.key))
                            event.accepted = true;
                    }
                    clip: true
                    font.family: ShellPalette.uiFont
                    font.pixelSize: 14
                    color: LiquidPalette.polkitInk
                    onAccepted: card.submit()
                    Text {
                        anchors.fill: parent
                        visible: !password.text.length
                        text: qsTranslate("PolkitPromptCard", "Password for %1").arg(card.controller.backend.accountName || Copy.unavailable())
                        textFormat: Text.PlainText
                        font: password.font
                        color: LiquidPalette.polkitDim
                        elide: Text.ElideRight
                    }
                }
                PasswordToggle {
                    id: reveal
                    objectName: "polkit-reveal"
                    anchors.right: parent.right
                    anchors.rightMargin: 5
                    anchors.verticalCenter: parent.verticalCenter
                    ink: LiquidPalette.polkitDim
                    Accessible.name: revealed ? qsTranslate("PolkitPromptCard", "Hide") : qsTranslate("PolkitPromptCard", "Show")
                    Keys.onPressed: event => {
                        if (event.key === Qt.Key_Up || event.key === Qt.Key_Left) {
                            card.focusPassword();
                            event.accepted = true;
                        } else {
                            Keyboard.handle(card, event);
                        }
                    }
                    enabled: card.controller.accepting
                    onToggled: {
                        password.deselect();
                        revealed = !revealed;
                        card.focusPassword();
                    }
                }
            }
            Text {
                objectName: "polkit-error"
                width: parent.width
                // A closing card has no request behind it any more; that is not an unsupported request.
                visible: card.controller.wrong || (card.controller.opened && !card.controller.backend.passwordRequest)
                text: card.controller.wrong ? qsTranslate("PolkitPromptCard", "That password is not right. Try again.") : Copy.unsupported()
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                font.family: ShellPalette.uiFont
                font.pixelSize: 12
                color: LiquidPalette.polkitDanger
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
                            color: LiquidPalette.polkitDim
                        }
                        Text {
                            width: parent.width
                            text: detail.modelData.value
                            textFormat: Text.PlainText
                            wrapMode: Text.WrapAnywhere
                            font.family: ShellPalette.uiFont
                            font.pixelSize: 12
                            color: LiquidPalette.polkitInk
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
                    objectName: "polkit-details"
                    text: qsTranslate("PolkitPromptCard", "Details")
                    checked: card.detailsOpen
                    onClicked: card.detailsOpen = !card.detailsOpen
                }
                PolkitExpander {
                    id: keyButton
                    objectName: "polkit-sudo-key"
                    visible: card.keyAvailable
                    text: qsTranslate("PolkitPromptCard", "Sudo key")
                    checked: card.keyOpen
                    onClicked: card.keyOpen = !card.keyOpen
                }
                Item {
                    width: Math.max(0, content.width - detailsButton.width - (keyButton.visible ? keyButton.width : 0) - cancelButton.width - allowButton.width - (keyButton.visible ? 16 : 12))
                    height: 1
                }
                SettingsButton {
                    id: cancelButton
                    text: qsTranslate("PolkitPromptCard", "Cancel")
                    onClicked: card.controller.cancel("cancel")
                }
                SettingsButton {
                    id: allowButton
                    text: card.keyOpen ? qsTranslate("PolkitPromptCard", "Allow with sudo key") : qsTranslate("PolkitPromptCard", "Allow")
                    font.weight: Font.DemiBold
                    enabled: card.controller.accepting && (!card.keyOpen || (card.keyGrantAllowed && keyChoices.amountValid))
                    background: Rectangle {
                        radius: 12
                        color: card.keyOpen ? LiquidPalette.polkitKey : LiquidPalette.polkitAllow
                        opacity: allowButton.enabled ? 1 : 0.5
                        border.width: allowButton.activeFocus ? 2 : 0
                        border.color: ShellPalette.critical
                    }
                    contentItem: Text {
                        text: allowButton.text
                        textFormat: Text.PlainText
                        font: allowButton.font
                        color: card.keyOpen ? LiquidPalette.polkitKeyText : LiquidPalette.polkitAllowText
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    onClicked: card.submit()
                }
            }
        }
    }
}
