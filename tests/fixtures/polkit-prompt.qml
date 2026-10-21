// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Emaki.I18n
import QtQuick.Window
import QtTest
import Quickshell
import Quickshell.Io
import "PolkitDetails.js" as Details

ShellRoot {
    id: root
    property int clears: 0
    property int focuses: 0
    property int shakes: 0
    property int notices: 0
    property string noticeTitle: ""
    property string field: ""

    QtObject {
        id: backend
        property var flow: null
        property bool responseRequired: true
        property bool passwordRequest: true
        property int cancels: 0
        property int submissions: 0
        property string requestLine: "Fixture requests administrator rights."
        property var detailRows: []
        signal rejected
        signal interrupted
        function cancel(): void {
            ++cancels;
            flow = null;
        }
        function submit(value: string): void {
            root.check(value === "PRIVATE_AUTH_FIXTURE_91", "answer forwarded");
            ++submissions;
            responseRequired = false;
        }
    }
    PolkitPromptController {
        id: controller
        backend: backend
        onClearInput: {
            root.field = "";
            ++root.clears;
        }
        onFocusInput: ++root.focuses
        onShake: ++root.shakes
        onNotice: (title, body) => {
            ++root.notices;
            root.noticeTitle = title;
            root.check(body.length > 0, "cancellation notice body");
        }
    }
    PolkitLockState {
        id: lockState
        gateHold: 300
    }
    Window {
        id: window
        width: 1280
        height: 720
        visible: true
        TextInput {
            id: clipboardProbe
            visible: false
        }
        PolkitPromptCard {
            id: card
            controller: controller
            reducedMotion: true
            sudoKeyInstalled: false
        }
        SudoKeySettingsPage {
            id: settings
            x: 900
            width: 300
            sudoKeyInstalled: false
        }
        TestCase {
            id: input
            when: false
        }
    }
    function cardChecks(): void {
        controller.locked = false;
        begin();
        const password = input.findChild(card, "polkit-password");
        const key = input.findChild(card, "polkit-sudo-key");
        const row = input.findChild(settings, "settings-sudo-key");
        check(password !== null && key !== null && row !== null, "production controls found");
        card.focusPassword();
        input.wait(20);
        check(password.activeFocus && password.echoMode === TextInput.Password, "password focus and masking");
        const reveal = input.findChild(card, "polkit-reveal");
        clipboardProbe.text = "clipboard sentinel";
        clipboardProbe.selectAll();
        clipboardProbe.copy();
        password.text = "PRIVATE_AUTH_FIXTURE_91";
        reveal.toggled();
        check(password.echoMode === TextInput.Normal, "password revealed");
        for (const shortcut of [[Qt.Key_A, Qt.ControlModifier], [Qt.Key_Left, Qt.ShiftModifier], [Qt.Key_Home, Qt.ShiftModifier], [Qt.Key_C, Qt.ControlModifier], [Qt.Key_X, Qt.ControlModifier], [Qt.Key_Insert, Qt.ControlModifier], [Qt.Key_Delete, Qt.ShiftModifier]]) {
            input.keyClick(shortcut[0], shortcut[1]);
            check(password.selectedText === "", "keyboard cannot select revealed password");
        }
        input.mouseDoubleClickSequence(password, 15, password.height / 2);
        check(password.selectedText === "", "mouse cannot select revealed password");
        clipboardProbe.text = "";
        clipboardProbe.paste();
        check(clipboardProbe.text === "clipboard sentinel", "revealed password cannot replace clipboard");
        check(password.text === "PRIVATE_AUTH_FIXTURE_91", "cut cannot remove password");
        reveal.toggled();
        check(!key.visible && !row.visible, "marker absent hides both entries");
        settings.sudoKeyInstalled = true;
        card.sudoKeyInstalled = true;
        check(row.visible && !key.visible, "marker enables settings but never polkit sudo key");
        card.requestKind = "sudo";
        check(key.visible, "future sudo request permits key expander");
        card.keyOpen = true;
        card.sudoKeyInstalled = false;
        check(!key.visible && !card.keyOpen, "marker removal collapses key expander");
        settings.sudoKeyInstalled = false;
        check(!row.visible, "marker removal hides settings row");
        card.requestKind = "polkit";
        password.text = "PRIVATE_AUTH_FIXTURE_91";
        card.submit();
        check(password.text === "", "actual field cleared on submit");
        backend.rejected();
        backend.responseRequired = true;
        input.wait(20);
        check(password.text === "" && password.activeFocus, "actual field focused after rejection");
        password.text = "PRIVATE_AUTH_FIXTURE_91";
        begin();
        check(password.text === "", "actual field cleared on flow replacement");
        password.text = "PRIVATE_AUTH_FIXTURE_91";
        backend.flow = null;
        check(password.text === "" && !controller.opened, "actual field cleared on successful completion");
        begin();
        password.text = "PRIVATE_AUTH_FIXTURE_91";
        controller.locked = true;
        check(password.text === "" && !controller.opened, "actual field cleared on lock");
        controller.locked = false;
        begin();
        card.focusPassword();
        input.wait(20);
        password.text = "PRIVATE_AUTH_FIXTURE_91";
        input.keyClick(Qt.Key_Escape);
        check(password.text === "" && !controller.opened && backend.flow === null, "real Escape clears and cancels");
    }
    function check(condition: bool, name: string): void {
        if (!condition)
            throw new Error("Authentication fixture failed: " + name);
    }
    function begin(): void {
        backend.responseRequired = true;
        backend.passwordRequest = true;
        backend.flow = {};
        if (controller.opened)
            input.wait(1050);
    }
    function run(): string {
        check(controller.locked && !controller.opened, "locked initial state");
        begin();
        check(!controller.opened && backend.flow === null, "new request while locked cancels");
        check(root.notices === 1 && root.noticeTitle === "Not done — password requests are paused", "blocked request gives notice");
        const blockedNotices = root.notices;
        controller.locked = false;
        begin();
        check(controller.opened && controller.accepting, "request opens");
        check(controller.timeoutMs === 120000, "120 second timeout");
        const deadline = controller.deadline;
        check(deadline > Date.now() + 118000, "wall clock deadline includes input guard");
        for (let attempt = 0; attempt < 3; ++attempt) {
            root.field = "PRIVATE_AUTH_FIXTURE_91";
            controller.submit(root.field);
            check(root.field === "" && !controller.accepting, "submit clears input and waits");
            const focusBefore = root.focuses;
            backend.rejected();
            backend.responseRequired = true;
            check(controller.opened && controller.wrong && root.shakes === attempt + 1, "repeat rejection shakes");
            check(root.field === "" && root.focuses > focusBefore, "rejection clears and focuses");
            check(controller.deadline === deadline, "retry does not extend timeout");
        }
        check(backend.submissions === 3, "one answer per submission");
        controller.tick(deadline - 1);
        check(controller.opened, "not expired early");
        controller.tick(deadline);
        check(!controller.opened && backend.flow === null && root.notices === blockedNotices + 1, "timeout cancels caller");
        check(root.noticeTitle === "Not done — nobody answered", "timeout notice");
        const expiredNotices = root.notices;
        controller.tick(deadline + 1);
        backend.rejected();
        check(root.notices === expiredNotices && !controller.opened, "late callback stays closed");
        for (const reason of ["cancel", "escape"]) {
            begin();
            root.field = "PRIVATE_AUTH_FIXTURE_91";
            controller.cancel(reason);
            check(!controller.opened && root.field === "" && backend.flow === null, "dismiss clears and cancels");
        }
        begin();
        root.field = "PRIVATE_AUTH_FIXTURE_91";
        const clearsBefore = root.clears;
        begin();
        check(root.field === "" && root.clears > clearsBefore && !controller.wrong, "flow replacement clears");
        root.field = "PRIVATE_AUTH_FIXTURE_91";
        controller.locked = true;
        check(root.field === "" && !controller.opened && backend.flow === null, "lock cancels and clears");
        controller.locked = false;
        check(!controller.opened, "unlock never resumes cancelled request");
        begin();
        backend.passwordRequest = false;
        const submitsBefore = backend.submissions;
        controller.submit("PRIVATE_AUTH_FIXTURE_91");
        check(backend.submissions === submitsBefore && !controller.accepting, "nonpassword request cannot submit");
        controller.cancel("cancel");
        const rows = Details.rows("action", "Message --password=PRIVATE_AUTH_FIXTURE_91", {
            program: "/opt/example",
            argv: "unclassified PRIVATE_AUTH_FIXTURE_91",
            api_key: "PRIVATE_AUTH_FIXTURE_91",
            command_line: "/opt/example PRIVATE_AUTH_FIXTURE_91",
            safe: "https://user:PRIVATE_AUTH_FIXTURE_91@example.com",
            plain: "ordinary value"
        }, "");
        const encoded = JSON.stringify(rows);
        check(encoded.indexOf("PRIVATE_AUTH_FIXTURE_91") < 0, "details redact secrets");
        check(rows[2].value === "Enter an administrator password to run example as administrator.", "message names only the executable");
        check(encoded.indexOf("ordinary value") >= 0, "details preserve safe values");
        check(rows[0].value === "Unknown program", "requester explicitly unavailable");
        const pidDetails = {
            "polkit.caller-pid": "4100",
            "polkit.subject-pid": "4099"
        };
        const execAction = "org.freedesktop.policykit.exec";
        const execMessage = "Authentication is needed to run `/opt/bin/nmcli --password=PRIVATE_AUTH_FIXTURE_91' as the super user";
        const execHeadline = "Enter an administrator password to run nmcli as administrator.";
        check(Details.headline(execAction, execMessage, pidDetails) === execHeadline, "pid-only request names executable");
        const execRows = Details.rows(execAction, execMessage, pidDetails, "");
        check(execRows[2].value === execHeadline, "pid-only Message names executable");
        check(JSON.stringify(execRows).indexOf("PRIVATE_AUTH_FIXTURE_91") < 0, "pid-only details hide arguments");
        for (const nameCase of [["bo\u200fb\uDB40\uDC01\n", "bo b"], ["n".repeat(80), "n".repeat(64)]]) {
            const userMessage = "Authentication is needed to run `/opt/bin/id PRIVATE_AUTH_FIXTURE_91' as user " + nameCase[0];
            const userHeadline = "Enter an administrator password to run id as " + nameCase[1] + ".";
            check(Details.headline(execAction, userMessage, pidDetails) === userHeadline, "target account is cleaned and bounded");
            check(Details.rows(execAction, userMessage, pidDetails, "")[2].value === userHeadline, "Message preserves target account");
        }
        const ownMessage = "Enter\u0001an\nadministrator password\u202e\u200b\u061c\uD834\uDD73\uDB40\uDC01.";
        const ownHeadline = "Enter an administrator password" + " ".repeat(5) + ".";
        check(Details.headline("org.emaki.fixture", ownMessage, pidDetails) === ownHeadline, "own headline cleans controls and Unicode format characters");
        check(Details.rows("org.emaki.fixture", ownMessage, pidDetails, "")[2].value === ownHeadline, "own Message cleans controls and Unicode format characters");
        const joinedText = "සත්\u200dයාපනය می\u200cشود";
        for (const action of ["org.emaki.fixture", "org.example.fixture"]) {
            check(Details.headline(action, joinedText, pidDetails) === joinedText, "headline preserves script joiners");
            check(Details.rows(action, joinedText, pidDetails, "")[2].value === joinedText, "Message preserves script joiners");
        }
        const secretForms = ["password=ab]PRIVATE_AUTH_FIXTURE_91", '--password ab"PRIVATE_AUTH_FIXTURE_91', "password=ab'PRIVATE_AUTH_FIXTURE_91", "password=ab`PRIVATE_AUTH_FIXTURE_91", "password=ab}PRIVATE_AUTH_FIXTURE_91", '--password=ab"PRIVATE_AUTH_FIXTURE_91', 'Cookie: sid=ab"PRIVATE_AUTH_FIXTURE_91', 'Bearer ab"PRIVATE_AUTH_FIXTURE_91', "token=ab]PRIVATE_AUTH_FIXTURE_91"];
        for (const secretForm of secretForms) {
            const redacted = Details.redact(secretForm);
            check(redacted.indexOf("PRIVATE_AUTH_FIXTURE_91") < 0 && redacted.indexOf("Hidden") >= 0, "quote and bracket secret is fully hidden");
            check(Details.headline("org.example.fixture", secretForm, pidDetails) === redacted, "third-party headline redacts quote and bracket secret");
            check(Details.rows("org.example.fixture", secretForm, pidDetails, "")[2].value === redacted, "third-party Message redacts quote and bracket secret");
        }
        for (const prefix of ["/opt/emaki/libexec", "/nix/store/example-emaki/libexec/emaki"]) {
            for (const operation of ["install", "remove"]) {
                for (const flatpak of [false, true]) {
                    const action = "org.emaki.programs." + (flatpak ? "flatpak." : "") + operation;
                    const helper = prefix + "/emaki-programs-" + (flatpak ? "flatpak" : operation);
                    const target = flatpak ? "com.github.tchx84.Flatseal" : operation === "install" ? "extra/firefox" : "firefox";
                    const token = operation === "install" || flatpak ? "a".repeat(64) : "firefox,mozilla-common";
                    const command = helper + " " + (flatpak ? operation + " flathub app/" + target + "/x86_64/stable" : target) + " " + token;
                    const metadata = {
                        program: helper,
                        command_line: command
                    };
                    const headline = Details.headline(action, "generic", metadata, prefix);
                    check(headline.startsWith(target + " — "), "program is first in headline");
                    check(headline.indexOf(helper) < 0 && headline.indexOf(token) < 0, "headline hides helper and token");
                    check(headline.indexOf(operation === "install" ? "Install" : "Remove") >= 0, "headline shows operation");
                    const commandRow = Details.rows(action, "generic", metadata, "", prefix).filter(row => row.label === "command_line")[0];
                    check(Details.rows(action, "generic", metadata, "", prefix)[2].value === headline, "Message uses the program headline");
                    check(commandRow.value === command, "full validated command remains in Details");
                    for (const bad of [command + " extra", command + "\n", command.replace(target, target + "\u202e"), command.replace(helper, "/tmp/helper"), command.replace(token, "bad;token")]) {
                        check(Details.programRequest(action, {
                            program: helper,
                            command_line: bad
                        }, prefix) === null, "malformed program command not displayed");
                    }
                    check(Details.programRequest(action, {
                        program: "/tmp/helper",
                        command_line: command
                    }, prefix) === null, "helper must match fixed path");
                    check(Details.programRequest("unrelated.action", metadata, prefix) === null, "unrelated actions cannot claim program hint");
                    check(Details.programRequest(action, metadata, "/different/prefix") === null, "rendered prefix is required");
                }
            }
            const receipt = "20261012T120000123456Z-" + "b".repeat(32);
            for (const request of [
                {
                    action: "org.emaki.programs.aur",
                    helper: "emaki-programs-aur",
                    words: "paint-bin " + "a".repeat(64),
                    headline: "AUR · paint-bin — Build"
                },
                {
                    action: "org.emaki.programs.undo",
                    helper: "emaki-programs-undo",
                    words: receipt + " krita " + "a".repeat(64),
                    headline: "krita — Undo"
                },
                {
                    action: "org.emaki.programs.flatpak.undo",
                    helper: "emaki-programs-flatpak",
                    words: "undo flathub app/org.gimp.GIMP/x86_64/stable " + receipt + " " + "a".repeat(64),
                    headline: "org.gimp.GIMP — Undo"
                }
            ]) {
                const helper = prefix + "/" + request.helper;
                const metadata = {
                    program: helper,
                    command_line: helper + " " + request.words
                };
                check(Details.headline(request.action, "generic", metadata, prefix).startsWith(request.headline), "source and program lead the headline: " + request.action);
                for (const bad of [metadata.command_line + " extra", metadata.command_line.replace(" ", " --run "), metadata.command_line + "\n"])
                    check(Details.programRequest(request.action, {
                        program: helper,
                        command_line: bad
                    }, prefix) === null, "malformed request not displayed: " + request.action);
            }
        }

        check(Details.rows("", "", null, "").length === 4, "missing detail state");
        check(Details.clean("a\u202eb\u0000c") === "a b c", "control text sanitized");
        const many = {};
        for (let i = 0; i < 50; ++i)
            many["item" + i] = "x".repeat(4000);
        const bounded = Details.rows("", "", many, "");
        check(bounded.length === 35 && bounded[3].value.length <= 2048, "details bounded");
        lockState.sample(JSON.stringify({
            locked: false,
            generation: lockState.generation
        }));
        check(!lockState.locked, "fresh unlocked observation");
        const oldGeneration = lockState.generation;
        lockState.prepareLock();
        lockState.sample(JSON.stringify({
            locked: false,
            generation: oldGeneration
        }));
        check(lockState.locked, "buffered observation cannot reopen input after pre-lock IPC");
        lockState.sample(JSON.stringify({
            locked: true,
            generation: lockState.generation
        }));
        lockState.sample(JSON.stringify({
            locked: false,
            generation: lockState.generation
        }));
        check(!lockState.locked, "fresh gate removal reopens input");
        // emaki-lock could not write its gate: the fresh sample says unlocked.
        lockState.prepareLock();
        lockState.sample(JSON.stringify({
            locked: false,
            generation: lockState.generation
        }));
        check(lockState.locked, "a lock request keeps input closed until its gate is seen");
        input.wait(400);
        check(!lockState.awaitingGate, "a gate that never appears is not awaited forever");
        lockState.sample(JSON.stringify({
            locked: false,
            generation: lockState.generation
        }));
        check(!lockState.locked, "after the hold a fresh unlocked sample reopens input");
        input.wait(1100);
        check(!lockState.locked, "observer silence does not report a lock");
        lockState.sample(JSON.stringify({
            locked: "false",
            generation: lockState.generation
        }));
        check(lockState.locked, "invalid lock sample fails closed");
        cardChecks();
        return "POLKIT_PROMPT_OK";
    }
    IpcHandler {
        target: "tests"
        function exercise(): string {
            return root.run();
        }
    }
    Timer {
        interval: 50
        running: Quickshell.env("EMAKI_POLKIT_SELFTEST") === "1"
        onTriggered: {
            try {
                console.info(root.run());
                Qt.quit();
            } catch (error) {
                console.error(String(error));
                Qt.exit(1);
            }
        }
    }
}
