// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import QtTest
import Quickshell
import "SudoText.js" as Copy

ShellRoot {
    id: root
    property int serial: 0
    property var notices: []
    QtObject {
        id: client
        property bool connected: true
        property var request: ({})
        property var keys: []
        property bool installed: false
        property bool warningAccepted: false
        property bool btrfs: false
        property bool snapguardAvailable: false
        property int submissions: 0
        property string submittedMode: ""
        property int submittedSeconds: -1
        property string revoked: ""
        signal authenticationFailed
        signal rejected
        function submit(id: string, password: string, mode: string, seconds: int): void {
            root.check(id === request.id && password === "PRIVATE_SUDO_FIXTURE_71", "correct request and response forwarded");
            submissions++;
            submittedMode = mode;
            submittedSeconds = seconds;
        }
        function cancel(id: string): void {
            root.check(id === request.id, "cancel current request only");
            request = {};
        }
        function revoke(id: string): void {
            revoked = id;
            keys = [];
        }
        function acknowledgeWarning(): void {
            warningAccepted = true;
        }
    }
    SudoPromptAdapter {
        id: adapter
        client: client
    }
    PolkitPromptController {
        id: controller
        backend: adapter
        locked: false
        onNotice: (title, body) => root.notices.push({
                title: title,
                body: body
            })
    }
    Window {
        id: window
        width: 1280
        height: 900
        visible: true
        PolkitPromptCard {
            id: card
            controller: controller
            sudoKeyInstalled: client.installed
            reducedMotion: true
        }
        SudoKeyWarning {
            id: warning
            x: 700
            width: 500
            height: 800
            backend: client
            visible: false
        }
        SudoKeyIndicator {
            id: indicator
            x: 850
            y: 820
            width: 320
            installed: client.installed
            backend: client
        }
        TestCase {
            id: input
            when: false
        }
    }
    function check(condition: bool, name: string): void {
        if (!condition)
            throw new Error("Sudo fixture failed: " + name);
    }
    function begin(known: bool): void {
        client.request = {
            id: String(++serial),
            accountName: "fixture",
            requester: known ? "Fixture Product" : "",
            command: "fixture-program",
            arguments: ["--secret=PRIVATE_ARGUMENT_83"],
            agent: known,
            keyEligible: true,
            turnEligible: known
        };
        input.wait(1050);
    }
    function run(): void {
        check(Copy.keyTimestampPolicy === "After a key ends, sudo's usual five minutes still apply.", "approved sudo timestamp warning");
        check(Copy.keyMinutesError === "At least 5 minutes", "approved typed minutes error");
        check(Copy.keyTurns(1) === "1 turn" && Copy.keyTurns(7) === "7 turns", "approved singular and plural turn counts");
        SudoKeySupport.backend = client;
        SudoKeySupport.installed = false;
        check(!SudoKeySupport.warningNeeded, "absent package never opens warning");
        SudoKeySupport.installed = true;
        check(SudoKeySupport.warningNeeded, "installation automatically opens warning on connected shell");
        SudoKeySupport.warningDismissed = true;
        check(!SudoKeySupport.warningNeeded, "dismissed warning does not reopen continuously");
        client.connected = false;
        client.connected = true;
        check(!SudoKeySupport.warningNeeded, "reconnect does not repeatedly reopen dismissed warning");
        SudoKeySupport.warningRequested = true;
        check(SudoKeySupport.warningNeeded, "settings can explicitly reopen warning");
        SudoKeySupport.installed = false;
        SudoKeySupport.installed = true;
        check(SudoKeySupport.warningNeeded && !SudoKeySupport.warningDismissed, "reinstallation resets warning dismissal");
        SudoKeySupport.installed = false;
        begin(false);
        check(controller.opened && !card.agentRequest, "unknown requester uses generic card");
        check(adapter.requestLine === Copy.requestUnavailable, "unknown requester uses generic request text");
        check(adapter.detailRows[1].value === "fixture-program", "command basename shown for generic request");
        check(JSON.stringify(adapter.detailRows).indexOf("PRIVATE_ARGUMENT_83") < 0, "command arguments stay hidden");
        client.request = Object.assign({}, client.request, {
            command: "fixture\u202eprogram\n"
        });
        check(adapter.detailRows[1].value === "fixture program ", "command controls and direction overrides sanitized");
        client.request = Object.assign({}, client.request, {
            command: ""
        });
        check(adapter.detailRows[1].value === Copy.unavailable, "missing program uses unavailable placeholder");
        begin(true);
        check(adapter.requestLine === "Fixture Product wants to run a command as administrator", "known requester uses product label");
        check(adapter.detailRows[1].value === "fixture-program", "known requester shows command basename");
        const password = input.findChild(card, "polkit-password");
        const expander = input.findChild(card, "polkit-sudo-key");
        check(controller.opened && controller.accepting && card.agentRequest, "sudo opens with authenticated classification");
        check(!expander.visible && !indicator.visible, "absent package hides all key controls");
        password.text = "PRIVATE_SUDO_FIXTURE_71";
        card.submit();
        check(client.submittedMode === "none" && password.text === "" && !controller.accepting, "plain response clears and waits");
        client.rejected();
        check(controller.wrong && controller.accepting, "retry signal restores password input");
        password.text = "PRIVATE_SUDO_FIXTURE_71";
        card.submit();
        client.authenticationFailed();
        check(controller.wrong && !controller.accepting && password.text === "", "final PAM failure shakes without reopening completed request");
        begin(true);
        client.installed = true;
        check(expander.visible && !card.keyGrantAllowed, "warning required before key grants");
        card.keyOpen = true;
        const before = client.submissions;
        password.text = "PRIVATE_SUDO_FIXTURE_71";
        card.submit();
        check(client.submissions === before, "unacknowledged warning cannot grant");
        warning.visible = true;
        const accept = input.findChild(warning, "sudo-key-warning-accept");
        accept.clicked();
        warning.visible = false;
        check(client.warningAccepted && card.keyGrantAllowed, "explicit acknowledgement permits grant");
        const choices = input.findChild(card, "sudo-key-choices");
        check(choices.way === "time" && choices.minutesText === "" && choices.turnsText === "" && !choices.amountValid, "amounts start empty without presets");
        const minutesInput = input.findChild(choices, "sudo-key-minutes");
        const turnsInput = input.findChild(choices, "sudo-key-turn-count");
        const amountError = input.findChild(choices, "sudo-key-amount-error");
        for (const amount of ["", "no", "5.5", "-5", "0", "4", "1441", "9999999999999999", "1e2", " 5"]) {
            minutesInput.text = amount;
            const submitted = client.submissions;
            password.text = "PRIVATE_SUDO_FIXTURE_71";
            card.submit();
            check(!choices.amountValid && amountError.visible && amountError.text === Copy.keyMinutesError, "invalid minutes show inline range message");
            check(client.submissions === submitted && password.text === "PRIVATE_SUDO_FIXTURE_71", "invalid minutes do not submit or clear password");
        }
        for (const minutes of [5, 23, 137, 1440]) {
            begin(true);
            card.keyOpen = true;
            check(choices.minutesText === "" && choices.turnsText === "", "new request clears both amounts");
            minutesInput.text = String(minutes);
            password.text = "PRIVATE_SUDO_FIXTURE_71";
            card.submit();
            check(client.submittedMode === "time" && client.submittedSeconds === minutes * 60, "typed minutes reach native boundary as seconds");
        }
        begin(true);
        card.keyOpen = true;
        choices.way = "session";
        minutesInput.text = "bad";
        turnsInput.text = "bad";
        password.text = "PRIVATE_SUDO_FIXTURE_71";
        card.submit();
        check(choices.amountValid && !amountError.visible && client.submittedMode === "session" && client.submittedSeconds === 0, "session ignores unused amounts and has no arbitrary duration");
        begin(true);
        card.keyOpen = true;
        const turnChoice = input.findChild(choices, "sudo-key-turns");
        check(turnChoice.visible, "eligible request exposes typed turn option");
        turnChoice.clicked();
        for (const amount of ["", "no", "1.5", "-1", "0", "1001", "9999999999999999", "1e2", " 1"]) {
            turnsInput.text = amount;
            const submitted = client.submissions;
            password.text = "PRIVATE_SUDO_FIXTURE_71";
            card.submit();
            check(!choices.amountValid && amountError.visible && amountError.text === Copy.keyTurnsError, "invalid turns show inline range message");
            check(client.submissions === submitted && password.text === "PRIVATE_SUDO_FIXTURE_71", "invalid turns do not submit or clear password");
        }
        for (const turns of [1, 7, 43, 1000]) {
            begin(true);
            card.keyOpen = true;
            turnChoice.clicked();
            turnsInput.text = String(turns);
            password.text = "PRIVATE_SUDO_FIXTURE_71";
            card.submit();
            check(client.submittedMode === "turns:" + turns && client.submittedSeconds === 0, "typed turns reach native boundary");
        }
        begin(true);
        const turnSubmissions = client.submissions;
        for (const mode of ["turns:", "turns:1001", "turns:0", "turns:-1", "turns:1.5", "turns:07", "turns:1e2", "turns:NaN"])
            adapter.submitKey("PRIVATE_SUDO_FIXTURE_71", mode, 0);
        check(client.submissions === turnSubmissions, "invalid or noncanonical turn count rejected by adapter");
        card.keyOpen = true;
        choices.way = "turns";
        client.request = Object.assign({}, client.request, {
            turnEligible: false
        });
        check(!choices.turnEligible && choices.way === "time" && !turnChoice.visible, "missing report capability hides and resets turn choice");
        adapter.submitKey("PRIVATE_SUDO_FIXTURE_71", "turns:7", 0);
        check(client.submissions === turnSubmissions, "request without reports cannot grant turns");
        begin(true);
        client.warningAccepted = false;
        check(!choices.turnEligible, "warning gate also hides turn options");
        adapter.submitKey("PRIVATE_SUDO_FIXTURE_71", "turns:7", 0);
        check(client.submissions === turnSubmissions, "unacknowledged warning cannot grant turns");
        client.warningAccepted = true;
        begin(true);
        const validSubmissions = client.submissions;
        for (const seconds of [0, 1, 240, 301, 86460])
            adapter.submitKey("PRIVATE_SUDO_FIXTURE_71", "time", seconds);
        check(client.submissions === validSubmissions, "out-of-range and fractional-minute durations rejected by adapter");
        begin(true);
        password.text = "PRIVATE_SUDO_FIXTURE_71";
        controller.tick(controller.deadline + 1);
        check(!controller.opened && !adapter.flow && password.text === "", "timeout cancels and clears");
        check(notices[notices.length - 1].title === "Sudo request cancelled", "timeout has its own sudo notice");
        begin(true);
        controller.cancel("cancel");
        check(!adapter.flow, "cancel ends request");
        check(notices[notices.length - 1].title === "Not done — you cancelled", "person cancellation keeps its own notice");
        client.keys = [
            {
                id: "key1",
                label: "",
                remainingSeconds: 1379,
                session: false
            }
        ];
        check(indicator.visible, "live key indicator visible");
        const revoke = input.findChild(indicator, "sudo-key-revoke-key1");
        check(revoke !== null && revoke.text.indexOf("23 min") >= 0, "indicator includes remaining time");
        check(revoke.text.indexOf(Copy.requesterUnavailable) >= 0, "unknown key owner uses generic label");
        revoke.clicked();
        check(client.revoked === "key1" && !indicator.visible, "one click revokes indicated key");
        client.keys = [
            {
                id: "turn-key",
                label: "Fixture Product",
                kind: "turns",
                remainingTurns: 2,
                remainingSeconds: 14400,
                session: false
            }
        ];
        const turnIndicator = input.findChild(indicator, "sudo-key-revoke-turn-key");
        check(turnIndicator !== null && turnIndicator.text.includes(Copy.keyTurnsRemaining(2)), "indicator shows remaining turns rather than cap time");
        client.keys = [
            {
                id: "turn-key",
                label: "Fixture Product",
                kind: "turns",
                remainingTurns: 1,
                remainingSeconds: 14399,
                session: false
            }
        ];
        check(input.findChild(indicator, "sudo-key-revoke-turn-key").text.includes(Copy.keyTurnsRemaining(1)), "indicator updates remaining turn count");
        input.findChild(indicator, "sudo-key-revoke-turn-key").clicked();
        check(client.revoked === "turn-key" && !indicator.visible, "turn indicator revokes its own grant");
        begin(true);
        password.text = "PRIVATE_SUDO_FIXTURE_71";
        controller.locked = true;
        check(!controller.opened && password.text === "" && !adapter.flow, "lock clears and cancels");
        controller.locked = false;
        begin(true);
        card.keyOpen = true;
        client.installed = false;
        check(!expander.visible && !card.keyOpen && !adapter.keyEligible, "package removal disables grant immediately");
        begin(true);
        password.text = "PRIVATE_SUDO_FIXTURE_71";
        client.connected = false;
        check(!controller.opened && password.text === "", "channel loss clears active password");
        check(notices[notices.length - 1].title === "Sudo request cancelled", "channel loss is not labelled a screen lock");
        client.request = {};
        client.connected = true;
        begin(true);
        password.text = "PRIVATE_SUDO_FIXTURE_71";
        card.submit();
        const beforeSuccess = notices.length;
        client.request = {};
        check(notices.length === beforeSuccess, "submitted completion does not falsely claim cancellation");
        begin(true);
        password.text = "PRIVATE_SUDO_FIXTURE_71";
        card.submit();
        client.authenticationFailed();
        client.request = {};
        check(notices.length === beforeSuccess + 1 && notices[notices.length - 1].title === "Sudo request cancelled", "failed submitted request gets its own notice");
        begin(true);
        password.text = "PRIVATE_SUDO_FIXTURE_71";
        card.submit();
        client.connected = false;
        check(notices.length === beforeSuccess + 2, "disconnect during authentication gets a notice");
        console.log("SUDO_PROMPT_OK");
    }
    Timer {
        interval: 100
        running: true
        onTriggered: {
            try {
                root.run();
                Qt.quit();
            } catch (error) {
                console.error(String(error));
                Qt.exit(1);
            }
        }
    }
}
