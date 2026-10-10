// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import "settings"

Column {
    id: page
    required property var networkSettings
    property string connectionId: ""
    readonly property var connection: networkSettings.connections.find(item => item.uuid === connectionId) || null
    property string vpnType: "wireguard"
    readonly property var vpnTypes: networkSettings.snapshot?.vpn_types ?? [
        {
            label: "WireGuard",
            value: "wireguard"
        }
    ]
    spacing: 20
    function chooseConnection(): void {
        if (!connection && networkSettings.connections.length)
            connectionId = networkSettings.connections[0].uuid;
    }
    function importVpn(url): void {
        if (!networkSettings.busy)
            networkSettings.run(["import-vpn", vpnType, url.toString()]);
    }
    Connections {
        target: page.networkSettings
        function onSnapshotChanged(): void {
            page.chooseConnection();
            if (!page.vpnTypes.some(item => item.value === page.vpnType))
                page.vpnType = page.vpnTypes[0]?.value ?? "";
        }
    }
    Component.onCompleted: chooseConnection()

    component Entry: SettingsRow {
        id: entry
        property string placeholder: ""
        signal submitted(string text)
        controlWidth: Math.min(230, width * .43)
        control: Component {
            TextField {
                id: field
                text: String(entry.value || "")
                placeholderText: entry.placeholder
                Accessible.name: entry.title
                selectByMouse: true
                color: SettingsTheme.ink
                placeholderTextColor: SettingsTheme.faint
                font.family: SettingsTheme.fontFamily
                font.pixelSize: 13
                leftPadding: 10
                rightPadding: 10
                onEditingFinished: {
                    if (entry.editable && text !== String(entry.value || "")) {
                        entry.submitted(text);
                        field.text = Qt.binding(() => String(entry.value || ""));
                    }
                }
                background: Rectangle {
                    implicitHeight: 34
                    radius: 10
                    color: SettingsTheme.field
                    border.width: field.activeFocus ? 2 : 1
                    border.color: field.activeFocus ? SettingsTheme.accent : SettingsTheme.rim
                }
            }
        }
    }

    SettingsCard {
        objectName: "settings-network-wired"
        width: parent.width
        title: "Wired"
        Repeater {
            model: page.networkSettings.wired
            delegate: SettingsRow {
                required property var modelData
                width: parent.width
                title: modelData.device
                explanation: modelData.connection ? modelData.connection + " · " + modelData.state : modelData.state
                controlWidth: 0
            }
        }
        SettingsRow {
            width: parent.width
            visible: !page.networkSettings.wired.length
            title: "Wired connection"
            explanation: "No wired adapter is available."
            controlWidth: 0
        }
    }
    SettingsCard {
        width: parent.width
        title: "Connection"
        SettingsChoiceRow {
            objectName: "settings-network-connection"
            width: parent.width
            title: "Connection"
            explanation: "DNS changes belong to this saved connection."
            value: page.connectionId
            defaultValue: value
            options: page.networkSettings.connections.map(item => ({
                        label: item.name,
                        value: item.uuid
                    }))
            unavailable: !options.length
            busy: page.networkSettings.busy
            onValueRequested: value => page.connectionId = value
        }
    }
    SettingsCard {
        objectName: "settings-network-dns"
        width: parent.width
        title: "DNS"
        Entry {
            objectName: "settings-network-dns-servers"
            width: parent.width
            title: "DNS servers"
            explanation: "Separate server addresses with commas. Empty uses automatic DNS."
            placeholder: "Automatic"
            value: page.connection?.dns.join(", ") || ""
            defaultValue: page.connection?.dnsAutomatic === false ? "Automatic" : ""
            unavailable: !page.connection
            busy: page.networkSettings.busy
            onSubmitted: text => {
                if (editable)
                    page.networkSettings.run(["dns", page.connectionId, text.trim() || "reset"]);
            }
            onResetRequested: page.networkSettings.run(["dns", page.connectionId, "reset"])
        }
    }
    SettingsCard {
        objectName: "settings-network-vpn"
        width: parent.width
        title: "VPN"
        SettingsChoiceRow {
            width: parent.width
            title: "VPN type"
            explanation: "Available formats depend on the installed NetworkManager plugins."
            value: page.vpnType
            defaultValue: value
            busy: page.networkSettings.busy
            options: page.vpnTypes
            onValueRequested: value => page.vpnType = value
        }
        SettingsRow {
            width: parent.width
            title: "Import VPN from a file"
            explanation: "Add a saved connection using your provider’s configuration file."
            busy: page.networkSettings.busy
            unavailable: page.vpnType === ""
            control: Component {
                SettingsButton {
                    text: "Choose file…"
                    onClicked: vpnFile.open()
                }
            }
        }
    }
    FileDialog {
        id: vpnFile
        title: "Import VPN from a file"
        fileMode: FileDialog.OpenFile
        onAccepted: page.importVpn(selectedFile)
    }
    Text {
        objectName: "settings-network-status"
        width: parent.width
        text: page.networkSettings.error || page.networkSettings.message
        visible: text !== ""
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        color: page.networkSettings.error ? "#a01b45" : SettingsTheme.dim
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 13
    }
    SettingsButton {
        text: page.networkSettings.busy ? "Checking…" : "Refresh"
        enabled: !page.networkSettings.busy
        onClicked: page.networkSettings.refresh()
    }
}
