pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

// Only greetd can authenticate a login. No PAM factory, password IPC, or file
// persistence lives here. Enter moves input into a private pending response;
// its single secret prompt and every terminal path clear that submitted secret.
AuthController {
    id: root
    property string user: ""
    property list<string> sessionCommand: []
    signal launched
    property string handoffMarker: ""
    property bool selectionReady: true
    property bool _answeredPassword: false
    property bool _fatal: false
    property bool _launching: false
    property bool _recovering: false
    property bool _cleanupSeen: false
    property bool _cleanupCleared: false
    property bool _cleanupOk: false
    property bool _starting: false
    property bool _beginSent: false
    property bool _cleanupOnly: false
    property string _outcome: ""
    property string _cleanupReason: ""
    property int _workerAttempt: -1
    property bool _retryBlocked: false
    property bool _launched: false
    property int _technicalFailures: 0
    property string _pendingPassword: ""
    property string _pamMessage: ""
    property bool _pamMessageError: false
    usernameMode: !user.length
    prompt: usernameMode ? "Username" : "Password"
    signal selectUser(string user)
    signal fatalError

    function edit(text: string): bool {
        if (!enabled || !selectionReady || checking || _terminal || _fatal)
            return false;
        if (length(text) > maximumLength || text.indexOf("\u0000") !== -1) {
            message = text.indexOf("\u0000") !== -1 ? (usernameMode ? "This character cannot be used in a username" : "This character cannot be used in a password") : (usernameMode ? "Username is too long (maximum 1024 characters)" : "Password is too long (maximum 1024 characters)");
            messageKind = "input";
            return false;
        }
        buffer = text;
        dotCount = length(text);
        if (messageKind === "input" || messageKind === "username") {
            message = "";
            messageKind = "";
        }
        return true;
    }
    function submit(): void {
        if (!enabled || !selectionReady || checking || _terminal || _fatal || !buffer.length)
            return;
        if (_recovering || _retryBlocked) {
            queued = true;
            if (_retryBlocked && !_recovering)
                beginCleanup();
            return;
        }
        if (!fieldReady) {
            queued = true;
            return;
        }
        queued = false;
        if (usernameMode) {
            const name = buffer.trim();
            wipe(true);
            if (name.length)
                selectUser(name);
            return;
        }
        ++attemptId;
        _answeredPassword = false;
        _pamMessage = "";
        _pamMessageError = false;
        message = "";
        messageKind = "";
        checking = true;
        _pendingPassword = buffer;
        wipe(false);
        if (!(Quickshell.env("GREETD_SOCK") || "").length || !sessionCommand.length) {
            failTransport();
            return;
        }
        watchdog.restart();
        _outcome = "pending";
        _cleanupOnly = false;
        startWorker();
    }
    function wipe(dots: bool): void {
        buffer = "";
        if (dots)
            dotCount = 0;
        clearInput();
    }
    function complete(outcome: string): void {
        if (!checking || _terminal || _fatal)
            return;
        watchdog.stop();
        checking = false;
        queued = false;
        _pendingPassword = "";
        wipe(outcome !== "success");
        if (outcome === "success") {
            _technicalFailures = 0;
            _terminal = true;
            message = "";
            messageKind = "";
            authenticated(attemptId);
        } else if (outcome === "rejected") {
            _technicalFailures = 0;
            if (_pamMessage.length) {
                message = _pamMessage;
                messageKind = _pamMessageError ? "pam-error" : "pam";
            } else {
                message = "Wrong password";
                messageKind = "wrong";
            }
            rejected();
        } else {
            message = "Authentication error. Please try again.";
            messageKind = "technical";
        }
        _pamMessage = "";
    }
    function startWorker(): void {
        _workerAttempt = attemptId;
        _cleanupSeen = false;
        _cleanupCleared = false;
        _cleanupOk = false;
        _beginSent = false;
        _starting = true;
        authWorker.stdinEnabled = true;
        startGuard.restart();
        authWorker.running = true;
    }
    function retireWorker(): void {
        // The descriptor owner must drain a pending reply even if QProcess kills
        // its supervisor during Qt.exit. Retirement never sends a late global
        // cancel into a replacement greeter's newer conversation.
        if (authWorker.running) {
            authWorker.write('{"op":"retire"}\n');
            authWorker.stdinEnabled = false;
        }
    }
    function failTransport(): void {
        if (_fatal)
            return;
        watchdog.stop();
        startGuard.stop();
        _starting = false;
        checking = false;
        queued = false;
        _recovering = false;
        _pendingPassword = "";
        _fatal = true;
        retireWorker();
        wipe(true);
        message = "Authentication error. Opening another login screen.";
        messageKind = "technical";
        Qt.callLater(function () {
            root.fatalError();
        });
    }
    function cancel(): void {
        if (_terminal || _launching || _fatal)
            return;
        const active = checking;
        ++attemptId;
        checking = false;
        queued = false;
        wipe(true);
        message = "";
        messageKind = "";
        _pamMessage = "";
        _pamMessageError = false;
        _pendingPassword = "";
        if (active) {
            _outcome = "cancel";
            _cleanupReason = "cancel";
            _recovering = true;
            if (_beginSent)
                authWorker.write('{"op":"abandon"}\n');
            // Before the first begin message no request exists to drain. The
            // onStarted handler retires that unused worker instead of starting it.
        } else if (!_recovering) {
            watchdog.stop();
        }
    }
    function beginCleanup(): void {
        _cleanupReason = "cancel";
        _outcome = "cancel";
        _cleanupOnly = true;
        _recovering = true;
        _retryBlocked = false;
        startWorker();
        watchdog.restart();
    }
    function startFailed(): void {
        if (!_starting || _fatal)
            return;
        _starting = false;
        startGuard.stop();
        if (_outcome === "cancel" && !_cleanupOnly && !_beginSent && !authWorker.running) {
            _recovering = false;
            if (queued)
                submit();
        } else {
            failTransport();
        }
    }
    function workerExited(): void {
        startGuard.stop();
        _starting = false;
        if (_fatal || _launched)
            return;
        if (_terminal || _launching) {
            failTransport();
            return;
        }
        watchdog.stop();
        if (_outcome === "cancel" && !_cleanupOnly && !_beginSent) {
            _recovering = false;
            if (queued)
                submit();
            return;
        }
        if (_outcome !== "cancel" && _outcome !== "rejected" && _outcome !== "technical") {
            failTransport();
            return;
        }
        if (!_cleanupSeen || !_cleanupCleared) {
            if (_outcome !== "cancel") {
                failTransport();
                return;
            }
            _recovering = false;
            _retryBlocked = true;
            queued = false;
            message = "Authentication error. Please try again.";
            messageKind = "technical";
            return;
        }
        if (_outcome === "technical" && (!_cleanupOk || _technicalFailures >= 3)) {
            failTransport();
            return;
        }
        _recovering = false;
        _retryBlocked = false;
        if (queued)
            submit();
    }
    function workerEvent(line: string): void {
        if (_fatal)
            return;
        let value;
        try {
            if (line.length > 65536)
                throw new Error("oversize reply");
            value = JSON.parse(line);
            if (!value || typeof value.event !== "string" || typeof value.attempt !== "number")
                throw new Error("invalid reply");
        } catch (_) {
            failTransport();
            return;
        }
        if (value.attempt !== _workerAttempt)
            return;
        if (value.event === "hello") {
            _starting = false;
            startGuard.stop();
            return;
        }
        if (value.event === "cleaned") {
            _cleanupSeen = true;
            _cleanupOk = value.ok === true;
            _cleanupCleared = value.cleared === true;
            return;
        }
        if (_outcome === "cancel" || value.attempt !== attemptId)
            return;
        if (_terminal) {
            if (_launching && value.event === "launched") {
                _launched = true;
                watchdog.stop();
                launched();
            } else {
                failTransport();
            }
            return;
        }
        if (!checking)
            return;
        watchdog.restart();
        if (value.event === "prompt") {
            if (_answeredPassword || !_pendingPassword.length) {
                failTransport();
                return;
            }
            _answeredPassword = true;
            authWorker.write(JSON.stringify({
                op: "respond",
                password: _pendingPassword
            }) + "\n");
            _pendingPassword = "";
        } else if (value.event === "message" && typeof value.message === "string" && typeof value.error === "boolean") {
            _pamMessage = value.message;
            _pamMessageError = value.error;
            message = value.message;
            messageKind = value.error ? "pam-error" : "pam";
        } else if (value.event === "ready" && _answeredPassword) {
            _outcome = "ready";
            complete("success");
        } else if (value.event === "rejected") {
            _outcome = "rejected";
            _recovering = true;
            complete("rejected");
            watchdog.restart();
        } else if (value.event === "technical") {
            _outcome = "technical";
            _recovering = true;
            complete("technical");
            watchdog.restart();
            ++_technicalFailures;
        } else {
            failTransport();
        }
    }
    function recoveryEnvironment(): var {
        return {
            GREETD_SOCK: Quickshell.env("GREETD_SOCK")
        };
    }
    function suspend(): void {
        cancel();
    }
    function reset(): void {
        cancel();
    }
    function launch(): void {
        if (!_terminal || _fatal || _launching)
            return;
        if (_outcome !== "ready" || !authWorker.running || !sessionCommand.length) {
            failTransport();
            return;
        }
        _launching = true;
        watchdog.restart();
        const environment = ["XDG_SESSION_TYPE=wayland"];
        // Decoration is optional and never authenticates or blocks a session.
        if (handoffMarker.length)
            environment.push("EMAKI_LOGIN_HANDOFF=" + handoffMarker);
        authWorker.write(JSON.stringify({
            op: "launch",
            cmd: sessionCommand,
            env: environment
        }) + "\n");
    }
    onFieldReadyChanged: {
        if (fieldReady && queued)
            submit();
    }
    onEnabledChanged: {
        if (!enabled && !_terminal)
            cancel();
    }
    readonly property Process authWorker: Process {
        command: [Platform.python, "-I", "-B", Quickshell.shellPath("helpers/greeter-auth.py"), String(Quickshell.processId)]
        clearEnvironment: true
        environment: root.recoveryEnvironment()
        stdinEnabled: true
        onStarted: {
            if ((root.checking && root._workerAttempt === root.attemptId) || root._cleanupOnly) {
                root._beginSent = true;
                write(JSON.stringify({
                    op: root._cleanupOnly ? "cleanup" : "begin",
                    user: root.user,
                    attempt: root._workerAttempt
                }) + "\n");
            } else {
                root.retireWorker();
            }
        }
        onRunningChanged: {
            if (!running && root._starting) {
                Qt.callLater(function () {
                    if (!root.authWorker.running)
                        root.startFailed();
                });
            }
        }
        stdout: SplitParser {
            onRead: line => root.workerEvent(line)
        }
        stderr: SplitParser {
            onRead: _line => {}
        }
        onExited: root.workerExited()
    }
    readonly property Timer startGuard: Timer {
        interval: 1000
        onTriggered: root.startFailed()
    }
    readonly property Timer watchdog: Timer {
        interval: 60000
        onTriggered: root.failTransport()
    }
}
