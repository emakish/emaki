// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Services.Polkit
import "PolkitDetails.js" as Details

// All native API dependencies, including the additive request metadata, live here.
Scope {
    id: adapter
    readonly property var flow: native.flow
    readonly property bool registered: native.isRegistered
    readonly property bool responseRequired: !!flow && flow.isResponseRequired && !flow.isCompleted
    readonly property bool passwordRequest: !!flow && !flow.responseVisible
    readonly property string accountName: flow ? Details.clean(flow.selectedIdentity?.displayName || flow.selectedIdentity?.string || "") : ""
    readonly property string iconName: flow && /^[A-Za-z0-9_.-]{1,128}$/.test(flow.iconName) ? flow.iconName : ""
    readonly property string requestLine: flow ? Details.headline(flow.actionId, flow.message, optional("details"), Platform.libexecDir) : ""
    readonly property var detailRows: flow ? Details.rows(flow.actionId, flow.message, optional("details"), requester(), {
        binDir: Platform.binDir,
        libexecDir: Platform.libexecDir,
        libDir: Platform.libDir
    }) : []
    signal rejected
    signal interrupted
    function optional(name: string): var {
        return flow && typeof flow[name] !== "undefined" ? flow[name] : null;
    }
    function requester(): string {
        // Never substitute the authenticating account for the requesting process.
        const subject = optional("subject");
        if (!subject || typeof subject !== "object")
            return "";
        return ["description", "pid", "uid"].filter(key => subject[key] !== undefined && subject[key] !== null).map(key => key + ": " + Details.redact(subject[key])).join(" · ");
    }
    function submit(value: string): void {
        if (responseRequired && passwordRequest)
            flow.submit(value);
    }
    function cancel(reason: string): void {
        if (flow && !flow.isCompleted)
            flow.cancelAuthenticationRequest();
    }
    PolkitAgent {
        id: native
    }
    Connections {
        target: adapter.flow
        function onAuthenticationFailed(): void {
            adapter.rejected();
        }
    }
}
