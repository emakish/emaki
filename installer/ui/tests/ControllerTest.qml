// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
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
    // real-erase-btrfs replays a recorded install (cut-recording.py): what the window showed while it ran.
    readonly property var phaseKeys: ["prepare_disk", "copy_packages", "bootloader", "account", "settings", "snapshot", "update", "finish"]
    property real highestTotal: 0
    property int highestPhase: 0
    property int packages: 0
    property int packagesAtFull: 0
    property int idlePhases: 0
    property string lastLine: ""
    function followRecording(message: var): void {
        const session = controller.session;
        if (message.type === "state" || message.type === "progress") {
            check(session.totalPct >= highestTotal, "the total bar never moves back (" + highestTotal + " -> " + session.totalPct + ")");
            check(phaseKeys.indexOf(session.phase) >= highestPhase, "phases only move forward (" + session.phase + ")");
            highestTotal = session.totalPct;
            highestPhase = phaseKeys.indexOf(session.phase);
            // A phase without a counter shows the busy indicator, never a frozen "0%".
            if (message.type === "state" && session.phasePct === 0 && session.phase !== "copy_packages" && !session.indeterminate)
                ++idlePhases;
        }
        if (message.type === "log")
            lastLine = message.line;
        if (message.type === "log" && session.phase === "copy_packages" && /^installing \S+\.\.\.$/.test(message.line)) {
            ++packages;
            if (session.phasePct >= 99)
                ++packagesAtFull;
        }
    }
    function checkRecording(message: var): void {
        const session = controller.session;
        console.log("RECORDING packages " + packages + ", at 99% or more " + packagesAtFull + ", phases at a frozen 0% " + idlePhases + ", total " + session.totalPct);
        check(packages > 0, "the recording carries the package copy");
        check(packagesAtFull <= packages / 20, "the copy bar follows the packages instead of waiting at 99% (" + packagesAtFull + " of " + packages + " packages)");
        check(idlePhases === 0, "every phase without a counter shows the busy indicator (" + idlePhases + " showed 0%)");
        check(session.totalPct === 100 && session.seconds === message.seconds, "the done page shows 100% and the recorded duration");
        check(session.logs.length === 100 && session.logs[99] === lastLine, "the window keeps the last 100 log lines, ending with the last one received (" + session.logs.length + ")");
    }
    // Copied to RAM: no worker ever answers (over --unix the socket does not exist at all).
    readonly property bool noWorker: scenario === "no-boot-medium"
    readonly property string copiedToRam: "Restart from the USB stick without the 'copy to RAM' option to install."
    function withoutWorker(): void {
        // What ui-helper.py's catalog answers when /run/archiso/bootmnt is gone.
        controller.helperOp = "catalog";
        controller.helperResult({
            ok: true,
            layouts: [],
            zones: ["UTC"],
            trial: false,
            boot_medium: false
        });
        check(controller.session.notice === copiedToRam, "the window says what to do instead of connecting (" + controller.session.notice + ")");
        if (!socketMode) {
            // What every failed connect does.
            controller.lost();
            controller.lost();
        }
        noWorkerCheck.start();
    }
    // Scenarios that also look at the page the production view shows.
    readonly property bool withView: ["error-retry", "real-preflight", "preflight-progress", "update-progress"].indexOf(scenario) >= 0
    property int counts: 0
    // Served by the real installer controller and worker (real-worker.py), not by a transcript.
    readonly property bool realWorker: scenario === "real-preflight"
    function findButton(item: var, text: string): var {
        if (!item)
            return null;
        if (item.visible && item.text === text && typeof item.clicked === "function")
            return item;
        for (const child of item.children) {
            const found = findButton(child, text);
            if (found)
                return found;
        }
        return null;
    }
    function findItem(item: var, name: string): var {
        if (!item)
            return null;
        if (item.objectName === name)
            return item;
        for (const child of item.children) {
            const found = findItem(child, name);
            if (found)
                return found;
        }
        return null;
    }
    // The error page as a person sees it: the stopped job's code and the retained log lines.
    function checkErrorPage(lastLine: string, code: string, plain: string, action: string): void {
        const page = viewLoader.item;
        check(!!page, "the error page is rendered");
        const details = findItem(page, "errorDetails");
        const toggle = findItem(page, "errorDetailsToggle");
        const codeLine = findItem(page, "errorCode");
        check(!!details && !!toggle && !!codeLine, "the error page has errorDetails, errorDetailsToggle and errorCode");
        if (!details || !toggle || !codeLine)
            return;
        check(codeLine.visible && codeLine.text === "Error code: " + code, "the error code is shown (" + codeLine.text + ")");
        check(!details.visible && toggle.visible && !toggle.checked, "details start hidden behind Show details");
        toggle.toggle();
        check(details.visible && details.text.split("\n").pop() === lastLine, "Show details reveals the retained log ending in the last line (" + JSON.stringify(details.text) + ")");
        check(details.readOnly && details.selectByMouse, "the details can be selected but not edited");
        // The plain sentence replaces the worker's message, which stays first in the details.
        const raw = controller.session.error.message;
        const sentence = findItem(page, "errorSentence");
        const actionLine = findItem(page, "errorAction");
        check(!!sentence && sentence.text === plain, "the error page shows the plain sentence, not the worker's message (" + sentence?.text + ")");
        check(!!actionLine && actionLine.visible && actionLine.text === action, "the error page shows one action (" + actionLine?.text + ")");
        check(details.text.indexOf(raw + "\n\n") === 0, "the worker's message opens the details");
    }
    function check(condition: bool, message: string): void {
        if (!condition) {
            failed = true;
            console.error("ASSERTION_FAILED " + message);
            Qt.quit();
        }
    }
    function finish(): void {
        check(clears >= plans, "password fields receive a clear signal after every plan acknowledgement");
        // The shipped controller starts with no partition changes.
        check(JSON.stringify(shipped.partitionEdits) === JSON.stringify({
            new_table: false,
            delete: [],
            create: []
        }), "the shipped controller starts with no partition changes");
        if (!failed)
            console.log("SCENARIO_OK " + scenario);
        Qt.quit();
    }
    // manual-editor (transcripts/manual-editor.json): the partition editor on editor_fixture.py's
    // disk (Windows' ESP, Windows, an old Linux, free space). The worker's replies to the plans are
    // the real planner's (mock-worker.py), so the window's partitioning is what the review checks.
    function editEditorDisk(): void {
        const GIB = 1073741824;
        controller.diskId = "/dev/vda";
        controller.mode = "manual";
        controller.step = "disk";
        test.check(controller.manualAssignments && controller.step === "disk", "the fresh inventory opens the partition editor");
        controller.next();
        test.check(controller.step === "disk", "the editor cannot continue without / and /efi");
        const disk = controller.selectedDisk;
        // Windows needs its typed DELETE; anything else is refused, and Keep it forgets the request.
        controller.deletePartition(disk.partitions[1]);
        test.check(controller.deleteRequest?.id === "/dev/vda2" && controller.partitionEdits.delete.length === 0, "deleting Windows asks for DELETE first");
        test.check(!controller.confirmDelete("delete") && controller.partitionEdits.delete.length === 0, "a wrong confirmation deletes nothing");
        controller.deleteRequest = null;
        // The old Linux goes, confirmed; its identity travels in the plan.
        controller.deletePartition(disk.partitions[2]);
        test.check(controller.confirmDelete("DELETE"), "the typed DELETE is taken");
        test.check(JSON.stringify(controller.partitionEdits.delete) === JSON.stringify([
            {
                partition_id: "/dev/vda3",
                confirm: {
                    path: "/dev/vda3",
                    start_bytes: disk.partitions[2].start_bytes,
                    size_bytes: disk.partitions[2].size_bytes,
                    system: "Linux"
                }
            }
        ]), "the deletion names the partition and its system (" + JSON.stringify(controller.partitionEdits.delete) + ")");
        let free = controller.editorRows.find(row => row.kind === "free");
        test.check(free.start === disk.partitions[2].start_bytes, "the deleted partition's space is free");
        test.check(controller.createPartition(free, "/", "btrfs", 12 * GIB, false) === "Root needs at least " + (controller.rootMinimum / GIB).toFixed(2) + " GiB.", "a too small root is refused in the window, the LUKS2 header counted until encryption is chosen");
        test.check(controller.createPartition(free, "/", "btrfs", free.size + 1048576, false).indexOf("Only ") === 0, "a partition larger than the free space is refused");
        test.check(controller.createPartition(free, "/", "btrfs", 40 * GIB, false) === "", "root is added");
        free = controller.editorRows.find(row => row.kind === "free");
        test.check(controller.createPartition(free, "/home", "ext4", 10 * GIB, true) === "", "/home is added");
        test.check(!controller.mounts.find(m => m.partition_id === "new-2").encrypt, "without an encrypted root /home stays unencrypted");
        free = controller.editorRows.find(row => row.kind === "free");
        test.check(controller.createPartition(free, "swap", "swap", 2 * GIB, false) === "", "swap is added");
        test.check(JSON.stringify(controller.editorRows.map(row => row.kind + (row.number || ""))) === JSON.stringify(["partition1", "partition2", "deleted3", "new3", "new4", "new5", "free"]), "new partitions take the free GPT entries in order (" + controller.editorRows.map(row => row.kind + (row.number || "")).join(" ") + ")");
        // Removing a new partition frees its space; keeping the deleted one drops what was made there.
        controller.removeNew("new-3");
        test.check(controller.partitionEdits.create.length === 2 && !controller.mounts.some(m => m.partition_id === "new-3"), "Remove takes a new partition and its use out of the plan");
        free = controller.editorRows.find(row => row.kind === "free" && row.start > disk.partitions[2].start_bytes);
        test.check(controller.createPartition(free, "swap", "swap", 2 * GIB, false) === "", "swap is added again");
        controller.next();
        test.check(controller.step === "disk", "still no /efi: the editor cannot continue");
        controller.assign(disk.partitions[0], "/efi", "vfat", false, false);
        test.check(controller.manualReady && controller.needsAgreement, "the layout is complete; its deletion asks for the agreement");
        controller.next();
        test.check(controller.step === "encryption", "the editor's layout continues to Encryption");
    }
    function prepare(): void {
        controller.step = "you";
        controller.diskId = "/dev/vda";
        // Planning follows an explicit disk choice; preserve manual fixtures on reprobe.
        if (!controller.mode)
            controller.mode = "erase";
        controller.encryption = "none";
        controller.login = "demo";
        controller.fullName = "Demo User";
        if (scenario === "alongside") {
            controller.mode = "alongside";
            test.check(controller.canAlongside, "verified Windows offered");
            test.check(controller.shrinkBytes === 37 * 1073741824, "half of available space by default");
            test.check(controller.steps.indexOf("filesystem") >= 0, "alongside chooses filesystem");
        }
        const secret = "ephemeral-" + Date.now();
        controller.stageAccount(secret, secret);
        check(controller.step === "software" && controller.accountPassword === secret, "account advances to software with a private in-memory password");
        if (scenario === "choices") {
            controller.software = "minimal";
            controller.encryption = "encrypted";
            controller.encryptionPassword = "separate";
            controller.diskPassword = secret;
            controller.diskConfirmation = secret;
            let refused = false;
            try {
                Object.assign(controller, {
                    hibernation: true
                });
            } catch (error) {
                refused = true;
            }
            test.check(refused && controller.hibernation === false, "hibernation cannot be enabled through controller state");
        }
        ++plans;
        controller.reviewSoftware();
        check(controller.accountPassword === "", "password dropped immediately after sending plan");
        check(controller.diskPassword === "" && controller.diskConfirmation === "", "disk secrets dropped immediately after sending plan");
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
        if (scenario === "real-erase-btrfs" && controller.session.jobId)
            test.followRecording(message);
        if (message.type === "inventory") {
            ++probes;
            test.check(controller.mounts.length === 0 && !controller.session.plan, "probe invalidates mounts and plan");
            if (scenario === "choices") {
                test.check(controller.timezone === "Europe/Berlin", "worker time zone guess preselected");
                controller.step = "timezone";
                return;
            }
            if (scenario === "manual-editor") {
                // A new probe forgets the editor's changes: the disk may not be the same any more.
                test.check(JSON.stringify(controller.partitionEdits) === JSON.stringify({
                    new_table: false,
                    delete: [],
                    create: []
                }) && !controller.deleteRequest, "a probe drops the partition changes");
                if (!controller.manualAssignments) {
                    controller.diskId = "/dev/vda";
                    controller.mode = "manual";
                    controller.step = "disk";
                    controller.next();
                    test.check(controller.session.probing && !controller.manualAssignments, "opening the editor waits for fresh inventory");
                    return;
                }
                test.editEditorDisk();
            }
            prepare();
        } else if (scenario === "choices" && message.type === "reply" && message.timezone === "Europe/Berlin") {
            test.check(controller.zoneClock() !== "—:—", "selected zone has a ticking local clock");
            controller.chooseTimezone("UTC");
            controller.next();
            test.check(controller.step === "timezone", "cannot leave before the live clock is applied");
        } else if (scenario === "choices" && message.type === "reply" && !message.ok) {
            test.check(!!controller.timezoneMessage && !controller.timezoneInfo, "live clock failure is shown");
            controller.chooseTimezone("Asia/Kathmandu");
        } else if (scenario === "choices" && message.type === "reply" && message.timezone === "Asia/Kathmandu") {
            controller.next();
            test.check(controller.step === "disk", "time zone continues to disk after success");
            prepare();
        } else if (message.type === "plan_ack") {
            test.check(controller.step === "review" && !controller.agreed, "new review needs fresh agreement");
            if (scenario === "choices")
                test.check(controller.session.plan.summary.some(line => line.indexOf("Software: Minimal") === 0), "review includes chosen software");
            if (scenario === "plan-errors") {
                controller.confirm();
                test.check(!controller.session.confirming, "errors cannot be confirmed");
                finish();
            } else if (scenario === "manual-editor" && plans === 1) {
                // The real planner reviewed the window's changes, each in plain words.
                const summary = controller.session.plan.summary;
                test.check((controller.session.plan.errors || []).length === 0 && summary.indexOf("Delete partition 3 (/dev/vda3, 40.00 GiB, ext4, Linux): all its files are lost.") >= 0 && summary.indexOf("Create partition 4: 10.00 GiB, ext4; use: /home.") >= 0 && summary.indexOf("Keep partition 1 (/dev/vda1, 0.50 GiB, vfat) and its files; use: /efi. It holds another system's startup files; its fallback loader will not be changed.") >= 0, "the planner accepts the window's partition changes (" + JSON.stringify(controller.session.plan.errors || summary) + ")");
                controller.probe(false);
                test.check(!controller.session.plan, "a new probe drops the reviewed plan");
            } else {
                controller.confirm();
                if (controller.needsAgreement)
                    test.check(!controller.session.confirming, "disk writes require checkbox");
                controller.agreed = true;
                if (!controller.session.confirming)
                    controller.confirm();
            }
        } else if (message.type === "reply" && message.code === "token_expired") {
            test.check(controller.step === "you" && !controller.session.plan, "expired confirmation returns to account");
            prepare();
        } else if (message.type === "state" && scenario === "cancel" && !controller.session.cancelPending) {
            controller.send("cancel", {});
        } else if (["preflight-progress", "update-progress"].indexOf(scenario) >= 0 && (message.type === "progress" || message.type === "state")) {
            // The install page's current phase row: the worker's count, then the busy word again.
            if (scenario === "update-progress") {
                const snapshotMark = test.findItem(viewLoader.item, "phaseMarker-snapshot");
                const snapshotStatus = test.findItem(viewLoader.item, "phaseSkipped-snapshot");
                test.check(snapshotMark?.text === "—", "a skipped snapshot has no success tick");
                test.check(snapshotStatus?.visible && snapshotStatus.text === "Skipped: snapshots need btrfs", "the skipped snapshot keeps its reason after the phase advances");
            }
            const status = test.findItem(viewLoader.item, "phaseStatus");
            const activity = message.activity;
            const words = !activity ? "" : activity.name === "signatures" ? "Checking package signatures…" : "Downloading updates…";
            const expected = activity ? words + (activity.total ? " " + activity.done + " of " + activity.total : "") : message.indeterminate ? "Working…" : message.phase_pct + "%";
            test.check(controller.step === "install" && !!status && status.visible && status.text === expected, "the phase row shows " + expected + " (" + status?.text + ")");
            const stepLine = test.findItem(viewLoader.item, "installStep");
            const stepText = message.step?.text || "";
            test.check(!!stepLine && stepLine.text === stepText && stepLine.visible === !!stepText, "the running step appears below the bar and clears at its boundary");
            if (activity)
                ++test.counts;
        } else if (message.type === "error") {
            if (scenario === "error-retry") {
                test.check(controller.step === "error", "worker error shown");
                test.checkErrorPage("pacstrap: exit 1", "command_failed", "A program the installer runs stopped with an error.", "Save the log below; it records what the installer did.");
                controller.retry();
            } else if (scenario === "cancel") {
                test.check(controller.session.cancelMessage.indexOf("not been undone") >= 0, "cancel_ack shown");
                finish();
            } else if (scenario === "real-preflight") {
                // The worker's own event: the repository preflight fails before any disk write.
                console.log("REAL_WORKER_ERROR " + JSON.stringify({
                    code: message.code,
                    phase: message.phase,
                    retryable: message.retryable,
                    message: message.message,
                    logs: controller.session.logs
                }));
                test.check(controller.step === "error" && message.code === "offline_repo_incomplete" && message.phase === "prepare_disk", "the real worker's preflight failure is shown (" + controller.step + ", " + message.code + ")");
                test.check(message.retryable === true, "the real worker sends a failed preflight as retryable");
                test.check(controller.session.logs.length > 0 && controller.session.logs[controller.session.logs.length - 1] === "pacman: exit 1", "the worker's log lines arrived (" + controller.session.logs.slice(-1) + ")");
                test.checkErrorPage("pacman: exit 1", "offline_repo_incomplete", "Some packages on the USB stick are missing or could not be verified.", "Check that the USB stick is still connected.");
                const again = test.findButton(viewLoader.item, "Try again");
                test.check(!!again && again.enabled, "the page offers Try again");
                if (!again)
                    return;
                again.clicked();
                test.check(controller.step === "disk" && !controller.session.error && controller.session.probing, "Try again returns to the disk step and probes again");
                finish();
            }
        } else if (message.type === "done") {
            test.check(controller.step === "done" && !controller.session.running, "done reached");
            if (scenario === "real-erase-btrfs")
                test.checkRecording(message);
            if (scenario === "disconnect-resume")
                test.check(controller.session.logs.length === 2, "missed log replayed once");
            if (scenario === "preflight-progress" || scenario === "update-progress")
                test.check(test.counts === (scenario === "preflight-progress" ? 3 : 4), "every count reached the page (" + test.counts + ")");
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
        catalog: ({
                layouts: [],
                zones: [],
                trial: false,
                output_scales: {
                    "eDP-1": 1.25,
                    "HDMI-A-1": 1.5
                }
            })
        onClearPasswords: ++test.clears
        onOutbound: message => {
            if (message.type === "plan") {
                test.check(JSON.stringify(message.config.output_scales) === JSON.stringify(controller.catalog.output_scales), "plan keeps both fractional session output scales");
                test.check(message.config.software === (test.scenario === "choices" ? "minimal" : "rich"), "plan carries software selection");
                test.check(message.config.encryption === (test.scenario === "choices" ? "separate" : "none"), "plan carries explicit encryption choice");
                test.check(message.config.hibernation === false, "every UI plan explicitly disables hibernation");
                if (test.scenario === "choices")
                    test.check(!!message.config.disk_password, "plan carries separate password");
                test.check(JSON.stringify(controller.session).indexOf(message.config.user.password) === -1, "shared state never contains secrets");
                if (test.scenario === "alongside") {
                    test.check(message.config.partition_id === "/dev/vda2", "alongside partition ID");
                    test.check(message.config.shrink_bytes === 37 * 1073741824, "shrink_bytes means bytes for Emaki");
                    test.check(message.config.fs === "btrfs", "alongside filesystem");
                }
            }
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
    // The production defaults, never started: no transport, no helpers.
    UI.InstallerController {
        id: shipped
        mockTransport: true
        helpersEnabled: false
    }
    FloatingWindow {
        implicitWidth: 960
        implicitHeight: 640
        visible: test.withView
        color: "#fff8f3"
        Loader {
            id: viewLoader
            anchors.fill: parent
            active: test.withView
            sourceComponent: Component {
                UI.InstallerView {
                    controller: controller
                }
            }
        }
    }
    // Qt.quit() from the root's own onCompleted is ignored (GOTCHAS): a failed check must quit.
    Component.onCompleted: if (noWorker)
        Qt.callLater(withoutWorker)
    Timer {
        id: noWorkerCheck
        interval: 1600
        onTriggered: {
            test.check(controller.session.notice === test.copiedToRam && !controller.session.ready && !controller.session.greeted, "failed connects keep the sentence (" + controller.session.notice + ")");
            if (test.socketMode)
                test.check(controller.retryDelay > 500, "the real socket failed and reconnected (" + controller.retryDelay + ")");
            test.finish();
        }
    }
    Process {
        id: mock
        running: !test.socketMode && !test.noWorker
        command: test.realWorker ? ["python3", "-I", "-B", Qt.resolvedUrl("real-worker.py").toString().replace("file://", "")] : ["python3", "-I", "-B", Qt.resolvedUrl("mock-worker.py").toString().replace("file://", ""), "--stdio", "--scenario", test.scenario, "--delay", "0"]
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
