// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import QtTest
import Quickshell

ShellRoot {
    id: root
    property var calls: []
    QtObject {
        id: backend
        property var snapshot: ({
                connections: [
                    {
                        uuid: "first",
                        name: "Same name",
                        dns: ["1.1.1.1"],
                        dnsAutomatic: false,
                        proxyMode: "none",
                        proxyValue: ""
                    },
                    {
                        uuid: "second",
                        name: "Same name",
                        dns: [],
                        dnsAutomatic: false,
                        proxyMode: "auto",
                        proxyValue: "https://example.test/proxy.pac"
                    }
                ],
                wired: [
                    {
                        device: "ethernet0",
                        state: "connected",
                        connection: "Same name"
                    }
                ]
            })
        readonly property var connections: snapshot.connections
        readonly property var wired: snapshot.wired
        property bool busy: false
        property string error: ""
        property string message: ""
        function run(args) {
            root.calls = root.calls.concat([args]);
        }
        function refresh() {
            run(["status", "--json"]);
        }
    }
    SettingsNetworkService {
        id: service
    }
    FloatingWindow {
        id: window
        visible: true
        implicitWidth: 760
        implicitHeight: 1000
        Rectangle {
            id: canvas
            width: parent.width
            height: page.height + 40
            color: "#eee4db"
            SettingsNetworkPage {
                id: page
                width: parent.width - 40
                x: 20
                y: 20
                networkSettings: backend
            }
        }
    }
    TestCase {
        name: "SettingsNetworkPage"
        when: window.backingWindowVisible
        onCompletedChanged: if (completed)
            console.log("NETWORK_SETTINGS_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
        function equal(actual, expected, message) {
            if (actual !== expected)
                console.error("CHECK", message || "", "actual:", actual, "expected:", expected);
            compare(actual, expected, message || "");
        }
        function check(value, message) {
            if (!value)
                console.error("CHECK", message || "condition failed");
            verify(value, message || "");
        }
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
        function last(args) {
            equal(JSON.stringify(root.calls[root.calls.length - 1]), JSON.stringify(args));
        }
        function capture(name, width) {
            if (!Quickshell.env("NETWORK_SETTINGS_SHOT"))
                return;
            page.Window.window.width = width;
            wait(80);
            let saved = false;
            canvas.grabToImage(result => saved = result.saveToFile(Quickshell.env("NETWORK_SETTINGS_SHOT") + "-" + name + ".png"));
            tryVerify(() => saved, 3000);
        }
        function test_page() {
            wait(80);
            const select = find(page, "settings-network-connection");
            const dns = find(page, "settings-network-dns-servers");
            equal(find(page, "settings-network-proxy"), null);
            equal(page.connectionId, "first");
            equal(select.options[0].label, select.options[1].label);
            dns.controlItem.text = " 9.9.9.9, 2001:db8::1 ";
            dns.controlItem.editingFinished();
            last(["dns", "first", "9.9.9.9, 2001:db8::1"]);
            let before = root.calls.length;
            dns.controlItem.editingFinished();
            equal(root.calls.length, before, "Focus changes must not resubmit stale DNS drafts");
            dns.requestReset();
            last(["dns", "first", "reset"]);
            select.requestValue("second");
            equal(page.connectionId, "second");
            check(dns.changed, "Manual-only DNS with an empty list still needs automatic reset");
            dns.requestReset();
            last(["dns", "second", "reset"]);
            capture("wide", 970);
            capture("narrow", 560);
            backend.error = "DNS could not be saved.";
            const status = find(page, "settings-network-status");
            check(status.visible);
            equal(status.text, "DNS could not be saved.");
            page.vpnType = "wireguard";
            page.importVpn("file:///tmp/provider%20vpn.conf");
            last(["import-vpn", "wireguard", "file:///tmp/provider%20vpn.conf"]);
            backend.busy = true;
            before = root.calls.length;
            dns.requestReset();
            select.requestValue("first");
            page.importVpn("file:///tmp/ignored.conf");
            equal(root.calls.length, before);
            check(!dns.controlItem.enabled);
            backend.busy = false;
            backend.snapshot = ({
                    connections: [],
                    wired: []
                });
            check(dns.unavailable);
            dns.requestReset();
            equal(root.calls.length, before);
        }
        function test_service() {
            equal(service.busy, false);
            service.run(["failure"]);
            equal(service.busy, false, "Disabled service must not start a process");
            service.enabled = true;
            tryCompare(service, "busy", false, 3000);
            tryVerify(() => service.connections.length === 1, 3000);
            equal(service.connections[0].uuid, "fixture-uuid");
            service.run(["failure"]);
            service.run(["malformed"]);
            equal(service.operation, "failure", "Busy service must reject concurrent requests");
            tryCompare(service, "busy", false, 3000);
            equal(service.error, "Fixture permission denied.");
            service.refresh();
            tryCompare(service, "busy", false, 3000);
            equal(service.error, "Fixture permission denied.", "Polling must preserve action failure");
            service.run(["malformed"]);
            tryCompare(service, "busy", false, 3000);
            equal(service.error, "Network settings are unavailable. Try again.");
            service.run(["success"]);
            tryVerify(() => service.message === "Fixture imported." && !service.busy, 3000);
            equal(service.error, "");
            for (const mode of ["bad-version", "bad-shape", "bad-entry"]) {
                service.run(["status", mode]);
                tryCompare(service, "busy", false, 3000);
                equal(service.error, "Network settings are unavailable. Try again.");
                equal(service.connections[0].uuid, "fixture-uuid", "Malformed status must preserve the last good snapshot");
                service.refresh();
                tryCompare(service, "busy", false, 3000);
                equal(service.error, "", "Successful refresh clears a previous read failure");
            }
            service.run(["remove-self"]);
            tryVerify(() => service.error === "Network settings are unavailable. Try again.", 3000);
            equal(service.busy, false, "Missing command must release the busy state promptly");
            service.enabled = false;
        }
    }
}
