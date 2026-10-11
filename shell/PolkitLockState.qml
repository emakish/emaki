// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: state
    property bool locked: true
    property int generation: 0
    // After a lock request, input stays closed until the supervisor's gate has been seen.
    // emaki-lock still locks when it cannot write the gate; without this hold the fresh
    // "no gate" sample would reopen input before the compositor shows its lock surfaces.
    property bool awaitingGate: false
    // Bounds that hold for a gate that never appears, so authorization does not stay
    // blocked for the rest of the session; by then the compositor lock has taken over.
    property int gateHold: 10000
    function block(): void {
        locked = true;
    }
    function prepareLock(): void {
        // Ignore snapshots produced before the input cancellation request.
        generation += 1;
        awaitingGate = true;
        gateWait.restart();
        block();
        if (observer.running)
            observer.write(generation + "\n");
    }
    function sample(data: string): void {
        try {
            const sample = JSON.parse(data);
            if (typeof sample.locked !== "boolean" || typeof sample.generation !== "number") {
                block();
                return;
            }
            if (sample.generation !== generation)
                return;
            if (sample.locked) {
                awaitingGate = false;
                gateWait.stop();
            }
            locked = sample.locked || awaitingGate;
        } catch (_) {
            block();
        }
    }
    Process {
        id: observer
        stdinEnabled: true
        running: true
        command: [Platform.python, "-I", "-B", Qt.resolvedUrl("helpers/polkit-lock-state.py").toString().replace(/^file:\/\//, "")]
        stdout: SplitParser {
            onRead: data => state.sample(data)
        }
        stderr: StdioCollector {}
        onRunningChanged: {
            if (running) {
                observer.write(state.generation + "\n");
            } else {
                state.block();
                retry.restart();
            }
        }
    }
    Timer {
        id: gateWait
        interval: state.gateHold
        onTriggered: {
            state.awaitingGate = false;
            // Ask for a fresh sample of the same generation instead of trusting the last one.
            if (observer.running)
                observer.write(state.generation + "\n");
        }
    }
    Timer {
        id: retry
        interval: 1000
        onTriggered: observer.running = true
    }
}
