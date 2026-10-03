pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io
import "Protocol.js" as Protocol

Item {
    id: root
    property var session: Protocol.initial()
    property string step: "welcome"
    property string mode: "erase"
    property string diskId: ""
    property string filesystem: "btrfs"
    property var mounts: []
    property var diskReasons: ({})
    property var layouts: ["us"]
    property string fullName: ""
    property string login: ""
    property bool loginEdited: false
    property string hostname: "emaki"
    property string timezone: "UTC"
    property bool timezoneEdited: false
    property bool onlineUpdate: true
    property bool agreed: false
    property var catalog: ({
            layouts: [],
            zones: ["UTC"],
            trial: false
        })
    property var network: ({
            networks: [],
            wired: false
        })
    property var media: []
    property string helperMessage: ""
    property bool helperFailed: false
    property string helperOp: ""
    property string helperInput: ""
    property bool manualAssignments: false
    property bool gpartedWarning: false
    property bool editorPending: false
    property var partitionEditorCommand: ["sudo", "-n", "gparted"]
    property real clockMs: Date.now()
    property int retryDelay: 500
    // Test transport is injected only by the separate tests entry point.
    property bool mockTransport: false
    property bool helpersEnabled: true
    signal outbound(var message)
    signal messageReceived(var message)
    signal clearPasswords
    readonly property bool locked: session.running || session.confirming || session.planning || session.probing || gparted.running
    readonly property bool helperBusy: helper.running
    readonly property bool partitioning: gparted.running
    // Set by the Socket itself: a local socket can connect synchronously while the Loader is
    // still creating it, before wire.item exists, and the hello sent then would be dropped.
    property Socket transport: null
    readonly property var selectedDisk: session.inventory ? session.inventory.disks.find(d => d.id === diskId) || null : null
    readonly property bool canAlongside: Protocol.alongside(selectedDisk)
    readonly property var steps: mode !== "erase" ? ["welcome", "keyboard", "network", "disk", "you", "review", "install", "done"] : ["welcome", "keyboard", "network", "disk", "filesystem", "you", "review", "install", "done"]
    readonly property int elapsed: Math.max(0, Math.floor((clockMs - session.started) / 1000))

    function publish(): void {
        session = Object.assign({}, session);
    }
    function write(message: var): void {
        if (mockTransport)
            outbound(message);
        else if (transport && transport.connected) {
            transport.write(JSON.stringify(message) + "\n");
            transport.flush();
        }
    }
    function send(type: string, fields: var): void {
        if (!session.ready && type !== "hello")
            return;
        write(Protocol.request(session, type, fields));
        publish();
    }
    function receive(message: var): void {
        const wasPlanning = session.planning;
        const wasConfirming = session.confirming;
        const replies = Protocol.receive(session, message, Date.now());
        publish();
        replies.forEach(m => write(m));
        if (message.type === "plan_ack" && wasPlanning) {
            clearPasswords();
            agreed = false;
            if (session.plan)
                step = "review";
            const refusal = (message.errors || []).find(e => ["boot_medium", "disk_busy", "disk_too_small", "unsafe_disk", "disk_not_found"].indexOf(e.code) >= 0);
            if (refusal) {
                const reasons = Object.assign({}, diskReasons);
                reasons[diskId] = refusal.msg;
                diskReasons = reasons;
            }
        }
        if (message.type === "inventory" && session.inventory) {
            if (!timezoneEdited && message.tz_guess)
                timezone = message.tz_guess;
            if (!selectedDisk)
                diskId = "";
            mounts = [];
            diskReasons = ({});
        }
        if (message.type === "reply" && !message.ok && wasConfirming)
            step = session.plan ? "review" : "you";
        if (message.type === "hello" && wasConfirming && !session.running && !session.jobId)
            step = "you";
        if (session.running || session.confirming)
            step = "install";
        else if (session.outcome)
            step = session.outcome;
        if (message.type === "reply" && !message.ok && wasPlanning)
            clearPasswords();
        if (step === "done" || step === "error")
            callHelper("media", {});
        messageReceived(message);
    }
    function lost(): void {
        Protocol.disconnected(session);
        publish();
        clearPasswords();
        agreed = false;
        if (step === "review")
            step = "you";
        if (!mockTransport)
            Qt.callLater(function () {
                wire.active = false;
                reconnect.restart();
            });
    }
    function probe(): void {
        if (session.running || session.confirming || session.planning || session.probing || partitioning)
            return;
        agreed = false;
        mounts = [];
        send("probe", {});
    }
    function edit(target: string): void {
        if (locked || session.outcome)
            return;
        Protocol.invalidate(session);
        agreed = false;
        publish();
        clearPasswords();
        step = target;
    }
    function next(): void {
        if (locked)
            return;
        if (step === "disk" && mode === "manual" && !manualAssignments) {
            manualAssignments = true;
            return;
        }
        const index = steps.indexOf(step);
        if (index >= 0 && index < steps.indexOf("you"))
            step = steps[index + 1];
    }
    function back(): void {
        if (locked)
            return;
        edit(steps[Math.max(0, steps.indexOf(step) - 1)]);
    }
    function chooseDisk(id: string): void {
        if (locked)
            return;
        diskId = id;
        mounts = [];
        agreed = false;
        manualAssignments = false;
        Protocol.invalidate(session);
        publish();
    }
    function toggleLayout(layout: string): void {
        const list = layouts.slice();
        const index = list.indexOf(layout);
        if (index >= 0 && list.length > 1)
            list.splice(index, 1);
        else if (index < 0 && list.length < 4)
            list.push(layout);
        layouts = list;
    }
    function defaultLayout(layout: string): void {
        layouts = [layout].concat(layouts.filter(x => x !== layout));
    }
    function assign(partition: var, mountpoint: string, fs: string, format: bool): void {
        mounts = mounts.filter(m => m.partition_id !== partition.id).concat(mountpoint === "none" ? [] : [
            {
                partition_id: partition.id,
                mountpoint: mountpoint,
                fs: fs,
                format: format,
                subvolume: null
            }
        ]);
    }
    function plan(password: string, confirmation: string): void {
        if (locked || !session.ready)
            return;
        const error = Protocol.accountError(fullName, login, password, confirmation, hostname);
        if (error) {
            helperMessage = error;
            helperFailed = true;
            return;
        }
        helperMessage = "";
        const config = {
            mode: mode,
            disk_id: diskId,
            fs: filesystem,
            layouts: layouts.slice(),
            user: {
                name: fullName,
                login: login,
                password: password
            },
            hostname: hostname,
            timezone: timezone,
            online_update: onlineUpdate
        };
        if (mode === "manual")
            config.mounts = mounts;
        send("plan", {
            config: config
        });
        config.user.password = "";
    }
    function confirm(): void {
        if (locked || !session.ready || !session.plan || !session.plan.token || (mode === "erase" && !agreed))
            return;
        if (Protocol.expire(session, Date.now())) {
            publish();
            step = "you";
            return;
        }
        send("confirm", {
            plan_id: session.plan.plan_id,
            token: session.plan.token
        });
        step = "install";
    }
    function retry(): void {
        if (!session.error || !session.error.retryable || session.running)
            return;
        session.outcome = "";
        session.error = null;
        session.jobId = "";
        session.cancelMessage = "";
        session.notice = "Check the disk and enter your password again for a new review.";
        publish();
        step = "disk";
        probe();
    }
    function openGparted(): void {
        if (locked || !gpartedWarning || !session.ready)
            return;
        Protocol.invalidate(session);
        publish();
        agreed = false;
        mounts = [];
        gpartedWarning = false;
        editorPending = true;
        gparted.running = true;
    }
    function gpartedExited(code: int): void {
        manualAssignments = true;
        helperMessage = code === 0 ? "Assign the partitions below. The disk list has been refreshed." : "GParted could not complete successfully. The disk list has been refreshed.";
        helperFailed = code !== 0;
        probe();
    }
    function callHelper(op: string, fields: var): void {
        if (helper.running || mockTransport || !helpersEnabled)
            return;
        helperOp = op;
        helperInput = JSON.stringify(Object.assign({
            op: op
        }, fields)) + "\n";
        helper.running = true;
    }
    function helperResult(result: var): void {
        helperFailed = !result.ok;
        if (helperOp === "catalog" && result.ok)
            catalog = result;
        else if (helperOp === "network")
            network = result;
        else if (helperOp === "media")
            media = result.media || [];
        else if (helperOp === "join" && result.ok)
            probe();
        if (result.message)
            helperMessage = result.message;
        else if (helperOp !== "network")
            helperMessage = "";
    }
    function saveLog(index: int): void {
        if (index < 0 || index >= media.length)
            return;
        send("save_log", {
            dest: media[index] + "/emaki-install-" + Date.now() + ".log"
        });
    }
    onStepChanged: {
        helperMessage = "";
        if (step === "network")
            callHelper("network", {});
        if (step === "done" || step === "error")
            callHelper("media", {});
    }
    Component.onCompleted: if (!mockTransport)
        callHelper("catalog", {})
    Timer {
        interval: 1000
        running: true
        repeat: true
        onTriggered: {
            root.clockMs = Date.now();
            if (Protocol.expire(root.session, root.clockMs)) {
                root.agreed = false;
                root.publish();
                if (root.step === "review")
                    root.step = "you";
            }
        }
    }
    Timer {
        id: reconnect
        interval: root.retryDelay
        onTriggered: {
            root.retryDelay = Math.min(8000, root.retryDelay * 2);
            wire.active = true;
        }
    }
    Loader {
        id: wire
        active: !root.mockTransport
        sourceComponent: Component {
            Socket {
                id: socket
                path: Quickshell.env("EMAKI_INSTALLER_SOCKET") || "/run/emaki-installer/sock"
                connected: true
                Component.onDestruction: {
                    if (root.transport === socket)
                        root.transport = null;
                }
                onConnectionStateChanged: {
                    if (connected) {
                        root.transport = socket;
                        root.retryDelay = 500;
                        root.send("hello", {
                            proto: 1
                        });
                    } else
                        root.lost();
                }
                onError: root.lost()
                parser: SplitParser {
                    onRead: data => {
                        try {
                            root.receive(JSON.parse(data));
                        } catch (_) {
                            root.session.notice = "The worker sent an unreadable message.";
                            root.lost();
                        }
                    }
                }
            }
        }
    }
    Process {
        id: helper
        command: ["python3", "-I", "-B", Qt.resolvedUrl("ui-helper.py").toString().replace("file://", "")]
        stdinEnabled: true
        onStarted: {
            write(root.helperInput);
            root.helperInput = "";
        }
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    root.helperResult(JSON.parse(text));
                } catch (_) {
                    root.helperMessage = "This operation is unavailable. Please try again.";
                    root.helperFailed = true;
                }
            }
        }
        onExited: {
            root.helperInput = "";
            if (root.helperOp === "join")
                Qt.callLater(function () {
                    root.callHelper("network", {});
                });
        }
        onRunningChanged: if (!running)
            root.helperInput = ""
    }
    Process {
        id: gparted
        command: root.partitionEditorCommand
        onExited: code => {
            root.editorPending = false;
            // Process emits exited before runningChanged in Quickshell 0.3.1.
            // Defer until the cached partitioning/locked bindings are updated.
            Qt.callLater(function () {
                root.gpartedExited(code);
            });
        }
        onRunningChanged: {
            if (!running && root.editorPending) {
                root.editorPending = false;
                Qt.callLater(function () {
                    root.gpartedExited(-1);
                });
            }
        }
    }
}
