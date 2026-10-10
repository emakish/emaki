// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import "settings"

Column {
    id: page
    required property var backend
    readonly property alias sound: sound
    spacing: 20
    function label(node): string {
        return node ? String(node.description || node.nickname || node.name || node.id) : "No device connected";
    }
    function options(nodes): var {
        return [
            {
                label: "Automatic",
                value: -1
            }
        ].concat(nodes.map(node => ({
                    label: label(node),
                    value: node.id
                })));
    }
    SoundSettingsBackend {
        id: sound
        backend: page.backend
        active: page.visible
    }
    Text {
        width: parent.width
        visible: text !== ""
        text: sound.error
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        color: "#a01b45"
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 13
    }
    SettingsCard {
        width: parent.width
        title: "Output"
        SettingsChoiceRow {
            objectName: "settings-sound-output"
            width: parent.width
            title: "Output device"
            explanation: sound.sink ? "Sound plays through " + page.label(sound.sink) + "." : page.label(null)
            options: page.options(sound.outputs)
            value: sound.preferredOutputId
            defaultValue: -1
            unavailable: !sound.ready
            onValueRequested: value => sound.chooseOutput(value)
            onResetRequested: sound.resetOutput()
        }
        SettingsSliderRow {
            objectName: "settings-sound-volume"
            width: parent.width
            title: "Volume"
            explanation: sound.sink?.audio ? value + "% of the output level. Reset restores 100%." : page.label(null)
            value: Math.round((sound.sink?.audio?.volume ?? 0) * 100)
            defaultValue: 100
            unavailable: !sound.sink?.audio
            onValueRequested: value => sound.setOutputVolume(value)
            onResetRequested: sound.setOutputVolume(100)
        }
        SettingsToggleRow {
            objectName: "settings-sound-mute"
            width: parent.width
            title: "Mute output"
            explanation: "Silence your speakers or headphones."
            value: sound.sink?.audio?.muted ?? false
            defaultValue: false
            unavailable: !sound.sink?.audio
            onValueRequested: value => sound.setOutputMuted(value)
            onResetRequested: sound.setOutputMuted(false)
        }
    }
    SettingsCard {
        width: parent.width
        title: "Input"
        SettingsChoiceRow {
            objectName: "settings-sound-input"
            width: parent.width
            title: "Input device"
            explanation: sound.source ? "Record through " + page.label(sound.source) + "." : page.label(null)
            options: page.options(sound.inputs)
            value: sound.preferredInputId
            defaultValue: -1
            unavailable: !sound.ready
            onValueRequested: value => sound.chooseInput(value)
            onResetRequested: sound.resetInput()
        }
        SettingsSliderRow {
            objectName: "settings-sound-input-volume"
            width: parent.width
            title: "Input volume"
            explanation: sound.source?.audio ? value + "% of the input level. Reset restores 100%." : page.label(null)
            value: Math.round((sound.source?.audio?.volume ?? 0) * 100)
            defaultValue: 100
            unavailable: !sound.source?.audio
            onValueRequested: value => sound.setInputVolume(value)
            onResetRequested: sound.setInputVolume(100)
        }
        SettingsToggleRow {
            objectName: "settings-sound-input-mute"
            width: parent.width
            title: "Mute microphone"
            explanation: "Stop sound from the selected microphone."
            value: sound.source?.audio?.muted ?? false
            defaultValue: false
            unavailable: !sound.source?.audio
            onValueRequested: value => sound.setInputMuted(value)
            onResetRequested: sound.setInputMuted(false)
        }
        SettingsRow {
            objectName: "settings-sound-input-level"
            width: parent.width
            title: "Input level"
            explanation: "Speak to test your microphone while this page is open."
            value: 0
            defaultValue: 0
            unavailable: !sound.source?.audio
            controlWidth: 180
            control: Component {
                Rectangle {
                    implicitHeight: 8
                    radius: 4
                    color: SettingsTheme.rim
                    Rectangle {
                        width: parent.width * sound.inputLevel
                        height: parent.height
                        radius: 4
                        color: SettingsTheme.accent
                    }
                    Accessible.role: Accessible.ProgressBar
                    Accessible.name: "Microphone input level"
                    Accessible.description: Math.round(sound.inputLevel * 100) + "%"
                }
            }
        }
    }
    SettingsCard {
        objectName: "settings-sound-streams"
        width: parent.width
        title: "Volume per app"
        Text {
            width: parent.width
            visible: sound.streams.length === 0
            text: "Apps appear here while they play sound."
            color: SettingsTheme.dim
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 13
            wrapMode: Text.WordWrap
        }
        Repeater {
            model: sound.streams
            delegate: SettingsSliderRow {
                required property var modelData
                objectName: "settings-sound-stream-" + modelData.id
                width: parent.width
                title: modelData.name || "Application audio"
                explanation: (modelData.detail ? modelData.detail + " · " : "") + value + "% of the app level."
                value: Math.round((modelData.audio?.volume ?? 0) * 100)
                defaultValue: 100
                unavailable: !modelData.audio
                onValueRequested: value => sound.setStreamVolume(modelData.id, value)
                onResetRequested: sound.setStreamVolume(modelData.id, 100)
            }
        }
    }
}
