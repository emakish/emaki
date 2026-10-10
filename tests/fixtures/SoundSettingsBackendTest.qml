// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

ShellRoot {
    id: root
    property int failures: 0
    function check(value: bool, message: string): void {
        if (!value) {
            ++failures;
            console.error("SOUND_SETTINGS_FAIL " + message);
        }
    }
    QtObject {
        id: outputAudio
        property real volume: .42
        property bool muted: false
    }
    QtObject {
        id: inputAudio
        property real volume: .64
        property bool muted: true
    }
    QtObject {
        id: appAudio
        property real volume: .75
        property bool muted: false
    }
    property var output: ({
            id: 1,
            isSink: true,
            audio: outputAudio
        })
    property var input: ({
            id: 2,
            isSink: false,
            audio: inputAudio
        })
    SystemBackend {
        id: fake
        audioReady: true
        sink: root.output
        source: root.input
        audioNodes: [root.output, root.input]
        streams: [
            {
                id: 3,
                name: "Player",
                audio: appAudio
            }
        ]
        function chooseOutput(id: int): var {
            preferredOutputId = id;
            return () => sink?.id === id;
        }
        function chooseInput(id: int): var {
            preferredInputId = id;
            return () => source?.id === id;
        }
        function resetOutput(): var {
            preferredOutputId = -1;
            return () => preferredOutputId === -1;
        }
        function resetInput(): var {
            preferredInputId = -1;
            return () => preferredInputId === -1;
        }
    }
    SystemBackend {
        id: replacement
        audioReady: true
    }
    SoundSettingsBackend {
        id: sound
        backend: fake
        confirmationDelay: 0
    }
    Timer {
        interval: 1
        running: true
        onTriggered: root.run()
    }
    function run(): void {
        check(sound.ready && sound.outputs.length === 1 && sound.inputs.length === 1, "separate device lists");
        check(sound.outputs[0] === output && sound.inputs[0] === input, "direction stays correct");
        check(outputAudio.volume === .42 && inputAudio.volume === .64 && appAudio.volume === .75, "loading does not write defaults");
        sound.active = true;
        fake.micLevel = .65;
        check(fake.settingsMicMeter && sound.inputLevel === .65, "live active input meter");
        fake.micMeter = true;
        sound.active = false;
        check(!fake.settingsMicMeter && fake.micMeter && sound.inputLevel === 0, "closing releases only settings meter");
        sound.active = true;
        sound.backend = replacement;
        check(!fake.settingsMicMeter && replacement.settingsMicMeter, "replacement releases old meter");
        sound.backend = fake;
        check(!replacement.settingsMicMeter && fake.settingsMicMeter, "replacement tracks new meter");
        check(sound.chooseOutput(1) && sound.chooseInput(2), "device selection accepted");
        sound.confirm();
        check(sound.preferredOutputId === 1 && sound.preferredInputId === 2 && !sound.error, "selection confirmed");
        check(sound.chooseOutput(-1) && sound.chooseInput(-1), "automatic policy accepted");
        sound.confirm();
        check(sound.preferredOutputId === -1 && sound.preferredInputId === -1 && !sound.error, "automatic policy restored");
        check(sound.setOutputVolume(70) && sound.setInputVolume(35), "independent volume writes");
        sound.confirm();
        check(Math.abs(outputAudio.volume - .7) < .001 && Math.abs(inputAudio.volume - .35) < .001 && !sound.error, "volume direction and percent");
        check(sound.setOutputMuted(true) && sound.setInputMuted(false), "explicit mute writes");
        sound.confirm();
        check(outputAudio.muted && !inputAudio.muted && !sound.error, "mute direction");
        sound.setOutputMuted(true);
        sound.confirm();
        check(outputAudio.muted, "same mute value does not toggle");
        check(sound.setStreamVolume(3, 25), "stream volume accepted");
        sound.confirm();
        check(Math.abs(appAudio.volume - .25) < .001 && !sound.error, "stream volume applied");
        check(!sound.setOutputVolume(101) && outputAudio.volume === .7 && !!sound.error, "out of range rejected");
        check(!sound.setInputVolume(NaN) && inputAudio.volume === .35, "nonfinite volume rejected");
        sound.setOutputVolume(80);
        outputAudio.volume = .7;
        sound.confirm();
        check(!!sound.error && !sound.busy, "server rejection is visible");
        sound.setOutputVolume(60);
        sound.setOutputVolume(50);
        sound.confirm();
        check(!sound.error && outputAudio.volume === .5, "latest slider request supersedes its predecessor");
        sound.setOutputVolume(30);
        fake.sink = null;
        sound.confirm();
        check(!!sound.error, "device disappearance cannot confirm old object");
        check(!sound.setOutputVolume(20), "missing output rejects write");
        sound.setStreamVolume(3, 90);
        fake.streams = [];
        sound.confirm();
        check(!!sound.error, "ended stream cannot confirm old object");
        check(!sound.setStreamVolume(3, 50), "ended stream rejects next write");
        sound.chooseInput(999);
        sound.confirm();
        check(!!sound.error, "selection not reflected is visible");
        fake.audioReady = false;
        check(!sound.ready && sound.inputs.length === 0 && sound.streams.length === 0 && !!sound.error, "disconnected service is unavailable");
        check(!sound.setInputMuted(true), "disconnected service refuses write");
        fake.audioReady = true;
        fake.sink = output;
        sound.setOutputVolume(70);
        outputAudio.volume = .5;
        finish.start();
    }
    Timer {
        id: finish
        interval: 250
        onTriggered: {
            root.check(!sound.busy && !!sound.error, "service rejection is reported by the timer");
            console.log("SOUND_SETTINGS_RESULT " + root.failures);
            Qt.quit();
        }
    }
}
