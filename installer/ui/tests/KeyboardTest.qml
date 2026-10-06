pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls as C
import QtTest
import Quickshell
import "FakeNiri.js" as FakeNiri
import ".." as UI

// The live keyboard behind the password fields, driven with real key and pointer events in the
// production view: a password field takes input only while niri runs the layout it needs.
ShellRoot {
    id: test
    property bool failed: false
    property int stage: 0
    property real stageStarted: Date.now()
    property bool refocused: false
    // A layout_state request kept unanswered while holdReads is set.
    property bool holdReads: false
    property var heldRead: null
    property var niri: FakeNiri.create(["us"])
    readonly property string numLockLine: "Num Lock is on — the login screen starts with Num Lock off; type digits on the main row."
    property var requests: []
    readonly property var rows: [["us", "English (US)"], ["de", "German"], ["fr", "French"], ["cz", "Czech"], ["ru", "Russian"]].map(row => ({
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
    function chip(name: string): string {
        return (findItem(content, name + "Layout") as Text)?.text ?? "";
    }
    function message(): string {
        const line = findItem(content, "keyboardProblem") as Text;
        return line && line.visible ? line.text : "";
    }
    function retryShown(): bool {
        const retry = findItem(content, "keyboardRetry");
        return !!retry && retry.visible;
    }
    // The trial lists written so far, as JSON.
    function trials(): string {
        return JSON.stringify(requests.filter(request => request.op === "trial").map(request => request.layouts));
    }
    function type(name: string, keys: var): void {
        field(name).forceActiveFocus();
        keys.forEach(key => input.keyClick(key));
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
            test.requests = test.requests.concat([request]);
            if (test.holdReads && request.op === "layout_state")
                test.heldRead = request;
            else
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
        TestCase {
            id: input
            name: "KeyboardTest"
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
                test.check(false, "stage " + test.stage + " timed out: switching=" + controller.keyboardPending + "/" + controller.keyboardSent + " failed=" + controller.keyboardFailed + " niri=" + JSON.stringify(test.niri));
            inside = false;
        }
        // Waits while a written list is still unconfirmed.
        function settled(): bool {
            return !controller.keyboardPending && !controller.keyboardSent && !controller.keyboardBusy;
        }
        function run(): void {
            const s = test.stage;
            if (s === 0) {
                // The window starts by writing its default list.
                if (!controller.keyboardReady)
                    return;
                test.requests = [];
                controller.session.ready = true;
                controller.publish();
                controller.fullName = "Demo User";
                controller.login = "demo";
                controller.encryption = "none";
                // German is chosen; this niri never loads the written file.
                test.niri.mode = "stuck";
                controller.layouts = ["de"];
                controller.step = "you";
            } else if (s === 1) {
                if (!settled() || !controller.keyboardFailed)
                    return;
                if (!test.refocused) {
                    test.check(test.trials() === '[["de"],["de"],["de"]]', "an unconfirmed list is written again twice before the switch fails (" + test.trials() + ")");
                    test.check(test.field("confirmPassword").readOnly, "after a failed switch the confirmation takes no input");
                    // Without a pointer, focusing the field is how the switch is tried again.
                    test.refocused = true;
                    test.field("userPassword").forceActiveFocus();
                    return test.check(controller.keyboardSwitching, "focusing the password field after a failed switch tries again");
                }
                test.check(test.trials() === '[["de"],["de"],["de"],["de"],["de"],["de"]]', "the new attempt writes the list three times as well (" + test.trials() + ")");
                [Qt.Key_Z, Qt.Key_Y, Qt.Key_Slash].forEach(key => input.keyClick(key));
                test.check(test.field("userPassword").readOnly && test.field("userPassword").text === "" && test.field("confirmPassword").text === "", "after a failed switch the password fields take no input (" + test.field("userPassword").text.length + ")");
                test.check(test.message() === "The keyboard could not be switched to German, so passwords cannot be typed yet.", "a failed switch is said plainly (" + test.message() + ")");
                test.check(test.chip("userPassword") === "US", "the field names the layout niri really runs (" + test.chip("userPassword") + ")");
                test.check(test.retryShown(), "a failed switch offers Try again");
                test.check(!(test.findItem(content, "accountContinue") as C.Button).enabled, "Continue stays grey");
                // niri reads the next write: Try again writes the file once more.
                test.niri.mode = "follow";
                input.mouseClick(test.findItem(content, "keyboardRetry"));
                test.check(test.field("userPassword").activeFocus, "Try again leaves the focus in the password field");
            } else if (s === 2) {
                if (!controller.keyboardReady)
                    return;
                test.check(test.trials() === '[["de"],["de"],["de"],["de"],["de"],["de"],["de"]]', "Try again writes the list again (" + test.trials() + ")");
                input.keyClick(Qt.Key_Z);
                test.check(test.field("userPassword").text === "z" && test.chip("userPassword") === "DE" && test.message() === "", "once niri runs German the field takes input (" + test.chip("userPassword") + ")");
                // The disk password needs English (US); niri keeps German.
                controller.edit("encryption");
            } else if (s === 3) {
                input.mouseClick(test.findItem(content, "encryptYes"));
                test.check(controller.encryptionPassword === "separate", "German first asks for a separate disk password");
                test.niri.mode = "stuck";
                test.requests = [];
                test.field("diskPassword").forceActiveFocus();
            } else if (s === 4) {
                if (!settled() || !controller.keyboardFailed)
                    return;
                test.check(test.trials() === '[["us"],["us"],["us"]]', "the disk password field asks for English (US) (" + test.trials() + ")");
                [Qt.Key_Z, Qt.Key_Y].forEach(key => input.keyClick(key));
                test.check(test.field("diskPassword").readOnly && test.field("diskConfirmation").readOnly && controller.diskPassword === "" && !controller.encryptionReady, "the disk password takes no input in German (" + controller.diskPassword.length + ")");
                test.check(test.message() === "The keyboard could not be switched to English (US), so passwords cannot be typed yet.", "the disk password names English (US) (" + test.message() + ")");
                test.check(!(test.findItem(content, "continueButton") as C.Button).enabled, "Continue stays grey on the encryption page");
                // The stock niri session: the trial file is never read.
                test.niri.mode = "unavailable";
                test.niri.layouts = ["us"];
                controller.edit("keyboard");
                controller.encryption = "none";
                controller.layouts = ["de"];
                controller.step = "you";
                test.requests = [];
            } else if (s === 5) {
                if (!settled() || !controller.keyboardFailed)
                    return;
                test.type("userPassword", [Qt.Key_Z]);
                test.check(test.field("userPassword").readOnly && test.field("userPassword").text === "", "a session that cannot switch takes no password in another layout");
                test.check(test.message() === "This session cannot switch the keyboard to German, so passwords cannot be typed here.", "a session that cannot switch says so (" + test.message() + ")");
                test.check(!test.retryShown(), "no Try again where it cannot work");
                test.check(test.trials() === '[["de"]]', "a refused write is not repeated (" + test.trials() + ")");
                // English (US) is what that session already runs.
                controller.edit("keyboard");
                controller.layouts = ["us"];
                controller.step = "you";
            } else if (s === 6) {
                if (!settled() || !controller.keyboardReady)
                    return;
                test.type("userPassword", [Qt.Key_Z]);
                test.check(test.field("userPassword").text === "z" && test.message() === "", "a list the session already runs can be typed in");
                // niri loads a list late: the failure ends with the next reading.
                test.niri.mode = "stuck";
                test.niri.layouts = ["us"];
                controller.edit("keyboard");
                controller.layouts = ["de"];
                controller.step = "you";
            } else if (s === 7) {
                if (!settled() || !controller.keyboardFailed)
                    return;
                test.field("userPassword").forceActiveFocus();
                test.niri.layouts = ["de"];
            } else if (s === 8) {
                if (!controller.keyboardReady)
                    return;
                test.type("userPassword", [Qt.Key_Y]);
                test.check(test.field("userPassword").text === "y" && test.message() === "" && test.chip("userPassword") === "DE", "a late reload makes the field usable");
                // After the window confirmed German, niri drops back to English (US) (an older write
                // loaded late): every later report counts.
                test.requests = [];
                test.niri.layouts = ["us"];
            } else if (s === 9) {
                if (test.trials() === "[]")
                    return;
                test.check(JSON.parse(test.trials())[0].join() === "de", "a list niri dropped is written again (" + test.trials() + ")");
                if (controller.keyboardReady)
                    return test.check(false, "the field waits for the new write");
                input.keyClick(Qt.Key_X);
                test.check(test.field("userPassword").text === "y", "a key typed after niri dropped the list is not taken");
                test.niri.mode = "follow";
            } else if (s === 10) {
                if (!controller.keyboardReady)
                    return;
                input.keyClick(Qt.Key_X);
                test.check(test.field("userPassword").text === "yx" && test.chip("userPassword") === "DE", "the field takes input again once niri runs German (" + test.chip("userPassword") + ")");
                // English (US) and Russian; Super+Space was pressed on the keyboard page.
                controller.edit("keyboard");
                controller.layouts = ["us", "ru"];
            } else if (s === 11) {
                if (!controller.keyboardReady)
                    return;
                test.niri.current = 1;
                test.requests = [];
                controller.step = "you";
            } else if (s === 12) {
                test.field("userPassword").forceActiveFocus();
            } else if (s === 13) {
                if (!controller.keyboardReady)
                    return;
                test.check(test.requests.some(request => request.op === "first_layout") && test.niri.current === 0, "focusing the account password switches niri to the first layout (" + JSON.stringify(test.requests) + ")");
                input.keyClick(Qt.Key_A);
                test.check(test.field("userPassword").text === "a" && test.chip("userPassword") === "US", "the password is typed in English (US), where the login screen starts (" + test.chip("userPassword") + ")");
            } else if (s === 14) {
                if (!controller.secretsChecked)
                    return;
                // Super+Space while the field has focus, and a key right after it.
                test.niri.current = 1;
                input.keyClick(Qt.Key_B);
            } else if (s === 15) {
                if (test.field("userPassword").text !== "")
                    return;
                test.check(test.field("userPassword").readOnly && test.chip("userPassword") === "RU", "after Super+Space the field takes no input and names Russian (" + test.chip("userPassword") + ")");
                test.check(test.message() === "The keyboard layout changed while you typed. Type the password again. The login screen starts in English (US), so type the password in English (US). Super+Space switches back.", "the field says why (" + test.message() + ")");
                input.keyClick(Qt.Key_C);
                test.check(test.field("userPassword").text === "", "no key is taken in the second layout");
                // Super+Space again: back in English (US).
                test.niri.current = 0;
            } else if (s === 16) {
                if (!controller.keyboardReady)
                    return;
                input.keyClick(Qt.Key_C);
                test.check(test.field("userPassword").text === "c" && test.message() === "", "back in English (US) the field takes input (" + test.message() + ")");
            } else if (s === 17) {
                if (!controller.secretsChecked)
                    return;
                test.field("confirmPassword").forceActiveFocus();
            } else if (s === 18) {
                if (controller.keyboardBusy)
                    return;
                // Continue (and Return) wait until niri has confirmed the layout of the last key.
                test.holdReads = true;
                input.keyClick(Qt.Key_C);
                test.check(test.field("confirmPassword").text === "c" && !controller.secretsChecked && !(test.findItem(content, "accountContinue") as C.Button).enabled, "Continue waits for the last key's layout check");
                input.keyClick(Qt.Key_Return);
                test.check(controller.step === "you", "Return does not continue before the check");
                test.holdReads = false;
                const held = test.heldRead;
                test.heldRead = null;
                controller.keyboardReceive(FakeNiri.answer(test.niri, test.rows, held));
            } else if (s === 19) {
                if (!controller.secretsChecked)
                    return;
                test.check((test.findItem(content, "accountContinue") as C.Button).enabled, "Continue is enabled once the last key is checked");
                controller.edit("encryption");
            } else if (s === 20) {
                input.mouseClick(test.findItem(content, "encryptYes"));
                input.mouseClick(test.findItem(content, "encryptSeparate"));
                test.field("diskPassword").forceActiveFocus();
            } else if (s === 21) {
                if (!controller.keyboardReady)
                    return;
                input.keyClick(Qt.Key_A);
            } else if (s === 22) {
                if (!controller.secretsChecked)
                    return;
                test.check(controller.diskPassword === "a", "the disk password is typed in English (US)");
                // niri loads another list just before the next key.
                test.niri.layouts = ["ru"];
                input.keyClick(Qt.Key_B);
            } else if (s === 23) {
                if (test.field("diskPassword").text !== "")
                    return;
                test.check(controller.diskPassword === "" && controller.diskConfirmation === "" && test.message().indexOf("The keyboard layout changed while you typed. Type the password again.") === 0, "a disk password key typed in another list empties the disk password (" + test.message() + ")");
            } else if (s === 24) {
                if (!controller.keyboardReady)
                    return;
                input.keyClick(Qt.Key_A);
                test.check(controller.diskPassword === "a" && test.message() === "", "after the new write the disk password is typed again");
                // A Wi-Fi password is used now, not typed at the login screen: Russian first,
                // Super+Space to English (US) for it.
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
                controller.edit("keyboard");
                controller.encryption = "none";
                controller.layouts = ["ru", "us"];
            } else if (s === 25) {
                if (!controller.keyboardReady)
                    return;
                test.niri.current = 1;
                controller.step = "network";
            } else if (s === 26) {
                input.mouseClick(test.findItem(content, "wifiNetwork"));
                test.type("wifiPassword", [Qt.Key_W, Qt.Key_I]);
            } else if (s === 27) {
                // One more tick: the report read on focus has arrived.
            } else if (s === 28) {
                const field = test.field("wifiPassword");
                test.check(field.text === "wi" && !field.readOnly && test.niri.current === 1 && test.message() === "", "the Wi-Fi password is typed in the layout the person chose (" + field.text.length + ", " + test.niri.current + ", " + test.message() + ")");
                // Super+Space, a key and Super+Space again inside one layout_state round trip: the
                // report shows English (US) again, niri's event stream shows the two switches.
                controller.edit("keyboard");
                controller.layouts = ["us", "ru"];
            } else if (s === 29) {
                if (!settled() || !controller.keyboardReady)
                    return;
                test.niri.current = 0;
                controller.step = "you";
            } else if (s === 30) {
                test.field("userPassword").forceActiveFocus();
            } else if (s === 31) {
                if (!settled() || !controller.keyboardReady || !controller.secretsChecked)
                    return;
                input.keyClick(Qt.Key_A);
            } else if (s === 32) {
                if (!controller.secretsChecked || controller.keyboardBusy)
                    return;
                test.holdReads = true;
                test.niri.current = 1;
                controller.keyboardSwitched();
                input.keyClick(Qt.Key_X);
                test.niri.current = 0;
                controller.keyboardSwitched();
                test.holdReads = false;
                const held = test.heldRead;
                test.heldRead = null;
                test.check(!!held, "the key asked niri for its layout");
                controller.keyboardReceive(FakeNiri.answer(test.niri, test.rows, held));
            } else if (s === 33) {
                if (!controller.secretsChecked || controller.keyboardBusy)
                    return;
                test.check(test.field("userPassword").text === "" && test.message().indexOf("The keyboard layout changed while you typed. Type the password again.") === 0, "a switch and a switch back around a key empty the field although the report looks right (" + test.field("userPassword").text.length + ", " + test.message() + ")");
            } else if (s === 34) {
                if (!settled() || !controller.keyboardReady)
                    return;
                // Switches while no key waits for its check (Super+Space twice before typing) cost nothing.
                controller.keyboardSwitched();
                controller.keyboardSwitched();
                input.keyClick(Qt.Key_B);
            } else if (s === 35) {
                if (!controller.secretsChecked || controller.keyboardBusy)
                    return;
                test.check(test.field("userPassword").text === "b" && test.message() === "", "a key after earlier switches is kept (" + test.field("userPassword").text.length + ", " + test.message() + ")");
                test.check(!test.findItem(content, "capsLock").visible, "no Caps Lock line while it is off");
                // Caps Lock on: the next report says so (the keyboard LED, as on the lock screen).
                test.niri.caps = true;
                input.keyClick(Qt.Key_C);
            } else if (s === 36) {
                if (!controller.secretsChecked || controller.keyboardBusy)
                    return;
                const caps = test.findItem(content, "capsLock") as Text;
                const field = test.field("userPassword");
                const a = caps.mapToItem(content, 0, 0, caps.width, caps.height);
                const b = field.mapToItem(content, 0, 0, field.width, field.height);
                test.check(caps.visible && caps.text === "Caps Lock is on" && a.y >= b.y + b.height && Math.abs(a.x - b.x) < 1, "Caps Lock is named under the password fields (" + caps.visible + ", " + caps.text + ", " + a.y + " / " + (b.y + b.height) + ")");
                test.check(test.field("userPassword").text === "bc" && test.message() === "", "Caps Lock does not refuse the key");
                test.niri.caps = false;
                input.keyClick(Qt.Key_D);
            } else if (s === 37) {
                if (!controller.secretsChecked || controller.keyboardBusy)
                    return;
                test.check(!test.findItem(content, "capsLock").visible, "the line goes when Caps Lock is off again");
                const line = test.findItem(content, "numLock");
                test.check(!!line && !line.visible, "a hidden Num Lock line under the password fields while it is off (" + (line ? line.visible : "no line") + ")");
                if (!line)
                    return;
                // Num Lock on: the next report says so (the keyboard LED, as Caps Lock).
                test.niri.num = true;
                input.keyClick(Qt.Key_E);
            } else if (s === 38) {
                if (!controller.secretsChecked || controller.keyboardBusy)
                    return;
                const num = test.findItem(content, "numLock") as Text;
                const field = test.field("userPassword");
                const a = num.mapToItem(content, 0, 0, num.width, num.height);
                const b = field.mapToItem(content, 0, 0, field.width, field.height);
                test.check(num.visible && num.text === test.numLockLine && a.y >= b.y + b.height && Math.abs(a.x - b.x) < 1, "Num Lock is named under the password fields (" + num.visible + ", " + num.text + ", " + a.y + " / " + (b.y + b.height) + ")");
                test.check(field.text === "bcde" && test.message() === "", "Num Lock does not refuse a main-row key");
                // Keypad digits and the keypad's decimal key type nothing: the login screen starts
                // with Num Lock off, where these keys move the cursor.
                input.keyClick(Qt.Key_5, Qt.KeypadModifier);
                input.keyClick(Qt.Key_0, Qt.KeypadModifier);
                input.keyClick(Qt.Key_Period, Qt.KeypadModifier);
                input.keyClick(Qt.Key_Comma, Qt.KeypadModifier);
                test.check(field.text === "bcde", "a keypad digit types nothing into the account password (" + field.text.length + ")");
                // Num Lock off again (the key below is answered while it is clicked), and the same digit
                // on the main row is taken.
                test.niri.num = false;
                input.keyClick(Qt.Key_5);
            } else if (s === 39) {
                if (!controller.secretsChecked || controller.keyboardBusy)
                    return;
                test.check(test.field("userPassword").text === "bcde5" && test.message() === "", "a main-row digit is taken (" + test.field("userPassword").text.length + ", " + test.message() + ")");
                test.check(!test.findItem(content, "numLock").visible, "the line goes when Num Lock is off again");
                // The disk password.
                test.niri.caps = true;
                test.niri.num = true;
                controller.edit("encryption");
            } else if (s === 40) {
                input.mouseClick(test.findItem(content, "encryptYes"));
                input.mouseClick(test.findItem(content, "encryptSeparate"));
                test.field("diskPassword").forceActiveFocus();
            } else if (s === 41) {
                if (!settled() || !controller.keyboardReady || controller.keyboardBusy)
                    return;
                const caps = test.findItem(content, "diskCapsLock") as Text;
                test.check(caps.visible && caps.text === "Caps Lock is on", "Caps Lock is named under the disk password (" + caps.visible + ")");
                const num = test.findItem(content, "diskNumLock") as Text;
                const a = num.mapToItem(content, 0, 0);
                const b = caps.mapToItem(content, 0, 0, caps.width, caps.height);
                test.check(num.visible && num.text === test.numLockLine && a.y >= b.y + b.height, "Num Lock is named under the disk password, below Caps Lock (" + num.visible + ", " + a.y + " / " + (b.y + b.height) + ")");
                const before = controller.diskPassword;
                input.keyClick(Qt.Key_7, Qt.KeypadModifier);
                test.check(controller.diskPassword === before, "a keypad digit types nothing into the disk password (" + controller.diskPassword.length + ")");
                test.field("diskPassword").focus = false;
                test.check(!caps.visible && !num.visible, "the lines are shown only while a password field has focus");
                // The stock niri session cannot switch to German: only the pages with the account or
                // disk password fields say that passwords cannot be typed there.
                test.niri.mode = "unavailable";
                test.niri.layouts = ["us"];
                controller.edit("keyboard");
                controller.encryption = "none";
                controller.layouts = ["de"];
            } else if (s === 42) {
                if (!settled() || !controller.keyboardFailed)
                    return;
                const sentence = "This session cannot switch the keyboard to German, so passwords cannot be typed here.";
                const pages = {};
                for (const page of ["keyboard", "network", "timezone", "encryption", "you"]) {
                    controller.step = page;
                    pages[page] = test.message();
                }
                test.check(pages.you === sentence, "the You page says passwords cannot be typed (" + pages.you + ")");
                test.check(["keyboard", "network", "timezone", "encryption"].every(page => pages[page] === ""), "pages without account or disk password fields say nothing about it (" + JSON.stringify(pages) + ")");
                // With a separate disk password the Encryption page has its fields.
                controller.encryption = "encrypted";
                controller.encryptionPassword = "separate";
                controller.step = "encryption";
                test.check(test.message() === sentence, "the Encryption page with the disk password fields says it (" + test.message() + ")");
            } else {
                if (!test.failed)
                    console.log("KEYBOARD_OK failed switch, retry, stock session, late reload, dropped list, first layout, Super+Space, key checks, Wi-Fi, switch and back, Caps Lock, Num Lock and keypad digits, pages without passwords");
                Qt.quit();
            }
            ++test.stage;
        }
    }
}
