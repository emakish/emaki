// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

// Native providers expose their current request. Keep those requests in arrival
// order and route the one visible card exclusively to the first provider.
Scope {
    id: queue
    required property var polkitBackend
    property var sudoBackend: null
    property var requests: []
    readonly property var activeBackend: flow?.backend ?? null
    // Publishing explicitly permits synchronous cancellation while locked. A
    // binding here would re-enter its own evaluation when the next source ends.
    property var flow: null
    readonly property string requestKind: activeBackend?.requestKind || "polkit"
    readonly property bool responseRequired: !!activeBackend && activeBackend.responseRequired
    readonly property bool passwordRequest: !!activeBackend && activeBackend.passwordRequest
    readonly property string accountName: activeBackend?.accountName || ""
    readonly property string iconName: activeBackend?.iconName || ""
    readonly property string requestLine: activeBackend?.requestLine || ""
    readonly property var detailRows: activeBackend?.detailRows || []
    readonly property bool agent: activeBackend?.agent === true
    readonly property bool keyOffered: activeBackend?.keyOffered === true
    readonly property bool keyEligible: activeBackend?.keyEligible === true
    readonly property bool turnEligible: activeBackend?.turnEligible === true
    signal rejected
    signal interrupted

    function identity(source: var): var {
        return source?.requestKind === "sudo" ? source.flow?.id || null : source?.flow || null;
    }
    function sync(): void {
        const next = requests.filter(entry => (entry.backend === polkitBackend || entry.backend === sudoBackend) && entry.identity === identity(entry.backend) && entry.identity !== null);
        for (const source of [polkitBackend, sudoBackend]) {
            const current = identity(source);
            if (current !== null && !next.some(entry => entry.backend === source))
                next.push({
                    backend: source,
                    identity: current
                });
        }
        if (next.length !== requests.length || next.some((entry, index) => entry !== requests[index])) {
            requests = next;
            flow = next.length ? next[0] : null;
        }
    }
    function submit(value: string): void {
        activeBackend?.submit(value);
    }
    function submitKey(value: string, mode: string, seconds: int): void {
        if (keyEligible)
            activeBackend.submitKey(value, mode, seconds);
    }
    function cancel(reason: string): void {
        activeBackend?.cancel(reason);
    }
    onPolkitBackendChanged: sync()
    onSudoBackendChanged: sync()
    Component.onCompleted: sync()
    Connections {
        target: queue.polkitBackend
        function onFlowChanged(): void {
            queue.sync();
        }
        function onRejected(): void {
            if (queue.activeBackend === queue.polkitBackend)
                queue.rejected();
        }
    }
    Connections {
        target: queue.sudoBackend
        function onFlowChanged(): void {
            queue.sync();
        }
        function onRejected(): void {
            if (queue.activeBackend === queue.sudoBackend)
                queue.rejected();
        }
        function onInterrupted(): void {
            queue.interrupted();
        }
    }
}
