pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

// Night Light = wlsunset held at one temperature (chosen 2026-09-23).
// State survives restarts in $XDG_STATE_HOME/emaki/night-light.json ({on, warmth}).
// wlsunset has no "fixed temperature" mode: -t K -T K+1 keeps it within 1 K of the
// target whatever the time of day (high must be strictly above low, main.c).
// A missing binary is reported as `not_installed`; the switch then does nothing.
Scope {
    id: night
    property bool enabled: false
    property bool on: false
    // Mockup "Warmth" 0–100 (default 50); 50 = wlsunset's own default low temperature 4000 K.
    property int warmth: 50
    readonly property int temperature: 6500 - warmth * 50
    property bool installed: false
    property string checkState: "idle"
    readonly property string state: !enabled ? "disabled" : checkState !== "ready" ? (checkState === "idle" || checkState === "pending" ? "checking" : checkState) : !installed ? "not_installed" : runner.running ? "on" : on ? "starting" : "off"
    readonly property string statePath: (Quickshell.env("XDG_STATE_HOME") || Quickshell.env("HOME") + "/.local/state") + "/emaki/night-light.json"
    property bool restored: false
    property bool restartPending: false
    function setOn(value: bool): bool {
        if (!enabled || !installed)
            return false;
        on = value;
        apply();
        return true;
    }
    function setWarmth(value: int): bool {
        const v = Math.max(0, Math.min(100, Math.round(value)));
        if (v === warmth)
            return true;
        warmth = v;
        if (on && runner.running) {
            // wlsunset reads its temperatures only at start: stop, then start again with the new ones.
            restartPending = true;
            runner.signal(15);
        }
        return true;
    }
    function apply(): void {
        if (on && installed && enabled) {
            if (!runner.running && !restartPending)
                runner.running = true;
        } else if (runner.running) {
            restartPending = false;
            runner.signal(15);
        }
    }
    onEnabledChanged: {
        if (enabled && checkState === "idle")
            check.start({
                op: "night-light-check"
            });
        apply();
    }
    onOnChanged: if (restored)
        saveTimer.restart()
    onWarmthChanged: if (restored)
        saveTimer.restart()
    Component.onCompleted: {
        try {
            const saved = JSON.parse(stateFile.text() || "{}");
            on = saved.on === true;
            if (typeof saved.warmth === "number" && saved.warmth >= 0 && saved.warmth <= 100)
                warmth = Math.round(saved.warmth);
        } catch (error) {
            // Missing or damaged file: defaults, the next save replaces it.
        }
        restored = true;
        if (enabled)
            check.start({
                op: "night-light-check"
            });
    }
    FileView {
        id: stateFile
        path: night.statePath
        blockLoading: true
        atomicWrites: true
        printErrors: false
    }
    Timer {
        id: saveTimer
        interval: 500
        onTriggered: stateFile.setText(JSON.stringify({
            version: 1,
            on: night.on,
            warmth: night.warmth
        }))
    }
    PrivateJob {
        id: check
        helper: Quickshell.shellPath("helpers/system-tools.py")
        onCompleted: r => {
            night.installed = r.installed === true;
            night.checkState = r.state;
            night.apply();
        }
        onStateChanged: {
            if (["timeout", "helper_failed", "invalid_response"].includes(state))
                night.checkState = state;
        }
    }
    // The guard ends wlsunset when this stdin pipe closes, so a dead shell never leaves the screen tinted.
    Process {
        id: runner
        command: [Quickshell.env("EMAKI_PYTHON") || "python3", "-B", Quickshell.shellPath("helpers/child-guard.py"), Quickshell.env("EMAKI_WLSUNSET") || "wlsunset", "-t", String(night.temperature), "-T", String(night.temperature + 1)]
        stdinEnabled: true
        stderr: SplitParser {
            onRead: _line => {}
        }
        onRunningChanged: {
            if (!running && night.restartPending) {
                night.restartPending = false;
                if (night.on && night.installed && night.enabled)
                    running = true;
            }
        }
    }
    function status(): var {
        return {
            state: state,
            warmth: warmth,
            temperature: temperature
        };
    }
}
