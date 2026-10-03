pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

// The production native bridge (SystemNative) on Quickshell's own NetworkManager backend,
// talking to tests/fixtures/nm-fake.py on the test's private bus (DBUS_SYSTEM_BUS_ADDRESS):
// what QS lists, and when it asks NetworkManager to scan, is QS's decision, not a fixture's.
ShellRoot {
    id: root
    function passwordIn(item: var): var {
        if (item.echoMode === TextInput.Password)
            return item;
        for (const child of item.children ?? []) {
            const found = passwordIn(child);
            if (found)
                return found;
        }
        return null;
    }
    SystemService {
        id: systemService
        backend: native
    }
    NiriService {
        id: niriService
        binary: ""
    }
    Window {
        width: 480
        height: 900
        visible: true
        SystemBody {
            id: body
            anchors.fill: parent
            service: systemService
            niri: niriService
            page: "wifi"
        }
    }
    SystemNative {
        id: native
    }
    IpcHandler {
        target: "test"
        // What SystemBody does while the Wi-Fi page is open.
        function scan(on: bool): void {
            body.opened = on;
        }
        function deadline(ticks: int): void {
            systemService.confirmationTicks = ticks;
        }
        function select(key: string): void {
            body.activateRow(body.rows.find(r => r.value === key));
        }
        function submit(correct: bool): void {
            body.wifiPassword = correct ? "fixture-password" : "bad-password";
            body.wifiConnect();
        }
        function disconnect(key: string): void {
            systemService.act("wifi-disconnect", {
                key: key
            });
        }
        function status(): string {
            return JSON.stringify(systemService.status());
        }
        function state(): string {
            return JSON.stringify({
                action: systemService.actionState,
                message: body.message,
                selected: body.selectedNetwork,
                passwordEmpty: body.wifiPassword === "",
                fields: Array.from({
                    length: body.networkList.count
                }, (_, i) => {
                    const row = body.networkList.itemAt(i);
                    const input = root.passwordIn(row);
                    return {
                        key: row.row.value,
                        visible: !!input?.visible,
                        focused: !!input?.activeFocus
                    };
                }),
                ready: native.networkReady,
                enabled: native.wifiEnabled,
                devices: native.wifiDevices.map(d => ({
                            name: d.name,
                            scanner: d.scannerEnabled
                        })),
                networks: native.networks.map(n => n.key + (n.known ? ":known" : "") + (n.connected ? ":connected" : "")).sort()
            });
        }
    }
}
