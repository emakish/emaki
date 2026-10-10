// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    id: root
    property var calls: []
    SystemBackend {
        id: backend
        networkReady: true
        wifiEnabled: true
        wifiHardwareEnabled: true
        wifiDevices: [
            {
                scannerEnabled: false
            }
        ]
        networks: [
            {
                key: "one",
                name: "Fixture wireless",
                known: false,
                connected: false,
                signal: 0.8,
                open: false,
                psk: true,
                device: "wlan0"
            }
        ]
    }
    SystemService {
        id: service
        backend: backend
        function act(kind: string, value: var): bool {
            actionSerial += 1;
            root.calls = root.calls.concat([
                {
                    kind: kind,
                    value: value
                }
            ]);
            return true;
        }
    }
    QtObject {
        id: metadata
        property var wifiProfiles: [
            {
                uuid: "12345678-1234-1234-1234-123456789abc",
                name: "Fixture wireless",
                ssid: "Fixture wireless",
                device: "wlan0",
                security: "wpa-psk",
                addresses: ["192.0.2.1/24"]
            }
        ]
        property string error: ""
        function refresh() {
        }
    }
    FloatingWindow {
        id: window
        visible: true
        implicitWidth: 760
        implicitHeight: 1050
        color: "#eee4db"
        SettingsWifiPage {
            id: page
            x: 20
            y: 20
            width: parent.width - 40
            service: service
            networkSettings: metadata
        }
    }
    Loader {
        id: hostedFooter
        width: 680
        active: page.footerHosted
        sourceComponent: page.footerContent
    }
    TestCase {
        name: "SettingsWifiPage"
        when: window.backingWindowVisible
        onCompletedChanged: if (completed)
            console.log("WIFI_SETTINGS_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
        function find(item, name) {
            if (item.objectName === name)
                return item;
            for (const child of item.children ?? []) {
                const match = find(child, name);
                if (match)
                    return match;
            }
            return null;
        }
        function test_controls() {
            verify(backend.settingsWifiScan);
            verify(backend.wifiScanning);
            verify(find(page, "settings-wifi-restart"));
            page.footerHosted = true;
            verify(!find(page, "settings-wifi-restart"));
            verify(find(hostedFooter, "settings-wifi-restart"));
            verify(hostedFooter.item.height > 0);
            page.footerHosted = false;
            const power = find(page, "settings-wifi-power");
            power.requestValue(false);
            compare(root.calls[root.calls.length - 1].kind, "wifi-power");
            page.beginJoin(backend.networks[0]);
            compare(page.selectedKey, "one");
            const input = find(page, "settings-wifi-password");
            input.text = "fixture-secret";
            backend.networks = [Object.assign({}, backend.networks[0], {
                    signal: .5
                })];
            compare(input.text, "fixture-secret");
            page.join();
            compare(root.calls[root.calls.length - 1].kind, "wifi-connect");
            compare(root.calls[root.calls.length - 1].value.password, "fixture-secret");
            compare(input.text, "");
            backend.wifiPasswordRequired("one");
            compare(page.selectedKey, "one");
            page.closeJoin();
            page.hiddenJoin = true;
            find(page, "settings-wifi-hidden-name").text = "Hidden fixture";
            input.text = "another-secret";
            page.join();
            compare(root.calls[root.calls.length - 1].kind, "hidden");
            compare(root.calls[root.calls.length - 1].value.ssid, "Hidden fixture");
            compare(root.calls[root.calls.length - 1].value.password, "another-secret");
            verify(!service.revealWifiPassword("12345678-1234-1234-1234-123456789abc"));
            compare(service.wifiPassword, "");
            page.closeJoin();
            backend.wifiEnabled = false;
            backend.networks = [];
            const forget = find(page, "settings-wifi-forget-12345678-1234-1234-1234-123456789abc");
            verify(forget.enabled);
            forget.clicked();
            compare(root.calls[root.calls.length - 1].kind, "wifi-forget-saved");
            compare(root.calls[root.calls.length - 1].value, "12345678-1234-1234-1234-123456789abc");
            backend.wifiEnabled = true;
            backend.networks = [
                {
                    key: "one",
                    name: "Fixture wireless",
                    known: false,
                    connected: false,
                    signal: 0.5,
                    open: false,
                    psk: true,
                    device: "wlan0"
                }
            ];
            service.wifiPassword = "temporary-secret";
            backend.wifiScan = true;
            page.visible = false;
            verify(!backend.settingsWifiScan);
            verify(backend.wifiScanning, "Quick panel keeps its independent scanner demand");
            compare(service.wifiPassword, "");
            backend.wifiScan = false;
            verify(!backend.wifiScanning);
            page.visible = true;
            service.actionState = "wifi_restart_failed";
            verify(!find(page, "settings-wifi-action-status").visible, "An unrelated restart failure stays out of this page");
            service.actionState = "pending";
            page.request("wifi-restart", null);
            service.actionState = "wifi_restart_failed";
            verify(find(page, "settings-wifi-action-status").visible);
            verify(find(page, "settings-wifi-action-status").text.includes("could not restart"));
            service.actionState = "target_gone";
            verify(find(page, "settings-wifi-action-status").text.includes("could not restart"), "Completed feedback stays local");
            page.visible = false;
            page.visible = true;
            verify(!find(page, "settings-wifi-action-status").visible);
            service.actionState = "pending";
            page.request("wifi-restart", null);
            service.act("brightness", 30);
            service.actionState = "target_gone";
            verify(!find(page, "settings-wifi-action-status").visible, "Overlapping panel action is not a Wi-Fi failure");
            service.actionState = "idle";
            const adapters = backend.wifiDevices;
            page.beginJoin(backend.networks[0]);
            input.text = "removed-adapter-secret";
            service.wifiPassword = "temporary-secret";
            backend.wifiDevices = [];
            compare(input.text, "");
            compare(service.wifiPassword, "");
            compare(power.explanation, "No Wi-Fi adapter");
            verify(power.unavailable);
            verify(!power.value);
            verify(!backend.settingsWifiScan);
            verify(!find(page, "settings-wifi-connected").visible);
            verify(!find(page, "settings-wifi-networks").visible);
            verify(!find(page, "settings-wifi-restart").enabled);
            const absentBefore = root.calls.length;
            power.requestValue(true);
            verify(!page.request("wifi-restart", null));
            page.beginJoin(backend.networks[0]);
            compare(page.selectedKey, "");
            compare(root.calls.length, absentBefore);
            verify(forget.enabled, "Saved profiles remain manageable without an adapter");
            backend.wifiDevices = adapters;
            verify(!power.unavailable);
            verify(power.value);
            verify(backend.settingsWifiScan);
            verify(find(page, "settings-wifi-networks").visible);
            verify(power.explanation.startsWith("On."));
            backend.wifiHardwareEnabled = false;
            verify(power.unavailable);
            compare(power.explanation, "The wireless radio is blocked by the system.");
            backend.wifiHardwareEnabled = true;
            wait(80);
            let saved = false;
            page.grabToImage(result => saved = result.saveToFile(Quickshell.env("SYSTEM_SETTINGS_SHOT")));
            tryVerify(() => saved, 5000);
        }
    }
}
