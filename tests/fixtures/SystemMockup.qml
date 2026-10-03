pragma ComponentBehavior: Bound
import QtQuick

// The system island's data of docs/mockups/liquid-glass/system.js (OUTPUTS, INPUTS, STREAMS,
// NETWORKS, DEVICES, battery 70 %), as the native services would describe it: for the GPU
// pictures of tests/glass-shots.py only. Nothing here touches a real device.
SystemBackend {
    id: mockup
    audioReady: true
    sink: speaker
    source: microphone
    audioNodes: [speaker, budsOut, microphone, budsIn]
    streams: [0, 1, 2, 3, 4].map(i => ({
                id: 11 + i,
                name: "Firefox",
                detail: "Tab " + (i + 1),
                icon: "firefox",
                audio: firefox
            })).concat([
        {
            id: 21,
            name: "Telegram",
            detail: "Voice message",
            icon: "org.telegram.desktop",
            audio: telegram
        }
    ])
    micLevel: 0
    battery: cell
    charging: false
    batteryPower: true
    networkReady: true
    wifiEnabled: true
    wifiHardwareEnabled: true
    wifiDevices: [({
                name: "wlan0"
            })]
    connectivity: "full"
    networks: [["home-5g", "HomeNet_5G", .92, true, false], ["home", "HomeNet", .7, false, false], ["phone", "Phone hotspot", .45, false, false], ["cafe", "Cafe_Guest", .2, false, true]].map(n => ({
                key: "wlan0/" + n[0],
                name: n[1],
                signal: n[2],
                connected: n[3],
                known: n[3],
                busy: false,
                open: n[4],
                psk: !n[4],
                ref: null
            }))
    adapter: ({
            enabled: true,
            discovering: false,
            discoverable: true,
            name: "emaki"
        })
    devices: [["buds", "Pixel Buds Pro", "audio-headphones", true, 80], ["k2", "Keychron K2", "input-keyboard", false, -1], ["pixel", "Pixel 11 Pro", "phone", false, -1]].map(d => ({
                key: "/org/bluez/hci0/dev_" + d[0],
                name: d[1],
                icon: d[2],
                connected: d[3],
                paired: true,
                pairing: false,
                battery: d[4],
                ref: null
            }))
    property var speaker: ({
            id: 1,
            name: "alsa_output.speaker",
            description: "Ryzen HD Audio Controller Speaker",
            isSink: true,
            properties: {
                "device.profile.description": "Speaker",
                "device.bus": "pci",
                "device.icon_name": "audio-speakers"
            },
            audio: speakerAudio
        })
    property var budsOut: ({
            id: 2,
            name: "bluez_output.buds",
            description: "Pixel Buds Pro",
            isSink: true,
            properties: {
                "device.bus": "bluetooth",
                "device.icon_name": "audio-headphones"
            },
            audio: budsAudio
        })
    property var microphone: ({
            id: 3,
            name: "alsa_input.mic",
            description: "Ryzen HD Audio Controller Stereo Microphone",
            isSink: false,
            properties: {
                "device.profile.description": "Stereo Microphone",
                "device.bus": "pci",
                "device.icon_name": "audio-input-microphone"
            },
            audio: micAudio
        })
    property var budsIn: ({
            id: 4,
            name: "bluez_input.buds",
            description: "Pixel Buds Pro",
            isSink: false,
            properties: {
                "device.bus": "bluetooth",
                "device.icon_name": "audio-headphones"
            },
            audio: budsMicAudio
        })
    QtObject {
        id: speakerAudio
        property double volume: .72
        property bool muted: false
    }
    QtObject {
        id: budsAudio
        property double volume: .5
        property bool muted: false
    }
    QtObject {
        id: micAudio
        property double volume: .6
        property bool muted: false
    }
    QtObject {
        id: budsMicAudio
        property double volume: .5
        property bool muted: false
    }
    QtObject {
        id: firefox
        property double volume: 1
        property bool muted: false
    }
    QtObject {
        id: telegram
        property double volume: .45
        property bool muted: false
    }
    QtObject {
        id: cell
        property double percentage: .7
        property bool isPresent: true
        property bool ready: true
        property double timeToEmpty: 15600
        property double timeToFull: 0
    }
}
