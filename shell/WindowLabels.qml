// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

// Private presentation channel, never serialized by CLI or diagnostic IPC.
// Enumeration, workspace IDs and all actions still come from the core model.
Scope {
    id: labels
    required property NiriService niri
    required property bool active
    property var values: ({})
    // When each window last held the focus (niri's focus_timestamp, in ms; absent: never
    // focused). Kept apart from `values`: focus moves often and must not rebuild the labels'
    // readers. The window switcher orders by it, most recent first.
    property var focusTimes: ({})
    // Where each tiled window sits in its workspace view, as Emaki's niri reports it
    // (`layout.emaki_tile_pos_in_view`): the switcher's live pictures. False vetoes crops
    // during transitions or with an older fork; null means safe with no private position.
    property var positions: ({})
    property string state: "closed"
    property bool acknowledged: false
    // A window the compositor opened after the initial snapshot (never the snapshot itself).
    signal appeared(int id, string appId)
    readonly property bool wanted: active && niri.connected && Quickshell.env("NIRI_SOCKET") !== ""
    function stamp(value: var): var {
        const secs = Number(value?.secs), nanos = Number(value?.nanos);
        return Number.isFinite(secs) && Number.isFinite(nanos) ? secs * 1000 + nanos / 1e6 : undefined;
    }
    function place(map: var, id: var, layout: var): void {
        const at = layout?.emaki_tile_pos_in_view;
        // False is an output-wide crop veto, including floating windows and old forks.
        if (layout?.emaki_capture_safe !== true)
            map[id] = false;
        else if (Array.isArray(at) && at.length === 2 && at.every(Number.isFinite))
            map[id] = [at[0], at[1]];
        else
            map[id] = null; // Known safe, with only the upstream floating position.
    }
    function reset(): void {
        socket.connected = false;
        values = {};
        focusTimes = {};
        positions = {};
        acknowledged = false;
        state = wanted ? "connecting" : "closed";
        initial.stop();
        retry.restart();
    }
    onWantedChanged: reset()
    Connections {
        target: labels.niri
        function onGenerationChanged(): void {
            labels.reset();
        }
    }
    function receive(line: string): void {
        if (!wanted || !socket.connected)
            return;
        try {
            const event = JSON.parse(line);
            if (!acknowledged) {
                if (event.Ok !== "Handled")
                    throw new Error("ack");
                acknowledged = true;
                return;
            }
            if (event.WindowLayoutsChanged) {
                const moved = Object.assign({}, positions);
                for (const change of event.WindowLayoutsChanged.changes ?? [])
                    if (Array.isArray(change) && Number.isSafeInteger(change[0]))
                        place(moved, change[0], change[1]);
                positions = moved;
                return;
            }
            if (event.WindowFocusTimestampChanged) {
                const change = event.WindowFocusTimestampChanged;
                if (Number.isSafeInteger(change.id) && change.id in values) {
                    const times = Object.assign({}, focusTimes);
                    const time = stamp(change.focus_timestamp);
                    if (time === undefined)
                        delete times[change.id];
                    else
                        times[change.id] = time;
                    focusTimes = times;
                }
                return;
            }
            let next = Object.assign({}, values);
            let times = focusTimes;
            let places = positions;
            if (event.WindowsChanged) {
                next = {};
                times = {};
                places = {};
                for (const w of event.WindowsChanged.windows)
                    if (Number.isSafeInteger(w.id)) {
                        next[w.id] = ({
                                title: String(w.title ?? ""),
                                appId: String(w.app_id ?? "")
                            });
                        const time = stamp(w.focus_timestamp);
                        if (time !== undefined)
                            times[w.id] = time;
                        place(places, w.id, w.layout);
                    }
                state = "ready";
                initial.stop();
            } else if (event.WindowOpenedOrChanged) {
                const w = event.WindowOpenedOrChanged.window;
                if (Number.isSafeInteger(w.id)) {
                    const fresh = state === "ready" && !(w.id in values);
                    next[w.id] = ({
                            title: String(w.title ?? ""),
                            appId: String(w.app_id ?? "")
                        });
                    const time = stamp(w.focus_timestamp);
                    if (time !== undefined && time !== times[w.id])
                        times = Object.assign({}, times, {
                            [w.id]: time
                        });
                    places = Object.assign({}, places);
                    place(places, w.id, w.layout);
                    if (fresh)
                        appeared(w.id, next[w.id].appId);
                }
            } else if (event.WindowClosed) {
                delete next[event.WindowClosed.id];
                if (event.WindowClosed.id in times) {
                    times = Object.assign({}, times);
                    delete times[event.WindowClosed.id];
                }
                if (event.WindowClosed.id in places) {
                    places = Object.assign({}, places);
                    delete places[event.WindowClosed.id];
                }
            } else {
                return; // Layout/keyboard/cast events cannot change private labels.
            }
            values = next;
            if (times !== focusTimes)
                focusTimes = times;
            if (places !== positions)
                positions = places;
        } catch (_) {
            reset();
            state = "invalid_response";
        }
    }
    Socket {
        id: socket
        path: Quickshell.env("NIRI_SOCKET")
        onConnectionStateChanged: {
            if (connected) {
                write('"EventStream"\n');
                flush();
                initial.restart();
            } else {
                labels.values = {};
                labels.focusTimes = {};
                labels.positions = {};
                labels.acknowledged = false;
                labels.state = labels.wanted ? "unavailable" : "closed";
                retry.restart();
            }
        }
        onError: {
            labels.values = {};
            labels.focusTimes = {};
            labels.positions = {};
            labels.state = labels.wanted ? "unavailable" : "closed";
            retry.restart();
        }
        parser: SplitParser {
            onRead: line => labels.receive(line)
        }
    }
    Timer {
        id: retry
        interval: 500
        onTriggered: {
            if (labels.wanted && !socket.connected)
                socket.connected = true;
        }
    }
    Timer {
        id: initial
        interval: 2000
        onTriggered: {
            labels.reset();
            labels.state = "timeout";
        }
    }
}
