pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

Item {
    id: service
    property bool live: false
    property bool helpersEnabled: live
    property SystemBackend backend: native.item as SystemBackend
    // A failed native Loader remains unready; the independent startup caps still
    // reveal the desktop. Headless/test services need no native service discovery.
    readonly property bool startupReady: !live || (native.status === Loader.Ready && backend?.startupReady === true)
    property string actionState: "idle"
    property var pendingCheck: null
    property string pendingKind: ""
    // The Wayland surface supplies readiness after mapping with OnDemand focus.
    property bool pairingFocusManaged: false
    property bool pairingFocusReady: true
    property bool pairingQueued: false
    // The value the pending check was started with (a pairing's device key).
    property var pendingValue: null
    property int attempts: 0
    property int confirmationTicks: 25
    property var brightness: ({
            state: "unavailable",
            percent: null
        })
    property var profiles: ({
            state: "unavailable",
            profiles: [],
            current: ""
        })
    property var vpn: ({
            state: "unavailable",
            connections: []
        })
    property bool canHibernate: false
    property bool lowSent: false
    property bool criticalSent: false
    signal lowBattery(int percent)
    // emaki-sleep-guard could not confirm the lock before sleep; the policy it then applied.
    signal sleepLockFailed(string policy)
    // OSD: only changes of an already known value, never the first reading or an output switch.
    signal osdRequested(string page)
    property var seenSink: null
    property real seenVolume: -1
    property int seenMuted: -1
    property int seenBrightness: -1
    readonly property var sinkAudio: backend?.sink?.audio ?? null
    readonly property real sinkVolume: sinkAudio ? sinkAudio.volume : -1
    readonly property bool sinkMuted: sinkAudio ? sinkAudio.muted : false
    function noteSound(): void {
        // Read the node directly: cached bindings can still hold the previous sink's values here.
        const sink = backend?.sink ?? null;
        const audio = sink?.audio ?? null;
        const volume = audio ? audio.volume : -1;
        const muted = audio ? (audio.muted ? 1 : 0) : -1;
        const changed = seenSink === sink && seenVolume >= 0 && volume >= 0 && (Math.abs(volume - seenVolume) >= .005 || seenMuted !== muted);
        seenSink = sink;
        seenVolume = volume;
        seenMuted = muted;
        if (changed)
            osdRequested("sound");
    }
    onSinkVolumeChanged: noteSound()
    onSinkMutedChanged: noteSound()
    onSinkAudioChanged: noteSound()
    onBrightnessChanged: {
        const percent = brightness.state === "ready" && brightness.percent !== null ? Number(brightness.percent) : -1;
        if (seenBrightness >= 0 && percent >= 0 && percent !== seenBrightness)
            osdRequested("light");
        seenBrightness = percent;
    }
    // Wheel steps: one target at a time; a step during a pending action replaces the queued target.
    // Steps count from the target in flight, so fast scrolling never loses ticks or re-reads a stale value.
    property var queuedStep: null
    property var inflightStep: null
    function step(page: string, delta: int): bool {
        if (page !== "sound" && page !== "light")
            return false;
        const last = queuedStep && queuedStep.page === page ? queuedStep : inflightStep && inflightStep.page === page && (pendingCheck || action.busy) ? inflightStep : null;
        const base = last ? last.value : page === "sound" ? (sinkVolume >= 0 ? Math.round(sinkVolume * 100) : -1) : (brightness.state === "ready" && brightness.percent !== null ? Number(brightness.percent) : -1);
        if (base < 0)
            return false;
        const value = Math.max(page === "light" ? 5 : 0, Math.min(100, base + delta));
        queuedStep = ({
                page: page,
                value: value
            });
        flushStep();
        return true;
    }
    function flushStep(): void {
        if (!queuedStep || pendingCheck || action.busy)
            return;
        const next = queuedStep;
        queuedStep = null;
        inflightStep = act(next.page === "sound" ? "volume-step" : "brightness", next.value) ? next : null;
    }
    onActionStateChanged: {
        if (actionState !== "pending" && actionState !== "busy")
            Qt.callLater(flushStep);
    }
    readonly property int batteryPercent: backend?.battery ? Math.round(backend.battery.percentage * 100) : -1
    readonly property string batteryLabel: batteryPercent < 0 ? "—" : (backend.charging ? "+ " : "") + batteryPercent + "%"
    readonly property string soundLabel: !backend?.audioReady || !backend.sink?.audio ? "—" : backend.sink.audio.muted ? "Muted" : String(Math.round(backend.sink.audio.volume * 100))
    onBatteryPercentChanged: checkBattery()
    Connections {
        target: service.backend
        function onWifiPasswordRequired(key: string): void {
            // NM may reject the secret after the generic confirmation deadline has elapsed.
            // The backend still tracks that attempt and the panel reopens its password field.
            if (!service.pendingCheck && !action.busy)
                service.actionState = "wrong_password";
        }
        function onBatteryPowerChanged(): void {
            service.checkBattery();
        }
        function onChargingChanged(): void {
            service.checkBattery();
        }
    }
    function checkBattery(): void {
        if (batteryPercent < 0 || !backend)
            return;
        if (!backend.batteryPower || backend.charging) {
            // Re-arm each threshold only after recovering above it on external power.
            // Brief plug/unplug cycles below a threshold must not repeat its warning.
            if (batteryPercent > 10)
                lowSent = false;
            if (batteryPercent > 5)
                criticalSent = false;
            return;
        }
        // Charging may happen entirely while asleep; a recovered battery re-arms both.
        if (batteryPercent >= 20) {
            lowSent = false;
            criticalSent = false;
        }
        // A first reading below 5% sends just the urgent warning, not both.
        // Fluctuations below 20% do not re-arm either warning.
        if (batteryPercent <= 5 && !criticalSent) {
            lowSent = true;
            criticalSent = true;
            lowBattery(batteryPercent);
        } else if (batteryPercent <= 10 && !lowSent) {
            lowSent = true;
            lowBattery(batteryPercent);
        }
    }
    Loader {
        id: native
        active: service.live
        source: active ? "SystemNative.qml" : ""
    }
    readonly property alias tray: tray
    TrayService {
        id: tray
    }
    readonly property alias night: night
    NightLight {
        id: night
        enabled: service.helpersEnabled
    }
    function act(kind: string, value: var): bool {
        // Cancel replaces the pairing's check: that check would report the deliberate stop as a failure.
        // Only for the device being paired; a cancel the backend refuses reports why, and the
        // pairing stays tracked.
        if (kind === "bt-cancel-pair" && pendingKind === "bt-pair" && pendingValue === value && !action.busy && backend) {
            if (pairingQueued) {
                pairingQueued = false;
                pendingCheck = () => true;
                pendingKind = kind;
                return true;
            }
            const cancel = backend.act(kind, value);
            if (typeof cancel !== "function") {
                actionState = cancel;
                return false;
            }
            pendingCheck = cancel;
            pendingKind = kind;
            actionState = "pending";
            attempts = 0;
            ticks = confirmationTicks;
            return true;
        }
        if (pendingCheck || action.busy) {
            actionState = "busy";
            return false;
        }
        if (kind === "brightness" || kind === "profile" || kind === "session" || kind === "lock") {
            if (!helpersEnabled) {
                actionState = "disabled";
                return false;
            }
            // Lock may need recovery plus the full pour; outlast the 21 s helper deadline.
            action.timeoutMs = kind === "session" && value === "hibernate" ? 30000 : kind === "lock" || (kind === "session" && value === "suspend") ? 25000 : 8000;
            action.start({
                op: kind === "brightness" ? "brightness-set" : kind === "profile" ? "profile-set" : kind === "lock" ? "lock" : "session",
                value: value,
                confirmed: kind === "session"
            });
            actionState = "pending";
            return true;
        }
        if (kind === "hidden" || kind === "vpn" || kind === "portal") {
            if (!helpersEnabled) {
                actionState = "disabled";
                return false;
            }
            // The helper waits for NM activation (hidden: 30 s, VPN: nmcli --wait 25) and the deadline must outlast it.
            action.timeoutMs = kind === "portal" ? 8000 : 45000;
            action.start(kind === "hidden" ? {
                op: "wifi-hidden",
                ssid: value.ssid,
                password: value.password
            } : kind === "vpn" ? {
                op: "vpn-set",
                uuid: value.uuid,
                up: value.up
            } : {
                op: "portal-open"
            });
            actionState = "pending";
            return true;
        }
        if (!backend) {
            actionState = "unavailable";
            return false;
        }
        let check;
        if (kind === "bt-pair" && pairingFocusManaged) {
            pairingQueued = true;
            check = () => {
                if (!service.pairingFocusReady)
                    return false;
                // Start only after the compositor focuses the remapped panel, so
                // that remap cannot steal focus from a fast native PIN dialog.
                service.pairingQueued = false;
                const started = service.backend.act(kind, value);
                service.pendingCheck = typeof started === "function" ? started : () => started;
                return service.pendingCheck();
            };
        } else {
            check = kind === "output" && typeof backend.chooseOutput === "function" ? backend.chooseOutput(Number(value)) : kind === "input" && typeof backend.chooseInput === "function" ? backend.chooseInput(Number(value)) : backend.act(kind, value);
        }
        if (typeof check !== "function") {
            actionState = check;
            return false;
        }
        pendingCheck = check;
        pendingKind = kind;
        pendingValue = value;
        actionState = "pending";
        attempts = 0;
        // Pairing waits for the other device (and a code on it): up to 60 s instead of 5 s.
        ticks = kind === "bt-pair" ? 300 : confirmationTicks;
        confirmation.start();
        return true;
    }
    property int ticks: 25
    Timer {
        id: confirmation
        interval: 200
        repeat: true
        onTriggered: {
            try {
                const result = service.pendingCheck();
                if (result === true)
                    service.actionState = "confirmed";
                else if (typeof result === "string")
                    service.actionState = result;
                else
                // the check itself observed a failure
                if (++service.attempts >= service.ticks)
                    service.actionState = "confirmation_timeout";
                else
                    return;
            } catch (_) {
                service.actionState = "target_gone";
            }
            service.pendingCheck = null;
            service.pairingQueued = false;
            service.pendingKind = "";
            service.pendingValue = null;
            stop();
        }
    }
    PrivateJob {
        id: bright
        helper: Quickshell.shellPath("helpers/system-tools.py")
        onCompleted: r => service.brightness = r.state === "ready" ? r : {
                state: r.state,
                percent: null
            }
        onStateChanged: {
            if (["timeout", "helper_failed", "invalid_response"].includes(state))
                service.brightness = {
                    state: state,
                    percent: null
                };
        }
    }
    PrivateJob {
        id: power
        timeoutMs: 8000
        helper: Quickshell.shellPath("helpers/system-tools.py")
        onCompleted: r => service.profiles = r.state === "ready" ? r : {
                state: r.state,
                profiles: [],
                current: ""
            }
        onStateChanged: {
            if (["timeout", "helper_failed", "invalid_response"].includes(state))
                service.profiles = {
                    state: state,
                    profiles: [],
                    current: ""
                };
        }
    }
    PrivateJob {
        id: action
        timeoutMs: 8000
        helper: Quickshell.shellPath("helpers/system-tools.py")
        onCompleted: r => {
            if (r.percent !== undefined)
                service.brightness = {
                    state: "ready",
                    percent: r.percent
                };
            if (r.current !== undefined)
                service.profiles = Object.assign({}, service.profiles, {
                    current: r.current
                });
            if (r.connections !== undefined)
                service.vpn = {
                    state: "ready",
                    connections: r.connections
                };
            action.timeoutMs = 8000;
            service.actionState = r.state;
            service.refresh();
        }
        onStateChanged: {
            if (["timeout", "helper_failed", "invalid_response"].includes(state)) {
                action.timeoutMs = 8000;
                service.actionState = state;
            }
        }
    }
    PrivateJob {
        id: vpnJob
        timeoutMs: 8000
        helper: Quickshell.shellPath("helpers/system-tools.py")
        onCompleted: r => service.vpn = r.state === "ready" ? r : {
                state: r.state,
                connections: []
            }
        onStateChanged: {
            if (["timeout", "helper_failed", "invalid_response"].includes(state))
                service.vpn = {
                    state: state,
                    connections: []
                };
        }
    }
    PrivateJob {
        id: hibernation
        timeoutMs: 8000
        helper: Quickshell.shellPath("helpers/system-tools.py")
        onCompleted: r => service.canHibernate = r.state === "ready" && r.available === true
        onStateChanged: {
            if (["timeout", "helper_failed", "invalid_response"].includes(state))
                service.canHibernate = false;
        }
    }
    function refresh(): void {
        if (!helpersEnabled || action.busy)
            return;
        hibernation.start({
            op: "hibernate-read"
        });
        bright.start({
            op: "brightness-read"
        });
        power.start({
            op: "profiles-read"
        });
        vpnJob.start({
            op: "vpn-list"
        });
    }
    // Readings come on events, not on a 5 s poll (that was three python processes every
    // 5 s, ~36 a minute, all day): brightness on the kernel's backlight uevent (every sysfs
    // write emits one — swayosd keys included), VPN on `nmcli monitor`, power profiles at
    // start, when the panel opens and after our own change. Battery, network, Bluetooth
    // and sound are native Quickshell services and were never polled.
    property bool panelOpen: false
    onPanelOpenChanged: if (panelOpen)
        refresh()
    Component.onCompleted: if (helpersEnabled) {
        refresh();
        takeSleepFlags(true);
    }
    onHelpersEnabledChanged: if (helpersEnabled) {
        refresh();
        takeSleepFlags(true);
    }
    // The sleep guard leaves a flag for a one-time notice (scripts/emaki-sleep-guard): in the
    // runtime directory while the session goes on, in the state directory when it ended the
    // session. The helper reads and deletes them; the state flag only at a start in a later
    // session than the one it names (a restart of the shell in the same session leaves it).
    property bool sleepFlagsAgain: false
    function takeSleepFlags(sessionStart: bool): void {
        if (helpersEnabled && !sleepFlags.start({
            op: "sleep-lock-flags",
            session_start: sessionStart
        }))
            sleepFlagsAgain = true;
    }
    PrivateJob {
        id: sleepFlags
        helper: Quickshell.shellPath("helpers/system-tools.py")
        onCompleted: r => {
            if (r.state === "ready" && Array.isArray(r.policies))
                for (const policy of r.policies)
                    service.sleepLockFailed(String(policy));
        }
        onBusyChanged: {
            if (!busy && service.sleepFlagsAgain) {
                service.sleepFlagsAgain = false;
                service.takeSleepFlags(false);
            }
        }
    }
    FileView {
        // Quickshell also watches the directory, so a flag created later is seen.
        path: service.helpersEnabled && Quickshell.env("XDG_RUNTIME_DIR") ? Quickshell.env("XDG_RUNTIME_DIR") + "/emaki-sleep-lock-failed" : ""
        preload: false
        watchChanges: true
        printErrors: false
        onFileChanged: service.takeSleepFlags(false)
    }
    Process {
        id: backlightMonitor
        running: service.helpersEnabled
        command: [Quickshell.env("EMAKI_UDEVADM") || "udevadm", "monitor", "--udev", "--subsystem-match=backlight"]
        stdout: SplitParser {
            onRead: line => {
                if (String(line).includes("change"))
                    brightDebounce.restart();
            }
        }
        stderr: SplitParser {
            onRead: _line => {}
        }
    }
    Timer {
        id: brightDebounce
        interval: 80
        onTriggered: {
            if (service.helpersEnabled && !bright.busy && !action.busy)
                bright.start({
                    op: "brightness-read"
                });
        }
    }
    Process {
        id: vpnMonitor
        running: service.helpersEnabled
        command: [Quickshell.env("EMAKI_NMCLI") || "nmcli", "monitor"]
        stdout: SplitParser {
            onRead: _line => vpnDebounce.restart()
        }
        stderr: SplitParser {
            onRead: _line => {}
        }
    }
    Timer {
        id: vpnDebounce
        interval: 300
        onTriggered: {
            if (service.helpersEnabled && !vpnJob.busy && !action.busy)
                vpnJob.start({
                    op: "vpn-list"
                });
        }
    }
    function status(): var {
        return {
            audio: backend?.audioReady ? "ready" : "unavailable",
            volume: soundLabel,
            outputs: backend?.audioNodes.filter(n => n.isSink).length ?? 0,
            inputs: backend?.audioNodes.filter(n => !n.isSink).length ?? 0,
            streams: backend?.streams.length ?? 0,
            mic_meter: backend?.micMeter ?? false,
            battery: batteryPercent,
            charging: backend?.charging ?? false,
            network: backend?.networkReady ? (backend.wifiDevices.length ? (backend.wifiEnabled ? "on" : "off") : "no_adapter") : "unavailable",
            networks: backend?.networks.length ?? 0,
            wifi_scanners: Array.from(backend?.wifiDevices ?? []).filter(d => d?.scannerEnabled === true).length,
            connectivity: backend?.connectivity ?? "unknown",
            vpn: vpn.state,
            vpn_count: vpn.connections.length,
            vpn_active: vpn.connections.filter(v => v.active).length,
            bluetooth: backend?.adapter ? (backend.adapter.enabled ? "on" : "off") : "no_adapter_or_service",
            devices: backend?.devices.length ?? 0,
            paired: backend?.devices.filter(d => d.paired).length ?? 0,
            discovering: backend?.adapter?.discovering ?? false,
            brightness: brightness.state,
            profiles: profiles.state,
            tray: tray.state,
            tray_count: tray.items.length,
            night: night.status(),
            action: actionState
        };
    }
}
