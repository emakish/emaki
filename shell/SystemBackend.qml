pragma ComponentBehavior: Bound
import QtQuick

// Data/action seam: production uses SystemNative; tests supply objects, never hardware.
Item {
    id: backend
    // Fixtures and non-native backends have no asynchronous discovery to await.
    property bool startupReady: true
    property bool audioReady: false
    property var sink: null
    property var source: null
    property var audioNodes: []
    // Playback streams (Stream/Output/Audio) for the per-app mixer: [{id, name, detail, audio}]
    property var streams: []
    // Microphone level 0..1 while the sound page asks for it (PwNodePeakMonitor in production).
    property bool micMeter: false
    property real micLevel: 0
    property var battery: null
    property bool charging: false
    property bool batteryPower: false
    property bool networkReady: false
    property bool wifiEnabled: false
    property bool wifiHardwareEnabled: false
    property var wifiDevices: []
    property var networks: []
    // NetworkManager connectivity: unknown | none | portal | limited | full
    property string connectivity: "unknown"
    property var adapter: null
    property var devices: []
    // Capture streams: [{kind: "mic"|"cam", name}] — presentation data, never serialized by status.
    property var captures: []
    property string actionError: ""
    // Confirm from published properties, not an optimistic local switch.
    function act(kind: string, value: var): var {
        actionError = "";
        // One stream (`id`) or an app's streams at once (`ids`: the panel's slider per app).
        if (kind === "stream-volume") {
            const ids = value?.ids !== undefined ? Array.from(value.ids).map(Number) : [Number(value?.id)];
            const list = Array.from(streams).filter(s => ids.includes(s.id) && s.audio);
            if (!list.length)
                return "stream_gone";
            const v = Number(value.percent);
            if (!Number.isFinite(v) || v < 0 || v > 100)
                return "invalid_volume";
            for (const s of list)
                s.audio.volume = v / 100;
            return () => list.every(s => !!s.audio && Math.abs(s.audio.volume * 100 - v) < 1);
        }
        if (kind === "volume" || kind === "volume-step" || kind === "mute" || kind === "mic" || kind === "mic-volume") {
            const node = kind === "mic" || kind === "mic-volume" ? source : sink;
            if (!audioReady || !node?.audio)
                return "audio_unavailable";
            if (kind === "mic-volume") {
                const v = Number(value);
                if (!Number.isFinite(v) || v < 0 || v > 100)
                    return "invalid_volume";
                node.audio.volume = v / 100;
                return () => !!node.audio && Math.abs(node.audio.volume * 100 - v) < 1;
            }
            if (kind === "volume" || kind === "volume-step") {
                const v = Number(value);
                if (!Number.isFinite(v) || v < 0 || v > 100)
                    return "invalid_volume";
                node.audio.volume = v / 100;
                // Scrolling the bar icon also unmutes, as in the mockup's stepValue.
                if (kind === "volume-step")
                    node.audio.muted = false;
                return () => !!node.audio && Math.abs(node.audio.volume * 100 - v) < 1 && (kind === "volume" || !node.audio.muted);
            }
            const muted = !node.audio.muted;
            node.audio.muted = muted;
            return () => !!node.audio && node.audio.muted === muted;
        }
        if (kind === "wifi-power") {
            if (!networkReady || !wifiHardwareEnabled)
                return "wifi_unavailable_or_blocked";
            const enabled = !wifiEnabled;
            setWifiEnabled(enabled);
            return () => wifiEnabled === enabled;
        }
        if (kind.startsWith("wifi-")) {
            const entry = networks.find(n => n.key === value.key);
            if (!entry || !entry.ref)
                return "network_gone";
            const n = entry.ref;
            if (kind === "wifi-connect") {
                if (n.known || entry.open)
                    n.connect();
                else if (entry.psk && typeof value.password === "string" && value.password.length >= 8 && value.password.length <= 63)
                    n.connectWithPsk(value.password);
                else
                    return "password_or_security_unsupported";
                return () => n.connected === true;
            }
            if (kind === "wifi-disconnect") {
                n.disconnect();
                return () => !n.connected;
            }
            if (kind === "wifi-forget") {
                n.forget();
                return () => !n.known;
            }
        }
        if (kind === "bt-power") {
            if (!adapter)
                return "no_bluetooth_adapter";
            const enabled = !adapter.enabled;
            adapter.enabled = enabled;
            return () => adapter.enabled === enabled;
        }
        if (kind === "bt-connect" || kind === "bt-disconnect") {
            const d = devices.find(d => d.key === value)?.ref;
            if (!d || !d.paired)
                return "paired_device_required";
            if (kind === "bt-connect")
                d.connect();
            else
                d.disconnect();
            return () => d.connected === (kind === "bt-connect");
        }
        if (kind === "bt-scan") {
            if (!adapter || !adapter.enabled)
                return "bluetooth_off";
            const on = !adapter.discovering;
            adapter.discovering = on;
            return () => adapter.discovering === on;
        }
        // QS 0.3.1 only logs BlueZ pairing errors: `pairing` dropping without `paired` is the failure.
        if (kind === "bt-pair" || kind === "bt-cancel-pair" || kind === "bt-forget") {
            const d = devices.find(d => d.key === value)?.ref;
            if (!d)
                return "device_gone";
            if (kind === "bt-pair") {
                if (d.paired)
                    return "already_paired";
                d.pair();
                return () => d.paired ? true : d.pairing ? false : "pairing_failed";
            }
            if (kind === "bt-cancel-pair") {
                d.cancelPair();
                return () => !d.pairing;
            }
            d.forget();
            return () => !devices.some(x => x.key === value);
        }
        return "unsupported";
    }
    function chooseOutput(id: int): var {
        return "output_unavailable";
    }
    function chooseInput(id: int): var {
        return "input_unavailable";
    }
    function setWifiEnabled(value: bool): void {
        wifiEnabled = value;
    }
}
