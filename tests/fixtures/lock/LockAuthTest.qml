pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

ShellRoot {
    id: root
    property int successes: 0
    property int failures: 0
    property int clears: 0
    property int savedAttempt: 0
    LockAuth {
        id: auth
        onAuthenticated: ++root.successes
        onRejected: ++root.failures
        onClearInput: ++root.clears
        onUsernameModeChanged: {
            if (usernameMode)
                throw new Error("Lock authentication must stay in password mode");
        }
    }
    IpcHandler {
        id: test
        target: "test"
        function state(): string {
            if (auth.usernameMode)
                throw new Error("Lock authentication must stay in password mode");
            return JSON.stringify({
                shellPath: Quickshell.shellPath(""),
                shaderPath: Quickshell.shellPath("shaders"),
                bufferLength: auth.length(auth.buffer),
                dotCount: auth.dotCount,
                checking: auth.checking,
                succeeded: auth.succeeded,
                queued: auth.queued,
                ready: auth.fieldReady,
                awaiting: auth.awaitingResponse,
                prompt: auth.prompt,
                message: auth.message,
                displayMessage: auth.displayMessage,
                lockoutRemaining: auth.lockoutRemaining,
                kind: auth.messageKind,
                attempt: auth.attemptId,
                successes: root.successes,
                failures: root.failures,
                clears: root.clears
            });
        }
        function action(name: string): bool {
            if (name === "early")
                return auth.edit("fixture-éЖ🔒");
            if (name === "erase")
                return auth.edit("");
            if (name === "cap")
                return auth.edit("🔒".repeat(1024));
            if (name === "excess")
                return auth.edit("🔒".repeat(1025));
            if (name === "nul")
                return auth.edit("bad\u0000input");
            if (name === "submit")
                auth.submit();
            else if (name === "ready")
                auth.fieldReady = true;
            else if (name === "notReady")
                auth.fieldReady = false;
            else if (name === "cancel")
                auth.cancel();
            else if (name === "suspend")
                auth.suspend();
            else if (name === "expireLockout") {
                auth._lockoutUntil = Date.now() - 1;
                auth.updateLockoutTime();
            } else if (name === "reset")
                auth.reset();
            else if (name === "disable")
                auth.enabled = false;
            else if (name === "enable")
                auth.enabled = true;
            else if (name === "save")
                root.savedAttempt = auth.attemptId;
            else if (name === "lateSuccess")
                auth.complete(root.savedAttempt, "success");
            else if (name === "timeout")
                auth.watchdog.triggered();
            else
                return false;
            return true;
        }
        // These methods are fixture-only; no status or authentication IPC is
        // exported by the production controller.
        function challenge(text: string, visible: bool): void {
            auth.challenge(auth.attemptId, text, visible);
        }
        function notice(text: string, error: bool): void {
            auth.notice(auth.attemptId, text, error);
        }
        function finish(outcome: string): void {
            auth.complete(auth.attemptId, outcome);
        }
        function answerStats(): string {
            return JSON.stringify({
                responses: auth._attempt ? auth._attempt.responses : 0,
                length: auth._attempt ? auth._attempt.responseLength : 0
            });
        }
    }
    property int lastRequest: 0
    FileView {
        id: commands
        path: Quickshell.env("EMAKI_AUTH_FIXTURE") + "/request.json"
        blockLoading: true
        printErrors: false
    }
    FileView {
        id: replies
        path: Quickshell.env("EMAKI_AUTH_FIXTURE") + "/reply.json"
        atomicWrites: true
        printErrors: false
    }
    Timer {
        interval: 10
        running: true
        repeat: true
        onTriggered: {
            commands.reload();
            const request = JSON.parse(commands.text() || "{}");
            if (!request.id || request.id === root.lastRequest)
                return;
            root.lastRequest = request.id;
            const result = test[request.method].apply(test, request.args);
            replies.setText(JSON.stringify({
                id: request.id,
                value: result === undefined ? null : result
            }));
        }
    }
    Component.onCompleted: Quickshell.watchFiles = false
}
