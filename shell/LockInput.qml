pragma ComponentBehavior: Bound
import QtQuick

// Qt handles composed text, dead keys, AltGr and Unicode. No physical-key mapping.
Item {
    id: input
    required property AuthController auth
    property bool revealed: false
    property bool showToggle: true
    property real toggleX: width / 2 + 118
    property real toggleY: height / 2 - 16
    onShowToggleChanged: if (!showToggle)
        revealed = false
    signal engaged
    signal edited
    function takeFocus(): void {
        editor.forceActiveFocus();
    }
    TextInput {
        id: editor
        anchors.fill: parent
        opacity: 0
        focus: true
        KeyNavigation.tab: revealButton
        KeyNavigation.backtab: revealButton
        echoMode: input.auth.usernameMode || input.revealed ? TextInput.Normal : TextInput.Password
        passwordMaskDelay: 0
        maximumLength: 2147483647
        inputMethodHints: Qt.ImhSensitiveData | Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase
        selectByMouse: false
        readOnly: !input.auth.enabled || (input.auth.checking && !input.auth.awaitingResponse)
        Accessible.ignored: true
        onActiveFocusChanged: {
            if (activeFocus)
                input.engaged();
        }
        onTextEdited: {
            input.auth.edit(text);
            if (text !== input.auth.buffer)
                text = input.auth.buffer;
            cursorPosition = text.length;
            input.edited();
        }
        Keys.onPressed: event => {
            input.engaged();
            if (!input.auth.enabled) {
                event.accepted = true;
                return;
            }
            if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                if (!event.isAutoRepeat)
                    input.auth.submit();
                event.accepted = true;
            } else if (event.key === Qt.Key_Escape) {
                input.auth.cancel();
                event.accepted = true;
            } else if (event.matches(StandardKey.Copy) || event.matches(StandardKey.Cut) || event.matches(StandardKey.Paste)) {
                // A lock must not read or publish the session clipboard.
                event.accepted = true;
            }
        }
    }
    Connections {
        target: input.auth
        function onBufferChanged(): void {
            if (!input.auth.buffer.length)
                input.revealed = false;
            editor.text = input.auth.buffer;
            editor.cursorPosition = editor.text.length;
        }
        function onClearInput(): void {
            input.revealed = false;
            editor.clear();
        }
        function onEnabledChanged(): void {
            input.revealed = false;
        }
        function onCheckingChanged(): void {
            input.revealed = false;
        }
        function onUsernameModeChanged(): void {
            input.revealed = false;
        }
        function onPromptChanged(): void {
            input.revealed = false;
        }
    }
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
        onClicked: {
            input.engaged();
            input.takeFocus();
        }
        Accessible.role: Accessible.EditableText
        Accessible.name: input.auth.prompt || "Password"
        Accessible.description: input.auth.displayMessage
        Accessible.onPressAction: input.takeFocus()
    }
    PasswordToggle {
        id: revealButton
        x: input.toggleX
        y: input.toggleY
        visible: input.showToggle && !input.auth.usernameMode
        enabled: input.auth.enabled && !input.auth.checking
        ink: LiquidPalette.inkOnLight
        revealed: input.revealed
        onToggled: {
            input.engaged();
            input.revealed = !input.revealed;
        }
        KeyNavigation.tab: editor
        KeyNavigation.backtab: editor
    }
}
