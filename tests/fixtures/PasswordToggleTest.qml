pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell
import "../../shell" as Shell

ShellRoot {
    Shell.AuthController {
        id: auth
        function edit(value: string): bool {
            buffer = value;
            return true;
        }
    }
    Shell.SystemBackend {
        id: fakeBackend
        networkReady: true
        wifiEnabled: true
        wifiHardwareEnabled: true
        wifiDevices: [fakeDevice]
        QtObject {
            id: fakeDevice
            property bool scannerEnabled: false
        }
        networks: [
            {
                key: "network",
                name: "HomeNet",
                connected: false,
                known: false,
                busy: false,
                signal: .8,
                open: false,
                psk: true
            }
        ]
    }
    Shell.SystemService {
        id: service
        backend: fakeBackend
    }
    Shell.NiriService {
        id: fakeNiri
        binary: ""
    }
    Window {
        id: window
        visible: true
        width: 480
        height: 900
        Shell.LockInput {
            id: lockInput
            anchors.fill: parent
            auth: auth
        }
        Shell.SystemBody {
            id: body
            anchors.fill: parent
            visible: false
            service: service
            niri: fakeNiri
            page: "wifi"
        }
        TextInput {
            id: clipboard
            visible: false
        }
        TestCase {
            name: "PasswordToggle"
            when: window.visible
            onCompletedChanged: if (completed)
                console.log("PASSWORD_TEST_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
            function equal(actual, expected) {
                if (actual !== expected)
                    console.error("Comparison failed: " + actual + " != " + expected + " " + new Error().stack);
                compare(actual, expected);
            }
            function descendants(item) {
                let found = [item];
                for (const child of item.children ?? [])
                    found = found.concat(descendants(child));
                return found;
            }
            function test_lock() {
                window.requestActivate();
                wait(50);
                const toggle = descendants(lockInput).find(c => c.toggled !== undefined);
                verify(toggle !== undefined);
                auth.edit("test-secret");
                equal(lockInput.revealed, false);
                lockInput.takeFocus();
                keyClick(Qt.Key_Tab);
                equal(toggle.activeFocus, true);
                keyClick(Qt.Key_Space);
                equal(lockInput.revealed, true);
                keyClick(Qt.Key_Return);
                equal(lockInput.revealed, false);
                mouseClick(toggle);
                equal(lockInput.revealed, true);
                auth.buffer = "";
                equal(lockInput.revealed, false);
                auth.edit("test-secret");
                toggle.toggled();
                auth.clearInput();
                equal(lockInput.revealed, false);
                toggle.toggled();
                auth.checking = true;
                equal(lockInput.revealed, false);
                auth.checking = false;
                toggle.toggled();
                lockInput.showToggle = false;
                equal(lockInput.revealed, false);
                lockInput.showToggle = true;
                auth.usernameMode = true;
                equal(toggle.visible, false);
                auth.usernameMode = false;
                auth.edit("test-secret");
                toggle.toggled();
                clipboard.text = "clipboard-sentinel";
                clipboard.selectAll();
                clipboard.copy();
                lockInput.takeFocus();
                keyClick(Qt.Key_A, Qt.ControlModifier);
                keyClick(Qt.Key_C, Qt.ControlModifier);
                keyClick(Qt.Key_X, Qt.ControlModifier);
                keyClick(Qt.Key_V, Qt.ControlModifier);
                equal(auth.buffer, "test-secret");
                clipboard.clear();
                clipboard.paste();
                equal(clipboard.text, "clipboard-sentinel");
            }
            function test_wifi() {
                lockInput.visible = false;
                body.visible = true;
                body.opened = true;
                body.selectedNetwork = "network";
                body.hiddenOpen = true;
                body.wifiPassword = "network-secret";
                body.hiddenPassword = "hidden-secret";
                wait(50);
                const fields = descendants(body).filter(c => c.secret === true);
                equal(fields.length, 2);
                for (const field of fields) {
                    field.text = "test-secret";
                    const toggle = descendants(field).find(c => c.toggled !== undefined);
                    const editor = descendants(field).find(c => c.echoMode !== undefined);
                    equal(editor.echoMode, TextInput.Password);
                    toggle.forceActiveFocus();
                    keyClick(Qt.Key_Space);
                    equal(field.revealed, true);
                    equal(editor.echoMode, TextInput.Normal);
                    field.text = "";
                    equal(field.revealed, false);
                    toggle.toggled();
                    body.opened = false;
                    equal(field.revealed, false);
                    body.opened = true;
                    body.hiddenOpen = true;
                    body.selectedNetwork = "network";
                }
            }
        }
    }
}
