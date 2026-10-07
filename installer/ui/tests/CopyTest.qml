pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls as C
import QtTest
import Quickshell
import "FakeNiri.js" as FakeNiri
import ".." as UI

// Copying out of the account, disk and Wi-Fi password fields, with real key and pointer events in the
// production view: a shown password is plain text to Qt, and the session keeps clipboard
// history, so no Copy or Cut key reaches the clipboard, shown or not, and a shown password cannot
// be selected with the mouse (Qt publishes a mouse selection as the primary selection).
ShellRoot {
    id: test
    property bool failed: false
    property int stage: 0
    property real stageStarted: Date.now()
    property var niri: FakeNiri.create(["us"])
    // What the clipboard held before: it must still hold it after every copy attempt.
    readonly property string before: "clipboard-before"
    readonly property var rows: [["us", "English (US)"], ["de", "German"]].map(row => ({
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
    // Scroll the step body so the item is in view, as a person would before clicking it.
    function reveal(item: Item): void {
        const flick = (findItem(content, "installerBody") as C.ScrollView).contentItem as Flickable;
        const top = item.mapToItem(flick.contentItem, 0, 0).y;
        flick.contentY = Math.max(0, Math.min(top - 12, flick.contentHeight - flick.height));
        input.wait(30);
    }
    // The eye beside the field is checked while the password is shown.
    function shown(name: string): bool {
        return (findItem(content, name + "Toggle") as C.AbstractButton).checked;
    }
    function clipboard(): string {
        clip.clear();
        clip.paste();
        return clip.text;
    }
    // Every Copy and Cut key on a selection of the whole field; the text and the clipboard stay.
    function copyKeys(name: string): void {
        const password = field(name);
        const text = password.text;
        const state = shown(name) ? " shown" : " hidden";
        const keys = [[Qt.Key_C, Qt.ControlModifier, "Ctrl+C"], [Qt.Key_Insert, Qt.ControlModifier, "Ctrl+Insert"], [Qt.Key_X, Qt.ControlModifier, "Ctrl+X"], [Qt.Key_Delete, Qt.ShiftModifier, "Shift+Delete"]];
        for (const key of keys) {
            input.keyClick(Qt.Key_A, Qt.ControlModifier);
            check(password.selectedText === text, name + ": Ctrl+A selects the password before " + key[2]);
            input.keyClick(key[0], key[1]);
            check(clipboard() === before, name + state + ": " + key[2] + " copies nothing (" + clipboard().length + ")");
            check(password.text === text, name + state + ": " + key[2] + " cuts nothing (" + password.text.length + ")");
        }
        check(message() === "", name + ": a refused copy says nothing (" + message() + ")");
    }
    // A shown password: a mouse drag and a double click select nothing.
    function pointer(name: string): void {
        const password = field(name);
        const y = password.height / 2;
        input.mousePress(password, 20, y);
        input.mouseMove(password, 120, y);
        check(password.selectedText === "", name + ": a drag over the shown password selects nothing (" + password.selectedText.length + ")");
        input.mouseRelease(password, 120, y);
        input.mouseDoubleClickSequence(password, 20, y);
        check(password.selectedText === "", name + ": a double click on the shown password selects nothing (" + password.selectedText.length + ")");
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
        // Reads the clipboard back.
        TextInput {
            id: clip
            width: 10
            height: 10
            visible: false
        }
        TestCase {
            id: input
            name: "CopyTest"
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
                clip.text = test.before;
                clip.selectAll();
                clip.copy();
                test.check(test.clipboard() === test.before, "the clipboard reads back");
                test.field("userPassword").forceActiveFocus();
            } else if (s === 2) {
                if (!settled())
                    return;
                input.keyClick(Qt.Key_A);
            } else if (s === 3) {
                if (!settled())
                    return;
                input.keyClick(Qt.Key_B);
            } else if (s === 4) {
                if (!settled())
                    return;
                test.check(test.field("userPassword").text === "ab", "typed keys are taken");
                // Hidden: Qt copies nothing from a password echo, but Cut still deleted the selection.
                test.copyKeys("userPassword");
                input.mouseClick(test.findItem(content, "userPasswordToggle"));
                test.check(test.shown("userPassword"), "the eye shows the password");
                test.field("userPassword").forceActiveFocus();
            } else if (s === 5) {
                if (!settled())
                    return;
                test.copyKeys("userPassword");
                test.pointer("userPassword");
                // The disk password.
                controller.edit("encryption");
            } else if (s === 6) {
                input.mouseClick(test.findItem(content, "encryptYes"));
            } else if (s === 7) {
                // The choices below appear with the first click: the second waits for the layout.
                test.findItem(content, "encryptSeparate").forceActiveFocus();
                input.wait(40);
                input.keyClick(Qt.Key_Space);
                test.check(controller.encryptionPassword === "separate", "a separate disk password is chosen");
                test.field("diskPassword").forceActiveFocus();
            } else if (s === 8) {
                if (!settled())
                    return;
                input.keyClick(Qt.Key_A);
            } else if (s === 9) {
                if (!settled())
                    return;
                test.check(controller.diskPassword === "a", "the disk password takes a typed key");
                // Below the visible part of the page at this size: scrolled into view, as a person would.
                test.reveal(test.field("diskPassword"));
                input.mouseClick(test.findItem(content, "diskPasswordToggle"));
                test.check(test.shown("diskPassword"), "the eye shows the disk password");
                test.field("diskPassword").forceActiveFocus();
            } else if (s === 10) {
                if (!settled())
                    return;
                test.copyKeys("diskPassword");
                test.pointer("diskPassword");
                test.check(controller.diskPassword === "a", "the disk password is kept (" + controller.diskPassword.length + ")");
            } else if (s === 11) {
                controller.network = {
                    wired: false,
                    networks: [
                        {
                            ssid: "Copy test",
                            bssid: "02:00:00:00:00:01",
                            device: "wlan0",
                            strength: 90,
                            security: "WPA2",
                            connected: false
                        }
                    ]
                };
                controller.step = "network";
            } else if (s === 12) {
                input.mouseClick(test.findItem(content, "wifiNetwork"));
                input.wait(80);
                test.check(test.field("wifiPassword").activeFocus, "Wi-Fi selection focuses its password");
                input.keyClick(Qt.Key_A);
                input.keyClick(Qt.Key_B);
                test.check(test.field("wifiPassword").text === "ab", "Wi-Fi password takes typed keys");
                test.copyKeys("wifiPassword");
                input.mouseClick(test.findItem(content, "wifiPasswordToggle"));
                test.check(test.shown("wifiPassword"), "the eye shows the Wi-Fi password");
                test.field("wifiPassword").forceActiveFocus();
            } else if (s === 13) {
                test.copyKeys("wifiPassword");
                const menu = test.field("wifiPassword").C.ContextMenu.menu;
                test.check(menu && menu.count === 1 && menu.actionAt(0).text === "Paste", "the Wi-Fi menu offers Paste without Copy or Cut");
                test.pointer("wifiPassword");
                test.check(test.field("wifiPassword").text === "ab", "the Wi-Fi password is kept");
            } else {
                if (!test.failed)
                    console.log("COPY_OK Ctrl+C, Ctrl+Insert, Ctrl+X, Shift+Delete hidden and shown, drag, double click, account, disk and Wi-Fi password");
                Qt.quit();
            }
            ++test.stage;
        }
    }
}
