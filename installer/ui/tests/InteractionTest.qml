pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls as C
import QtTest
import Quickshell
import "FakeNiri.js" as FakeNiri
import ".." as UI

ShellRoot {
    id: test
    property bool failed: false
    property int stage: 0
    property string selected: ""
    property var sent: []
    property var lastPlan: null
    property bool luksRecovery: false
    property bool luksEditorStarted: false
    readonly property string luksReason: "__ENCRYPTED_WARNING__"
    readonly property string unidentifiedReason: "__UNIDENTIFIED_WARNING__"
    readonly property string apfsReason: "__APFS_WARNING__"
    readonly property var wifiNetworks: ["__WIFI_NETWORKS__"]
    readonly property string wifiFailure: "Could not connect. Check the password."
    readonly property var encryptedCases: ["__ENCRYPTED_CASES__"]
    readonly property var destructiveTexts: ["Erase disk and install", "Install", "Open GParted", "Cancel after this phase", "Restart now"]
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
    function tabTo(item: Item, message: string): void {
        // Start from the page's existing focus and use only real Tab events, including
        // when the target already has focus: it must belong to the reachable tab chain.
        let presses = 0;
        do {
            input.keyClick(Qt.Key_Tab);
            ++presses;
        } while (!item.activeFocus && presses < 40)
        check(item.activeFocus, message);
        input.wait(40); // Allow focus scrolling and the page layout to settle.
        const viewport = (find("installerBody") as C.ScrollView).contentItem as Flickable;
        check(inside(item, viewport), message + " and the whole control is inside the visible page (" + item.objectName + ", " + JSON.stringify(item.mapToItem(viewport, 0, 0, item.width, item.height)) + ", viewport=" + viewport.width + "x" + viewport.height + ", contentY=" + viewport.contentY + ", contentHeight=" + viewport.contentHeight + ")");
    }
    // A button that erases, formats, installs, cancels, opens the partition editor or restarts.
    function isDestructive(item: var): bool {
        return destructiveTexts.indexOf(item.text) >= 0 && (item.text !== "Open GParted" || item.destructive === true);
    }
    function focusedDestructive(item: var): string {
        if (item.activeFocus && item.visible && isDestructive(item))
            return item.text;
        for (const child of item.children) {
            const found = focusedDestructive(child);
            if (found)
                return found;
        }
        return "";
    }
    function hiddenFocus(item: var): string {
        let out = item.activeFocus && !item.visible && typeof item.clicked === "function" ? "[" + item.text + "]" : "";
        for (const child of item.children)
            out += hiddenFocus(child);
        return out;
    }
    function findButton(item: var, text: string, destructive: bool): var {
        if (item.visible && item.text === text && !!item.destructive === destructive && typeof item.clicked === "function")
            return item;
        for (const child of item.children) {
            const found = findButton(child, text, destructive);
            if (found)
                return found;
        }
        return null;
    }
    function findType(item: var, match: var): var {
        if (match(item))
            return item;
        for (const child of item.children) {
            const found = findType(child, match);
            if (found)
                return found;
        }
        return null;
    }
    function verticalBar(body: Item): var {
        for (const child of body.children)
            if (child instanceof C.ScrollBar && child.orientation === Qt.Vertical)
                return child;
        return null;
    }
    function visibleTexts(item: Item, sentence: string): int {
        let count = item.visible && (item as Text)?.text === sentence ? 1 : 0;
        for (const child of item.children)
            count += visibleTexts(child, sentence);
        return count;
    }
    // A stand-in for niri behind the keyboard helper (FakeNiri.js): "follow" runs every written
    // list, "stuck" keeps its old list, "refuse" fails the write, "hold" keeps the request
    // unanswered. It reports layout names, turned into codes through the catalog, and its real
    // active index.
    property var keyboardRequests: []
    property var niri: FakeNiri.create(["us"])
    property string niriMode: "follow"
    property var heldKeyboard: null
    function answerKeyboard(request: var): var {
        niri.mode = niriMode === "hold" ? "follow" : niriMode;
        return FakeNiri.answer(niri, controller.catalog.layouts, request);
    }
    function trials(): string {
        return JSON.stringify(keyboardRequests.filter(request => request.op === "trial").map(request => request.layouts));
    }
    function find(name: string): var {
        return findItem(content, name);
    }
    // Scroll the step body so the item is in view, as a person would before clicking it.
    function reveal(item: Item): void {
        const flick = (findItem(content, "installerBody") as C.ScrollView).contentItem as Flickable;
        // A page that has just changed (a mode switch adds rows) is laid out on the next pass;
        // scrolling before that clamps to the old, shorter content and leaves the item below
        // the body, where a click lands on the footer instead.
        for (let attempt = 0; attempt < 10; attempt++) {
            input.wait(30);
            const top = item.mapToItem(flick.contentItem, 0, 0).y;
            flick.contentY = Math.max(0, Math.min(top - 12, flick.contentHeight - flick.height));
            input.wait(30);
            const area = item.mapToItem(flick, 0, 0, item.width, item.height);
            if (area.y >= -0.5 && area.y + area.height <= flick.height + 0.5)
                return;
        }
        check(false, "scrolled into view: " + item.objectName);
    }
    // The line under a grey main button, as shown ("" when hidden).
    function reason(): string {
        const line = findItem(content, "blockedReason") as Text;
        return line && line.visible ? line.text : "";
    }
    // A field's own problem line: right below the field, starting at its left edge, not wider.
    function under(item: Item, field: Item): bool {
        input.wait(40); // a line that has just appeared is placed by the next layout pass
        const a = item.mapToItem(content, 0, 0, item.width, item.height);
        const b = field.mapToItem(content, 0, 0, field.width, field.height);
        return item.visible && a.y >= b.y + b.height - 0.5 && a.y <= b.y + b.height + 16 && Math.abs(a.x - b.x) <= 1 && a.x + a.width <= b.x + b.width + 1;
    }
    function inside(inner: Item, outer: Item): bool {
        const area = inner.mapToItem(outer, 0, 0, inner.width, inner.height);
        return area.x >= -0.5 && area.y >= -0.5 && area.x + area.width <= outer.width + 0.5 && area.y + area.height <= outer.height + 0.5;
    }
    function wifiRow(index: int): var {
        return findType(content, item => item.objectName === "wifiNetwork" && item.text === wifiNetworks[index].ssid).parent;
    }
    function wifiInViewport(index: int, message: string): void {
        const row = wifiRow(index);
        const form = findItem(row, "wifiJoinForm");
        const button = findItem(row, "wifiNetwork");
        const viewport = (find("installerBody") as C.ScrollView).contentItem as Flickable;
        const bounds = form.mapToItem(viewport, 0, 0, form.width, form.height);
        check(form.visible && inside(form, viewport), message + ": complete join form is visible (network=" + index + ", bounds=" + JSON.stringify(bounds) + ", viewport=" + viewport.width + "x" + viewport.height + ", contentY=" + viewport.contentY + ", contentHeight=" + viewport.contentHeight + ")");
        check(inside(findItem(row, "wifiConnect"), viewport), message + ": Connect is visible");
        check(under(form, button), message + ": form sits directly below the chosen network");
    }
    function wifiSelect(index: int, key: int): void {
        const row = wifiRow(index);
        const button = findItem(row, "wifiNetwork");
        reveal(button);
        if (key) {
            button.forceActiveFocus();
            input.keyClick(key);
        } else {
            input.mouseClick(button);
        }
        input.wait(80);
        wifiInViewport(index, "select network " + index);
        const field = findItem(row, "wifiPassword") as C.TextField;
        check(field.activeFocus, "secured network focuses its password field");
        input.keyClick(Qt.Key_A);
        input.keyClick(Qt.Key_B);
        input.keyClick(Qt.Key_C);
        check(field.text === "abc", "typing after selection enters the Wi-Fi password");
    }
    function wifiFailureVisible(index: int): void {
        for (let attempt = 0; attempt < 60 && (!controller.network.fixtureScan || controller.helperBusy); ++attempt)
            input.wait(50);
        input.wait(100);
        check(controller.network.fixtureScan === true, "join is followed by a network rescan");
        check(controller.joinFailed && controller.joinMessage === wifiFailure, "exact join payload reached helper and wrong-password result survives rescan: " + controller.joinMessage);
        wifiInViewport(index, "failed join after rescan");
        const row = wifiRow(index);
        const message = findItem(row, "wifiJoinMessage") as Text;
        const connect = findItem(row, "wifiConnect");
        const gap = message.mapToItem(row, 0, 0).y - connect.mapToItem(row, 0, connect.height).y;
        check(Qt.colorEqual(message.color, content.danger), "failed join uses the danger colour");
        check(message.visible && message.text === wifiFailure && gap >= 0 && gap <= 16, "failed join appears directly below Connect");
        check(inside(message, (find("installerBody") as C.ScrollView).contentItem), "failed join text stays in the viewport");
    }
    function wifiScanDone(): void {
        for (let attempt = 0; attempt < 100 && controller.helperBusy; ++attempt)
            input.wait(50);
        input.wait(100);
        check(!controller.helperBusy, "network helper finishes its scan");
    }
    function wifiRescanning(): void {
        for (let attempt = 0; attempt < 100 && !(controller.joinFailed && controller.helperBusy && controller.helperOp === "network"); ++attempt)
            input.wait(10);
        check(controller.joinFailed && controller.helperBusy && controller.helperOp === "network", "failure is visible while the delayed rescan is running");
    }
    function warningInViewport(message: string): void {
        const field = find("encryptedEraseConfirmation");
        const block = field.parent;
        const viewport = (find("installerBody") as C.ScrollView).contentItem as Flickable;
        const top = block.mapToItem(viewport, 0, 0).y;
        check(block.visible && block.height > 0, message + ": warning block is shown");
        if (block.height > viewport.height) {
            check(Math.abs(top) <= 0.5, message + ": oversized warning starts at the viewport top");
        } else {
            check(inside(block, viewport), message + ": whole warning block is inside viewport (top=" + top + ", height=" + block.height + ", viewport=" + viewport.height + ")");
            for (const child of block.children) {
                if (child.visible && child instanceof Text)
                    check(inside(child, viewport), message + ": warning text is inside viewport");
            }
            if (field.visible)
                check(inside(field, viewport), message + ": ERASE field is inside viewport");
        }
    }
    function visibleTextsContaining(item: Item, word: string): int {
        if (!item.visible)
            return 0;
        let count = String((item as Text)?.text ?? "").indexOf(word) >= 0 ? 1 : 0;
        for (const child of item.children)
            count += visibleTextsContaining(child, word);
        return count;
    }
    UI.InstallerController {
        id: controller
        mockTransport: true
        helpersEnabled: false
        keyboardConfirmMs: 300
        onPartitioningChanged: {
            if (test.luksRecovery && partitioning)
                test.luksEditorStarted = true;
        }
        onKeyboardOutbound: request => {
            test.keyboardRequests = test.keyboardRequests.concat([request]);
            if (test.niriMode === "hold")
                test.heldKeyboard = request;
            else
                Qt.callLater(function () {
                    controller.keyboardReceive(test.answerKeyboard(request));
                });
        }
        catalog: ({
                zones: ["UTC", "Europe/Berlin", "America/New_York", "Asia/Kathmandu"],
                layouts: [],
                trial: false
            })
        network: ({
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
            })
        onOutbound: message => {
            test.sent = test.sent.concat([message.type]);
            if (message.type === "plan") {
                test.check(message.config.hibernation === false, "mouse and keyboard interactions never send a hibernation plan");
                test.lastPlan = JSON.parse(JSON.stringify(message.config));
            }
            if (test.luksRecovery && message.type === "probe" && controller.editorProbing) {
                Qt.callLater(function () {
                    controller.receive(Object.assign({}, controller.session.inventory, {
                        type: "inventory",
                        id: message.id
                    }));
                });
            }
            if (message.type === "set_timezone") {
                test.selected = message.timezone;
                Qt.callLater(function () {
                    controller.receive({
                        type: "reply",
                        id: message.id,
                        for_id: message.id,
                        ok: true,
                        timezone: message.timezone,
                        unix_ms: Date.now(),
                        offset_seconds: 0,
                        abbreviation: "UTC"
                    });
                });
            }
        }
    }
    FloatingWindow {
        id: window
        implicitWidth: Number(Quickshell.env("EMAKI_INSTALLER_WIDTH") || 1024)
        implicitHeight: Number(Quickshell.env("EMAKI_INSTALLER_HEIGHT") || 700)
        UI.InstallerView {
            id: content
            anchors.fill: parent
            controller: controller
        }
        TestCase {
            id: input
            name: "InstallerInteraction"
            when: false
        }
    }
    Timer {
        running: true
        repeat: true
        interval: 180
        // TestCase.wait() and key clicks process events, this timer's included: a stage that
        // runs longer than the interval must not be entered a second time from inside itself.
        property bool inside: false
        onTriggered: {
            if (test.failed || inside)
                return;
            inside = true;
            run();
            inside = false;
        }
        function run(): void {
            if (test.stage === 0) {
                test.check(controller.mode === "", "no disk installation mode is preselected");
                const install = test.findItem(content, "installChoice");
                if (!install)
                    return test.check(false, "welcome choice is named installChoice");
                test.check(install.activeFocus, "welcome starts with focus on Install Emaki");
                controller.catalog = Object.assign({}, controller.catalog, {
                    trial: true
                });
                input.keyClick(Qt.Key_Return);
                test.check(controller.step === "keyboard", "Return on the welcome page starts the installation");
                const layoutSearch = test.findItem(content, "layoutSearch");
                test.check(!!layoutSearch && layoutSearch.activeFocus, "keyboard page starts in the layout search, not on Apply layouts");
                controller.catalog = Object.assign({}, controller.catalog, {
                    trial: false
                });
                controller.session.ready = true;
                controller.session.inventory = Object.assign({
                    disks: []
                }, controller.session.inventory, {
                    tz_guess: null
                });
                controller.publish();
                test.check(!controller.timezoneChosen, "an unfinished background lookup needs a time-zone choice");
                controller.timezoneGuessPending = true;
                controller.pollTimezoneGuess();
                const guessId = Object.keys(controller.session.pending).find(id => controller.session.pending[id] === "get_timezone_guess");
                const sentCount = test.sent.length;
                controller.pollTimezoneGuess();
                test.check(test.sent.length === sentCount, "only one cached guess request is outstanding");
                controller.receive({
                    type: "reply",
                    id: guessId,
                    ok: true,
                    pending: true,
                    tz_guess: null
                });
                test.check(controller.timezoneGuessPending && !controller.timezoneGuessRequest, "unfinished lookup remains pollable");
                controller.pollTimezoneGuess();
                const readyId = Object.keys(controller.session.pending).find(id => controller.session.pending[id] === "get_timezone_guess");
                controller.receive({
                    type: "reply",
                    id: readyId,
                    ok: true,
                    pending: false,
                    tz_guess: "Europe/Berlin"
                });
                test.check(controller.step === "keyboard" && controller.timezone === "Europe/Berlin" && test.selected === "Europe/Berlin", "background guess applies live before the time-zone page");
                test.check(controller.timezoneChosen, "a completed background lookup supplies the time-zone choice");
                test.check(!controller.timezoneGuessPending, "completed lookup stops polling");
                controller.session.plan = {
                    token: "timezone-review",
                    summary: [],
                    errors: [],
                    warnings: []
                };
                controller.session.deadline = Date.now() + 600000;
                controller.agreed = true;
                controller.step = "review";
                controller.timezoneInfo = {
                    timezone: "Europe/Berlin"
                };
                controller.acceptTimezoneGuess("Europe/Berlin");
                test.check(!!controller.session.plan && controller.step === "review" && controller.agreed, "same detected zone preserves the review");
                controller.acceptTimezoneGuess("Asia/Kathmandu");
                test.check(controller.timezone === "Asia/Kathmandu" && !controller.session.plan && controller.session.deadline === 0 && !controller.agreed && controller.step === "you", "late changed zone invalidates review and returns to account");
                controller.chooseTimezone("America/New_York");
                controller.session.plan = {
                    token: "chosen-zone-review",
                    summary: [],
                    errors: [],
                    warnings: []
                };
                controller.step = "review";
                controller.acceptTimezoneGuess("Europe/Berlin");
                test.check(controller.timezone === "America/New_York" && !!controller.session.plan && controller.step === "review", "late guess cannot override chosen zone or its review");
                controller.acceptTimezoneGuess(null);
                test.check(controller.timezone === "America/New_York", "empty inventory guess cannot reset chosen zone");
                controller.chooseTimezone("UTC");
                controller.timezoneEdited = false;
                controller.step = "timezone";
                test.check(controller.timezone === "UTC", "UTC fallback");
            } else if (test.stage === 1) {
                const search = test.findItem(content, "zoneSearch") as C.TextField;
                search.forceActiveFocus();
                search.text = "berlin";
                input.wait(30);
                input.keyClick(Qt.Key_Return);
                test.check(controller.timezone === "Europe/Berlin", "search Enter selects filtered city");
            } else if (test.stage === 2) {
                const search = test.findItem(content, "zoneSearch") as C.TextField;
                search.text = "";
                input.wait(30);
                search.forceActiveFocus();
                input.keyClick(Qt.Key_Down);
                input.keyClick(Qt.Key_Return);
                test.check(controller.timezone === "America/New_York", "Down and Enter choose next list row");
            } else if (test.stage === 3) {
                const search = test.findItem(content, "zoneSearch") as C.TextField;
                search.text = "no match";
                const map = test.findItem(content, "timezoneMap") as UI.TimezoneMap;
                input.mouseClick(map, (13.4 + 180) / 360 * map.width, (90 - 52.5) / 180 * map.height);
                test.check(controller.timezone === "Europe/Berlin" && search.text === "", "map click synchronizes selection and clears search");
                const list = test.findItem(content, "zoneList") as ListView;
                test.check(list.currentIndex === 1, "map click scrolls list to selected zone");
                controller.step = "you";
            } else if (test.stage === 4) {
                const fullName = test.findItem(content, "fullName");
                test.check(!!fullName && fullName.activeFocus, "You starts in the Full name field");
                const loginField = test.findItem(content, "loginField");
                if (!loginField)
                    return test.check(false, "Login field is named loginField");
                const loginX = loginField.x;
                const loginWidth = loginField.width;
                controller.fullName = "N".repeat(60);
                input.wait(100);
                test.check(loginField.x === loginX && loginField.width === loginWidth, "a long full name does not move the Login field (" + loginX + "/" + loginWidth + " -> " + loginField.x + "/" + loginField.width + ")");
                test.check(Math.abs(fullName.width - loginField.width) <= 1, "the two form columns are equal (" + fullName.width + " / " + loginField.width + ")");
                const passwords = ["userPassword", "confirmPassword"].map(name => test.findItem(content, name).width);
                test.check(Math.abs(passwords[0] - passwords[1]) <= 1, "the two password fields are equal (" + passwords + ")");
                controller.fullName = "Demo User";
                for (const name of ["userPassword", "confirmPassword"]) {
                    const field = test.findItem(content, name) as C.TextField;
                    field.text = "disposable-fixture";
                    test.check(field.echoMode === TextInput.Password, "password starts masked");
                    const toggle = test.findItem(content, name + "Toggle") as C.AbstractButton;
                    field.forceActiveFocus();
                    if (name === "userPassword") {
                        input.keyClick(Qt.Key_Tab);
                        test.check(test.findItem(content, "confirmPassword").activeFocus, "Tab goes from password to confirmation");
                    }
                    test.tabTo(toggle, "password reveal control is reachable");
                    test.check(toggle.activeFocus, "Tab reaches password reveal control");
                    input.keyClick(Qt.Key_Space);
                    test.check(field.echoMode === TextInput.Normal, "Space reveals password");
                    input.keyClick(Qt.Key_Return);
                    test.check(field.echoMode === TextInput.Password, "Enter hides password");
                    input.keyClick(Qt.Key_Space);
                }
                controller.login = "demo";
                content.requestPlan();
                test.check(controller.step === "software" && controller.accountPassword !== "", "You advances to Software");
                test.check(controller.software === "rich", "Rich default");
            } else if (test.stage === 5) {
                test.check(test.findItem(content, "softwareRich").activeFocus, "Software starts on the chosen Rich");
                const minimal = test.findItem(content, "softwareMinimal") as C.AbstractButton;
                minimal.forceActiveFocus();
                input.keyClick(Qt.Key_Space);
                test.check(controller.software === "minimal", "keyboard selects Minimal");
                controller.back();
                test.check(controller.accountPassword === "", "Back clears staged password");
            } else if (test.stage === 6) {
                for (const name of ["userPassword", "confirmPassword"]) {
                    const field = test.findItem(content, name) as C.TextField;
                    test.check(field.echoMode === TextInput.Password && field.text === "", "leaving account resets reveal and clears fields");
                }
                controller.step = "network";
            } else if (test.stage === 7) {
                const field = test.findItem(content, "wifiPassword") as C.TextField;
                field.text = "wifi-fixture";
                const toggle = test.findItem(content, "wifiPasswordToggle") as C.AbstractButton;
                const network = test.findItem(content, "wifiNetwork") as C.AbstractButton;
                input.mouseClick(network);
                input.wait(60);
                field.text = "wifi-fixture";
                toggle.forceActiveFocus();
                input.keyClick(Qt.Key_Space);
                test.check(field.echoMode === TextInput.Normal, "Wi-Fi keyboard reveal");
                controller.clearPasswords();
                test.check(field.text === "" && field.echoMode === TextInput.Password, "clear signal also masks Wi-Fi password");
                controller.step = "timezone";
            } else if (test.stage === 8) {
                controller.chooseTimezone("UTC");
                controller.chooseTimezone("America/New_York");
                controller.chooseTimezone("Asia/Kathmandu");
                test.check(controller.timezoneInfo === null, "queued selections cannot show a stale clock");
            } else if (test.stage === 9) {
                test.check(controller.timezoneInfo.timezone === "Asia/Kathmandu" && test.selected === "Asia/Kathmandu", "last rapid selection wins");
                controller.step = "network";
            } else if (test.stage === 10) {
                const field = test.findItem(content, "wifiPassword") as C.TextField;
                test.check(field.echoMode === TextInput.Password && field.text === "", "leaving network resets reveal");
                controller.step = "encryption";
                test.check(controller.encryption === "" && !controller.encryptionReady, "encryption has no assumed choice");
                controller.next();
                test.check(controller.step === "encryption", "cannot continue without an encryption choice");
                test.check(test.reason() === "Choose whether to encrypt the disk.", "the grey Continue says why: no encryption choice (" + test.reason() + ")");
            } else if (test.stage === 11) {
                const no = test.findItem(content, "encryptNo") as C.AbstractButton;
                input.mouseClick(no);
                test.check(controller.encryptionReady, "unencrypted choice allows Continue");
                const yes = test.findItem(content, "encryptYes") as C.AbstractButton;
                yes.forceActiveFocus();
                input.keyClick(Qt.Key_Space);
                test.check(controller.encryption === "encrypted" && controller.encryptionPassword === "account", "keyboard chooses encryption with recommended account password");
                test.check(controller.accountProblems("a", "a").password === "Use at least 8 characters for the startup password.", "account password used for disk unlock also needs eight characters");
                const separate = test.findItem(content, "encryptSeparate") as C.AbstractButton;
                separate.forceActiveFocus();
                input.keyClick(Qt.Key_Space);
                test.check(!controller.encryptionReady, "separate password requires entry and confirmation");
                test.check(test.reason().indexOf("English (US) keyboard for the startup password") >= 0, "the grey Continue says why: no disk password yet (" + test.reason() + ")");
            } else if (test.stage === 12) {
                for (const name of ["diskPassword", "diskConfirmation"]) {
                    const field = test.findItem(content, name) as C.TextField;
                    field.forceActiveFocus();
                    input.keyClick(Qt.Key_A);
                    input.wait(100); // Let password validation lay out before changing focus.
                    const toggle = test.findItem(content, name + "Toggle") as C.AbstractButton;
                    if (name === "diskPassword") {
                        input.keyClick(Qt.Key_Tab);
                        test.check(test.findItem(content, "diskConfirmation").activeFocus, "Tab goes from disk password to confirmation");
                    }
                    test.tabTo(toggle, "password reveal control is reachable");
                    test.check(toggle.activeFocus, "Tab reaches disk password eye");
                    input.keyClick(Qt.Key_Space);
                    test.check(field.echoMode === TextInput.Normal, "disk password eye reveals");
                    input.keyClick(Qt.Key_Return);
                    test.check(field.echoMode === TextInput.Password, "disk password eye masks");
                }
                test.check(!controller.encryptionReady && test.reason() === "Use at least 8 characters for the startup password.", "matching one-character disk passwords are refused with a plain explanation");
                controller.next();
                test.check(controller.step === "encryption", "short disk password cannot leave encryption");
                for (const name of ["diskPassword", "diskConfirmation"]) {
                    const field = test.findItem(content, name) as C.TextField;
                    field.forceActiveFocus();
                    input.keyClick(Qt.Key_End);
                    for (let index = 0; index < 7; ++index)
                        input.keyClick(Qt.Key_A);
                }
                test.check(controller.encryptionReady, "matching eight-character disk passwords allow Continue");
                const confirmation = test.findItem(content, "diskConfirmation") as C.TextField;
                confirmation.forceActiveFocus();
                input.keyClick(Qt.Key_B);
                test.check(!controller.encryptionReady, "mismatched disk confirmation blocks Continue");
                test.check(test.reason() === "The disk passwords do not match.", "the grey Continue says why: a mismatch (" + test.reason() + ")");
                input.keyClick(Qt.Key_Backspace);
                controller.next();
                test.check(controller.step === "you" && controller.diskPassword === "aaaaaaaa", "disk password retained privately until planning");
                controller.lost();
                test.check(controller.diskPassword === "" && controller.diskConfirmation === "", "disconnect clears both disk secrets");
                controller.session.ready = true;
                controller.session.inventory = {
                    memory_bytes: 4294967296,
                    disks: [],
                    uefi: true
                };
                controller.step = "disk";
            } else if (test.stage === 13) {
                for (const mode of ["erase", "manual", "alongside"]) {
                    controller.mode = mode;
                    input.wait(30);
                    test.check(test.findItem(content, "hibernationCheck") === null, "hibernation has no mouse or keyboard target in " + mode);
                    test.check(test.visibleTextsContaining(content, "Enable hibernation") === 0, "hibernation is not offered in " + mode);
                    for (let tab = 0; tab < 40; ++tab) {
                        input.keyClick(Qt.Key_Tab);
                        test.check(controller.hibernation === false, "Tab navigation cannot enable hibernation in " + mode);
                    }
                    const erase = test.find("eraseChoice");
                    test.reveal(erase);
                    input.mouseClick(erase, erase.width / 2, erase.height / 2);
                    test.check(controller.mode === "erase" && controller.hibernation === false, "mouse selection keeps hibernation disabled");
                    erase.forceActiveFocus();
                    input.keyClick(Qt.Key_Space);
                    test.check(controller.hibernation === false, "keyboard selection keeps hibernation disabled");
                }
                test.check(!test.findItem(content, "alongsideChoice").visible, "no alongside choice without a worker offer");
                // A Windows disk as the 0.2 worker publishes it: alongside is off
                // in the installer core, so no partition carries a shrink offer.
                controller.session.inventory = {
                    memory_bytes: 16 * 1073741824,
                    uefi: true,
                    disks: [
                        {
                            id: "/dev/vda",
                            path: "/dev/vda",
                            model: "Test disk",
                            size_bytes: 100 * 1073741824,
                            shrink: null,
                            partitions: [
                                {
                                    id: "/dev/vda1",
                                    fs: "vfat",
                                    esp: true,
                                    os_hint: "windows",
                                    size_bytes: 100 * 1048576
                                },
                                {
                                    id: "/dev/vda3",
                                    fs: "ntfs",
                                    size_bytes: 96 * 1073741824
                                }
                            ]
                        }
                    ]
                };
                controller.mode = "";
                controller.chooseDisk("/dev/vda");
                input.wait(30);
                test.check(controller.mode === "" && test.reason() === "Choose how to install on this disk.", "selecting a disk does not select Erase");
                test.check(!test.find("continueButton").enabled, "Continue waits for an explicit installation mode");
                controller.next();
                test.check(controller.step === "disk", "next refuses an unchosen installation mode");
                input.mouseClick(test.find("eraseChoice"));
                test.check(controller.mode === "erase", "clicking Erase selects it explicitly");
                input.wait(30);
                test.check(!test.findItem(content, "alongsideChoice").visible && !controller.canAlongside, "a Windows disk without a worker offer does not offer alongside");
                // The card states what is on the disk; nothing offers to keep or shrink Windows.
                for (const words of ["alongside", "Windows keeps", "Windows retains", "in Windows", "chkdsk"])
                    test.check(test.visibleTextsContaining(content, words) === 0, "no alongside or shrink text on the disk step without a worker offer (" + words + ")");
                const windowsCard = test.find("disk-/dev/vda");
                if (!windowsCard)
                    return test.check(false, "disk cards are named disk-<id>");
                test.check(windowsCard.detail.endsWith(" · contains Windows"), "the card says the disk contains Windows (" + windowsCard.detail + ")");
                const mac = JSON.parse(JSON.stringify(controller.session.inventory));
                mac.disks[0].partitions = [
                    {
                        id: "/dev/vda2",
                        fs: "apfs",
                        os_hint: "macos",
                        size_bytes: 96 * 1073741824
                    }
                ];
                controller.session.inventory = mac;
                controller.publish();
                const macCard = test.find("disk-/dev/vda");
                test.check(macCard.detail.endsWith(" · contains macOS") && macCard.detail.indexOf("Windows") < 0, "the card says the disk contains macOS (" + macCard.detail + ")");
                controller.session.inventory = {
                    memory_bytes: 16 * 1073741824,
                    uefi: true,
                    disks: [
                        {
                            id: "/dev/vda",
                            path: "/dev/vda",
                            model: "Test disk",
                            size_bytes: 100 * 1073741824,
                            partitions: [
                                {
                                    id: "/dev/vda3",
                                    fs: "ntfs",
                                    os_hint: "windows",
                                    size_bytes: 96 * 1073741824,
                                    shrink: {
                                        min_bytes: 20 * 1073741824,
                                        max_free_bytes: 74 * 1073741824
                                    }
                                }
                            ]
                        }
                    ]
                };
                controller.chooseDisk("/dev/vda");
                controller.mode = "erase";
            } else if (test.stage === 14) {
                const warning = test.find("windowsEraseWarning");
                test.check(warning.visible && warning.text.includes("deletes Windows"), "erase mode names the Windows installation it removes");
                const choice = test.findItem(content, "alongsideChoice") as C.AbstractButton;
                test.check(choice.visible, "worker offer exposes alongside choice");
                choice.forceActiveFocus();
                input.keyClick(Qt.Key_Space);
                test.check(controller.mode === "alongside", "keyboard selects alongside");
                const slider = test.findItem(content, "alongsideSlider") as C.Slider;
                const before = controller.shrinkBytes;
                slider.forceActiveFocus();
                input.keyClick(Qt.Key_Right);
                test.check(controller.shrinkBytes === before + 1048576, "keyboard changes shrink by one MiB");
                controller.shrinkBytes = 32 * 1073741824;
                input.wait(30);
                test.check(controller.shrinkBytes === 32 * 1073741824, "alongside reserves no hibernation space");
                test.check(controller.alongsideSizeValid, "alongside minimum fits without a RAM reservation");
                const refused = JSON.parse(JSON.stringify(controller.session.inventory));
                refused.disks[0].partitions[0].shrink.reason = "Windows is hibernated";
                controller.session.inventory = refused;
                controller.publish();
                input.wait(30);
                test.check(!choice.visible && !controller.alongsideSizeValid, "refused worker offer hides alongside and blocks progress");
            } else if (test.stage === 15) {
                // Install alongside on a Windows disk, then a disk without Windows: the mode does
                // not stay on alongside, and the grey Continue gives no hibernation reason (INST-13).
                const offered = JSON.parse(JSON.stringify(controller.session.inventory));
                delete offered.disks[0].partitions[0].shrink.reason;
                offered.disks.push({
                    id: "/dev/vdb",
                    path: "/dev/vdb",
                    bus: "sata",
                    size_bytes: 100 * 1073741824,
                    partitions: []
                });
                controller.session.inventory = offered;
                controller.publish();
                controller.chooseDisk("/dev/vda");
                controller.mode = "alongside";
                test.check(controller.canAlongside && controller.alongsideSizeValid, "the Windows disk offers alongside");
                controller.chooseDisk("/dev/vdb");
                test.check(controller.mode === "" && test.reason() === "Choose how to install on this disk.", "a disk without Windows requires a new explicit mode after alongside (" + controller.mode + ", " + test.reason() + ")");
                // Manual needs a GPT disk (planner.manual_partitions): an MBR disk says so on the
                // choice; a disk without a table can still get one from GParted.
                const tables = [["/dev/vdc", "dos"], ["/dev/vdd", null], ["/dev/vde", "gpt"]];
                controller.session.inventory = {
                    memory_bytes: 16 * 1073741824,
                    uefi: true,
                    disks: tables.map(row => ({
                                id: row[0],
                                path: row[0],
                                model: "Table " + row[1],
                                bus: "virtio",
                                size_bytes: 100 * 1073741824,
                                partition_table: row[1],
                                partitions: []
                            }))
                };
                controller.publish();
                controller.mode = "erase";
                controller.manualAssignments = false;
                test.check(!controller.selectedDisk && test.reason() === "Choose a disk.", "the grey Continue says why: no disk (" + test.reason() + ")");
                const manual = test.find("manualChoice");
                for (const row of tables) {
                    controller.chooseDisk(row[0]);
                    const mbr = row[1] === "dos";
                    test.check(manual.enabled === !mbr && (manual.detail === "Manual installation needs a GPT disk.") === mbr, "Manual on a " + row[1] + " disk: enabled " + manual.enabled + ", " + manual.detail);
                }
                controller.mode = "manual";
                controller.chooseDisk("/dev/vdc");
                test.check(!test.find("continueButton").enabled, "a Manual kept from another disk cannot continue on an MBR disk");
                test.check(test.reason() === "Manual installation needs a GPT disk.", "the grey Continue says why: MBR (" + test.reason() + ")");
                controller.next();
                test.check(controller.step === "disk" && !controller.manualAssignments, "next() refuses Manual on an MBR disk");
                // The planner's root minimum, repeated on the erase card.
                controller.mode = "erase";
                const erase = test.find("eraseChoice");
                test.check(erase.detail.indexOf("Root needs at least 20.0 GiB.") >= 0, "the erase card names the root minimum (" + erase.detail + ")");
                test.check(controller.rootMinimum === 20 * 1073741824 + (controller.encryption !== "none" ? 16 * 1048576 : 0), "root minimum reserves no hibernation space despite 16 GiB RAM");
                controller.session.inventory = {
                    memory_bytes: 16 * 1073741824,
                    uefi: true,
                    disks: [
                        {
                            id: "/dev/vdb",
                            path: "/dev/vdb",
                            model: "Manual disk",
                            bus: "virtio",
                            size_bytes: 100 * 1073741824,
                            partitions: [
                                {
                                    id: "/dev/vdb1",
                                    path: "/dev/vdb1",
                                    fs: "vfat",
                                    esp: true,
                                    size_bytes: 1073741824
                                },
                                {
                                    id: "/dev/vdb2",
                                    path: "/dev/vdb2",
                                    fs: "btrfs",
                                    size_bytes: 99 * 1073741824
                                }
                            ]
                        }
                    ]
                };
                controller.publish();
                controller.chooseDisk("/dev/vdb");
                controller.mode = "manual";
                controller.manualAssignments = true;
                const disks = controller.session.inventory.disks;
                const proceed = test.findItem(content, "continueButton");
                if (!proceed)
                    return test.check(false, "Continue action is named continueButton");
                test.check(proceed.visible && !proceed.enabled, "manual Continue disabled with nothing assigned");
                // An unassigned row shows the format the partition has.
                const espRow = test.find("fsSelect-/dev/vdb1");
                test.check(!!espRow && espRow.currentText === "vfat", "the unassigned ESP row shows vfat (" + espRow?.currentText + ")");
                test.check(test.reason() === "Exactly one / and one /efi are required.", "the grey Continue says why: no / and /efi (" + test.reason() + ")");
                controller.assign(disks[0].partitions[0], "/efi", "vfat", false);
                test.check(!proceed.enabled, "manual Continue disabled with only /efi");
                controller.assign(disks[0].partitions[1], "/", "btrfs", true);
                test.check(proceed.enabled, "manual Continue enabled with / and /efi");
                // The root row repeats the planner's minimum, red when the partition is smaller.
                const rootRule = test.find("rootMinimum-/dev/vdb2");
                if (!rootRule)
                    return test.check(false, "the root minimum line is named rootMinimum-<partition>");
                test.check(rootRule.visible && rootRule.text === "Root needs at least 20.0 GiB." && !Qt.colorEqual(rootRule.color, content.danger), "the root row names the minimum (" + rootRule.text + ")");
                test.check(!test.find("rootMinimum-/dev/vdb1").visible, "only the root row names it");
                controller.assign(disks[0].partitions[0], "/", "ext4", true);
                const smallRoot = test.find("rootMinimum-/dev/vdb1");
                test.check(smallRoot.visible && Qt.colorEqual(smallRoot.color, content.danger), "a root smaller than the minimum is marked");
                controller.assign(disks[0].partitions[0], "/efi", "vfat", false);
                // FAT is offered only for /efi: the planner refuses it for /home.
                const espFormats = test.find("fsSelect-/dev/vdb1");
                const homeFormats = test.find("fsSelect-/dev/vdb2");
                if (!espFormats || !homeFormats)
                    return test.check(false, "file system choices are named fsSelect-<partition>");
                test.check(JSON.stringify(espFormats.model) === '["vfat"]', "the /efi row offers FAT32 (" + JSON.stringify(espFormats.model) + ")");
                controller.assign(disks[0].partitions[1], "/home", "ext4", true);
                test.check(homeFormats.model.indexOf("vfat") < 0, "the /home row does not offer FAT (" + JSON.stringify(homeFormats.model) + ")");
                // Choosing /efi in the window keeps the ESP's FAT32 instead of the first listed format.
                controller.mounts = [];
                const espMount = test.find("mountSelect-/dev/vdb1");
                espMount.currentIndex = 2;
                espMount.activated(2);
                test.check(JSON.stringify(controller.mounts) === JSON.stringify([
                    {
                        partition_id: "/dev/vdb1",
                        mountpoint: "/efi",
                        fs: "vfat",
                        format: false,
                        subvolume: null
                    }
                ]), "choosing /efi assigns the ESP with FAT32 (" + JSON.stringify(controller.mounts) + ")");
                controller.assign(disks[0].partitions[1], "/", "btrfs", true);
            } else if (test.stage === 16) {
                const legacy = JSON.parse(JSON.stringify(controller.session.inventory));
                legacy.uefi = false;
                controller.session.inventory = legacy;
                controller.publish();
                const refusal = test.findItem(content, "uefiRefusal") as Text;
                if (!refusal)
                    return test.check(false, "UEFI refusal is named uefiRefusal");
                test.check(refusal.visible && refusal.text.indexOf("Emaki installs only on computers with UEFI.") === 0, "BIOS start explains the UEFI requirement");
                test.check(!test.findItem(content, "continueButton").enabled, "BIOS start blocks Continue");
                legacy.uefi_bits = 32;
                controller.session.inventory = JSON.parse(JSON.stringify(legacy));
                controller.publish();
                test.check(refusal.visible && refusal.text === "64-bit UEFI is required.", "32-bit UEFI names the 64-bit requirement");
            } else if (test.stage === 17) {
                const scanning = test.findItem(content, "diskScanning");
                if (!scanning)
                    return test.check(false, "disk scan row is named diskScanning");
                test.check(!scanning.visible, "no scan row while idle");
                test.check(test.reason() === "64-bit UEFI is required.", "the grey Continue says why: firmware (" + test.reason() + ")");
                controller.session.probing = true;
                controller.publish();
                test.check(scanning.visible, "scan row shown while the disks are probed");
                test.check(test.reason() === "", "no reason line while the window is busy (" + test.reason() + ")");
                controller.session.probing = false;
                controller.publish();
                test.check(!scanning.visible, "scan row hidden after the probe");
                controller.step = "software";
            } else if (test.stage === 18) {
                const minimal = test.findItem(content, "softwareMinimal") as C.AbstractButton;
                for (const key of [Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space]) {
                    controller.software = "rich";
                    minimal.forceActiveFocus();
                    input.keyClick(key);
                    test.check(controller.software === "minimal", "Return, Enter and Space choose a focused choice (key " + key + ")");
                }
                controller.encryption = "";
                controller.step = "encryption";
            } else if (test.stage === 19) {
                const no = test.findItem(content, "encryptNo") as C.AbstractButton;
                no.forceActiveFocus();
                input.keyClick(Qt.Key_Return);
                test.check(controller.encryption === "none", "Return chooses the focused encryption choice");
                controller.step = "keyboard";
            } else if (test.stage === 20) {
                const proceed = test.findItem(content, "continueButton");
                proceed.forceActiveFocus();
                input.keyClick(Qt.Key_Return);
                test.check(controller.step === "network", "Return activates a focused action");
                controller.step = "filesystem";
            } else if (test.stage === 21) {
                const btrfs = test.findItem(content, "filesystemBtrfs");
                test.check(!!btrfs && btrfs.activeFocus, "File system starts on the chosen btrfs");
                controller.encryption = "";
                controller.step = "encryption";
            } else if (test.stage === 22) {
                test.check(!test.findItem(content, "encryptYes").activeFocus && !test.findItem(content, "encryptNo").activeFocus, "Encryption starts without focus on a choice");
                input.keyClick(Qt.Key_Return);
                test.check(controller.encryption === "", "Return on arrival chooses no encryption option");
                controller.encryption = "none";
                controller.fullName = "";
                controller.login = "";
                controller.loginEdited = false;
                controller.hostname = "emaki";
                controller.session.notice = "Check the disk and enter your password again for a new review.";
                controller.publish();
                controller.step = "you";
            } else if (test.stage === 23) {
                // One line under each field, shown once the field was typed in or Continue was attempted.
                const names = ["nameProblem", "loginProblem", "passwordProblem", "confirmProblem", "hostnameProblem"];
                if (names.some(name => !test.find(name)))
                    return test.check(false, "each account field has its own problem line (" + names + ")");
                test.check(names.filter(name => name !== "passwordProblem").every(name => !test.find(name).visible) && test.find("passwordProblem").error === "", "untouched account form shows no error");
                test.check(test.find("passwordProblem").visible && test.find("passwordProblem").text === "Use only letters, digits and symbols of the English (US) keyboard.", "untouched account form shows the keyboard hint");
                // Continue follows the same rules as the review: an empty form cannot continue.
                const proceed = test.find("accountContinue");
                if (!proceed)
                    return test.check(false, "the You step's Continue is named accountContinue");
                test.check(proceed.visible && !proceed.enabled, "an empty account form cannot continue");
                test.check(test.reason().indexOf("for your login") >= 0, "the grey Continue names the first rule no field shows yet (" + test.reason() + ")");
                content.requestPlan();
                const login = test.find("loginProblem");
                test.check(controller.step === "you" && login.visible && login.text.indexOf("for your login") >= 0, "empty submit names the login rule (" + login.text + ")");
                test.check(test.under(login, test.find("loginField")), "the login rule sits directly under the login field");
                test.check(test.find("passwordProblem").visible && ["nameProblem", "confirmProblem", "hostnameProblem"].every(name => !test.find(name).visible), "an empty submit also names the password; the name, confirmation and computer name are fine");
                test.check(test.visibleTexts(content, login.text) === 1 && controller.helperMessage === "", "each rule is shown once, not again in the footer");
                test.check(test.reason() === "", "once each field shows its rule, the footer repeats none (" + test.reason() + ")");
            } else if (test.stage === 24) {
                // Measured after the layout settled: the page has grown by the error lines.
                const problem = test.find("loginProblem");
                const body = test.findItem(content, "installerBody");
                const area = problem.mapToItem(body, 0, 0, problem.width, problem.height);
                test.check(problem.height > 0 && area.y >= 0 && area.y + area.height <= body.height + 0.5, "the error is in view (" + area.y + "+" + area.height + " in " + body.height + ")");
                controller.fullName = "Demo User";
                controller.login = "demo";
                const password = test.findItem(content, "userPassword") as C.TextField;
                const confirmation = test.findItem(content, "confirmPassword") as C.TextField;
                const confirm = test.find("confirmProblem");
                password.text = "disposable-a";
                confirmation.text = "disposable-b";
                content.requestPlan();
                test.check(test.visibleTexts(content, "The passwords do not match.") === 1 && confirm.visible && controller.helperMessage === "", "a mismatch is shown exactly once");
                test.check(test.under(confirm, confirmation), "the mismatch sits under the confirmation");
                test.check(!problem.visible && test.find("passwordProblem").error === "", "fixed fields lose their errors");
                confirmation.text = "disposable-a";
                test.check(!confirm.visible && controller.helperMessage === "", "matching passwords leave no stale error");
                test.check(test.find("accountContinue").enabled && test.reason() === "", "a valid account form can continue, with no reason line");
                for (const value of ["Zebraф2026", "Zebra😀2026", "Zebra\t2026"]) {
                    password.text = value;
                    confirmation.text = value;
                    test.check(!test.find("accountContinue").enabled && test.find("passwordProblem").error === "Use only letters, digits and symbols of the English (US) keyboard.", "an invalid keyboard character cannot continue");
                }
                password.text = "disposable-a";
                confirmation.text = "disposable-a";
                test.check(test.find("accountContinue").enabled && test.find("passwordProblem").error === "", "correcting the password restores Continue and the hint");
                confirmation.text = "disposable-c";
                test.check(!test.find("accountContinue").enabled, "a mismatch cannot continue");
                confirmation.text = "disposable-a";
                controller.encryption = "encrypted";
                controller.encryptionPassword = "account";
                password.text = "é1";
                confirmation.text = "é1";
                content.requestPlan();
                const rule = test.find("passwordProblem");
                test.check(controller.step === "you" && rule.visible && rule.text === "Use only letters, digits and symbols of the English (US) keyboard." && test.under(rule, password), "one password for everything names the account keyboard rule under the password");
                controller.clearPasswords();
                test.check(["nameProblem", "loginProblem", "confirmProblem", "hostnameProblem"].every(name => !test.find(name).visible) && test.find("passwordProblem").error === "", "a cleared form shows no error");
                controller.encryption = "none";
                controller.session.notice = "";
                controller.publish();
            } else if (test.stage === 25) {
                const password = test.findItem(content, "userPassword") as C.TextField;
                const confirmation = test.findItem(content, "confirmPassword") as C.TextField;
                const confirm = test.find("confirmProblem");
                password.text = "disposable-a";
                test.check(!confirm.visible, "no mismatch while the confirmation is still empty (" + confirm.text + ")");
                const host = test.find("hostnameField");
                host.text = "-bad";
                host.textEdited();
                const hostRule = test.find("hostnameProblem");
                test.check(hostRule.visible && hostRule.text.indexOf("Computer name:") === 0 && test.under(hostRule, host), "a wrong computer name shows under it while the confirmation is empty");
                host.text = "emaki";
                host.textEdited();
                test.check(!hostRule.visible && controller.hostname === "emaki", "a fixed computer name loses its line");
                // The worker's reserved logins (hello) are refused before the review.
                test.check(controller.accountProblem("disposable-a", "disposable-a") === "" && controller.login === "demo", "demo is a free login");
                controller.session.reservedLogins = ["demo", "wheel"];
                controller.publish();
                test.check(controller.accountProblem("disposable-a", "disposable-a").indexOf("for your login") >= 0, "a login the worker reserves is refused in the window");
                controller.session.reservedLogins = null;
                controller.publish();
                const name = test.find("fullName");
                name.text = "A:lex";
                name.textEdited();
                const nameRule = test.find("nameProblem");
                test.check(nameRule.visible && nameRule.text.indexOf("no colon") >= 0 && test.under(nameRule, name), "a name with a colon shows its rule under the name");
                name.text = "Demo User";
                name.textEdited();
                test.check(!nameRule.visible, "a fixed name loses its line");
                controller.login = "demo";
                confirmation.text = "disposable-b";
                test.check(confirm.visible && confirm.text === "The passwords do not match.", "a different confirmation is a mismatch");
                confirmation.text = "";
                content.requestPlan();
                test.check(controller.step === "you" && confirm.visible && confirm.text === "The passwords do not match.", "submitting without a confirmation is refused as a mismatch");
                controller.clearPasswords();
                controller.filesystem = "ext4";
                controller.step = "filesystem";
            } else if (test.stage === 26) {
                input.keyClick(Qt.Key_Return);
                test.check(controller.filesystem === "ext4", "Return on returning to File system keeps the chosen ext4 (" + controller.filesystem + ")");
                const ext4 = test.findItem(content, "filesystemExt4");
                test.check(!!ext4 && ext4.activeFocus, "File system starts on the chosen ext4");
                controller.filesystem = "btrfs";
                controller.software = "minimal";
                controller.step = "software";
            } else if (test.stage === 27) {
                input.keyClick(Qt.Key_Return);
                test.check(controller.software === "minimal", "Return on returning to Software keeps the chosen Minimal (" + controller.software + ")");
                test.check(test.findItem(content, "softwareMinimal").activeFocus, "Software starts on the chosen Minimal");
                controller.software = "rich";
                // Never start a real partition editor from this test.
                controller.partitionEditorCommand = ["true"];
                controller.session.inventory = Object.assign({}, controller.session.inventory, {
                    uefi: true
                });
                controller.publish();
                controller.mode = "manual";
                controller.manualAssignments = false;
                controller.gpartedWarning = false;
                controller.step = "disk";
            } else if (test.stage === 28) {
                test.check(test.focusedDestructive(content) === "", "disk page opens without focus on a destructive button");
                const open = test.findButton(content, "Open GParted", false);
                if (!open)
                    return test.check(false, "first Open GParted button found");
                open.forceActiveFocus();
                input.keyClick(Qt.Key_Return);
                test.check(controller.gpartedWarning, "Return on the focused first Open GParted shows the warning");
                test.check(test.focusedDestructive(content) === "", "the warning's Open GParted does not take focus (" + test.focusedDestructive(content) + ")");
                input.keyClick(Qt.Key_Return);
                test.check(!controller.partitioning && !controller.editorPending, "a second Return does not open the partition editor");
                controller.gpartedWarning = false;
                controller.mode = "erase";
                controller.step = "software";
            } else if (test.stage === 29) {
                const review = test.findButton(content, "Review installation", false);
                if (!review)
                    return test.check(false, "Review installation button found");
                // As if the person had moved focus there and the plan was acknowledged.
                review.forceActiveFocus();
                controller.session.plan = {
                    token: "fixture",
                    plan_id: "fixture",
                    summary: [],
                    errors: []
                };
                controller.session.deadline = Date.now() + 600000;
                controller.publish();
                controller.agreed = true;
                controller.step = "review";
            } else if (test.stage === 30) {
                test.check(test.hiddenFocus(content) === "", "no hidden button keeps keyboard focus " + test.hiddenFocus(content));
                test.check(test.focusedDestructive(content) === "", "review opens without focus on the install button (" + test.focusedDestructive(content) + ")");
                input.keyClick(Qt.Key_Return);
                input.keyClick(Qt.Key_Enter);
                test.check(test.sent.indexOf("confirm") < 0 && controller.step === "review", "Return on arrival at Review does not start the installation (" + test.sent + " / " + controller.step + ")");
                const install = test.findButton(content, "Erase disk and install", true);
                if (!install)
                    return test.check(false, "Erase disk and install button found");
                install.forceActiveFocus();
                input.keyClick(Qt.Key_Return);
                test.check(test.sent.indexOf("confirm") >= 0 && controller.step === "install", "Return on the install button the person focused starts the installation");
                // A refused confirmation returns to Review; the install button must not be focused again.
                controller.session.confirming = false;
                controller.publish();
                controller.step = "review";
                test.check(test.focusedDestructive(content) === "", "returning to Review after a refused confirmation does not refocus the install button (" + test.focusedDestructive(content) + ")");
                controller.step = "install";
                controller.session.running = true;
                controller.session.phase = "copy_packages";
                controller.publish();
            } else if (test.stage === 31) {
                test.check(test.focusedDestructive(content) === "", "install page opens without focus on Cancel (" + test.focusedDestructive(content) + ")");
                input.keyClick(Qt.Key_Return);
                test.check(test.sent.indexOf("cancel") < 0, "Return on the install page does not cancel");
                controller.session.running = false;
                controller.session.outcome = "done";
                controller.publish();
                controller.step = "done";
            } else if (test.stage === 32) {
                test.check(test.focusedDestructive(content) === "", "done page opens without focus on Restart now");
                input.keyClick(Qt.Key_Return);
                test.check(test.sent.indexOf("reboot") < 0, "Return on the done page does not restart");
                controller.session.outcome = "";
                controller.session.inventory = Object.assign({}, controller.session.inventory, {
                    uefi: true
                });
                controller.chooseDisk("/dev/vdb");
                controller.mode = "erase";
                controller.agreed = false;
                // A long plan ending as the planner ends it: the keyboard line comes late in its list.
                controller.session.plan = {
                    token: "fixture",
                    summary: ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine"].map(word => "Plan line " + word + " of a long review that needs the full width.").concat(["Encryption: LUKS2 root, account password.", "Hibernation: reserve 6,442,450,944 bytes (6.00 GiB), equal to RAM.", "Account: demo; hostname: emaki; timezone: UTC.", "Keyboard: Czech (default), English (US). Super+Space switches.", "Software: Rich — desktop apps, office, email, media and utilities.", "Packages are installed from the signed offline USB repository."]),
                    warnings: [],
                    errors: []
                };
                controller.session.deadline = Date.now() + 600000;
                controller.publish();
                // Arrive the way a person does: from Software, scrolled to its end.
                controller.step = "software";
                input.wait(50);
                const software = (test.findItem(content, "installerBody") as C.ScrollView).contentItem as Flickable;
                software.contentY = Math.max(0, software.contentHeight - software.height);
                controller.step = "review";
            } else if (test.stage === 33) {
                // The keyboard line says which layouts the login screen uses: on screen without scrolling (VM, 1366x768).
                const reviewBody = test.findItem(content, "installerBody");
                const keyboardLine = test.findType(content, item => item instanceof Text && item.visible && item.text.indexOf("Keyboard: Czech (default), English (US).") >= 0) as Text;
                if (!keyboardLine)
                    return test.check(false, "Review shows the keyboard line");
                test.check(test.inside(keyboardLine, reviewBody), "the keyboard line is inside the visible review body (" + JSON.stringify(keyboardLine.mapToItem(reviewBody, 0, 0, keyboardLine.width, keyboardLine.height)) + " / " + reviewBody.width + "x" + reviewBody.height + ")");
                const agreement = test.findItem(content, "agreementCheck") as C.CheckBox;
                if (!agreement)
                    return test.check(false, "review agreement is named agreementCheck");
                const body = reviewBody;
                const footer = test.findItem(content, "installerFooter");
                const area = agreement.mapToItem(content, 0, 0, agreement.width, agreement.height);
                const footerTop = footer.mapToItem(content, 0, 0).y;
                test.check(agreement.visible && area.x >= 0 && area.y >= 0 && area.x + area.width <= content.width && area.y + area.height <= footerTop + 0.5, "the agreement is on screen above the buttons (" + area.y + "+" + area.height + " / footer " + footerTop + ")");
                let parent = agreement.parent;
                while (parent && parent !== body)
                    parent = parent.parent;
                test.check(!parent, "the agreement does not scroll with the review body");
                test.check(agreement.text.indexOf("Manual disk") >= 0 && agreement.text.indexOf("will be erased") >= 0, "the agreement names the disk and the erase (" + agreement.text + ")");
                input.mouseClick(agreement, 12, agreement.height / 2);
                test.check(controller.agreed, "clicking the agreement agrees");
                controller.step = "you";
            } else if (test.stage === 34) {
                test.check(!test.findItem(content, "agreementCheck").visible, "no agreement outside Review");
                const disk = controller.session.inventory.disks[0];
                controller.session.inventory = Object.assign({}, controller.session.inventory, {
                    disks: [disk, Object.assign({}, disk, {
                            id: "/dev/vdc",
                            path: "/dev/vdc",
                            model: "Second disk"
                        })]
                });
                controller.publish();
                controller.mode = "erase";
                controller.step = "disk";
            } else if (test.stage === 35 || test.stage === 36) {
                const body = test.findItem(content, "installerBody") as C.ScrollView;
                const bar = test.verticalBar(body);
                const page = test.findItem(content, "installerPage");
                const overflowing = body.contentHeight > body.height + 0.5;
                const where = controller.step + " " + body.contentHeight + "/" + body.height + " policy " + bar?.policy + " size " + bar?.size + " page " + page?.width + "/" + body.availableWidth;
                if (!bar || !page)
                    return test.check(false, "page scroll bar and installerPage found");
                if (test.stage === 35)
                    test.check(overflowing || window.height > 700, "the disk page with two disks overflows a small window (" + where + ")");
                else
                    test.check(!overflowing, "the file system page fits (" + where + ")");
                test.check(overflowing ? bar.policy === C.ScrollBar.AlwaysOn && bar.size < 1 && (body.contentItem as Flickable).contentY === 0 : bar.policy !== C.ScrollBar.AlwaysOn, "the page scroll bar shows exactly when the page is taller than the body, before any scrolling (" + where + ")");
                test.check(Math.abs(page.width - (body.availableWidth - (overflowing ? 14 : 0))) < 0.5, "the page leaves a gutter only for a shown bar (" + where + ")");
                if (test.stage === 35)
                    controller.step = "filesystem";
                else {
                    controller.catalog = Object.assign({}, controller.catalog, {
                        trial: true,
                        layouts: ["us", "gb", "de", "fr", "ru", "ua"].map(layout => ({
                                    layout: layout,
                                    variant: "",
                                    label: "Layout " + layout
                                })).concat([
                            {
                                layout: "ru",
                                variant: "phonetic",
                                label: "Layout ru phonetic"
                            }
                        ])
                    });
                    controller.step = "keyboard";
                }
            } else if (test.stage === 37 || test.stage === 38) {
                const trial = controller.catalog.trial;
                if (trial && controller.keyboardSwitching)
                    return;
                const minimum = trial ? 170 : 245;
                const body = test.findItem(content, "installerBody") as C.ScrollView;
                const list = test.findType(test.findItem(content, "installerPage"), item => item instanceof ListView);
                const where = "trial " + trial + ", list " + list?.height + ", page " + body.contentHeight + "/" + body.height;
                if (!list)
                    return test.check(false, "keyboard layout list found");
                test.check(list.count === 6 && list.height >= minimum, "the layout list keeps its minimum (" + where + ")");
                const search = test.findItem(content, "layoutSearch");
                search.text = "ru";
                input.wait(40);
                test.check(list.count === 1 && list.model[0].layout === "ru" && !list.model[0].variant, "layout search shows only the supported base layout");
                search.text = "phonetic";
                input.wait(40);
                test.check(list.count === 0, "unsupported variants are absent from search");
                search.text = "";
                input.wait(40);
                if (window.height <= 640)
                    test.check(list.height === minimum, "a small window keeps the list at its minimum (" + where + ")");
                if (window.height >= 886)
                    test.check(list.height > minimum, "a tall window gives the free page height to the list (" + where + ")");
                if (list.height > minimum)
                    test.check(body.contentHeight <= body.height + 0.5, "a grown list never pushes the page past the body (" + where + ")");
                if (trial && list.height > minimum) {
                    const apply = test.findButton(content, "Apply layouts for testing", false);
                    const field = test.findType(content, item => item.placeholderText === "Type to test · Super+Space to switch");
                    for (const item of [apply, field]) {
                        const area = item ? item.mapToItem(body, 0, 0, item.width, item.height) : null;
                        test.check(!!area && item.visible && area.y >= 0 && area.y + area.height <= body.height + 0.5, "the trial controls stay inside the body (" + (area ? area.y + "+" + area.height : "missing") + " in " + body.height + ")");
                    }
                }
                if (trial) {
                    test.tabTo(test.findButton(content, "Apply layouts for testing", false), "layout apply button is reachable");
                    test.tabTo(test.findType(content, item => item.placeholderText === "Type to test · Super+Space to switch"), "layout test field is reachable");
                }
                controller.catalog = Object.assign({}, controller.catalog, {
                    trial: false
                });
                if (test.stage === 38)
                    controller.step = "timezone";
            } else if (test.stage === 39) {
                const list = test.findItem(content, "zoneList") as ListView;
                const body = test.findItem(content, "installerBody") as C.ScrollView;
                const where = "list " + list.height + ", page " + body.contentHeight + "/" + body.height;
                test.check(list.height >= 108, "the zone list keeps its minimum (" + where + ")");
                if (window.height >= 886)
                    test.check(list.height > 108, "a tall window gives the free page height to the zone list (" + where + ")");
                // At 960x640 the page already ended 9 px above the body, so the list grows by that much.
                if (list.height > 108)
                    test.check(body.contentHeight <= body.height + 0.5 && body.contentHeight > body.height - 1, "a grown zone list fills the body without pushing the page past it (" + where + ")");
                // A BIOS start: the welcome page already says it and offers only the live session.
                controller.session.inventory = {
                    memory_bytes: 4294967296,
                    disks: [],
                    uefi: false,
                    uefi_bits: null
                };
                controller.publish();
                controller.step = "welcome";
            } else if (test.stage === 40) {
                const refusal = test.findItem(content, "welcomeUefiRefusal") as Text;
                if (!refusal)
                    return test.check(false, "welcome UEFI refusal is named welcomeUefiRefusal");
                test.check(refusal.visible && refusal.text.indexOf("Emaki installs only on computers with UEFI.") === 0, "a BIOS start explains the UEFI requirement on the welcome page (" + refusal.text + ")");
                const install = test.findItem(content, "installChoice") as C.AbstractButton;
                test.check(!install.enabled && !install.activeFocus, "a BIOS start cannot choose Install Emaki");
                input.keyClick(Qt.Key_Return);
                input.mouseClick(install);
                test.check(controller.step === "welcome", "neither Return nor a click starts the installation on BIOS (" + controller.step + ")");
                const trial = test.findItem(content, "tryChoice") as C.AbstractButton;
                test.check(!!trial && trial.visible && trial.enabled, "Try Emaki first stays available on BIOS");
                controller.session.inventory = Object.assign({}, controller.session.inventory, {
                    uefi_bits: 32
                });
                controller.publish();
                test.check(refusal.text === "64-bit UEFI is required.", "32-bit UEFI names the 64-bit requirement on the welcome page");
                controller.session.inventory = Object.assign({}, controller.session.inventory, {
                    uefi: true,
                    uefi_bits: 64
                });
                controller.publish();
                test.check(!refusal.visible && install.enabled, "a UEFI start shows no refusal and can install");
                controller.session.error = {
                    type: "error",
                    code: "command_failed",
                    message: "pacstrap exited with status 1.",
                    retryable: true
                };
                controller.session.logs = ["$ pacstrap -K /mnt base", "pacstrap: exit 1"];
                controller.session.outcome = "error";
                controller.publish();
                controller.step = "error";
            } else if (test.stage === 41) {
                test.check(test.focusedDestructive(content) === "" && test.hiddenFocus(content) === "", "the error page opens without focus on an action");
                const toggle = test.findItem(content, "errorDetailsToggle") as C.CheckBox;
                const box = test.findItem(content, "errorDetailsBox");
                if (!toggle || !box)
                    return test.check(false, "error details are named errorDetailsToggle and errorDetailsBox");
                test.check(toggle.visible && !box.visible, "the log starts closed");
                test.tabTo(toggle, "error details control is reachable");
                input.mouseClick(toggle, 12, toggle.height / 2);
                test.check(box.visible && (test.findItem(content, "errorDetails") as C.TextArea).text === "pacstrap exited with status 1.\n\n$ pacstrap -K /mnt base\npacstrap: exit 1", "a click on Show details opens the worker's message and the retained log");
                test.check(test.visibleTexts(content, "pacstrap exited with status 1.") === 0, "the worker's message is not shown outside the details");
                // No removable medium yet: the page says what to do in plain words, without a path.
                test.check(controller.media.length === 0 && test.visibleTexts(content, "To save the log, plug in a second USB stick and press Refresh.") === 1, "without a medium the page says to plug in a second USB stick");
                test.check(test.visibleTextsContaining(content, "/run/media") === 0, "no mount path is shown");
                // The live session: the chosen layouts run before any password is typed (KBD-LIVE).
                controller.session.outcome = "";
                controller.session.error = null;
                controller.publish();
                controller.catalog = Object.assign({}, controller.catalog, {
                    trial: true,
                    layouts: [["us", "English (US)"], ["de", "German"], ["fr", "French"]].map(row => ({
                                layout: row[0],
                                variant: "",
                                label: row[1]
                            }))
                });
                controller.layouts = ["us"];
                controller.encryption = "none";
                controller.step = "keyboard";
            } else if (test.stage === 42) {
                if (!controller.keyboardReady)
                    return;
                test.keyboardRequests = [];
                // The person picks German and leaves English (US); "Apply layouts for testing" is not pressed.
                controller.toggleLayout("de");
                controller.toggleLayout("us");
                test.check(JSON.stringify(controller.layouts) === '["de"]', "German alone is chosen (" + controller.layouts + ")");
                controller.step = "network";
                controller.step = "timezone";
                controller.step = "you";
            } else if (test.stage === 43) {
                if (!controller.keyboardReady)
                    return;
                test.check(test.trials() === '[["de"]]', "the live session was asked to run German without the Apply button (" + test.trials() + ")");
                const field = test.findItem(content, "userPassword") as C.TextField;
                const chip = test.findItem(content, "userPasswordLayout") as Text;
                if (!field || !chip)
                    return test.check(false, "the account password field shows its layout as userPasswordLayout");
                test.check(chip.visible && chip.text === "DE", "the account password field names the layout it types in (" + chip.text + ")");
                test.check(test.inside(chip, field), "the layout code sits inside the password field");
                field.forceActiveFocus();
                input.keyClick(Qt.Key_A);
                test.check(field.text === "a", "the password field takes input once niri runs German (" + field.text.length + ")");
                // Another list while the field is open: no input until niri runs it.
                test.niriMode = "hold";
                controller.toggleLayout("fr");
            } else if (test.stage === 44) {
                const field = test.findItem(content, "userPassword") as C.TextField;
                const chip = test.findItem(content, "userPasswordLayout") as Text;
                test.check(!!test.heldKeyboard && JSON.stringify(test.heldKeyboard.layouts) === '["de","fr"]', "the new list is sent to the live session (" + JSON.stringify(test.heldKeyboard) + ")");
                test.check(field.readOnly && chip.text === "Applying keyboard…", "a password field takes no input while the layouts are switched (" + chip.text + ")");
                input.keyClick(Qt.Key_B);
                test.check(field.text === "a", "a key typed while the layouts are switched is not taken");
                test.niriMode = "follow";
                const held = test.heldKeyboard;
                test.heldKeyboard = null;
                controller.keyboardReceive(test.answerKeyboard(held));
            } else if (test.stage === 45) {
                if (!controller.keyboardReady)
                    return;
                const field = test.findItem(content, "userPassword") as C.TextField;
                const chip = test.findItem(content, "userPasswordLayout") as Text;
                test.check(!field.readOnly && chip.text === "DE" && controller.keyboardMessage === "", "niri confirmed the new list (" + chip.text + ")");
                // niri keeps its old list: the window says so instead of guessing.
                test.niriMode = "stuck";
                controller.toggleLayout("fr");
            } else if (test.stage === 46) {
                if (controller.keyboardSwitching || !controller.keyboardFailed)
                    return;
                const problem = test.findItem(content, "keyboardProblem") as Text;
                const field = test.findItem(content, "userPassword") as C.TextField;
                const chip = test.findItem(content, "userPasswordLayout") as Text;
                test.check(!!problem && problem.visible && problem.text === "The keyboard could not be switched to German, so passwords cannot be typed yet.", "a list niri does not take is reported (" + problem?.text + ")");
                test.check(field.readOnly && chip.text === "DE", "the field takes no input and names the layout niri really runs (" + chip.text + ")");
                field.forceActiveFocus();
                input.keyClick(Qt.Key_Q);
                test.check(field.text === "a", "a key typed after a failed switch is not taken (" + field.text.length + ")");
                // A refused trial of a list niri does not run is reported the same way.
                test.niriMode = "refuse";
                controller.toggleLayout("us");
            } else if (test.stage === 47) {
                if (controller.keyboardSwitching || !controller.keyboardFailed)
                    return;
                const problem = test.findItem(content, "keyboardProblem") as Text;
                test.check(problem.visible && problem.text.indexOf("could not be switched") >= 0, "a refused trial is reported (" + problem.text + ")");
                test.niriMode = "follow";
                controller.layouts = ["de"];
                controller.applyLayouts();
                controller.encryption = "";
                controller.step = "encryption";
            } else if (test.stage === 48) {
                if (!controller.keyboardReady)
                    return;
                test.check(controller.keyboardMessage === "", "a list niri takes clears the report");
                // The startup prompt reads US key positions; German first cannot share one password.
                const yes = test.findItem(content, "encryptYes") as C.AbstractButton;
                input.mouseClick(yes);
                const account = test.find("encryptAccount");
                test.check(!account.enabled && account.detail === "Not available: the login screen starts in German, but the disk is unlocked at startup in English (US).", "one password for everything is refused with German first (" + account.detail + ")");
                test.check(controller.encryptionPassword === "separate", "the separate disk password is chosen");
                controller.encryptionPassword = "account";
                test.check(!controller.encryptionReady, "a kept account choice cannot continue with German first");
                controller.encryptionPassword = "separate";
                test.keyboardRequests = [];
                (test.findItem(content, "diskPassword") as C.TextField).forceActiveFocus();
            } else if (test.stage === 49) {
                if (!controller.keyboardReady)
                    return;
                const field = test.findItem(content, "diskPassword") as C.TextField;
                const chip = test.findItem(content, "diskPasswordLayout") as Text;
                test.check(test.trials() === '[["us"]]', "a disk password field runs English (US) alone (" + test.trials() + ")");
                test.check(chip.text === "English (US)" && !field.readOnly, "the disk password field says English (US) (" + chip.text + ")");
                input.keyClick(Qt.Key_Y);
                test.check(field.text === "y", "the disk password field takes input in English (US)");
                // Within the pair, and on its eye, the startup layout stays.
                (test.findItem(content, "diskPasswordToggle") as C.AbstractButton).forceActiveFocus();
                (test.findItem(content, "diskConfirmation") as C.TextField).forceActiveFocus();
            } else if (test.stage === 50) {
                test.check(test.trials() === '[["us"]]' && controller.keyboardReady, "moving between the disk password fields keeps English (US) (" + test.trials() + ")");
                (test.findItem(content, "encryptSeparate") as C.AbstractButton).forceActiveFocus();
            } else if (test.stage === 51) {
                if (!controller.keyboardReady)
                    return;
                test.check(test.trials() === '[["us"],["de"]]', "leaving the disk password restores the chosen layouts (" + test.trials() + ")");
                const chip = test.findItem(content, "diskPasswordLayout") as Text;
                test.check(chip.text === "English (US)", "an unfocused disk password field still names the startup layout (" + chip.text + ")");
                // English (US) first: one password for everything is typed in English (US) alone.
                controller.layouts = ["us", "de"];
                const account = test.find("encryptAccount");
                test.check(account.enabled && account.detail.indexOf("Use your account password") === 0, "one password for everything is offered with English (US) first");
                controller.encryptionPassword = "account";
                controller.diskPassword = "";
                controller.diskConfirmation = "";
                controller.step = "you";
            } else if (test.stage === 52) {
                if (!controller.keyboardReady)
                    return;
                test.keyboardRequests = [];
                (test.findItem(content, "userPassword") as C.TextField).forceActiveFocus();
            } else if (test.stage === 53) {
                if (!controller.keyboardReady)
                    return;
                const chip = test.findItem(content, "userPasswordLayout") as Text;
                test.check(test.trials() === '[["us"]]' && chip.text === "English (US)", "the account password that unlocks the disk is typed in English (US) alone (" + test.trials() + ", " + chip.text + ")");
                controller.encryption = "none";
                controller.catalog = Object.assign({}, controller.catalog, {
                    trial: false
                });
            } else if (test.stage === 54) {
                const chip = test.findItem(content, "userPasswordLayout") as Text;
                const field = test.findItem(content, "userPassword") as C.TextField;
                test.check(!chip.visible && !field.readOnly, "outside the live session there is no layout code and no wait");
            } else if (test.stage === 55) {
                // A plan the worker refused (VM, night e4) is read on Review, not replaced after a
                // second by "The review expired" and a jump back to You.
                const refusal = "/home cannot be FAT; choose ext4 or btrfs.";
                controller.session.pending["ui-refused"] = "plan";
                controller.session.planning = true;
                controller.receive({
                    type: "plan_ack",
                    id: "ui-refused",
                    seq: 1,
                    plan_id: "refused",
                    expires_s: 600,
                    summary: [],
                    warnings: [],
                    errors: [
                        {
                            code: "manual_layout",
                            msg: refusal
                        }
                    ]
                });
                test.check(controller.step === "review", "a refused plan opens Review (" + controller.step + ")");
                input.wait(3200);
                test.check(controller.step === "review" && !!controller.session.plan && controller.session.notice === "", "a refused plan is still on Review after 3 s (" + controller.step + ", " + controller.session.notice + ")");
                test.check(test.visibleTexts(content, refusal) === 1, "the refusal's own sentence is on the page");
                test.check(!!test.findButton(content, "Edit disk settings", false) && !!test.findButton(content, "Edit account", false), "the edit buttons are offered");
            } else if (test.stage === 56) {
                // Closed encryption is selectable, but needs a separate typed confirmation.
                controller.session.plan = null;
                controller.session.notice = "";
                controller.session.probing = false;
                controller.session.inventory = {
                    uefi: true,
                    disks: [
                        {
                            id: "/dev/vda",
                            path: "/dev/vda",
                            model: "Encrypted disk",
                            size_bytes: 42949672960,
                            bus: "virtio",
                            partitions: [],
                            closed_encrypted: [
                                {
                                    path: "/dev/vda",
                                    type: "crypto_LUKS",
                                    uuid: "luks-test",
                                    warning: test.luksReason
                                }
                            ]
                        }
                    ]
                };
                controller.diskId = "";
                controller.mode = "erase";
                controller.manualAssignments = false;
                controller.partitionEditorCommand = ["/usr/bin/true"];
                controller.publish();
                controller.step = "disk";
            } else if (test.stage === 57) {
                const disk = test.find("disk-/dev/vda");
                test.check(disk.visible && disk.enabled, "the locked encrypted disk can be selected");
                test.check(test.inside(disk, (test.find("installerBody") as C.ScrollView).contentItem), "the disk is on screen before the mouse click");
                input.mouseClick(disk, disk.width / 2, disk.height / 2);
                input.wait(40);
                test.warningInViewport("mouse selection of locked LUKS");
                test.check(disk.activeFocus, "revealing the warning keeps mouse focus on the disk");
                test.check(!test.find("continueButton").enabled, "encrypted disk requires typed confirmation");
                test.check(test.visibleTexts(content, test.luksReason) === 1, "the inventory warning is visible");
                const field = test.find("encryptedEraseConfirmation");
                test.tabTo(field, "Tab reaches ERASE after selecting the encrypted disk with the mouse");
                input.keyClick(Qt.Key_E);
                input.keyClick(Qt.Key_R);
                input.keyClick(Qt.Key_A);
                input.keyClick(Qt.Key_S);
                input.keyClick(Qt.Key_E);
                test.check(!test.find("continueButton").enabled, "lowercase erase does not confirm");
                input.keyClick(Qt.Key_A, Qt.ControlModifier);
                for (const key of ["E", "R", "A", "S", "E"])
                    input.keyClick(key);
                test.check(test.find("continueButton").enabled && controller.confirmedEncrypted[0].uuid === "luks-test", "exact ERASE enables continuation and binds the UUID (" + field.text + ", " + controller.encryptedEraseText + ", " + test.reason() + ")");
                const updated = JSON.parse(JSON.stringify(controller.session.inventory));
                updated.disks[0].closed_encrypted[0].uuid = "changed-luks";
                controller.session.inventory = updated;
                controller.publish();
                input.wait(40);
                test.check(!controller.encryptedConfirmed && field.text === "", "changed identity clears typed confirmation");
                controller.diskId = "";
                const manual = test.find("manualChoice");
                test.reveal(manual);
                input.mouseClick(manual, manual.width / 2, manual.height / 2);
                test.check(controller.mode === "manual", "Manual is available without a selectable disk");
            } else if (test.stage === 58) {
                const open = test.findButton(content, "Open GParted", false);
                test.check(!!open && open.enabled, "no disk selection does not disable Open GParted");
                test.check(input.waitForPolish(content.Window.window, 30000), "the manual page layout settles before scrolling");
                test.reveal(open);
                test.check(input.waitForPolish(content.Window.window, 30000), "the manual page layout settles after scrolling");
                const viewport = (test.find("installerBody") as C.ScrollView).contentItem as Flickable;
                test.check(test.inside(open, viewport), "Open GParted is inside the visible page before clicking");
                input.mouseClick(open, open.width / 2, open.height / 2);
                test.check(controller.gpartedWarning, "the editor still requires its destructive-action warning");
                const confirm = test.findButton(content, "Open GParted", true);
                test.reveal(confirm);
                test.luksRecovery = true;
                confirm.forceActiveFocus();
                input.keyClick(Qt.Key_Return);
            } else if (test.stage === 59) {
                test.check(test.luksEditorStarted, "the fresh probe starts the harmless editor stand-in (" + controller.helperMessage + ", " + controller.editorProbing + ", " + controller.session.probing + ")");
                test.check(controller.editorPath === "" && JSON.stringify(controller.partitionEditorArgv) === '["/usr/bin/true"]', "without a selection the editor can show all disks, including the locked encrypted disk");
            } else if (test.stage === 60) {
                controller.session.probing = false;
                controller.session.inventory = {
                    uefi: true,
                    disks: [
                        {
                            id: "/dev/vda",
                            path: "/dev/vda",
                            model: "Encrypted partition",
                            size_bytes: 42949672960,
                            bus: "virtio",
                            partition_table: "gpt",
                            partitions: [
                                {
                                    id: "root",
                                    path: "/dev/vda2",
                                    fs: "crypto_LUKS",
                                    size_bytes: 40000000000
                                },
                                {
                                    id: "efi",
                                    path: "/dev/vda1",
                                    fs: "vfat",
                                    size_bytes: 1073741824,
                                    esp: true
                                }
                            ],
                            closed_encrypted: [
                                {
                                    path: "/dev/vda2",
                                    type: "crypto_LUKS",
                                    uuid: "manual-luks",
                                    warning: test.luksReason
                                }
                            ]
                        }
                    ]
                };
                controller.publish();
                controller.chooseDisk("/dev/vda");
                controller.mode = "manual";
                controller.manualAssignments = true;
                controller.assign(controller.selectedDisk.partitions[0], "/", "ext4", false);
                controller.assign(controller.selectedDisk.partitions[1], "/efi", "vfat", false);
                input.wait(40);
                test.check(!test.find("encryptedEraseConfirmation").visible, "manual preservation needs no erase confirmation");
                controller.assign(controller.selectedDisk.partitions[0], "/", "ext4", true);
                input.wait(40);
                const field = test.find("encryptedEraseConfirmation");
                test.check(field.visible && !test.find("continueButton").enabled, "manual formatting shows warning and blocks Continue");
                test.warningInViewport("manual formatting");
                test.tabTo(field, "Tab reaches ERASE for manual formatting");
                for (const key of ["E", "R", "A", "S", "E"])
                    input.keyClick(key);
                test.check(test.find("continueButton").enabled && controller.confirmedEncrypted[0].path === "/dev/vda2", "manual typed confirmation binds only formatted partition");
                controller.encryption = "none";
                controller.fullName = "Test User";
                controller.login = "testuser";
                controller.hostname = "testhost";
                controller.plan("test-password", "test-password");
                test.check(test.lastPlan?.confirmed_encrypted[0].uuid === "manual-luks", "plan request carries the confirmed encrypted identity");
                controller.session.planning = false;
                controller.publish();
                controller.assign(controller.selectedDisk.partitions[0], "/", "btrfs", true);
                test.check(!controller.encryptedConfirmed, "changing manual action requires confirmation again");
                controller.mode = "alongside";
                test.check(controller.encryptedTargets.length === 0, "alongside ignores untouched encryption");
            } else if (test.stage === 61) {
                const updated = JSON.parse(JSON.stringify(controller.session.inventory));
                updated.disks[0].partitions = [];
                updated.disks[0].closed_encrypted = [
                    {
                        path: "/dev/vda",
                        type: "apfs",
                        uuid: null,
                        warning: test.unidentifiedReason
                    }
                ];
                controller.session.inventory = updated;
                controller.session.plan = null;
                controller.mode = "erase";
                controller.manualAssignments = false;
                controller.publish();
                controller.step = "disk";
            } else if (test.stage === 62) {
                const field = test.find("encryptedEraseConfirmation");
                test.check(!field.visible && !test.find("continueButton").enabled, "a missing volume ID hides ERASE and blocks Continue on Disk");
                test.warningInViewport("missing volume ID refusal");
                test.check(test.visibleTexts(content, test.unidentifiedReason) > 0 && test.reason() === test.unidentifiedReason, "Disk and the disabled Continue explain the same missing-ID refusal");
                controller.encryptedEraseText = "ERASE";
                test.check(!controller.encryptedConfirmed && controller.confirmedEncrypted.length === 0, "ERASE cannot produce a null-UUID confirmation");
                controller.next();
                test.check(controller.step === "disk", "a missing volume ID cannot advance past Disk");
                const requests = test.sent.length;
                controller.plan("test-password", "test-password");
                test.check(controller.step === "disk" && test.sent.length === requests, "a missing volume ID cannot send a plan request");
                const updated = JSON.parse(JSON.stringify(controller.session.inventory));
                updated.disks[0].closed_encrypted[0].uuid = "apfs-test";
                updated.disks[0].closed_encrypted[0].warning = test.apfsReason;
                controller.session.inventory = updated;
                controller.publish();
            } else if (test.stage === 63) {
                const field = test.find("encryptedEraseConfirmation");
                test.check(field.visible && field.text === "" && !test.find("continueButton").enabled, "a readable APFS identity restores an empty ERASE field");
                test.warningInViewport("APFS warning replaces refusal");
                test.check(test.visibleTexts(content, test.apfsReason) === 1, "APFS shows the macOS warning before erasure");
                test.tabTo(field, "Tab reaches ERASE for an APFS container");
                for (const key of ["E", "R", "A", "S", "E"])
                    input.keyClick(key);
                test.check(test.find("continueButton").enabled && controller.confirmedEncrypted[0].type === "apfs" && controller.confirmedEncrypted[0].uuid === "apfs-test", "APFS requires typed confirmation bound to its readable identity");
                controller.session.error = {
                    code: "encrypted_confirmation",
                    message: test.luksReason,
                    retryable: true
                };
                controller.session.logs = [];
                controller.session.outcome = "error";
                controller.publish();
                controller.step = "error";
            } else if (test.stage === 64) {
                const toggle = test.find("errorDetailsToggle");
                test.tabTo(toggle, "Tab reaches Show details for an encrypted confirmation error");
                input.keyClick(Qt.Key_Space);
                input.wait(40);
                const details = test.find("errorDetails");
                test.check(details.visible && details.text.indexOf("/dev/vda contains") === 0 && details.text.indexOf("several disks.") >= 0, "error details retain the affected path and volume facts");
                test.check(test.visibleTextsContaining(content, "Type ERASE") === 0 && details.text.indexOf("Type ERASE") < 0, "the error page never asks for ERASE in an absent field, even with details open");
                controller.session.error = {
                    code: "encrypted_confirmation",
                    message: test.unidentifiedReason,
                    retryable: true
                };
                controller.publish();
                input.wait(40);
                test.check(details.text === test.unidentifiedReason, "error details retain the missing-ID refusal and recovery options");
            } else if (test.stage === 65) {
                controller.session.outcome = "";
                controller.session.error = null;
                controller.session.notice = "";
                controller.diskId = "";
                controller.mode = "erase";
                controller.session.inventory = {
                    uefi: true,
                    disks: test.encryptedCases.map((node, index) => ({
                                id: "encrypted-" + index,
                                path: node.path.slice(0, -1),
                                model: "Encrypted disk " + index,
                                size_bytes: 30 * 1073741824,
                                bus: "virtio",
                                partition_table: "gpt",
                                partitions: [
                                    {
                                        id: "root",
                                        path: node.path,
                                        fs: node.type,
                                        size_bytes: 29 * 1073741824
                                    }
                                ],
                                closed_encrypted: [node]
                            }))
                };
                controller.publish();
                controller.step = "disk";
            } else if (test.stage === 66) {
                // Reveal only the card before a real click; the warning must reveal itself.
                for (let index = 0; index < test.encryptedCases.length; ++index) {
                    const disk = test.find("disk-encrypted-" + index);
                    if (!test.inside(disk, (test.find("installerBody") as C.ScrollView).contentItem))
                        test.reveal(disk);
                    input.mouseClick(disk, disk.width / 2, disk.height / 2);
                    input.wait(40);
                    test.check(controller.diskId === "encrypted-" + index, "mouse selects " + test.encryptedCases[index].type);
                    test.warningInViewport("mouse selects " + test.encryptedCases[index].type);
                    test.check(disk.activeFocus, "new warning content keeps focus on the selected disk");
                    for (const key of [Qt.Key_Down, Qt.Key_Up]) {
                        input.keyClick(key);
                        input.wait(40);
                        test.check(!!test.findType(content, item => item.activeFocus && item.objectName.indexOf("disk-encrypted-") === 0), "arrow navigation keeps focus in the disk list");
                    }
                    controller.mode = "manual";
                    controller.manualAssignments = true;
                    controller.assign(controller.selectedDisk.partitions[0], "/", "ext4", true);
                    input.wait(40);
                    test.warningInViewport("manual " + test.encryptedCases[index].type);
                    controller.mode = "erase";
                    controller.manualAssignments = false;
                    input.wait(40);
                }
                const updated = JSON.parse(JSON.stringify(controller.session.inventory));
                // Enough separate warnings to exceed even the tallest test viewport.
                updated.disks[3].closed_encrypted = Array.from({
                    length: 16
                }, (_, index) => Object.assign({}, test.encryptedCases[index % 4], {
                        path: "/dev/vdd" + (index + 1)
                    }));
                controller.session.inventory = updated;
                controller.publish();
                input.wait(40);
                const block = test.find("encryptedEraseConfirmation").parent;
                const viewport = (test.find("installerBody") as C.ScrollView).contentItem as Flickable;
                test.check(block.height > viewport.height, "multiple encrypted warnings exceed the viewport");
                test.warningInViewport("multiple encrypted volumes");
                // Same visible block, now a refusal with no ERASE field.
                updated.disks[3].closed_encrypted = [
                    {
                        path: "/dev/vdd1",
                        type: "apfs",
                        uuid: null,
                        warning: test.unidentifiedReason
                    }
                ];
                controller.session.inventory = JSON.parse(JSON.stringify(updated));
                controller.publish();
                input.wait(40);
                test.warningInViewport("refusal replaces multiple warnings");
                test.check(!test.find("encryptedEraseConfirmation").visible, "refusal has no ERASE field");
                for (const index of [0, 3]) {
                    const disk = test.find("disk-encrypted-" + index);
                    test.reveal(disk);
                    input.mouseClick(disk, disk.width / 2, disk.height / 2);
                    input.wait(40);
                    test.warningInViewport("mouse selection with refusal " + index);
                    test.check(disk.activeFocus, "refusal reveal keeps focus on the disk");
                }
                controller.mode = "manual";
                controller.manualAssignments = true;
                controller.assign(controller.selectedDisk.partitions[0], "/", "ext4", true);
                input.wait(40);
                test.warningInViewport("manual refusal");
                test.check(!test.find("encryptedEraseConfirmation").visible, "manual refusal has no ERASE field");
            } else if (test.stage === 67) {
                controller.network = {
                    wired: false,
                    networks: test.wifiNetworks
                };
                controller.step = "network";
                input.wait(80);
                const connected = test.wifiRow(1);
                const enterprise = test.wifiRow(2);
                test.check(test.findType(connected, item => item.objectName === "wifiNetwork").chosen, "connected network is marked chosen");
                test.reveal(test.findItem(connected, "wifiNetwork"));
                input.mouseClick(test.findItem(connected, "wifiNetwork"));
                input.wait(40);
                test.check(!test.findItem(connected, "wifiJoinForm").visible, "connected network has no join form");
                test.check(!test.findItem(enterprise, "wifiNetwork").enabled && !test.findItem(enterprise, "wifiJoinForm").visible, "enterprise network cannot open a password form");
                let previous = null;
                for (const index of [0, 12, 24]) {
                    test.wifiSelect(index, 0);
                    if (previous) {
                        test.check(!test.findItem(previous, "wifiJoinForm").visible, "switching networks hides the previous form");
                        test.check((test.findItem(previous, "wifiPassword") as C.TextField).text === "", "switching networks clears the previous password");
                    }
                    previous = test.wifiRow(index);
                }
                test.wifiSelect(0, Qt.Key_Space);
                test.wifiSelect(12, Qt.Key_Return);
                // The isolated worker socket is absent; keep reconnects out of these helper tests.
                controller.retryDelay = 60000;
                controller.mockTransport = false;
                controller.helpersEnabled = true;
                input.wait(80);
                test.wifiSelect(24, 0);
                input.keyClick(Qt.Key_Return);
            } else if (test.stage === 68) {
                test.wifiFailureVisible(24);
                const field = test.findItem(test.wifiRow(24), "wifiPassword") as C.TextField;
                test.check(field.activeFocus && field.text === "", "failed join restores focus with a cleared password (focus=" + field.activeFocus + ", length=" + field.text.length + ", active=" + content.Window.window.activeFocusItem + ")");
                input.keyClick(Qt.Key_A);
                input.keyClick(Qt.Key_B);
                input.keyClick(Qt.Key_C);
                controller.network.fixtureScan = false;
                const retry = test.findItem(test.wifiRow(24), "wifiConnect");
                input.mouseClick(retry);
                test.check(controller.joinMessage === "" && !controller.joinFailed, "retry clears the preceding failure");
            } else if (test.stage === 69) {
                test.wifiFailureVisible(24);
                const openRow = test.wifiRow(23);
                const openButton = test.findItem(openRow, "wifiNetwork");
                test.reveal(openButton);
                input.mouseClick(openButton);
                input.wait(80);
                test.wifiInViewport(23, "open network");
                test.check(!test.findItem(openRow, "wifiPassword").visible, "open network has no password field");
                const connect = test.findItem(openRow, "wifiConnect");
                test.check(connect.activeFocus, "open network focuses Connect");
                test.check(controller.joinMessage === "" && !test.findItem(openRow, "wifiJoinMessage").visible, "switching networks clears the old failure");
                controller.network.fixtureScan = false;
                input.keyClick(Qt.Key_Space);
            } else if (test.stage === 70) {
                test.wifiFailureVisible(23);
                test.check(test.findItem(test.wifiRow(23), "wifiConnect").activeFocus, "open network restores Connect focus after delegate rebuild");
                test.wifiSelect(24, 0);
                controller.network.fixtureScan = false;
                input.keyClick(Qt.Key_Return);
                test.wifiRescanning();
                const oldField = test.findItem(test.wifiRow(24), "wifiPassword") as C.TextField;
                const oldBssid = test.wifiRow(24).modelData.bssid;
                test.check(oldField.activeFocus && oldField.text === "", "failed join clears the submitted password before rescan");
                input.keyClick(Qt.Key_X);
                input.keyClick(Qt.Key_Y);
                input.keyClick(Qt.Key_Z);
                test.check(oldField.text === "xyz", "replacement password is typed during the rescan");
                test.wifiFailureVisible(24);
                const replacement = test.findItem(test.wifiRow(24), "wifiPassword") as C.TextField;
                test.check(oldField !== replacement, "signal jitter rebuilds the password delegate");
                test.check(test.wifiRow(24).modelData.bssid !== oldBssid, "the stronger access point changes during rescan");
                test.check(replacement.text === "xyz" && replacement.activeFocus, "rescan preserves replacement password and restores its focus across access points");
                input.keyClick(Qt.Key_Return);
                test.wifiRescanning();
                const back = test.findButton(content, "Back", false);
                for (let attempt = 0; attempt < 12 && !back.activeFocus; ++attempt)
                    input.keyClick(Qt.Key_Tab);
                test.check(back.activeFocus, "Tab leaves the failed form for Back during rescan");
                test.wifiFailureVisible(24);
                test.check(back.activeFocus, "rescan does not steal focus from Back");
                input.keyClick(Qt.Key_Space);
                input.wait(80);
                test.check(controller.step !== "network", "Space after rescan activates Back");
            } else if (test.stage === 71) {
                const scanCount = controller.network.fixtureScanCount;
                controller.step = "network";
                for (let attempt = 0; attempt < 60 && (controller.network.fixtureScanCount === scanCount || controller.helperBusy); ++attempt)
                    input.wait(50);
                test.check(controller.network.fixtureScanCount > scanCount && !controller.helperBusy, "returning to the network page finishes its initial scan");
                input.wait(80);
                const row = test.wifiRow(24);
                const button = test.findItem(row, "wifiNetwork");
                test.reveal(button);
                button.forceActiveFocus();
                input.keyPress(Qt.Key_Return);
                input.wait(80);
                const password = test.findItem(row, "wifiPassword") as C.TextField;
                test.check(password.activeFocus && password.text === "", "Return selects an empty secured password field: focused=" + password.activeFocus + ", text=" + password.text + ", enabled=" + button.enabled + ", active=" + content.Window.window.activeFocusItem + ", busy=" + controller.helperBusy);
                input.keyRelease(Qt.Key_Return);
                input.keyClick(Qt.Key_Return);
                input.wait(80);
                test.check(!controller.helperBusy, "Return on an empty secured field does not join");
                const connect = test.findItem(row, "wifiConnect");
                test.check(!connect.enabled, "empty secured password disables Connect");
                input.mouseClick(connect);
                input.wait(40);
                test.check(!controller.helperBusy, "mouse cannot activate empty secured Connect");
                password.forceActiveFocus();
                input.keyClick(Qt.Key_Tab);
                input.keyClick(Qt.Key_Tab);
                test.check(!connect.activeFocus, "keyboard skips disabled empty Connect");
                password.forceActiveFocus();
                input.keyClick(Qt.Key_A);
                test.check(connect.enabled, "typing a secured password enables Connect");
                input.keyClick(Qt.Key_B);
                input.keyClick(Qt.Key_C);
                controller.network.fixtureScan = false;
                input.keyClick(Qt.Key_Return);
            } else if (test.stage === 72) {
                test.wifiFailureVisible(24);
                const row = test.wifiRow(24);
                const field = test.findItem(row, "wifiPassword") as C.TextField;
                test.check(field.activeFocus, "secured field regains focus after rescan");
                input.keyClick(Qt.Key_X);
                input.keyClick(Qt.Key_Y);
                input.keyClick(Qt.Key_Z);
                test.reveal(test.findItem(row, "wifiNetwork"));
                input.mouseClick(test.findItem(row, "wifiNetwork"));
                input.wait(80);
                test.check(field.activeFocus && field.text === "xyz", "re-clicking the selected row preserves the draft and refocuses it");
                test.check(controller.joinFailed && controller.joinMessage === test.wifiFailure, "re-clicking the selected row preserves the failure");
                test.wifiFailureVisible(24);
            } else if (test.stage === 73) {
                test.wifiSelect(0, 0);
                input.keyClick(Qt.Key_Return);
                input.keyClick(Qt.Key_Tab);
                input.keyClick(Qt.Key_Tab);
                const update = test.findType(content, item => item.visible && item.text === "Update Emaki at the end when connected");
                const viewport = (test.find("installerBody") as C.ScrollView).contentItem as Flickable;
                test.check(update.activeFocus, "two Tabs leave the joining form for the update checkbox");
                test.wifiRescanning();
                input.wait(80);
                test.check(update.activeFocus && test.inside(update, viewport), "join error keeps the focused update checkbox visible");
                test.wifiScanDone();
                test.check(controller.joinFailed && controller.joinMessage === test.wifiFailure, "join failure survives rescan with focus outside the form");
                test.check(update.activeFocus && test.inside(update, viewport), "rescan keeps the focused update checkbox visible");
                const before = controller.onlineUpdate;
                input.keyClick(Qt.Key_Space);
                test.check(controller.onlineUpdate !== before, "Space toggles the visible focused update checkbox");
            } else if (test.stage === 74) {
                for (const index of [21, 22]) {
                    const row = test.wifiRow(index);
                    const button = test.findItem(row, "wifiNetwork");
                    test.reveal(button);
                    input.mouseClick(button);
                    input.wait(80);
                    const connect = test.findItem(row, "wifiConnect");
                    test.check(!test.findItem(row, "wifiPassword").visible, test.wifiNetworks[index].security + " has no password field");
                    test.check(connect.activeFocus && connect.enabled, test.wifiNetworks[index].security + " enables and focuses Connect without a secret");
                    controller.network.fixtureScan = false;
                    if (index === 21)
                        input.keyClick(Qt.Key_Space);
                    else
                        input.mouseClick(connect);
                    test.wifiFailureVisible(index);
                }
            } else if (test.stage === 75) {
                test.wifiSelect(3, 0);
                input.keyClick(Qt.Key_D);
                input.keyClick(Qt.Key_E);
                input.keyClick(Qt.Key_F);
                const row = test.wifiRow(3);
                const field = test.findItem(row, "wifiPassword") as C.TextField;
                input.mouseClick(test.findItem(row, "wifiPasswordToggle"));
                field.forceActiveFocus();
                input.keyClick(Qt.Key_Home);
                controller.callHelper("network", {});
                input.wait(30);
                input.keyClick(Qt.Key_X);
                test.check(field.text === "xabcdef" && field.cursorPosition === 1, "editing during scan inserts x at the start");
                test.wifiScanDone();
                const replacement = test.findItem(test.wifiRow(3), "wifiPassword") as C.TextField;
                test.check(replacement !== field, "network scan rebuilds the editing delegate");
                test.check(replacement.activeFocus && replacement.cursorPosition === 1 && replacement.echoMode === TextInput.Normal, "rebuild preserves cursor, focus and revealed password");
                input.keyClick(Qt.Key_Y);
                test.check(replacement.text === "xyabcdef", "typing y after scan continues at the preserved cursor");
                for (const reverse of [false, true]) {
                    const selected = test.findItem(test.wifiRow(3), "wifiPassword") as C.TextField;
                    selected.select(reverse ? 5 : 2, reverse ? 2 : 5);
                    test.check(selected.selectedText === "abc", "selection covers the intended password text");
                    controller.callHelper("network", {});
                    input.wait(30);
                    test.wifiScanDone();
                    const restored = test.findItem(test.wifiRow(3), "wifiPassword") as C.TextField;
                    test.check(restored !== selected && restored.activeFocus && restored.echoMode === TextInput.Normal, "selection rebuild preserves focus and reveal state");
                    test.check(restored.selectionStart === 2 && restored.selectionEnd === 5 && restored.cursorPosition === (reverse ? 2 : 5), "rebuild preserves selection bounds and direction");
                }
                controller.mockTransport = true;
                controller.helpersEnabled = false;
            } else if (test.stage === 76) {
                controller.session.ready = true;
                controller.session.inventory = Object.assign({}, controller.session.inventory, {
                    tz_guess: ""
                });
                controller.timezoneEdited = false;
                controller.timezone = "UTC";
                controller.timezoneInfo = {
                    timezone: "UTC",
                    abbreviation: "UTC"
                };
                controller.publish();
                controller.step = "timezone";
            } else if (test.stage === 77) {
                controller.applyingTimezone = "";
                controller.timezoneInfo = {
                    timezone: "UTC",
                    abbreviation: "UTC"
                };
                test.check(!controller.timezoneChosen && !test.find("continueButton").enabled, "an unknown time zone needs an explicit choice");
                controller.next();
                test.check(controller.step === "timezone", "UTC is not silently accepted");
                const utc = test.find("useUtc");
                test.tabTo(utc, "UTC choice is reachable");
                input.keyClick(Qt.Key_Space);
                test.check(controller.timezoneChosen && controller.timezone === "UTC", "UTC can be explicitly chosen");
                controller.session.running = true;
                controller.session.phase = "update";
                controller.session.activity = {
                    name: "downloads"
                };
                controller.publish();
                controller.step = "install";
            } else if (test.stage === 78) {
                const skip = test.find("skipUpdate");
                test.check(skip.visible && skip.enabled, "the final update has a skip control");
                skip.forceActiveFocus();
                input.keyClick(Qt.Key_Space);
                test.check(test.sent.includes("skip_update"), "skip sends the safe update request");
                controller.session.running = false;
                controller.session.outcome = "done";
                controller.session.doneWarnings = ["The update failed. Emaki is installed."];
                controller.catalog = Object.assign({}, controller.catalog, {
                    boot_removable: true
                });
                controller.publish();
                controller.step = "done";
            } else if (test.stage === 79) {
                test.check(test.find("doneWarnings").text === "The update failed. Emaki is installed.", "update warnings appear on the completion page");
                const before = test.sent.filter(x => x === "reboot").length;
                controller.requestReboot();
                test.check(!controller.removeUsbPrompt && test.sent.filter(x => x === "reboot").length === before, "USB removal waits for restart preparation");
                test.check(controller.session.notice.indexOf("Keep the USB stick connected") >= 0, "restart preparation displays progress before the removal prompt");
                test.check(!controller.logSaveEnabled, "log export is disabled while preparation runs");
                controller.preparationTimedOut();
                test.check(controller.session.notice.indexOf("hold the power button") >= 0 && !controller.removeUsbPrompt, "preparation timeout gives recovery text without claiming a prepared restart");
                test.check(test.sent.filter(x => x === "reboot").length === before, "preparation timeout does not request an unprepared reboot");
                let prepareId = Object.keys(controller.session.pending).find(id => controller.session.pending[id] === "prepare_reboot");
                controller.receive({
                    type: "reply",
                    id: prepareId,
                    ok: false,
                    msg: "Automatic restart is unavailable. Your installation is safe. If the computer has not restarted by itself, hold the power button to turn it off, then start it again."
                });
                test.check(!controller.removeUsbPrompt && controller.session.notice.indexOf("hold the power button") >= 0, "failed preparation gives a usable manual restart instruction");
                test.check(controller.logSaveEnabled, "log export is enabled after failed preparation");
                controller.requestReboot();
                prepareId = Object.keys(controller.session.pending).find(id => controller.session.pending[id] === "prepare_reboot");
                controller.receive({
                    type: "reply",
                    id: prepareId,
                    ok: true
                });
                test.check(controller.removeUsbPrompt && test.sent.filter(x => x === "reboot").length === before, "prepared removable boot waits for USB removal before reboot");
            } else if (test.stage === 80) {
                input.keyClick(Qt.Key_Return);
                test.check(test.sent.includes("reboot"), "Enter on the USB removal screen requests reboot");
                test.check((test.findItem(content.Window.window.contentItem, "removeUsbMessage") as Text).text === "Restarting…", "the removal prompt shows restart progress");
                const pendingCount = test.sent.filter(x => x === "reboot").length;
                input.keyClick(Qt.Key_Return);
                test.check(test.sent.filter(x => x === "reboot").length === pendingCount, "repeated Enter does not submit a second reboot");
                const rebootId = Object.keys(controller.session.pending).find(id => controller.session.pending[id] === "reboot");
                controller.receive({
                    type: "reply",
                    id: rebootId,
                    ok: false,
                    msg: "Could not restart. Try again, or hold the power button to turn the computer off, then start it again. Your installation is safe."
                });
            } else if (test.stage === 81) {
                test.check((test.findItem(content.Window.window.contentItem, "removeUsbMessage") as Text).text === "Could not restart. Try again, or hold the power button to turn the computer off, then start it again. Your installation is safe.", "the removal prompt shows a reboot failure and manual recovery");
                const failedCount = test.sent.filter(x => x === "reboot").length;
                input.keyClick(Qt.Key_Return);
                test.check(test.sent.filter(x => x === "reboot").length === failedCount + 1, "Enter retries a failed reboot");
                const rebootId = Object.keys(controller.session.pending).find(id => controller.session.pending[id] === "reboot");
                controller.receive({
                    type: "reply",
                    id: rebootId,
                    ok: false,
                    msg: "Could not restart. Try again, or hold the power button to turn the computer off, then start it again. Your installation is safe."
                });
                controller.removeUsbPrompt = false;
                controller.catalog = Object.assign({}, controller.catalog, {
                    boot_removable: false
                });
                const before = test.sent.filter(x => x === "reboot").length;
                controller.requestReboot();
                const prepareId = Object.keys(controller.session.pending).find(id => controller.session.pending[id] === "prepare_reboot");
                controller.receive({
                    type: "reply",
                    id: prepareId,
                    ok: true
                });
                test.check(!controller.removeUsbPrompt && test.sent.filter(x => x === "reboot").length === before + 1, "non-removable boot restarts without a USB prompt");
            } else if (test.stage === 82) {
                controller.session.pending = {};
                controller.requestReboot();
                const prepareId = Object.keys(controller.session.pending).find(id => controller.session.pending[id] === "prepare_reboot");
                const before = test.sent.filter(x => x === "reboot").length;
                controller.receive({
                    type: "reply",
                    id: prepareId,
                    ok: true,
                    forced_reboot: true
                });
                test.check(controller.removeUsbPrompt && controller.session.rebootMessage.indexOf("Restarting in 15 seconds") >= 0, "forced preparation displays the automatic restart and conditional USB removal instruction");
                test.check(test.sent.filter(x => x === "reboot").length === before, "forced preparation allows time to read the restart message");
                controller.session.ready = false;
                controller.reboot();
                test.check(controller.session.rebootMessage.indexOf("Restarting in") >= 0, "disconnected Enter preserves the automatic restart countdown");
                controller.session.ready = true;
                controller.receive({
                    type: "hello",
                    proto: 1,
                    restart: {
                        state: "forced",
                        job_id: controller.session.jobId,
                        remaining_s: 10
                    }
                });
                const restartButton = test.findItem(content.Window.window.contentItem, "restartPrepared");
                test.check(controller.removeUsbPrompt && restartButton.enabled, "reopened restart screen has an enabled Restart button");
                controller.receive({
                    type: "hello",
                    proto: 1,
                    restart: {
                        state: "ready",
                        job_id: controller.session.jobId,
                        remaining_s: 0
                    }
                });
                test.check((test.findItem(content.Window.window.contentItem, "removeUsbMessage") as Text).text === "Press Enter to restart.", "reopened non-removable boot does not request USB removal");
                test.check(controller.removeUsbPrompt, "reopened prepared boot waits for the person's restart request");
                input.mouseClick(restartButton);
                test.check(test.sent.filter(x => x === "reboot").length === before + 1, "reopened Restart button supports an immediate restart");
                controller.removeUsbPrompt = false;
                controller.session.error = {
                    type: "error",
                    code: "login_name_reserved",
                    message: "This login name belongs to the system. Choose another name; the disk has not been changed.",
                    retryable: true
                };
                controller.login = "system_account";
                controller.session.outcome = "error";
                controller.publish();
                controller.step = "error";
            } else if (test.stage === 83) {
                input.mouseClick(test.find("retryInstallation"));
            } else if (test.stage === 84) {
                test.check(controller.step === "you" && !controller.session.error, "a refused login retries on the You step");
                const login = test.find("loginField") as C.TextField;
                test.check(login.activeFocus && login.text === "system_account", "the refused login is retained and focused for correction");
                const viewport = (test.find("installerBody") as C.ScrollView).contentItem as Flickable;
                test.check(test.inside(login, viewport), "the focused login is fully visible");
                test.check(!controller.focusLogin, "the retry focus request is consumed");
                input.keyClick(Qt.Key_End);
                input.keyClick(Qt.Key_A);
                test.check(controller.login === "system_accounta", "typing edits the refused login without another click");
            } else if (test.stage === 85 || test.stage === 88) {
                controller.session.error = {
                    type: "error",
                    code: "bad_config",
                    message: test.stage === 85 ? "Test mode requires /etc/emaki-test/authorized_keys." : "Cannot determine the new account UID/GID.",
                    retryable: true
                };
                controller.session.outcome = "error";
                controller.publish();
                controller.step = "error";
            } else if (test.stage === 86 || test.stage === 89) {
                input.mouseClick(test.find("retryInstallation"));
            } else if (test.stage === 87 || test.stage === 90) {
                test.check(controller.step === "disk" && !controller.session.error, "unrelated settings failures retry on the Disk step");
                test.check(!controller.focusLogin, "unrelated settings failures do not focus the login");
            } else {
                if (!test.failed)
                    console.log("INTERACTION_OK map, search, keyboard, software, password toggles and resets");
                Qt.quit();
            }
            ++test.stage;
        }
    }
}
