// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

// Night Light = wlsunset held at one temperature (chosen 2026-09-23).
// State survives restarts in $XDG_STATE_HOME/emaki/night-light.json.
// Optional custom hours use local time and keep the same guarded child.
// wlsunset has no "fixed temperature" mode: -t K -T K+1 keeps it within 1 K of the
// target whatever the time of day (high must be strictly above low, main.c).
// A missing binary is reported as `not_installed`; the switch then does nothing.
Scope {
    id: night
    property bool enabled: false
    property bool on: false
    // Mockup "Warmth" 0–100 (default 50); 50 = wlsunset's own default low temperature 4000 K.
    property int warmth: 50
    property string schedule: "always"
    property string startTime: "20:00"
    property string endTime: "07:00"
    property string error: ""
    property bool failed: false
    property bool stopping: false
    function validTime(value: string): bool {
        return /^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/.test(value);
    }
    function changed(): void {
        error = "";
        failed = false;
        if (on && runner.running) {
            restartPending = true;
            stopping = true;
            runner.signal(15);
        } else {
            apply();
        }
    }
    function setSchedule(value: string): bool {
        if (!["always", "manual"].includes(value)) {
            error = "Choose a valid night light schedule.";
            return false;
        }
        schedule = value;
        changed();
        return true;
    }
    function setStartTime(value: string): bool {
        if (!validTime(value) || value === endTime) {
            error = "Use different start and end times in HH:MM format.";
            return false;
        }
        startTime = value;
        changed();
        return true;
    }
    function setEndTime(value: string): bool {
        if (!validTime(value) || value === startTime) {
            error = "Use different start and end times in HH:MM format.";
            return false;
        }
        endTime = value;
        changed();
        return true;
    }
    readonly property var sunsetArgs: schedule === "manual" ? ["-t", String(Math.min(6499, temperature)), "-T", "6500", "-s", startTime, "-S", endTime, "-d", "0"] : ["-t", String(temperature), "-T", String(temperature + 1)]
    readonly property int temperature: 6500 - warmth * 50
    property bool installed: false
    property string checkState: "idle"
    readonly property string state: failed ? "failed" : !enabled ? "disabled" : checkState !== "ready" ? (checkState === "idle" || checkState === "pending" ? "checking" : checkState) : !installed ? "not_installed" : runner.running ? "on" : on ? "starting" : "off"
    readonly property string statePath: (Quickshell.env("XDG_STATE_HOME") || Quickshell.env("HOME") + "/.local/state") + "/emaki/night-light.json"
    property bool restored: false
    // A file written by a newer shell (it outlives a system rollback in /home): its format is
    // unknown here, so it is neither read nor overwritten during this run.
    property bool foreign: false
    property bool restartPending: false
    function setOn(value: bool): bool {
        if (!enabled || !installed) {
            error = "Night Light is unavailable in this session.";
            return false;
        }
        error = "";
        failed = false;
        on = value;
        apply();
        return true;
    }
    function setWarmth(value: int): bool {
        const v = Math.max(0, Math.min(100, Math.round(value)));
        if (v === warmth)
            return true;
        warmth = v;
        changed();
        return true;
    }
    function apply(): void {
        if (on && installed && enabled && !failed) {
            if (!runner.running && !restartPending)
                runner.running = true;
        } else if (runner.running) {
            restartPending = false;
            stopping = true;
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
    onScheduleChanged: if (restored)
        saveTimer.restart()
    onStartTimeChanged: if (restored)
        saveTimer.restart()
    onEndTimeChanged: if (restored)
        saveTimer.restart()
    Component.onCompleted: {
        try {
            const file = JSON.parse(stateFile.text() || "{}");
            foreign = file.version !== undefined && file.version !== 1;
            const saved = foreign ? {} : file;
            on = saved.on === true;
            if (typeof saved.warmth === "number" && saved.warmth >= 0 && saved.warmth <= 100)
                warmth = Math.round(saved.warmth);
            if (saved.schedule === "manual")
                schedule = "manual";
            if (typeof saved.startTime === "string" && typeof saved.endTime === "string" && validTime(saved.startTime) && validTime(saved.endTime) && saved.startTime !== saved.endTime) {
                startTime = saved.startTime;
                endTime = saved.endTime;
            }
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
        onSaveFailed: night.error = "Night light settings could not be saved. Check your storage and try again."
        onSaved: if (night.error === "Night light settings could not be saved. Check your storage and try again.")
            night.error = ""
    }
    Timer {
        id: saveTimer
        interval: 500
        onTriggered: {
            if (night.foreign)
                return;
            const saved = {
                version: 1,
                on: night.on,
                warmth: night.warmth
            };
            // Keep the legacy representation when no schedule customization is present.
            if (night.schedule !== "always" || night.startTime !== "20:00" || night.endTime !== "07:00") {
                saved.schedule = night.schedule;
                saved.startTime = night.startTime;
                saved.endTime = night.endTime;
            }
            stateFile.setText(JSON.stringify(saved));
        }
    }
    PrivateJob {
        id: check
        helper: Quickshell.shellPath("helpers/system-tools.py")
        onCompleted: r => {
            night.installed = r.installed === true;
            night.checkState = r.state;
            if (!night.installed)
                night.error = "Night Light is unavailable in this session.";
            night.apply();
        }
        onStateChanged: {
            if (["timeout", "helper_failed", "invalid_response"].includes(state)) {
                night.checkState = state;
                night.error = "Night Light availability could not be checked. Reopen the session and try again.";
            }
        }
    }
    // The guard ends wlsunset when this stdin pipe closes, so a dead shell never leaves the screen tinted.
    Process {
        id: runner
        command: [Quickshell.env("EMAKI_PYTHON") || "python3", "-B", Quickshell.shellPath("helpers/child-guard.py"), Quickshell.env("EMAKI_WLSUNSET") || "wlsunset"].concat(night.sunsetArgs)
        stdinEnabled: true
        stderr: SplitParser {
            onRead: _line => {}
        }
        onExited: _code => {
            const restart = night.restartPending;
            const expected = night.stopping;
            night.restartPending = false;
            night.stopping = false;
            if (restart || expected) {
                Qt.callLater(night.apply);
            } else if (!expected && night.on && night.enabled) {
                night.failed = true;
                night.error = "Night light could not start or stopped unexpectedly. Try turning it on again.";
            }
        }
    }
    function status(): var {
        return {
            state: state,
            warmth: warmth,
            temperature: temperature,
            schedule: schedule,
            startTime: startTime,
            endTime: endTime,
            error: error
        };
    }
}
