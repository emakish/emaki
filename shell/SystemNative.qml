pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Services.Pipewire
import Quickshell.Services.UPower
import Quickshell.Networking
import Quickshell.Bluetooth

SystemBackend {
    id: native
    // Wait for known initial service data, including the tracked default sink.
    // No sink or battery hardware is required; absent services use the cover cap.
    startupReady: Pipewire.ready && (!sink || sink.ready === true) && UPower.displayDevice?.ready === true
    audioReady: Pipewire.ready
    sink: Pipewire.defaultAudioSink
    source: Pipewire.defaultAudioSource
    audioNodes: Pipewire.nodes.values.filter(n => !n.isStream && n.audio)
    streams: Pipewire.nodes.values.filter(n => (n.type & PwNodeType.AudioOutStream) === PwNodeType.AudioOutStream && n.audio).map(n => ({
                id: n.id,
                name: String(n.properties?.["application.name"] || n.nickname || n.name || ""),
                detail: String(n.properties?.["media.name"] || n.description || ""),
                // The app's icon name when the client sets it (the panel falls back to its
                // desktop entry by name).
                icon: String(n.properties?.["application.icon-name"] || n.properties?.["application.icon_name"] || ""),
                audio: n.audio
            }))
    micLevel: micPeak.peak
    PwNodePeakMonitor {
        id: micPeak
        node: native.source
        enabled: native.micMeter && native.source !== null
    }
    // Stream/Input/Audio = an app recording the microphone. QS 0.3.1 maps no type for
    // Stream/Input/Video (camera consumers), so that one comes from media.class directly.
    // The shell's own PwNodePeakMonitor (mic meter of the sound page) is such a stream too:
    // streams of this process never count as "an app using the microphone".
    captures: Pipewire.nodes.values.filter(n => String(n.properties?.["application.process.id"] ?? "") !== String(Quickshell.processId)).map(n => ({
                kind: (n.type & PwNodeType.AudioInStream) === PwNodeType.AudioInStream ? "mic" : n.properties?.["media.class"] === "Stream/Input/Video" ? "cam" : "",
                name: String(n.properties?.["application.name"] || n.nickname || n.description || n.name || "")
            })).filter(c => c.kind !== "")
    battery: UPower.displayDevice?.ready && UPower.displayDevice.isPresent ? UPower.displayDevice : null
    charging: battery?.state === UPowerDeviceState.Charging || battery?.state === UPowerDeviceState.PendingCharge
    batteryPower: UPower.onBattery
    networkReady: Networking.backend === NetworkBackendType.NetworkManager
    wifiEnabled: Networking.wifiEnabled
    connectivity: ({
            [NetworkConnectivity.None]: "none",
            [NetworkConnectivity.Portal]: "portal",
            [NetworkConnectivity.Limited]: "limited",
            [NetworkConnectivity.Full]: "full"
        })[Networking.connectivity] ?? "unknown"
    wifiHardwareEnabled: Networking.wifiHardwareEnabled
    wifiDevices: Networking.devices.values.filter(d => d.type === DeviceType.Wifi)
    wiredConnected: Networking.devices.values.some(d => d.type === DeviceType.Wired && d.connected)
    networks: wifiDevices.reduce((rows, d) => rows.concat(d.networks.values.map(n => ({
                    key: d.name + "/" + n.name,
                    name: n.name,
                    connected: n.connected,
                    known: n.known,
                    busy: n.stateChanging,
                    signal: n.signalStrength,
                    open: n.security === WifiSecurityType.Open,
                    psk: [WifiSecurityType.WpaPsk, WifiSecurityType.Wpa2Psk, WifiSecurityType.Sae].includes(n.security),
                    ref: n
                }))), [])
    // Object indirection: QS 0.3.1 qmltypes omit the qualified adapter return type.
    readonly property var bluetooth: Bluetooth
    adapter: bluetooth.defaultAdapter
    devices: bluetooth.devices.values.map(d => ({
                key: d.dbusPath,
                name: d.name || d.deviceName || d.address,
                connected: d.connected,
                paired: d.paired,
                pairing: d.pairing,
                battery: d.batteryAvailable ? Math.round(d.battery * 100) : -1,
                // BlueZ's icon ("audio-headphones", "input-keyboard", "phone").
                icon: String(d.icon || ""),
                ref: d
            }))
    PwObjectTracker {
        objects: [native.sink, native.source].concat(native.audioNodes).concat(Pipewire.nodes.values.filter(n => (n.type & PwNodeType.AudioOutStream) === PwNodeType.AudioOutStream)).filter(n => n !== null)
    }
    function setWifiEnabled(value: bool): void {
        Networking.wifiEnabled = value;
    }
    function chooseOutput(id: int): var {
        const node = audioNodes.find(n => n.id === id && n.isSink);
        if (!node)
            return "output_gone";
        Pipewire.preferredDefaultAudioSink = node;
        return () => sink?.id === id;
    }
    function chooseInput(id: int): var {
        const node = audioNodes.find(n => n.id === id && !n.isSink);
        if (!node)
            return "input_gone";
        Pipewire.preferredDefaultAudioSource = node;
        return () => source?.id === id;
    }
    Instantiator {
        model: native.networks
        delegate: Connections {
            required property var modelData
            target: modelData.ref
            // ConnectionFailReason (QS 0.3.1 enums.hpp): NoSecrets is the rejected-PSK case;
            // quickshell-emaki reports a failure without a reason of its own as Unknown. While
            // the signal is emitted, its Network.activation names the activation that failed
            // (undefined on a stock Quickshell).
            function onConnectionFailed(reason): void {
                native.wifiFailed(modelData.key, modelData.ref, reason === ConnectionFailReason.NoSecrets ? "wrong_password" : reason === ConnectionFailReason.WifiAuthTimeout ? "auth_timeout" : reason === ConnectionFailReason.WifiNetworkLost ? "network_lost" : "network_connection_failed", modelData.ref.activation);
            }
        }
    }
}
