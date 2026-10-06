pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls as C
import QtTest
import Quickshell
import "FakeNiri.js" as FakeNiri
import ".." as UI

// Pasted text in the account and disk password fields, with real key and pointer events in the
// production view: nothing says the login screen's keys type it, so no paste route reaches a
// field that checks the layout, and an edit that brings more than one character empties it.
ShellRoot {
    id: test
    property bool failed: false
    property int stage: 0
    property real stageStarted: Date.now()
    property var niri: FakeNiri.create(["us"])
    // What the person copied elsewhere: a typographic apostrophe, €, a Cyrillic letter, a
    // no-break space and a tab, none of which the login screen's English (US) keys type.
    readonly property string copied: "Rock’n’Roll €ф \t1"
    readonly property string refusal: "Pasted text cannot be used for this password. Type it key by key."
    readonly property var rows: [["us", "English (US)"], ["de", "German"], ["ru", "Russian"]].map(row => ({
                layout: row[0],
                variant: "",
                label: row[1]
            }))
    function check(ok: bool, message: string): void {
        if (!ok) {
            failed = true;
            console.error("ASSERTION_FAILED " + message);
            Qt.quit();
        }
    }
    function findItem(item: Item, name: string): Item {
        if (item.objectName === name)
            return item;
        for (const child of item.children) {
            const found = findItem(child, name);
            if (found)
                return found;
        }
        return null;
    }
    function field(name: string): C.TextField {
        return findItem(content, name) as C.TextField;
    }
    function message(): string {
        const line = findItem(content, "keyboardProblem") as Text;
        return line && line.visible ? line.text : "";
    }
    function copy(text: string): void {
        clip.text = text;
        clip.selectAll();
        clip.copy();
    }
    // The context menu's Paste, as a right click would offer it.
    function menuPaste(name: string): bool {
        const menu = field(name).C.ContextMenu.menu;
        if (!menu)
            return false;
        for (let i = 0; i < menu.count; ++i) {
            const action = menu.actionAt(i);
            if (action && action.text === "Paste" && action.enabled) {
                action.trigger();
                return true;
            }
        }
        return false;
    }
    UI.InstallerController {
        id: controller
        mockTransport: true
        helpersEnabled: false
        keyboardConfirmMs: 300
        catalog: ({
                zones: ["UTC"],
                trial: true,
                layouts: test.rows
            })
        onKeyboardOutbound: request => {
            Qt.callLater(function () {
                controller.keyboardReceive(FakeNiri.answer(test.niri, test.rows, request));
            });
        }
    }
    FloatingWindow {
        implicitWidth: 1024
        implicitHeight: 700
        UI.InstallerView {
            id: content
            anchors.fill: parent
            controller: controller
        }
        // Stands in for text the person selected and copied elsewhere.
        TextInput {
            id: clip
            width: 10
            height: 10
            visible: false
        }
        TestCase {
            id: input
            name: "PasteTest"
            when: false
        }
    }
    Timer {
        running: true
        repeat: true
        interval: 150
        property bool inside: false
        onTriggered: {
            if (test.failed || inside)
                return;
            inside = true;
            const before = test.stage;
            run();
            if (test.stage !== before)
                test.stageStarted = Date.now();
            else if (Date.now() - test.stageStarted > 8000)
                test.check(false, "stage " + test.stage + " timed out");
            inside = false;
        }
        function settled(): bool {
            return !controller.keyboardSwitching && !controller.keyboardBusy && controller.keyboardReady && controller.secretsChecked;
        }
        function run(): void {
            const s = test.stage;
            if (s === 0) {
                if (!controller.keyboardReady)
                    return;
                controller.session.ready = true;
                controller.publish();
                controller.fullName = "Demo User";
                controller.login = "demo";
                controller.encryption = "none";
                controller.step = "you";
            } else if (s === 1) {
                test.copy(test.copied);
                test.field("userPassword").forceActiveFocus();
            } else if (s === 2) {
                if (!settled())
                    return;
                input.keyClick(Qt.Key_A);
            } else if (s === 3) {
                if (!settled())
                    return;
                const password = test.field("userPassword");
                test.check(password.text === "a" && test.message() === "", "a typed key is taken (" + password.text.length + ")");
                // Ctrl+V and Shift+Insert.
                input.keyClick(Qt.Key_V, Qt.ControlModifier);
                test.check(password.text === "a", "Ctrl+V pastes nothing into the account password (" + password.text.length + ")");
                test.check(test.message() === test.refusal, "a refused paste is said plainly (" + test.message() + ")");
                input.keyClick(Qt.Key_Insert, Qt.ShiftModifier);
                test.check(password.text === "a", "Shift+Insert pastes nothing into the account password (" + password.text.length + ")");
                // A typed key ends the sentence.
                input.keyClick(Qt.Key_B);
            } else if (s === 4) {
                if (!settled())
                    return;
                const password = test.field("userPassword");
                test.check(password.text === "ab" && test.message() === "", "the next typed key is taken and ends the sentence (" + test.message() + ")");
                // A right click offers no Paste: the field has no context menu.
                input.mouseClick(password, 30, password.height / 2, Qt.RightButton);
                test.check(password.C.ContextMenu.menu === null && test.field("confirmPassword").C.ContextMenu.menu === null, "the account password fields have no context menu");
                test.check(!test.menuPaste("userPassword") && password.text === "ab", "a right click pastes nothing (" + password.text.length + ")");
                // A middle click (the primary selection) never reaches the field.
                input.mouseClick(password, 30, password.height / 2, Qt.MiddleButton);
                test.check(password.text === "ab" && test.message() === test.refusal, "a middle click is taken by the field's own area and pastes nothing (" + test.message() + ")");
                // Any other way text arrives in one edit (an input method's string): the page's
                // password fields are emptied.
                test.field("confirmPassword").text = "ab";
                password.paste();
                test.check(password.text === "" && test.field("confirmPassword").text === "", "an edit that brings several characters empties the password fields (" + password.text.length + ")");
                test.check(test.message() === test.refusal, "the emptied fields say why (" + test.message() + ")");
                // Undo would bring the emptied text back.
                input.keyClick(Qt.Key_Z, Qt.ControlModifier);
                input.keyClick(Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier);
                input.keyClick(Qt.Key_Y, Qt.ControlModifier);
                test.check(password.text === "", "Ctrl+Z and redo bring no emptied text back (" + password.text.length + ")");
                test.check(!(test.findItem(content, "accountContinue") as C.Button).enabled, "Continue stays grey with the fields emptied");
            } else if (s === 5) {
                if (!settled())
                    return;
                // A Wi-Fi password is used now, not at the login screen: it can still be pasted.
                controller.network = {
                    wired: false,
                    networks: [
                        {
                            ssid: "HomeNet",
                            bssid: "00:11:22:33:44:55",
                            device: "wlan0",
                            strength: 90,
                            security: "WPA2",
                            connected: false
                        }
                    ]
                };
                controller.step = "network";
            } else if (s === 6) {
                input.mouseClick(test.findItem(content, "wifiNetwork"));
                test.copy("wifi-secret");
                test.field("wifiPassword").forceActiveFocus();
                input.keyClick(Qt.Key_V, Qt.ControlModifier);
                const wifi = test.field("wifiPassword");
                test.check(wifi.text === "wifi-secret" && test.message() === "", "the Wi-Fi password takes Ctrl+V (" + wifi.text.length + ")");
                wifi.clear();
                test.check(test.menuPaste("wifiPassword") && wifi.text === "wifi-secret", "the Wi-Fi password keeps its context menu's Paste");
                // The disk password.
                controller.edit("encryption");
            } else if (s === 7) {
                input.mouseClick(test.findItem(content, "encryptYes"));
                input.mouseClick(test.findItem(content, "encryptSeparate"));
                test.copy(test.copied);
                test.field("diskPassword").forceActiveFocus();
            } else if (s === 8) {
                if (!settled())
                    return;
                input.keyClick(Qt.Key_A);
            } else if (s === 9) {
                if (!settled())
                    return;
                const disk = test.field("diskPassword");
                test.check(controller.diskPassword === "a", "the disk password takes a typed key");
                input.keyClick(Qt.Key_V, Qt.ControlModifier);
                input.keyClick(Qt.Key_Insert, Qt.ShiftModifier);
                input.mouseClick(disk, 30, disk.height / 2, Qt.MiddleButton);
                test.check(disk.text === "a" && controller.diskPassword === "a" && test.message() === test.refusal, "no paste reaches the disk password (" + controller.diskPassword.length + ")");
                test.check(disk.C.ContextMenu.menu === null && test.field("diskConfirmation").C.ContextMenu.menu === null, "the disk password fields have no context menu");
                controller.diskConfirmation = "a";
                disk.paste();
                test.check(disk.text === "" && controller.diskPassword === "" && controller.diskConfirmation === "", "an edit that brings several characters empties the disk password (" + controller.diskPassword.length + ")");
                // Without the live keyboard (no trial) a paste is refused all the same.
                controller.catalog = {
                    zones: ["UTC"],
                    trial: false,
                    layouts: test.rows
                };
                controller.edit("you");
            } else if (s === 10) {
                const password = test.field("userPassword");
                test.check(!controller.liveKeyboard, "the live keyboard is off");
                password.forceActiveFocus();
                input.keyClick(Qt.Key_A);
                input.keyClick(Qt.Key_V, Qt.ControlModifier);
                test.check(password.text === "a" && test.message() === test.refusal, "without the live keyboard Ctrl+V pastes nothing (" + password.text.length + ", " + test.message() + ")");
                password.paste();
                test.check(password.text === "", "without the live keyboard several characters in one edit empty the field");
            } else {
                if (!test.failed)
                    console.log("PASTE_OK Ctrl+V, Shift+Insert, context menu, middle click, one-edit strings, undo, Wi-Fi, disk password, no live keyboard");
                Qt.quit();
            }
            ++test.stage;
        }
    }
}
