// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    NotificationStore {
        id: notifications
    }
    ClockToolsService {
        id: service
        active: true
        store: notifications
        soundCommand: ["true"]
    }
    NotificationStore {
        id: policyStore
    }
    QtObject {
        id: policy
        property var values: []
        function set(key, value) {
        }
    }
    ClockToolsService {
        id: policyService
        store: policyStore
        soundCommand: ["true"]
    }
    SignalSpy {
        id: arrivals
        target: policyStore
        signalName: "arrived"
    }
    SignalSpy {
        id: sounds
        target: policyService
        signalName: "soundRunningChanged"
    }
    Window {
        visible: true
        width: 100
        height: 100
        TestCase {
            name: "ClockToolsService"
            when: true
            function settled(predicate) {
                tryVerify(predicate, 7000);
            }
            function test_notice_policy() {
                // This second store exercises policy only; the lifecycle store owns history.
                policyStore.foreign = true;
                const old = Date.now() - 120000;
                const base = {
                    ready: true,
                    timer: {},
                    stopwatch: {},
                    notices: [
                        {
                            id: "restored",
                            kind: "timer",
                            created: old / 1000
                        }
                    ]
                };
                policyService.accept(JSON.stringify(base));
                compare(arrivals.count, 0);
                compare(sounds.count, 0);
                compare(policyStore.entries[0].time, old);
                verify(policyStore.entries[0].transient);
                policyStore.catalog = policy;
                for (const quiet of ["off", "manual", "scheduled", "until"]) {
                    policy.values = [
                        {
                            key: "notifications.dnd",
                            value: quiet === "manual"
                        },
                        {
                            key: "notifications.until",
                            value: quiet === "until" ? String(Date.now() + 3600000) : "0"
                        },
                        {
                            key: "notifications.schedule",
                            value: JSON.stringify({
                                enabled: quiet === "scheduled",
                                start: "00:00",
                                end: "00:00"
                            })
                        },
                        {
                            key: "sound.system_sounds",
                            value: false
                        }
                    ];
                    policyService.soundEnabled = false;
                    compare(policyStore.effectiveDnd, quiet !== "off");
                    const id = "alarm-" + quiet;
                    base.notices.push({
                        id: id,
                        kind: quiet === "until" ? "timer" : "alarm",
                        created: Date.now() / 1000
                    });
                    const arrivalsBefore = arrivals.count;
                    const soundsBefore = sounds.count;
                    policyService.accept(JSON.stringify(base));
                    compare(arrivals.count, arrivalsBefore + 1);
                    verify(policyStore.peekAllowed(policyStore.entries[0]));
                    verify(policyStore.entries[0].clockAlert);
                    tryVerify(() => sounds.count > soundsBefore);
                    tryVerify(() => !policyService.soundRunning);
                    const repeatsBefore = sounds.count;
                    if (quiet !== "off")
                        policyService.repeatChime(Date.now() + 4000);
                    tryVerify(() => sounds.count > repeatsBefore, 5000);
                    tryVerify(() => !policyService.soundRunning);
                    policyService.repeatChime(Date.now() + 61000);
                    compare(Object.keys(policyService.ringing).length, 0);
                }
                for (const action of ["stop", "snooze", "restart", "dismiss"]) {
                    const notice = {
                        id: "action-" + action,
                        alarmId: "alarm",
                        kind: action === "stop" || action === "snooze" ? "alarm" : "timer",
                        created: Date.now() / 1000
                    };
                    base.notices.push(notice);
                    policyService.accept(JSON.stringify(base));
                    verify(Object.keys(policyService.ringing).length > 0);
                    policyService.handleAction(policyService.actionKey(notice, action));
                    if (action === "dismiss")
                        verify(policyService.pendingDismissals[notice.id]);
                    compare(Object.keys(policyService.ringing).length, 0);
                    tryVerify(() => !policyService.soundRunning);
                    wait(100);
                }
                const before = sounds.count;
                base.notices.push({
                    id: "quiet-phase",
                    kind: "pomodoro",
                    phase: "work",
                    cycle: 2,
                    created: Date.now() / 1000
                });
                policyService.accept(JSON.stringify(base));
                wait(100);
                compare(sounds.count, before);
                policyService.startedAt = Date.now();
                policyService.firstSnapshot = true;
                policyService.presented = ({});
                policyStore.entries = [];
                const beforeRestore = arrivals.count;
                policyService.accept(JSON.stringify(base));
                compare(arrivals.count, beforeRestore);
                compare(sounds.count, before);
            }
            function test_lifecycle() {
                settled(() => service.state.ready);
                verify(service.send({
                    op: "timer_start",
                    seconds: 1,
                    pomodoro: false
                }));
                settled(() => service.state.timer.status === "finished");
                settled(() => notifications.entries.some(n => n.summary === service.words.timer));
                let note = notifications.entries.find(n => n.summary === service.words.timer);
                compare(note.actions.length, 2);
                verify(notifications.activate(note.id, note.actions[0].id));
                settled(() => service.state.timer.status === "running");
                service.send({
                    op: "timer_reset"
                });
                settled(() => service.state.timer.status === "idle");
                service.send({
                    op: "stopwatch_start"
                });
                settled(() => service.state.stopwatch.elapsed > .2);
                service.send({
                    op: "stopwatch_lap"
                });
                settled(() => service.state.stopwatch.laps.length === 1);
                service.active = false;
                settled(() => !service.state.ready);
                wait(100);
                service.active = true;
                settled(() => service.state.ready);
                verify(service.state.stopwatch.running);
                compare(service.state.stopwatch.laps.length, 1);
                service.send({
                    op: "stopwatch_stop"
                });
                settled(() => !service.state.stopwatch.running);
                service.send({
                    op: "world_search",
                    query: "Kathmandu"
                });
                settled(() => service.state.search.includes("Asia/Kathmandu"));
                service.send({
                    op: "world_add",
                    zone: "Asia/Kathmandu"
                });
                settled(() => service.state.world.length === 1);
                verify(/^[+-]\d\d:\d\d$/.test(service.state.world[0].offset));
                const now = new Date();
                service.send({
                    op: "alarm_add",
                    hour: now.getHours(),
                    minute: now.getMinutes(),
                    days: [],
                    label: "Morning"
                });
                settled(() => notifications.entries.some(n => n.summary === "Morning"));
                note = notifications.entries.find(n => n.summary === "Morning");
                compare(note.actions[0].text, service.words.snooze);
                compare(note.actions[1].text, service.words.stop);
                const alarmId = service.state.alarms[0].id;
                verify(notifications.activate(note.id, note.actions[0].id));
                settled(() => service.state.alarms[0].snooze > 0);
                compare(service.state.notices.filter(n => n.kind === "alarm").length, 0);
                service.send({
                    op: "alarm_stop",
                    id: alarmId
                });
                settled(() => !service.state.alarms[0].snooze);
                service.send({
                    op: "alarm_remove",
                    id: alarmId
                });
                settled(() => service.state.alarms.length === 0);
                service.send({
                    op: "alarm_add",
                    hour: now.getHours(),
                    minute: now.getMinutes(),
                    days: [],
                    label: "Second"
                });
                settled(() => notifications.entries.some(n => n.summary === "Second"));
                note = notifications.entries.find(n => n.summary === "Second");
                verify(notifications.activate(note.id, note.actions[1].id));
                settled(() => service.state.notices.length === 0);
                service.send({
                    op: "timer_start",
                    seconds: 1,
                    pomodoro: false
                });
                settled(() => service.state.timer.status === "finished");
                settled(() => notifications.entries.some(n => n.summary === service.words.timer));
                note = notifications.entries.find(n => n.summary === service.words.timer);
                notifications.dismiss([note.id]);
                settled(() => service.state.notices.length === 0);
                service.send({
                    op: "timer_start",
                    seconds: 1,
                    pomodoro: false
                });
                settled(() => service.state.timer.status === "finished");
                settled(() => notifications.entries.some(n => n.summary === service.words.timer));
                // Dismissing while disconnected is replayed on the first reconnect snapshot.
                note = notifications.entries.find(n => n.summary === service.words.timer);
                service.active = false;
                settled(() => !service.state.ready);
                notifications.dismiss([note.id]);
                settled(() => Object.keys(service.pendingDismissals).length === 1);
                service.active = true;
                settled(() => service.state.ready && service.state.notices.length === 0);
                compare(Object.keys(service.pendingDismissals).length, 0);
                notifications.local("Files", "Keep me", "");
                service.send({
                    op: "timer_start",
                    seconds: 1,
                    pomodoro: false
                });
                settled(() => service.state.notices.length === 1);
                wait(1000);
                service.active = false;
            }
            onCompletedChanged: if (completed)
                console.log("CLOCK_SERVICE_RESULT", qtest_results.passCount, qtest_results.failCount)
        }
    }
}
