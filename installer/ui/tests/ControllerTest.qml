pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io
import ".." as UI

ShellRoot {
    id: test
    property string scenario: Quickshell.env("EMAKI_INSTALLER_SCENARIO")
    property int plans: 0
    property int probes: 0
    property int clears: 0
    property int confirmations: 0
    property int cursor: 0
    property bool failed: false
    property bool socketMode: Quickshell.env("EMAKI_INSTALLER_TEST_UNIX") === "1"
    function check(condition: bool, message: string): void {
        if (!condition) {
            failed = true;
            console.error("ASSERTION_FAILED " + message);
            Qt.quit();
        }
    }
    function finish(): void {
        check(clears >= plans, "password fields receive a clear signal after every plan acknowledgement");
        if (!failed)
            console.log("SCENARIO_OK " + scenario);
        Qt.quit();
    }
    function prepare(): void {
        controller.step = "you";
        controller.diskId = "/dev/vda";
        controller.login = "alex";
        controller.fullName = "Alex";
        const secret = "ephemeral-" + Date.now();
        ++plans;
        controller.plan(secret, secret);
    }
    function observe(message: var): void {
        if (message.type === "transport_disconnected") {
            test.cursor = controller.session.lastLogSeq;
            controller.lost();
            test.check(!controller.session.ready && controller.session.running, "disconnect retains running job");
            controller.send("hello", {
                proto: 1
            });
            return;
        }
        if (!test.socketMode)
            controller.receive(message);
        if (message.type === "inventory") {
            ++probes;
            test.check(controller.mounts.length === 0 && !controller.session.plan, "probe invalidates mounts and plan");
            if (scenario === "manual-reprobe" && probes === 2) {
                controller.mode = "manual";
                controller.assign(controller.session.inventory.disks[0].partitions[0], "/efi", "vfat", false);
                controller.assign(controller.session.inventory.disks[0].partitions[1], "/", "btrfs", true);
                test.check(controller.mounts.length === 2, "manual assignments");
            }
            prepare();
        } else if (message.type === "plan_ack") {
            test.check(controller.step === "review" && !controller.agreed, "new review needs fresh agreement");
            if (scenario === "plan-errors") {
                controller.confirm();
                test.check(!controller.session.confirming, "errors cannot be confirmed");
                finish();
            } else if (scenario === "manual-reprobe" && plans === 1) {
                controller.gpartedWarning = true;
                controller.openGparted();
                test.check(!controller.session.plan, "partition editor invalidates the old plan");
            } else {
                controller.confirm();
                if (controller.mode === "erase")
                    test.check(!controller.session.confirming, "erase requires checkbox");
                controller.agreed = true;
                if (!controller.session.confirming)
                    controller.confirm();
            }
        } else if (message.type === "reply" && message.code === "token_expired") {
            test.check(controller.step === "you" && !controller.session.plan, "expired confirmation returns to account");
            prepare();
        } else if (message.type === "state" && scenario === "cancel" && !controller.session.cancelPending) {
            controller.send("cancel", {});
        } else if (message.type === "error") {
            if (scenario === "error-retry") {
                test.check(controller.step === "error", "worker error shown");
                controller.retry();
            } else if (scenario === "cancel") {
                test.check(controller.session.cancelMessage.indexOf("not been undone") >= 0, "cancel_ack shown");
                finish();
            }
        } else if (message.type === "done") {
            test.check(controller.step === "done" && !controller.session.running, "done reached");
            if (scenario === "disconnect-resume")
                test.check(controller.session.logs.length === 2, "missed log replayed once");
            if (scenario === "token-expiry") {
                controller.session.outcome = "";
                controller.session.plan = {
                    token: "fixture"
                };
                controller.session.deadline = 1;
                controller.step = "review";
                expiry.start();
            } else
                finish();
        }
    }
    UI.InstallerController {
        id: controller
        mockTransport: !test.socketMode
        helpersEnabled: false
        partitionEditorCommand: ["/usr/bin/true"]
        onClearPasswords: ++test.clears
        onOutbound: message => {
            if (message.type === "resume")
                test.check(message.since_seq === test.cursor, "resume uses log cursor, not hello seq");
            if (message.type === "confirm")
                ++test.confirmations;
            mock.write(JSON.stringify(message) + "\n");
        }
        onMessageReceived: message => {
            if (test.socketMode)
                test.observe(message);
        }
    }
    Process {
        id: mock
        running: !test.socketMode
        command: ["python3", "-I", "-B", Qt.resolvedUrl("mock-worker.py").toString().replace("file://", ""), "--stdio", "--scenario", test.scenario, "--delay", "0"]
        stdinEnabled: true
        onStarted: controller.send("hello", {
            proto: 1
        })
        stdout: SplitParser {
            onRead: data => test.observe(JSON.parse(data))
        }
    }
    Timer {
        id: expiry
        interval: 1500
        onTriggered: {
            test.check(controller.step === "you" && !controller.session.plan, "timer expires token and asks for password");
            test.finish();
        }
    }
    Timer {
        running: true
        interval: 10000
        onTriggered: {
            console.error("ASSERTION_FAILED timeout");
            Qt.quit();
        }
    }
}
