// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import QtQuick.Controls
import "settings"

Column {
    id: root
    required property SystemService service
    required property var networkSettings
    readonly property bool hasAdapter: (backend?.wifiDevices?.length ?? 0) > 0
    readonly property var backend: service.backend
    readonly property bool busy: service.actionState === "pending" || service.actionState === "busy" || service.wifiRestartRunning
    readonly property var networks: Array.from(backend?.networks ?? [])
    readonly property var saved: networkSettings?.wifiProfiles ?? []
    property string selectedKey: ""
    property bool hiddenJoin: false
    readonly property var selected: networks.find(row => row.key === selectedKey) ?? null
    property string actionKind: ""
    property int actionSerial: -1
    property string actionResult: "idle"
    property bool awaitingAction: false
    Connections {
        target: root.Window.window
        function onVisibleChanged(): void {
            if (!root.Window.window.visible)
                root.clearAction();
        }
    }
    function clearAction() {
        awaitingAction = false;
        actionKind = "";
        actionResult = "idle";
    }
    function request(kind, value) {
        if (!hasAdapter && kind !== "wifi-forget-saved")
            return false;
        awaitingAction = false;
        actionKind = kind;
        const accepted = service.act(kind, value);
        actionSerial = service.actionSerial;
        actionResult = service.actionState;
        awaitingAction = accepted && ["pending", "busy"].includes(actionResult);
        return accepted;
    }
    spacing: 16

    property bool footerHosted: false
    property Component footerContent: Component {
        Item {
            readonly property int spacing: 14
            implicitHeight: Math.max(restartButton.implicitHeight, restartExplanation.implicitHeight)
            height: implicitHeight
            SettingsButton {
                id: restartButton
                objectName: "settings-wifi-restart"
                anchors.verticalCenter: parent.verticalCenter
                text: "Restart Wi-Fi"
                enabled: root.hasAdapter && !root.busy && (root.backend?.wifiRestartAvailable ?? false)
                onClicked: root.request("wifi-restart", null)
            }
            Text {
                id: restartExplanation
                x: restartButton.width + parent.spacing
                width: Math.max(80, parent.width - restartButton.width - parent.spacing)
                anchors.verticalCenter: parent.verticalCenter
                text: "Turns Wi-Fi off and on again. Try it when networks stop showing up or a connection hangs."
                font.family: SettingsTheme.fontFamily
                font.pixelSize: 12
                color: SettingsTheme.dim
                wrapMode: Text.WordWrap
                textFormat: Text.PlainText
            }
        }
    }
    component NetworkIndicators: Row {
        id: indicators
        required property var network
        readonly property real strength: Number(network?.signal ?? 0) * (Number(network?.signal ?? 0) <= 1 ? 100 : 1)
        width: 56
        height: 34
        spacing: 8
        Row {
            height: 18
            anchors.verticalCenter: parent.verticalCenter
            spacing: 2
            Repeater {
                model: 4
                Rectangle {
                    required property int index
                    width: 4
                    height: 5 + index * 4
                    anchors.bottom: parent.bottom
                    radius: 1
                    color: indicators.strength > index * 25 ? SettingsTheme.ink : SettingsTheme.rim
                }
            }
        }
        Image {
            width: 14
            height: 14
            anchors.verticalCenter: parent.verticalCenter
            visible: !indicators.network?.open
            source: "settings/icons/wifi-lock.svg"
        }
    }

    function sync(model, keys) {
        for (let i = model.count - 1; i >= 0; --i)
            if (!keys.includes(model.get(i).rowKey))
                model.remove(i);
        keys.forEach((key, index) => {
            let at = index;
            while (at < model.count && model.get(at).rowKey !== key)
                ++at;
            if (at === model.count)
                model.insert(index, {
                    rowKey: key
                });
            else if (at !== index)
                model.move(at, index, 1);
        });
    }
    function signalText(row) {
        const value = Number(row?.signal ?? 0);
        return Math.round(value <= 1 ? value * 100 : value) + "% signal";
    }
    function securityText(row) {
        return row?.open ? "Open" : row?.psk ? "Password protected" : "Secured";
    }
    function profileFor(row) {
        const matches = saved.filter(profile => profile.ssid === row?.name && (!profile.device || profile.device === row.device));
        return matches.length === 1 ? matches[0] : null;
    }
    function connectionText(row) {
        const profile = profileFor(row);
        return "Connected · " + signalText(row) + " · " + securityText(row) + (profile?.addresses?.length ? " · " + profile.addresses.join(", ") : "") + (row?.device ? " · " + row.device : "");
    }
    function beginJoin(row) {
        if (!hasAdapter)
            return;
        password.text = "";
        hiddenJoin = false;
        if (row.open || (row.known && !backend.wifiPasswordKeys.includes(row.key))) {
            selectedKey = "";
            request("wifi-connect", {
                key: row.key
            });
        } else {
            selectedKey = row.key;
            password.forceActiveFocus();
        }
    }
    function join() {
        if (busy)
            return;
        const accepted = hiddenJoin ? request("hidden", {
            ssid: hiddenName.text,
            password: hiddenSecured.checked ? password.text : ""
        }) : selected && request("wifi-connect", {
            key: selected.key,
            password: password.text
        });
        if (accepted)
            password.text = "";
    }
    function closeJoin() {
        selectedKey = "";
        hiddenJoin = false;
        password.text = "";
        hiddenName.text = "";
    }
    function updateScan() {
        service.settingsWifiScan = visible && hasAdapter;
    }
    onHasAdapterChanged: {
        updateScan();
        if (!hasAdapter) {
            closeJoin();
            clearAction();
            service.clearWifiPassword();
        }
    }
    onVisibleChanged: {
        updateScan();
        if (!visible) {
            closeJoin();
            clearAction();
            service.clearWifiPassword();
        }
    }
    onNetworksChanged: {
        sync(connectedModel, networks.filter(row => row.connected).map(row => row.key));
        sync(nearbyModel, networks.filter(row => !row.connected && row.signal > 0).sort((a, b) => b.signal - a.signal).map(row => row.key));
        if (selected?.connected || (selectedKey !== "" && !selected))
            closeJoin();
    }
    onSavedChanged: sync(savedModel, saved.map(row => row.uuid))
    Component.onCompleted: updateScan()
    Component.onDestruction: {
        service.settingsWifiScan = false;

        service.clearWifiPassword();
    }
    ListModel {
        id: connectedModel
    }
    ListModel {
        id: nearbyModel
    }
    ListModel {
        id: savedModel
    }
    Connections {
        target: root.backend
        function onWifiEnabledChanged(): void {
            if (!root.backend.wifiEnabled) {
                root.closeJoin();
                root.service.clearWifiPassword();
            }
        }
        function onWifiPasswordRequired(key: string): void {
            root.selectedKey = key;
            root.hiddenJoin = false;
            password.text = "";
        }
    }
    Connections {
        target: root.service
        function onActionStateChanged(): void {
            if (!root.awaitingAction)
                return;
            if (root.actionSerial !== root.service.actionSerial) {
                root.clearAction();
                return;
            }
            root.actionResult = root.service.actionState;
            root.awaitingAction = ["pending", "busy"].includes(root.actionResult);
            if (["confirmed", "ready"].includes(root.actionResult))
                root.networkSettings.refresh();
        }
    }
    Text {
        objectName: "settings-wifi-action-status"
        width: parent.width
        visible: !["idle", "confirmed", "ready", "requested"].includes(root.actionResult)
        text: root.actionResult === "wifi_restart_failed" ? "Wi-Fi could not restart. Try again later." : root.awaitingAction ? "Working…" : root.actionResult === "wrong_password" ? "The password was not accepted. Enter it again." : "The change could not be confirmed. Check the current value and try again."
        color: root.awaitingAction ? SettingsTheme.dim : "#a01b45"
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 13
        wrapMode: Text.WordWrap
        textFormat: Text.PlainText
    }
    SettingsCard {
        width: parent.width
        SettingsToggleRow {
            objectName: "settings-wifi-power"
            width: parent.width
            title: "Wi-Fi"
            explanation: !root.backend?.networkReady ? "Network status is unavailable." : !root.hasAdapter ? "No Wi-Fi adapter" : !root.backend.wifiHardwareEnabled ? "The wireless radio is blocked by the system." : root.backend.wifiEnabled ? "On. Emaki joins saved networks by itself." : "Off. Turn Wi-Fi on to see nearby networks."
            value: root.hasAdapter && (root.backend?.wifiEnabled ?? false)
            defaultValue: value
            busy: root.busy
            unavailable: !root.backend?.networkReady || !root.backend?.wifiHardwareEnabled || !root.backend?.wifiDevices.length
            onValueRequested: root.request("wifi-power", null)
        }
    }
    SettingsCard {
        width: parent.width
        title: "Connected"
        objectName: "settings-wifi-connected"
        visible: root.hasAdapter && (root.backend?.wifiEnabled ?? false)
        Repeater {
            model: connectedModel
            SettingsRow {
                id: connectedRow
                required property string rowKey
                readonly property var network: root.networks.find(row => row.key === rowKey)
                width: parent.width
                title: network?.name ?? "Wireless network"
                explanation: root.connectionText(network)
                busy: root.busy
                controlWidth: 180
                control: Component {
                    Row {
                        spacing: 8
                        NetworkIndicators {
                            network: connectedRow.network
                        }
                        SettingsButton {
                            text: "Disconnect"
                            onClicked: root.request("wifi-disconnect", {
                                key: connectedRow.rowKey
                            })
                        }
                    }
                }
            }
        }
        SettingsRow {
            width: parent.width
            visible: connectedModel.count === 0
            title: "No wireless connection is active."
            explanation: "Choose a network below to connect."
            controlWidth: 0
        }
    }
    SettingsCard {
        objectName: "settings-wifi-networks"
        width: parent.width
        title: "Other networks"
        visible: root.hasAdapter && (root.backend?.wifiEnabled ?? false)
        Repeater {
            model: nearbyModel
            SettingsRow {
                id: nearbyRow
                required property string rowKey
                readonly property var network: root.networks.find(row => row.key === rowKey)
                width: parent.width
                title: network?.name ?? "Wireless network"
                explanation: root.signalText(network) + " · " + root.securityText(network) + (network?.known ? " · Saved" : "")
                controlWidth: 160
                busy: root.busy || !!network?.busy
                control: Component {
                    Row {
                        spacing: 8
                        NetworkIndicators {
                            network: nearbyRow.network
                        }
                        SettingsButton {
                            text: "Join"
                            onClicked: root.beginJoin(nearbyRow.network)
                        }
                    }
                }
            }
        }
        SettingsRow {
            width: parent.width
            visible: nearbyModel.count === 0
            title: "Looking for networks…"
            explanation: "Nearby wireless networks appear here."
            controlWidth: 0
        }
        SettingsRow {
            width: parent.width
            title: "Join a hidden network…"
            explanation: "For a network that does not show its name."
            controlWidth: 90
            busy: root.busy
            control: Component {
                SettingsButton {
                    text: "Join"
                    onClicked: {
                        root.closeJoin();
                        root.hiddenJoin = true;
                        hiddenName.forceActiveFocus();
                    }
                }
            }
        }
    }
    SettingsCard {
        width: parent.width
        visible: root.hiddenJoin || root.selected !== null
        title: root.hiddenJoin ? "Hidden network" : root.selected?.name ?? "Join network"
        Column {
            width: parent.width
            spacing: 10
            TextField {
                id: hiddenName
                objectName: "settings-wifi-hidden-name"
                visible: root.hiddenJoin
                width: parent.width
                placeholderText: "Network name"
                Accessible.name: "Network name"
                maximumLength: 32
                font.family: SettingsTheme.fontFamily
            }
            CheckBox {
                id: hiddenSecured
                visible: root.hiddenJoin
                checked: true
                text: "Password protected"
                font.family: SettingsTheme.fontFamily
            }
            TextField {
                id: password
                objectName: "settings-wifi-password"
                width: parent.width
                visible: !root.hiddenJoin || hiddenSecured.checked
                placeholderText: "Password"
                Accessible.name: "Password"
                echoMode: TextInput.Password
                maximumLength: 63
                enabled: !root.busy
                font.family: SettingsTheme.fontFamily
                onAccepted: if (joinButton.enabled)
                    root.join()
            }
            Text {
                width: parent.width
                visible: !!root.selected && !root.selected.psk
                text: "This network requires credentials that cannot be entered here."
                color: SettingsTheme.dim
                font.family: SettingsTheme.fontFamily
                font.pixelSize: 13
                wrapMode: Text.WordWrap
                textFormat: Text.PlainText
            }
            Row {
                spacing: 8
                SettingsButton {
                    id: joinButton
                    objectName: "settings-wifi-join"
                    text: "Join"
                    enabled: !root.busy && (root.hiddenJoin ? hiddenName.text.trim().length > 0 && (!hiddenSecured.checked || password.text.length >= 8) : !!root.selected?.psk && password.text.length >= 8)
                    onClicked: root.join()
                }
                SettingsButton {
                    text: "Cancel"
                    onClicked: root.closeJoin()
                }
            }
        }
    }
    SettingsCard {
        objectName: "settings-wifi-saved"
        width: parent.width
        title: "Saved networks"
        Repeater {
            model: savedModel
            SettingsRow {
                id: savedRow
                required property string rowKey
                readonly property var profile: root.saved.find(row => row.uuid === rowKey)
                width: parent.width
                title: profile?.name ?? "Wireless network"
                explanation: root.service.wifiPasswordUuid === rowKey && root.service.wifiPasswordState === "ready" ? root.service.wifiPassword : "Forget the network or show its saved password."
                controlWidth: 206
                busy: root.busy
                control: Component {
                    Row {
                        spacing: 8
                        SettingsButton {
                            text: root.service.wifiPasswordUuid === savedRow.rowKey && root.service.wifiPasswordState === "ready" ? "Hide" : "Show password"
                            enabled: root.service.wifiPasswordState !== "pending" && savedRow.profile?.security !== "open"
                            onClicked: {
                                if (root.service.wifiPasswordUuid === savedRow.rowKey && root.service.wifiPasswordState === "ready")
                                    root.service.clearWifiPassword();
                                else
                                    root.service.revealWifiPassword(savedRow.rowKey);
                            }
                        }
                        SettingsButton {
                            objectName: "settings-wifi-forget-" + savedRow.rowKey
                            text: "Forget"
                            onClicked: {
                                root.service.clearWifiPassword();
                                root.request("wifi-forget-saved", savedRow.rowKey);
                            }
                        }
                    }
                }
            }
        }
        SettingsRow {
            visible: savedModel.count === 0
            width: parent.width
            title: "No saved networks"
            explanation: "Networks are remembered after you join them."
            controlWidth: 0
        }
    }
    Text {
        width: parent.width
        visible: root.networkSettings.error !== "" || ["pending", "failed", "unavailable"].includes(root.service.wifiPasswordState)
        text: root.service.wifiPasswordState === "pending" ? "Waiting for authentication…" : root.service.wifiPasswordState === "failed" || root.service.wifiPasswordState === "unavailable" ? "The password could not be shown. Try again later." : "Saved network details could not be loaded. Try again later."
        color: SettingsTheme.dim
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 13
        wrapMode: Text.WordWrap
        textFormat: Text.PlainText
    }
    Loader {
        width: parent.width
        active: !root.footerHosted
        visible: active
        sourceComponent: root.footerContent
    }
}
