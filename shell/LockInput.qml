pragma ComponentBehavior: Bound
import QtQuick

// Qt handles composed text, dead keys, AltGr and Unicode. No physical-key mapping.
Item {
    id: input
    required property AuthController auth
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
        echoMode: input.auth.usernameMode ? TextInput.Normal : TextInput.Password
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
            editor.text = input.auth.buffer;
            editor.cursorPosition = editor.text.length;
        }
        function onClearInput(): void {
            editor.clear();
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
        Accessible.description: input.auth.message
        Accessible.onPressAction: input.takeFocus()
    }
}
