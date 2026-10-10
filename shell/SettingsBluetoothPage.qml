// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import "settings"

Column {
    id: page
    required property SystemService service
    readonly property var backend: service.backend
    readonly property bool powered: backend?.adapter?.enabled ?? false
    readonly property bool pending: service.actionState === "pending" || !!service.pendingCheck
    readonly property var pairedDevices: Array.from(backend?.devices ?? []).filter(device => device.paired)
    readonly property var nearbyDevices: Array.from(backend?.devices ?? []).filter(device => !device.paired)
    property var discoveryAdapter: null
    property string actionKind: ""
    property int actionSerial: -1
    property string actionResult: "idle"
    property bool awaitingAction: false
    function clearAction() {
        awaitingAction = false;
        actionKind = "";
        actionResult = "idle";
    }
    function request(kind, value) {
        awaitingAction = false;
        actionKind = kind;
        const accepted = service.act(kind, value);
        actionSerial = service.actionSerial;
        actionResult = service.actionState;
        awaitingAction = accepted && ["pending", "busy"].includes(actionResult);
        return accepted;
    }
    Connections {
        target: page.service
        function onActionStateChanged(): void {
            if (!page.awaitingAction)
                return;
            if (page.actionSerial !== page.service.actionSerial) {
                page.clearAction();
                return;
            }
            page.actionResult = page.service.actionState;
            page.awaitingAction = ["pending", "busy"].includes(page.actionResult);
        }
    }
    spacing: 16

    function act(kind, key) {
        if (!backend?.adapter || (kind !== "bt-power" && !powered))
            return false;
        if (pending && kind !== "bt-cancel-pair")
            return false;
        const adapter = backend.adapter;
        const startingDiscovery = kind === "bt-scan" && !adapter.discovering;
        const accepted = request(kind, key);
        if (accepted && kind === "bt-scan")
            discoveryAdapter = startingDiscovery ? adapter : null;
        return accepted;
    }
    function stopDiscovery() {
        if (discoveryAdapter)
            discoveryAdapter.discovering = false;
        discoveryAdapter = null;
    }
    onVisibleChanged: if (!visible) {
        stopDiscovery();
        clearAction();
    }
    Component.onDestruction: stopDiscovery()
    Connections {
        target: page.Window.window
        function onVisibleChanged(): void {
            if (!page.Window.window.visible) {
                page.stopDiscovery();
                page.clearAction();
            }
        }
    }
    function deviceDetail(device) {
        const state = device.pairing ? "Pairing…" : device.connected ? "Connected" : device.paired ? "Not connected" : "Ready to pair";
        return state + (typeof device.battery === "number" && device.battery >= 0 && device.battery <= 100 ? " · Battery " + device.battery + "%" : "");
    }

    Text {
        objectName: "settings-bluetooth-status"
        width: parent.width
        visible: !["idle", "confirmed", "ready", "applied"].includes(page.actionResult)
        text: page.awaitingAction ? "Working…" : page.actionResult === "pairing_failed" ? "Couldn’t pair. Make sure it’s in pairing mode." : "Could not confirm the change. The current value is shown below."
        textFormat: Text.PlainText
        color: page.awaitingAction ? SettingsTheme.dim : "#a01b45"
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 13
        wrapMode: Text.WordWrap
    }
    SettingsCard {
        width: parent.width
        SettingsToggleRow {
            objectName: "settings-bluetooth-power"
            width: parent.width
            title: "Bluetooth"
            explanation: !page.backend?.adapter ? "No Bluetooth adapter" : page.powered ? "On. Paired devices can connect." : "Off. Turn Bluetooth on to connect devices."
            value: page.powered
            defaultValue: value
            unavailable: !page.backend?.adapter
            busy: page.pending
            onValueRequested: value => {
                if (value !== page.powered)
                    page.act("bt-power", null);
            }
        }
    }
    SettingsCard {
        width: parent.width
        objectName: "settings-bluetooth-devices"
        title: "My devices"
        visible: !!page.backend?.adapter
        SettingsRow {
            width: parent.width
            visible: page.pairedDevices.length === 0
            title: "No paired devices"
            explanation: "Pair a device to keep it in this list."
        }
        Repeater {
            model: page.pairedDevices
            SettingsRow {
                id: pairedRow
                required property var modelData
                objectName: "settings-bluetooth-device-" + modelData.key
                width: parent.width
                title: modelData.name || "Bluetooth device"
                explanation: page.deviceDetail(modelData)
                controlWidth: 190
                busy: page.pending
                unavailable: !page.powered
                control: Component {
                    Row {
                        spacing: 6
                        SettingsButton {
                            objectName: "connect"
                            text: pairedRow.modelData.connected ? "Disconnect" : "Connect"
                            Accessible.name: text + " " + pairedRow.title
                            onClicked: page.act(pairedRow.modelData.connected ? "bt-disconnect" : "bt-connect", pairedRow.modelData.key)
                        }
                        SettingsButton {
                            objectName: "forget"
                            text: "Forget"
                            Accessible.name: "Forget " + pairedRow.title
                            onClicked: page.act("bt-forget", pairedRow.modelData.key)
                        }
                    }
                }
            }
        }
    }
    SettingsCard {
        width: parent.width
        title: "Pair a new device"
        SettingsRow {
            width: parent.width
            title: "Nearby devices"
            explanation: !page.backend?.adapter ? "No Bluetooth adapter" : page.backend.adapter.discovering ? "Searching…" : "Put a device in pairing mode, then press Search."
            unavailable: !page.powered
            busy: page.pending
            control: Component {
                SettingsButton {
                    objectName: "settings-bluetooth-search"
                    text: page.backend?.adapter?.discovering ? "Stop" : "Search"
                    onClicked: page.act("bt-scan", null)
                }
            }
        }
        Repeater {
            model: page.powered ? page.nearbyDevices : []
            SettingsRow {
                id: nearbyRow
                required property var modelData
                readonly property bool pairing: modelData.pairing || (page.service.pendingKind === "bt-pair" && page.service.pendingValue === modelData.key && page.pending)
                objectName: "settings-bluetooth-nearby-" + modelData.key
                width: parent.width
                title: modelData.name || "Bluetooth device"
                explanation: pairing ? "Pairing…" : page.deviceDetail(modelData)
                busy: page.pending && !pairing
                control: Component {
                    SettingsButton {
                        objectName: "pair"
                        text: nearbyRow.pairing ? "Cancel" : "Pair"
                        Accessible.name: text + " " + nearbyRow.title
                        onClicked: page.act(nearbyRow.pairing ? "bt-cancel-pair" : "bt-pair", nearbyRow.modelData.key)
                    }
                }
            }
        }
    }
    Text {
        width: parent.width
        visible: page.powered
        text: "Confirm Bluetooth codes in the notification at the top of the screen; enter PINs in the Bluetooth dialog. While sharing your screen, open the notification drawer to read the request."
        textFormat: Text.PlainText
        color: SettingsTheme.dim
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 13
        wrapMode: Text.WordWrap
    }
}
