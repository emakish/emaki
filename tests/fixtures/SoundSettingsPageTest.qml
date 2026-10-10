// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    id: root
    QtObject {
        id: outputAudio
        property real volume: 0.4
        property bool muted: false
    }
    QtObject {
        id: inputAudio
        property real volume: 0.7
        property bool muted: false
    }
    QtObject {
        id: streamAudio
        property real volume: 0.6
        property bool muted: false
    }
    QtObject {
        id: backend
        property bool audioReady: false
        property bool settingsMicMeter: false
        property real micLevel: 0.2
        property int preferredOutputId: -1
        property int preferredInputId: -1
        property var sink: ({
                id: 10,
                description: "Speakers",
                isSink: true,
                audio: outputAudio
            })
        property var source: ({
                id: 20,
                description: "Microphone",
                isSink: false,
                audio: inputAudio
            })
        property var audioNodes: [sink, source]
        property var streams: [
            {
                id: 30,
                name: "Music",
                detail: "Playback",
                audio: streamAudio
            }
        ]
        property var calls: []
        property bool reject: false
        function record(kind, value) {
            calls = calls.concat([
                {
                    kind: kind,
                    value: value
                }
            ]);
        }
        function chooseOutput(id) {
            record("output", id);
            if (reject)
                return false;
            preferredOutputId = id;
            return () => preferredOutputId === id;
        }
        function chooseInput(id) {
            record("input", id);
            preferredInputId = id;
            return () => preferredInputId === id;
        }
        function resetOutput() {
            return chooseOutput(-1);
        }
        function resetInput() {
            return chooseInput(-1);
        }
        function act(kind, value) {
            record(kind, value);
            if (kind === "volume")
                outputAudio.volume = value / 100;
            if (kind === "mic-volume")
                inputAudio.volume = value / 100;
            if (kind === "mute")
                outputAudio.muted = !outputAudio.muted;
            if (kind === "mic")
                inputAudio.muted = !inputAudio.muted;
            if (kind === "stream-volume")
                streamAudio.volume = value.percent / 100;
            return () => true;
        }
    }
    FloatingWindow {
        id: window
        visible: true
        implicitWidth: 760
        implicitHeight: 1060
        color: "#eee4db"
        Rectangle {
            id: capture
            width: parent.width
            height: loader.item ? loader.item.height + 48 : 1060
            color: "#eee4db"
            Loader {
                id: loader
                x: 24
                y: 24
                width: parent.width - 48
                active: true
                sourceComponent: SoundSettingsPage {
                    backend: backend
                }
            }
        }
    }
    TestCase {
        name: "SoundSettingsPage"
        when: window.backingWindowVisible
        onCompletedChanged: if (completed)
            console.log("SOUND_PAGE_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
        function find(item, name) {
            if (item.objectName === name)
                return item;
            for (const child of item.children ?? []) {
                const result = find(child, name);
                if (result)
                    return result;
            }
            return null;
        }
        function row(name) {
            const found = find(loader.item, "settings-sound-" + name);
            verify(found !== null, name);
            return found;
        }
        function test_controls() {
            tryVerify(() => loader.item !== null);
            backend.audioReady = true;
            verify(backend.settingsMicMeter);
            compare(row("output").options.length, 2);
            row("output").requestValue(10);
            compare(backend.preferredOutputId, 10);
            compare(row("output").value, 10);
            row("output").requestReset();
            compare(backend.preferredOutputId, -1);
            row("input").requestValue(20);
            compare(backend.preferredInputId, 20);
            row("input").requestReset();
            compare(backend.preferredInputId, -1);
            for (const entry of [["volume", outputAudio], ["input-volume", inputAudio], ["stream-30", streamAudio]]) {
                row(entry[0]).requestValue(35);
                compare(entry[1].volume, 0.35);
                compare(row(entry[0]).value, 35);
                row(entry[0]).requestReset();
                compare(entry[1].volume, 1);
            }
            compare(backend.calls.filter(c => c.kind === "stream-volume")[0].value.id, 30);
            row("mute").requestValue(true);
            verify(outputAudio.muted);
            row("mute").requestReset();
            verify(!outputAudio.muted);
            row("mute").controlItem.forceActiveFocus();
            keyClick(Qt.Key_Space);
            verify(outputAudio.muted);
            mouseClick(row("mute").resetItem);
            verify(!outputAudio.muted);
            row("volume").controlItem.forceActiveFocus();
            const volumeCalls = backend.calls.filter(call => call.kind === "volume").length;
            for (let i = 0; i < 3; i++)
                keyClick(Qt.Key_Left);
            compare(outputAudio.volume, 1);
            compare(backend.calls.filter(call => call.kind === "volume").length, volumeCalls);
            tryCompare(outputAudio, "volume", 0.97);
            compare(backend.calls.filter(call => call.kind === "volume").length, volumeCalls + 1);
            mouseClick(row("volume").resetItem);
            compare(outputAudio.volume, 1);
            row("input-mute").requestValue(true);
            verify(inputAudio.muted);
            row("input-mute").requestReset();
            verify(!inputAudio.muted);
            backend.micLevel = 0.75;
            compare(loader.item.sound.inputLevel, 0.75);
            compare(row("input-level").controlItem.Accessible.description, "75%");
            compare(find(loader.item, "setting-sound.system_sounds"), null);
            backend.reject = true;
            row("output").requestValue(10);
            verify(loader.item.sound.error.length > 0);
            backend.reject = false;
            backend.audioReady = false;
            const count = backend.calls.length;
            row("volume").requestValue(44);
            row("input").requestValue(20);
            compare(backend.calls.length, count);
            verify(row("volume").unavailable);
            verify(loader.item.sound.error.indexOf("unavailable") >= 0);
            backend.audioReady = true;
            const oldSink = backend.sink;
            const oldSource = backend.source;
            backend.audioNodes = [];
            backend.sink = null;
            backend.source = null;
            compare(row("input").explanation, "No device connected");
            compare(row("output").explanation, "No device connected");
            compare(row("input-volume").explanation, "No device connected");
            compare(row("volume").explanation, "No device connected");
            backend.streams = [];
            verify(row("volume").unavailable);
            verify(row("input-mute").unavailable);
            compare(row("output").options.length, 1);
            compare(loader.item.sound.inputLevel, 0);
            backend.sink = oldSink;
            backend.source = oldSource;
            backend.audioNodes = [oldSink, oldSource];
            backend.streams = [
                {
                    id: 30,
                    name: "Music",
                    detail: "Playback",
                    audio: streamAudio
                }
            ];
            loader.item.sound.actionError = "";
            wait(100);
            let saved = false;
            capture.grabToImage(result => saved = result.saveToFile(Quickshell.env("SOUND_SETTINGS_SHOT")));
            tryVerify(() => saved, 5000);
            loader.visible = false;
            tryCompare(backend, "settingsMicMeter", false);
            compare(loader.item.sound.inputLevel, 0);
            loader.visible = true;
            tryCompare(backend, "settingsMicMeter", true);
            loader.active = false;
            tryCompare(backend, "settingsMicMeter", false);
        }
    }
}
