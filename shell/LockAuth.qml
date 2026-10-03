pragma ComponentBehavior: Bound
import QtQuick

// Process-wide authentication. No output owns this object and no visual state
// can manufacture success. The input widget owns no authoritative password copy.
AuthController {
    id: root
    property LockPam _attempt: null
    property bool _answeredPassword: false
    property string _pamMessage: ""
    property bool _pamMessageError: false

    function edit(text: string): bool {
        if (!enabled || _terminal || (checking && !_awaitingInput))
            return false;
        if (length(text) > maximumLength || text.indexOf("\u0000") !== -1) {
            message = text.indexOf("\u0000") !== -1 ? "This character cannot be used in a password" : "Password is too long (maximum 1024 characters)";
            messageKind = "input";
            return false;
        }
        buffer = text;
        dotCount = length(text);
        if (messageKind === "input") {
            message = "";
            messageKind = "";
        }
        return true;
    }
    function submit(): void {
        if (!enabled || _terminal)
            return;
        if (checking) {
            if (_awaitingInput && _attempt) {
                _awaitingInput = false;
                const response = buffer;
                wipe(false);
                watchdog.restart();
                _attempt.respond(response);
            }
            return;
        }
        // Additional PAM prompts can explicitly accept an empty response.
        if (!buffer.length)
            return;
        if (!fieldReady) {
            queued = true;
            return;
        }
        queued = false;
        checking = true;
        _answeredPassword = false;
        _pamMessage = "";
        _pamMessageError = false;
        prompt = "Password";
        message = "";
        messageKind = "";
        messageTimer.stop();
        ++attemptId;
        _attempt = pamFactory.createObject(root, {
            attemptId: attemptId
        });
        if (!_attempt) {
            complete(attemptId, "technical");
            return;
        }
        _attempt.challenge.connect(root.challenge);
        _attempt.notice.connect(root.notice);
        _attempt.finished.connect(root.complete);
        watchdog.restart();
        _attempt.begin();
    }
    function current(id: int): bool {
        return enabled && checking && !_terminal && id === attemptId;
    }
    function challenge(id: int, text: string, visible: bool): void {
        if (!current(id))
            return;
        // Never give a pretyped password to an OTP, visible username prompt,
        // PIN, or a second password request. Unknown prompts require new input.
        if (!_answeredPassword && !visible && /^\s*(?:unix\s+)?password\s*:\s*$/i.test(text) && buffer.length) {
            _answeredPassword = true;
            const response = buffer;
            wipe(false);
            watchdog.restart();
            _attempt.respond(response);
            return;
        }
        _answeredPassword = true;
        wipe(true);
        prompt = text.trim() || "Authentication response";
        _awaitingInput = true;
        watchdog.restart();
    }
    function notice(id: int, text: string, isError: bool): void {
        if (!current(id))
            return;
        _pamMessage = text;
        _pamMessageError = isError;
        message = text;
        messageKind = isError ? "pam-error" : "pam";
    }
    function complete(id: int, outcome: string): void {
        if (!current(id))
            return;
        watchdog.stop();
        checking = false;
        queued = false;
        _awaitingInput = false;
        // Retain only the non-secret dot count while the authenticated field
        // melts. The logical secret and every input copy are still cleared.
        wipe(outcome !== "success");
        release();
        prompt = "Password";
        if (outcome === "success") {
            _terminal = true;
            message = "";
            messageKind = "";
            authenticated(id);
        } else if (outcome === "rejected") {
            // Keep the stack's faillock/account message; do not turn it into
            // an ordinary wrong-password warning or hide it after two seconds.
            if (_pamMessage.length) {
                message = _pamMessage;
                messageKind = _pamMessageError ? "pam-error" : "pam";
            } else {
                message = "Wrong password";
                messageKind = "wrong";
                messageTimer.restart();
            }
            rejected();
        } else {
            message = outcome === "locked" ? (_pamMessage || "Account locked. Try again later.") : "Authentication error. Please try again.";
            messageKind = outcome === "locked" ? "pam-error" : "technical";
        }
        _pamMessage = "";
    }
    function wipe(dots: bool): void {
        buffer = "";
        if (dots)
            dotCount = 0;
        clearInput();
    }
    function release(): void {
        const old = _attempt;
        _attempt = null;
        if (old) {
            old.abort();
            old.destroy();
        }
    }
    function cancel(): void {
        // Invalidate first: abort may synchronously emit a completion.
        ++attemptId;
        _terminal = false;
        checking = false;
        queued = false;
        _awaitingInput = false;
        watchdog.stop();
        messageTimer.stop();
        wipe(true);
        release();
        prompt = "Password";
        message = "";
        messageKind = "";
        _pamMessage = "";
        _pamMessageError = false;
    }
    function suspend(): void {
        cancel();
        _terminal = false;
    }
    function reset(): void {
        cancel();
        _terminal = false;
    }
    onFieldReadyChanged: {
        if (fieldReady && queued)
            submit();
    }
    onEnabledChanged: {
        // A successful attempt remains current throughout the authenticated
        // melt/drain. Its buffer is already empty; an explicit cancel or sleep
        // revokes success and advances the identity.
        if (!enabled && !_terminal)
            cancel();
    }
    readonly property Component pamFactory: Component {
        LockPam {}
    }
    readonly property Timer messageTimer: Timer {
        interval: 2000
        onTriggered: {
            if (root.messageKind === "wrong") {
                root.message = "";
                root.messageKind = "";
            }
        }
    }
    readonly property Timer watchdog: Timer {
        interval: 60000
        onTriggered: root.complete(root.attemptId, "technical")
    }
}
