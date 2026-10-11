// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import "SudoText.js" as Copy
import "PolkitDetails.js" as Details

Scope {
    id: adapter
    required property var client
    property bool awaitingResponse: false
    property bool unsuccessful: false
    property bool cancelling: false
    property string previousId: ""
    readonly property string requestKind: "sudo"
    readonly property var flow: client.connected && client.request && client.request.id ? client.request : null
    readonly property bool responseRequired: !!flow && !awaitingResponse
    readonly property bool passwordRequest: true
    readonly property string accountName: flow ? flow.accountName || Copy.unavailable : ""
    readonly property string iconName: ""
    readonly property string requestLine: !flow ? "" : agent && flow.requester ? Details.clean(flow.requester) + " wants to run a command as administrator" : Copy.requestUnavailable
    readonly property var detailRows: !flow ? [] : [
        {
            label: Copy.sudoProcessChain,
            value: Details.clean(flow.processChain || Copy.unavailable)
        },
        {
            label: Copy.sudoCommand,
            value: Details.clean(flow.command || Copy.unavailable)
        },
        {
            label: Copy.sudoWorkingFolder,
            value: Details.clean(flow.workingDirectory || Copy.unavailable)
        },
        {
            label: Copy.sudoUser,
            value: Details.clean(flow.user || Copy.unavailable) + " → " + Details.clean(flow.targetUser || Copy.unavailable)
        }
    ]
    readonly property bool agent: !!flow && flow.agent === true
    readonly property bool keyOffered: !!flow && flow.keyEligible === true && client.installed
    readonly property bool keyEligible: keyOffered && client.warningAccepted
    readonly property bool turnEligible: keyEligible && flow.turnEligible === true
    signal rejected
    signal interrupted
    onFlowChanged: {
        const nextId = flow?.id || "";
        if (previousId && nextId !== previousId) {
            // The native channel does not expose every completion reason. A lost
            // connection, explicit failure, or end before submission is known
            // to be an interruption; an unclassified submitted end is not.
            if (!cancelling && (unsuccessful || !client.connected || !awaitingResponse))
                interrupted();
            awaitingResponse = false;
            unsuccessful = false;
        }
        previousId = nextId;
    }
    function submit(value: string): void {
        submitKey(value, "none", 0);
    }
    function submitKey(value: string, mode: string, seconds: int): void {
        if (!responseRequired || (mode !== "none" && !keyEligible))
            return;
        const turns = mode.startsWith("turns:") ? Number(mode.slice(6)) : 0;
        const turnMode = turnEligible && Number.isInteger(turns) && turns >= 1 && turns <= 1000 && mode === "turns:" + turns;
        if ((!turnMode && !["none", "time", "session"].includes(mode)) || (mode === "time" && (seconds < 300 || seconds > 86400 || seconds % 60 !== 0)))
            return;
        awaitingResponse = true;
        client.submit(flow.id, value, mode, mode === "time" ? seconds : 0);
    }
    function cancel(reason: string): void {
        cancelling = true;
        if (flow)
            client.cancel(flow.id);
        awaitingResponse = false;
        cancelling = false;
    }
    Connections {
        target: adapter.client
        function onAuthenticationFailed(): void {
            if (!adapter.flow)
                return;
            adapter.unsuccessful = true;
            adapter.awaitingResponse = true;
            adapter.rejected();
        }
        function onRejected(): void {
            if (!adapter.flow)
                return;
            adapter.awaitingResponse = false;
            adapter.rejected();
        }
    }
}
