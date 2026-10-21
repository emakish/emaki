// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: service
    required property NotificationStore store
    property bool active: false
    property bool soundEnabled: true
    property string helper: Quickshell.shellPath("helpers/clock-tools.py")
    property var soundCommand: [Platform.python, "-I", "-B", Quickshell.shellPath("helpers/clock-chime.py")]
    readonly property var words: ({
            app: "Clocks",
            disconnected: qsTranslate("ClockToolsService", "Clocks is reconnecting. Please try again shortly."),
            invalid: qsTranslate("ClockToolsService", "Clocks returned an unreadable response."),
            timer: qsTranslate("ClockToolsService", "Timer finished"),
            work: qsTranslate("ClockToolsService", "Time to focus"),
            rest: qsTranslate("ClockToolsService", "Time for a break"),
            alarm: qsTranslate("ClockToolsService", "Alarm"),
            restart: qsTranslate("ClockToolsService", "Restart"),
            dismiss: qsTranslate("ClockToolsService", "Dismiss"),
            snooze: qsTranslate("ClockToolsService", "Snooze 5 min"),
            stop: qsTranslate("ClockToolsService", "Stop"),
            cycle: qsTranslate("ClockToolsService", "Cycle %1"),
            soundFailed: qsTranslate("ClockToolsService", "The clock sound could not be played.")
        })
    property var state: ({
            ready: false,
            error: "",
            timer: {
                status: "idle",
                remaining: 300,
                duration: 300,
                pomodoro: false,
                phase: "work",
                cycle: 1
            },
            stopwatch: {
                running: false,
                elapsed: 0,
                laps: []
            },
            alarms: [],
            world: [],
            search: [],
            notices: []
        })
    property var presented: ({})
    property bool firstSnapshot: true
    property real startedAt: Date.now()
    property bool recoveryRequested: false
    property var pendingDismissals: ({})
    property var ringing: ({})
    readonly property bool soundRunning: sound.running
    property bool reconciling: false
    function recover(): void {
        if (!state.recoverable || worker.running)
            return;
        retry.stop();
        recoveryRequested = true;
        worker.running = true;
    }
    function playChime(): void {
        if (!sound.running)
            sound.running = true;
    }
    function repeatChime(now: real): void {
        for (const key of Object.keys(ringing)) {
            if (now >= ringing[key])
                delete ringing[key];
        }
        if (Object.keys(ringing).length)
            playChime();
        else {
            repeatSound.stop();
            sound.running = false;
        }
    }
    function flushDismissals(): void {
        if (!worker.running || !state.ready)
            return;
        for (const key of Object.keys(pendingDismissals))
            worker.write(JSON.stringify({
                op: "notice_dismiss",
                id: key
            }) + "\n");
    }
    function silence(id: string): void {
        delete ringing[id];
        if (!Object.keys(ringing).length) {
            repeatSound.stop();
            sound.running = false;
        }
    }

    property int retryMs: 1000
    function send(command: var): bool {
        if (!worker.running || !state.ready) {
            state = Object.assign({}, state, {
                error: words.disconnected
            });
            return false;
        }
        worker.write(JSON.stringify(command) + "\n");
        return true;
    }
    function actionKey(notice: var, action: string): string {
        return "clock:" + encodeURIComponent(notice.id) + ":" + action;
    }
    function accept(line: string): void {
        try {
            const next = JSON.parse(line);
            if (next.ready === false && next.error) {
                state = Object.assign({}, state, {
                    ready: false,
                    error: next.error,
                    recoverable: next.recoverable === true
                });
                return;
            }
            if (!next.timer || !next.stopwatch || !Array.isArray(next.notices))
                throw new Error("protocol");
            state = next;
            retryMs = 1000;
            reconcile(firstSnapshot);
            firstSnapshot = false;
            flushDismissals();
        } catch (_) {
            state = Object.assign({}, state, {
                ready: false,
                error: words.invalid
            });
        }
    }
    function reconcile(restoring: bool): void {
        reconciling = true;
        const current = state.notices || [];
        for (const key of Object.keys(presented)) {
            if (!current.some(n => String(n.id) === key)) {
                store.dismiss([presented[key]]);
                delete presented[key];
                silence(key);
            }
        }
        for (const key of Object.keys(pendingDismissals)) {
            if (!current.some(n => String(n.id) === key))
                delete pendingDismissals[key];
        }
        let chime = false;
        for (const notice of current) {
            if (presented[String(notice.id)] !== undefined || pendingDismissals[String(notice.id)])
                continue;
            const issued = Number(notice.issued ?? notice.created) * 1000;
            // A notice "issued" after now means the system clock went back: treat it as old, never as fresh.
            const restored = restoring && (issued < startedAt || issued > Date.now());
            const alarm = notice.kind === "alarm";
            const pomodoro = notice.kind === "pomodoro";
            const title = alarm ? (notice.label || words.alarm) : pomodoro ? (notice.phase === "break" ? words.rest : words.work) : words.timer;
            const actions = alarm ? [
                {
                    id: actionKey(notice, "snooze"),
                    text: words.snooze
                },
                {
                    id: actionKey(notice, "stop"),
                    text: words.stop
                }
            ] : pomodoro ? [
                {
                    id: actionKey(notice, "dismiss"),
                    text: words.dismiss
                }
            ] : [
                {
                    id: actionKey(notice, "restart"),
                    text: words.restart
                },
                {
                    id: actionKey(notice, "dismiss"),
                    text: words.dismiss
                }
            ];
            presented[String(notice.id)] = store.transientLocal(words.app, title, pomodoro ? words.cycle.arg(notice.cycle) : "", actions, {
                silent: restored,
                time: Number(notice.created) * 1000,
                clockAlert: !pomodoro
            });
            if (!restored && !pomodoro && Number(notice.created) * 1000 + 60000 > Date.now()) {
                ringing[String(notice.id)] = Math.min(Number(notice.created) * 1000 + 60000, Date.now() + 60000);
                repeatSound.start();
                chime = true;
            } else if (!restored && pomodoro && soundEnabled && store.soundAllowed("name:" + words.app)) {
                chime = true;
            }
        }
        reconciling = false;
        if (chime)
            playChime();
    }
    function handleAction(key: string): void {
        const match = /^clock:([^:]+):(restart|dismiss|snooze|stop)$/.exec(key);
        if (!match)
            return;
        const notice = state.notices.find(n => String(n.id) === decodeURIComponent(match[1]));
        if (!notice)
            return;
        silence(String(notice.id));
        if (match[2] === "restart")
            send({
                op: "timer_restart"
            });
        else if (match[2] === "snooze" || match[2] === "stop")
            send({
                op: "alarm_" + match[2],
                id: notice.alarmId
            });
        else {
            pendingDismissals[String(notice.id)] = true;
            store.dismiss([presented[String(notice.id)]]);
            flushDismissals();
        }
    }
    function dismissedNotices(): void {
        if (reconciling)
            return;
        for (const key of Object.keys(presented)) {
            if (!store.entries.some(n => n.id === presented[key])) {
                const notice = state.notices.find(n => String(n.id) === key);
                if (notice) {
                    pendingDismissals[key] = true;
                    silence(key);
                }
                // Keep the identifier until the worker confirms dismissal, so an
                // already queued snapshot cannot recreate the card or its sound.
            }
        }
        flushDismissals();
    }
    Connections {
        target: service.store
        function onLocalActionRequested(action: string): void {
            service.handleAction(action);
        }
        function onEntriesChanged(): void {
            if (!service.reconciling)
                Qt.callLater(service.dismissedNotices);
        }
    }
    onActiveChanged: {
        retry.stop();
        if (active)
            startedAt = Date.now();
        firstSnapshot = true;
        ringing = ({});
        repeatSound.stop();
        sound.running = false;
        worker.running = active;
    }
    Process {
        id: worker
        command: [Platform.python, "-I", "-B", service.helper, "--serve"].concat(service.recoveryRequested ? ["--recover"] : [])
        running: service.active
        stdinEnabled: true
        stdout: SplitParser {
            onRead: line => service.accept(line)
        }
        onExited: {
            service.firstSnapshot = true;
            service.recoveryRequested = false;
            service.ringing = ({});
            repeatSound.stop();
            sound.running = false;
            service.state = Object.assign({}, service.state, {
                ready: false,
                error: service.state.error || service.words.disconnected
            });
            if (service.active && !service.state.recoverable) {
                retry.interval = service.retryMs;
                service.retryMs = Math.min(60000, service.retryMs * 2);
                retry.start();
            }
        }
    }
    Timer {
        id: retry
        onTriggered: if (service.active)
            worker.running = true
    }
    Timer {
        id: repeatSound
        interval: 4000
        repeat: true
        onTriggered: service.repeatChime(Date.now())
    }
    Process {
        id: sound
        command: service.soundCommand
        onExited: (code, status) => {
            if (code !== 0)
                console.warn(service.words.soundFailed);
        }
    }
}
