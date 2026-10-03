pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

ShellRoot {
    id: root
    property int successes: 0
    property int failures: 0
    property int fatals: 0
    property int releases: 0
    property int handoffs: 0
    property int lastRequest: 0
    property bool autoLaunch: false
    property string selectedUser: ""
    property int userSelections: 0
    GreeterAuth {
        id: auth
        onLaunched: Qt.quit()
        user: "fixture-user"
        sessionCommand: ["/usr/bin/niri-session", "--fixture"]
        handoffMarker: '{"v":1,"token":"0123456789abcdef0123456789abcdef","createdMs":1,"idleMode":"lake","clock":2,"introStart":0.9}'
        onAuthenticated: ++root.successes
        onRejected: ++root.failures
        onFatalError: ++root.fatals
        onSelectUser: user => {
            ++root.userSelections;
            root.selectedUser = user;
            auth.user = user;
        }
    }
    GreeterSession {
        id: session
        auth: auth
        started: true
        secure: true
        onReleaseLock: ++root.releases
        onHandoffRequested: {
            ++root.handoffs;
            if (root.autoLaunch)
                auth.launch();
        }
    }
    function state(): var {
        return {
            bufferLength: auth.length(auth.buffer),
            pendingLength: auth.length(auth._pendingPassword),
            recovering: auth._recovering,
            technicalFailures: auth._technicalFailures,
            dotCount: auth.dotCount,
            checking: auth.checking,
            succeeded: auth.succeeded,
            queued: auth.queued,
            ready: auth.fieldReady,
            prompt: auth.prompt,
            message: auth.message,
            kind: auth.messageKind,
            attempt: auth.attemptId,
            successes: successes,
            failures: failures,
            fatals: fatals,
            releases: releases,
            handoffs: handoffs,
            phase: session.phase,
            wrongAt: session.wrongAt,
            user: auth.user,
            usernameMode: auth.usernameMode,
            selectedUser: selectedUser,
            userSelections: userSelections
        };
    }
    function action(name: string): bool {
        if (name === "password")
            return auth.edit("fixture-éЖ🔒");
        if (name === "cap")
            return auth.edit("🔒".repeat(1024));
        if (name === "excess")
            return auth.edit("🔒".repeat(1025));
        if (name === "nul")
            return auth.edit("bad\u0000input");
        if (name === "username")
            return auth.edit("fixture-user");
        if (name === "submit")
            auth.submit();
        else if (name === "ready") {
            session.clock = 2;
            session.go("locked");
        } else if (name === "cancel")
            auth.cancel();
        else if (name === "reset")
            auth.reset();
        else if (name === "suspend")
            auth.suspend();
        else if (name === "launch")
            auth.launch();
        else if (name === "autoLaunch")
            root.autoLaunch = true;
        else if (name === "noMarker")
            auth.handoffMarker = "";
        else if (name === "shortWatchdog")
            auth.watchdog.interval = 150;
        else if (name === "breakStart")
            auth.authWorker.command = ["/fixture/no-such-auth-helper"];
        else if (name === "shortStartGuard")
            auth.startGuard.interval = 150;
        else if (name === "slowWatchdog")
            auth.watchdog.interval = 2000;
        else if (name === "usernameStep") {
            auth.user = "";
        } else if (name === "selectionPending")
            auth.selectionReady = false;
        else if (name === "selectionReady")
            auth.selectionReady = true;
        else if (name === "unknownUser") {
            auth.message = "Unknown user";
            auth.messageKind = "username";
        } else if (name === "disable")
            auth.enabled = false;
        else if (name === "enable")
            auth.enabled = true;
        else
            return false;
        return true;
    }
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
            const result = request.method === "state" ? root.state() : root.action(request.args[0]);
            replies.setText(JSON.stringify({
                id: request.id,
                value: result
            }));
        }
    }
    Component.onCompleted: Quickshell.watchFiles = false
}
