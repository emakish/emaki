// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick

// Settings adapts the shell's tracked PipeWire objects; fixtures supply the same seam.
Item {
    id: sound
    property var backend: null
    property bool active: false
    readonly property bool ready: backend?.audioReady === true
    readonly property var sink: ready ? backend.sink : null
    readonly property var source: ready ? backend.source : null
    readonly property int preferredOutputId: ready ? backend.preferredOutputId : -1
    readonly property int preferredInputId: ready ? backend.preferredInputId : -1
    readonly property var outputs: ready ? Array.from(backend?.audioNodes ?? []).filter(n => n.isSink) : []
    readonly property var inputs: ready ? Array.from(backend?.audioNodes ?? []).filter(n => !n.isSink) : []
    readonly property var streams: ready ? backend.streams : []
    readonly property real inputLevel: active && source?.audio ? Math.max(0, Math.min(1, backend.micLevel)) : 0
    readonly property string error: !ready ? "Sound is unavailable. Check that the audio service is running." : actionError
    readonly property bool busy: Object.keys(pending).length > 0
    property string actionError: ""
    property var pending: ({})
    property var meterOwner: null
    // Wait for service echoes, including a rejected optimistic property write.
    property int confirmationDelay: 1200

    function updateMeter(): void {
        if (meterOwner && meterOwner !== backend)
            meterOwner.settingsMicMeter = false;
        meterOwner = backend;
        if (meterOwner)
            meterOwner.settingsMicMeter = active;
    }
    onActiveChanged: updateMeter()
    onBackendChanged: {
        pending = ({});
        updateMeter();
    }
    onReadyChanged: {
        if (!ready)
            pending = ({});
    }
    Component.onCompleted: updateMeter()
    Component.onDestruction: {
        if (meterOwner)
            meterOwner.settingsMicMeter = false;
    }

    function fail(message: string): bool {
        actionError = message;
        return false;
    }
    function request(key: string, perform: var, message: string): bool {
        if (!ready)
            return fail("Sound is unavailable. Check that the audio service is running.");
        try {
            const check = perform();
            if (typeof check !== "function")
                return fail(message);
            const next = Object.assign({}, pending);
            next[key] = {
                check: check,
                deadline: Date.now() + confirmationDelay,
                message: message
            };
            pending = next;
            actionError = "";
            return true;
        } catch (e) {
            return fail(message);
        }
    }
    function confirm(): void {
        const next = Object.assign({}, pending);
        for (const key of Object.keys(next)) {
            const attempt = next[key];
            if (Date.now() < attempt.deadline)
                continue;
            try {
                if (!ready || !attempt.check())
                    fail(attempt.message);
            } catch (e) {
                fail(attempt.message);
            }
            delete next[key];
        }
        pending = next;
    }
    Timer {
        interval: 100
        repeat: true
        running: sound.busy
        onTriggered: sound.confirm()
    }
    function chooseOutput(id: int): bool {
        if (id === -1)
            return resetOutput();
        return request("output", () => backend.chooseOutput(id), "The output device could not be changed. It may have disconnected.");
    }
    function chooseInput(id: int): bool {
        if (id === -1)
            return resetInput();
        return request("input", () => backend.chooseInput(id), "The input device could not be changed. It may have disconnected.");
    }
    function resetOutput(): bool {
        return request("output", () => backend.resetOutput(), "The automatic output device could not be restored.");
    }
    function resetInput(): bool {
        return request("input", () => backend.resetInput(), "The automatic input device could not be restored.");
    }
    function setVolume(input: bool, percent: real): bool {
        if (!Number.isFinite(percent) || percent < 0 || percent > 100)
            return fail("Choose a volume between 0 and 100 percent.");
        const node = input ? source : sink;
        const message = input ? "The input volume could not be changed." : "The output volume could not be changed.";
        return request(input ? "input-volume" : "output-volume", () => {
            if (!node?.audio || node.ready === false)
                return false;
            const check = backend.act(input ? "mic-volume" : "volume", percent);
            return typeof check === "function" ? () => (input ? source : sink) === node && check() : false;
        }, message);
    }
    function setOutputVolume(percent: real): bool {
        return setVolume(false, percent);
    }
    function setInputVolume(percent: real): bool {
        return setVolume(true, percent);
    }
    function setMuted(input: bool, muted: bool): bool {
        const node = input ? source : sink;
        return request(input ? "input-mute" : "output-mute", () => {
            if (!node?.audio || node.ready === false)
                return false;
            if (node.audio.muted !== muted)
                backend.act(input ? "mic" : "mute", null);
            return () => (input ? source : sink) === node && !!node.audio && node.audio.muted === muted;
        }, input ? "The microphone mute setting could not be changed." : "The output mute setting could not be changed.");
    }
    function setOutputMuted(muted: bool): bool {
        return setMuted(false, muted);
    }
    function setInputMuted(muted: bool): bool {
        return setMuted(true, muted);
    }
    function setStreamVolume(id: int, percent: real): bool {
        if (!Number.isFinite(percent) || percent < 0 || percent > 100)
            return fail("Choose a volume between 0 and 100 percent.");
        const stream = Array.from(streams).find(s => s.id === id);
        return request("stream-" + id, () => {
            if (!stream?.audio)
                return false;
            const check = backend.act("stream-volume", {
                id: id,
                percent: percent
            });
            return typeof check === "function" ? () => Array.from(streams).some(s => s.id === id && s.audio === stream.audio) && check() : false;
        }, "The application volume could not be changed. Its audio stream may have ended.");
    }
}
