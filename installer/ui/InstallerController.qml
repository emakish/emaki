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
    property real shrinkBytes: 0
    property var mounts: []
    property var diskReasons: ({})
    property var layouts: ["us"]
    property string fullName: ""
    property string login: ""
    property bool loginEdited: false
    property bool focusLogin: false
    property string hostname: "emaki"
    property string timezone: "UTC"
    property bool timezoneEdited: false
    property bool timezoneGuessPending: false
    property bool timezoneGuessOffline: false
    property bool timezoneGuessRequest: false
    readonly property bool timezoneChosen: timezoneEdited || !!session.inventory?.tz_guess
    property string joinedWifiUuid: ""
    property bool removeUsbPrompt: false
    property string applyingTimezone: ""
    property var timezoneInfo: null
    property string timezoneMessage: ""
    property string software: "rich"
    property string encryption: ""
    property string encryptionPassword: "account"
    property string diskPassword: ""
    property string diskConfirmation: ""
    property bool hibernation: false
    // The disk is unlocked at startup in this layout (render.unlock_layout(layouts, 'grub')).
    readonly property string unlockLayout: Protocol.unlockLayout(layouts)
    // One password for everything is typed at the startup prompt in the unlock layout and at the
    // login screen in the first layout: the same keys give the same password only when they agree.
    readonly property bool accountUnlocks: layouts[0] === unlockLayout
    readonly property bool encryptionReady: encryption === "none" || (encryption === "encrypted" && ((encryptionPassword === "account" && accountUnlocks) || (encryptionPassword === "separate" && !Protocol.diskPasswordError(diskPassword) && diskPassword === diskConfirmation)))
    // Until encryption is chosen the header is counted: the number shown never grows later.
    readonly property real rootMinimum: Protocol.rootMinimum(session.inventory?.memory_bytes || 0, hibernation, encryption !== "none")
    readonly property bool manualReady: mounts.filter(m => m.mountpoint === "/").length === 1 && mounts.filter(m => m.mountpoint === "/efi").length === 1
    // Kept only between You and Software; never passed to the shared protocol state.
    property string accountPassword: ""
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
    // A join is followed by a scan; that scan must not replace the join's result.
    property string joinMessage: ""
    property bool joinFailed: false
    property string helperOp: ""
    property string helperInput: ""
    // The live session types in the chosen layouts before any password is typed: each change of
    // the wanted list is written for niri (the helper's trial) and read back from niri itself.
    // A password field takes input only while niri runs the list it needs with that list's
    // first layout active (keyboardReady); a write niri never loads is never typed through.
    // A newer list replaces one still waiting for the keyboard helper; nothing is dropped while
    // it is busy.
    readonly property bool liveKeyboard: catalog.trial === true && (mockTransport || helpersEnabled)
    // Set by the view while a password field has focus: "unlock" for a disk passphrase, "secret" for
    // the account password, "plain" for a password used now (Wi-Fi), which no layout rule binds.
    property string secretFocus: ""
    readonly property var wantedLayouts: secretFocus === "unlock" ? [unlockLayout] : layouts
    // The list niri reported last, as layout names; every report replaces it.
    readonly property var liveLayouts: liveCodes.map(code => code.toLowerCase())
    property var keyboardTarget: null
    property var keyboardPending: null
    property var keyboardSent: null
    property real keyboardDeadline: 0
    // niri's config watcher can miss a write made while it parses the previous one
    // (src/utils/watcher.rs): an unconfirmed list is written again before the switch counts as failed.
    property int keyboardRewrites: 0
    property int keyboardMaxRewrites: 2
    property bool keyboardBusy: false
    property string keyboardOp: ""
    property string keyboardInput: ""
    property int keyboardSerial: 0
    property bool keyboardFailed: false
    // The session's niri never reads the written list (the stock niri session): only a list it
    // already runs can be typed in.
    property bool keyboardUnavailable: false
    property int keyboardConfirmMs: 2000
    property var liveCodes: []
    property int liveIndex: -1
    // Caps Lock, from the same reports (the keyboard LED, as on the lock screen): current while a
    // password field has focus.
    property bool capsLock: false
    // Num Lock, from the same reports: niri starts every session (the login screen too) with it off.
    property bool numLock: false
    readonly property string liveCode: liveIndex >= 0 && liveIndex < liveCodes.length ? liveCodes[liveIndex] : ""
    // A password field that takes focus wants niri's first layout active: the login screen
    // starts in it, and Super+Space may have been pressed before (switch-layout 0, not a reload).
    property bool keyboardWantFirst: false
    property bool keyboardFirstPending: false
    property bool keyboardFirstSent: false
    readonly property bool keyboardSwitching: !!keyboardPending || !!keyboardSent || keyboardFirstPending || keyboardFirstSent
    readonly property bool keyboardReady: !liveKeyboard || (!keyboardSwitching && Protocol.sameLayouts(liveLayouts, wantedLayouts) && liveIndex === 0)
    // Every key typed in a password field is checked by the next report from niri: a key typed
    // in another layout than the field needs (Super+Space a moment before, a late reload) was
    // already taken, so the page's password fields are emptied. Continue waits for the check.
    property int secretEdits: 0
    property int secretChecked: 0
    property var secretNeed: []
    property int keyboardReadCovers: 0
    property bool keyboardReadAgain: false
    property bool keyboardDiscarded: false
    // A report reads niri after the key: Super+Space, a key and Super+Space back inside one
    // round trip (23-31 ms) look right in it. niri's event stream counts every switch; one counted
    // since the oldest unchecked key fails that key's check as well.
    property int layoutSwitches: 0
    property int secretSwitchMark: 0
    readonly property bool secretsChecked: !liveKeyboard || secretChecked >= secretEdits
    signal secretsDiscarded
    // A password field that checks the layout takes no pasted text (InstallerView.PasswordField):
    // nothing says the login screen's keys type it. Set when a paste was refused there or when
    // one edit brought more than one character; the next typed key ends it.
    property bool secretPasteRefused: false
    // The step shows the account password fields, or the disk password fields.
    readonly property bool passwordPage: step === "you" || (step === "encryption" && encryption === "encrypted" && encryptionPassword === "separate")
    readonly property string keyboardMessage: {
        const parts = secretPasteRefused ? ["Pasted text cannot be used for this password. Type it key by key."] : [];
        if (!liveKeyboard)
            return parts.join(" ");
        if (keyboardDiscarded)
            parts.push("The keyboard layout changed while you typed. Type the password again.");
        // Only where the account or disk password fields are: the Wi-Fi password takes any layout.
        if (!keyboardSwitching && !keyboardReady && passwordPage) {
            if (keyboardUnavailable)
                parts.push("This session cannot switch the keyboard to " + layoutNames(wantedLayouts) + ", so passwords cannot be typed here.");
            else if (keyboardFailed)
                parts.push("The keyboard could not be switched to " + layoutNames(wantedLayouts) + ", so passwords cannot be typed yet.");
            else if (secretFocus === "secret" && Protocol.sameLayouts(liveLayouts, wantedLayouts) && liveIndex !== 0) {
                const first = layoutNames([wantedLayouts[0]]);
                parts.push("The login screen starts in " + first + ", so type the password in " + first + ". Super+Space switches back.");
            }
        }
        return parts.join(" ");
    }
    signal keyboardOutbound(var request)
    property bool manualAssignments: false
    property bool gpartedWarning: false
    property bool editorPending: false
    // Open GParted waits for a fresh probe: the disk list can be minutes old, and a replugged disk
    // can take the chosen disk's /dev name. editorDisk is the chosen disk as the list showed it.
    property bool editorProbing: false
    property var editorDisk: null
    // The device GParted is given: the chosen disk's path, checked against that probe.
    property string editorPath: ""
    // sudo resets the environment: without the session's Wayland display GParted cannot open a window.
    property var partitionEditorCommand: ["sudo", "-n", "--preserve-env=WAYLAND_DISPLAY,XDG_RUNTIME_DIR", "gparted"]
    // GParted shows only the devices it is given; without one it opens on the first disk, the live stick.
    readonly property var partitionEditorArgv: partitionEditorCommand.concat(editorPath ? [editorPath] : [])
    property real clockMs: Date.now()
    property int retryDelay: 500
    // Test transport is injected only by the separate tests entry point.
    property bool mockTransport: false
    property bool helpersEnabled: true
    signal outbound(var message)
    signal messageReceived(var message)
    signal clearPasswords
    onClearPasswords: {
        accountPassword = "";
        diskPassword = "";
        diskConfirmation = "";
    }
    readonly property bool locked: session.running || session.confirming || session.planning || session.probing || gparted.running
    readonly property bool helperBusy: helper.running
    readonly property bool partitioning: gparted.running
    // Set by the Socket itself: a local socket can connect synchronously while the Loader is
    // still creating it, before wire.item exists, and the hello sent then would be dropped.
    property Socket transport: null
    readonly property var selectedDisk: session.inventory ? session.inventory.disks.find(d => d.id === diskId) || null : null
    readonly property var encryptedTargets: {
        const nodes = selectedDisk?.closed_encrypted || [];
        if (mode === "erase")
            return nodes;
        if (mode !== "manual")
            return [];
        const paths = mounts.filter(m => m.format).map(m => selectedDisk.partitions.find(p => p.id === m.partition_id)?.path);
        return nodes.filter(node => paths.indexOf(node.path) >= 0);
    }
    readonly property string encryptedIdentity: JSON.stringify([diskId, mode, mounts, encryptedTargets])
    property string encryptedEraseText: ""
    onEncryptedIdentityChanged: encryptedEraseText = ""
    readonly property string encryptedRefusal: encryptedTargets.find(node => !node.uuid)?.warning || ""
    readonly property bool encryptedConfirmed: !encryptedTargets.length || (!encryptedRefusal && encryptedEraseText === "ERASE")
    readonly property var confirmedEncrypted: encryptedConfirmed ? encryptedTargets.map(node => ({
                path: node.path,
                type: node.type,
                uuid: node.uuid
            })) : []
    readonly property bool canAlongside: Protocol.alongside(selectedDisk)
    readonly property var windowsPartition: Protocol.alongsidePartition(selectedDisk)
    readonly property real alongsideMinimum: Math.max(32 * 1073741824, hibernation ? 20 * 1073741824 + (session.inventory?.memory_bytes || 0) + 16 * 1048576 : 0)
    // canAlongside and windowsPartition are separate bindings: either can be updated first.
    readonly property bool alongsideSizeValid: canAlongside && !!windowsPartition && shrinkBytes >= alongsideMinimum && shrinkBytes <= windowsPartition.shrink.max_free_bytes
    onAlongsideMinimumChanged: {
        Qt.callLater(function () {
            if (root.windowsPartition && root.shrinkBytes < root.alongsideMinimum)
                root.shrinkBytes = Math.min(root.alongsideMinimum, root.windowsPartition.shrink.max_free_bytes);
        });
    }
    readonly property bool needsAgreement: mode === "erase" || mode === "alongside"
    readonly property var steps: mode === "manual" ? ["welcome", "keyboard", "network", "timezone", "disk", "encryption", "you", "software", "review", "install", "done"] : ["welcome", "keyboard", "network", "timezone", "disk", "filesystem", "encryption", "you", "software", "review", "install", "done"]
    readonly property int elapsed: Math.max(0, Math.floor((clockMs - session.started) / 1000))

    function publish(): void {
        session = Object.assign({}, session);
    }
    function defaultAlongsideSize(): real {
        return windowsPartition ? Math.min(windowsPartition.shrink.max_free_bytes, Math.max(alongsideMinimum, Protocol.alongsideDefault(windowsPartition))) : 0;
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
        const guessReply = session.pending[message.for_id || message.id] === "get_timezone_guess";
        const rebootPrepared = session.pending[message.for_id || message.id] === "prepare_reboot";
        const timezoneReply = session.pending[message.for_id || message.id] === "set_timezone";
        // Only one probe is in flight at a time: this answers the one Open GParted waits for.
        const editorReply = editorProbing && session.pending[message.for_id || message.id] === "probe";
        const wasPlanning = session.planning;
        const wasConfirming = session.confirming;
        const replies = Protocol.receive(session, message, Date.now());
        publish();
        replies.forEach(m => write(m));
        if (message.type === "hello")
            timezoneGuessPending = message.timezone_guess === true;
        if (guessReply && message.type === "reply") {
            timezoneGuessRequest = false;
            timezoneGuessPending = message.ok && message.pending === true;
            timezoneGuessOffline = message.ok && message.offline === true;
            if (message.ok)
                acceptTimezoneGuess(message.tz_guess);
        }
        if (rebootPrepared && message.type === "reply" && message.ok) {
            if (catalog.boot_removable === true)
                removeUsbPrompt = true;
            else
                reboot();
        }
        if (timezoneReply && message.type === "reply") {
            const applied = applyingTimezone;
            applyingTimezone = "";
            if (message.ok && message.timezone === timezone) {
                timezoneInfo = Object.assign({
                    receivedMs: Date.now()
                }, message);
                timezoneMessage = "";
            } else if (!message.ok && applied === timezone) {
                timezoneMessage = message.msg || "Could not change the live clock. Try again.";
            }
            if (applied !== timezone)
                applyTimezone();
        }
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
            acceptTimezoneGuess(message.tz_guess);
            if (!selectedDisk)
                diskId = "";
            mounts = [];
            diskReasons = ({});
            if (step === "timezone")
                applyTimezone();
            shrinkBytes = defaultAlongsideSize();
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
        if (editorReply)
            startEditor(message.type === "inventory");
        messageReceived(message);
    }
    function lost(): void {
        timezoneGuessPending = false;
        timezoneGuessRequest = false;
        applyingTimezone = "";
        timezoneInfo = null;
        editorProbing = false;
        editorDisk = null;
        Protocol.disconnected(session);
        publish();
        clearPasswords();
        agreed = false;
        if (step === "review" || step === "software")
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
        if (step === "encryption" && (!encryptionReady || !secretsChecked))
            return;
        if (step === "timezone" && (!timezoneChosen || !timezoneInfo || timezoneInfo.timezone !== timezone || applyingTimezone))
            return;
        if (step === "disk" && !encryptedConfirmed)
            return;
        if (step === "disk" && mode === "alongside" && !alongsideSizeValid)
            return;
        if (step === "disk" && mode === "manual" && Protocol.manualReason(selectedDisk))
            return;
        if (step === "disk" && mode === "manual" && !manualAssignments) {
            manualAssignments = true;
            return;
        }
        if (step === "disk" && mode === "manual" && !manualReady)
            return;
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
        // A disk without a Windows offer cannot keep Install alongside.
        if (mode === "alongside" && !Protocol.alongside(selectedDisk))
            mode = "erase";
        shrinkBytes = defaultAlongsideSize();
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
    function layoutLabel(code: string): string {
        const row = catalog.layouts.find(x => !x.variant && x.layout.toUpperCase() === code);
        return row ? row.label : code;
    }
    // "German", "German and French", "German, French and Czech".
    function layoutNames(list: var): string {
        const names = Array.from(list).map(layout => layoutLabel(String(layout).toUpperCase()));
        return names.length > 1 ? names.slice(0, -1).join(", ") + " and " + names[names.length - 1] : names.join("");
    }
    // Queue the wanted list unless it is already the one sent, confirmed or refused.
    function syncKeyboard(): void {
        if (liveKeyboard && !Protocol.sameLayouts(keyboardTarget || [], wantedLayouts))
            applyLayouts();
    }
    // Also "Try again": every write makes niri reload the file.
    function applyLayouts(): void {
        if (!liveKeyboard)
            return;
        keyboardTarget = wantedLayouts.slice();
        keyboardPending = keyboardTarget;
        keyboardRewrites = 0;
        keyboardWantFirst = true;
        pumpKeyboard();
    }
    // A password field took focus.
    function secretEntered(): void {
        if (!liveKeyboard)
            return;
        keyboardWantFirst = true;
        // Without a pointer, focusing the field again is how a failed switch is tried again.
        if (keyboardFailed && !keyboardUnavailable && !keyboardSwitching)
            applyLayouts();
        else
            readLayouts();
    }
    // A key was typed (or a character deleted) in a password field.
    function secretEdited(): void {
        secretPasteRefused = false;
        if (!liveKeyboard)
            return;
        if (secretChecked >= secretEdits)
            secretSwitchMark = layoutSwitches;
        ++secretEdits;
        secretNeed = wantedLayouts.slice();
        keyboardDiscarded = false;
        readLayouts();
    }
    // niri's event stream reported a change of the layouts or of the active one (ui-helper
    // layout_events).
    function keyboardSwitched(): void {
        ++layoutSwitches;
    }
    // A paste was refused in a password field, or text came in that was not one typed key (a
    // paste route the field does not catch, an input method's string): with inserted, the
    // page's password fields are emptied, as after a layout change.
    function secretPasted(inserted: bool): void {
        secretPasteRefused = true;
        if (!inserted)
            return;
        if (step === "encryption") {
            diskPassword = "";
            diskConfirmation = "";
        }
        secretsDiscarded();
    }
    function pumpKeyboard(): void {
        if (keyboardBusy)
            return;
        // Keys waiting for their check go first: a write or a switch changes what niri reports.
        if (keyboardReadAgain && secretEdits > secretChecked)
            readLayouts();
        else if (keyboardPending) {
            keyboardSent = keyboardPending;
            keyboardPending = null;
            keyboardFailed = false;
            keyboardUnavailable = false;
            keyboardDeadline = 0;
            sendKeyboard({
                op: "trial",
                layouts: keyboardSent
            });
        } else if (keyboardFirstPending) {
            keyboardFirstPending = false;
            keyboardFirstSent = true;
            sendKeyboard({
                op: "first_layout"
            });
        } else if (keyboardReadAgain)
            readLayouts();
        else if (keyboardSent && keyboardDeadline)
            keyboardPoll.restart();
    }
    function sendKeyboard(request: var): void {
        keyboardBusy = true;
        keyboardOp = request.op;
        ++keyboardSerial;
        if (mockTransport)
            keyboardOutbound(request);
        else {
            keyboardInput = JSON.stringify(request) + "\n";
            keyboardHelper.running = true;
        }
    }
    function readLayouts(): void {
        if (!liveKeyboard)
            return;
        if (keyboardBusy) {
            keyboardReadAgain = true;
            return;
        }
        keyboardReadAgain = false;
        keyboardReadCovers = secretEdits;
        sendKeyboard({
            op: "layout_state"
        });
    }
    function keyboardReceive(result: var): void {
        if (!keyboardBusy)
            return;
        keyboardBusy = false;
        const ok = !!result && result.ok === true;
        if (keyboardOp === "trial") {
            if (ok)
                keyboardDeadline = Date.now() + keyboardConfirmMs;
            else {
                keyboardFailed = true;
                keyboardUnavailable = !!result && result.reloads === false;
                keyboardSent = null;
            }
        } else if (keyboardOp === "first_layout") {
            keyboardFirstSent = false;
            keyboardReadAgain = true;
        } else if (keyboardOp === "layout_state") {
            const codes = ok && Array.isArray(result.codes) ? result.codes.map(code => String(code)) : [];
            liveCodes = codes;
            liveIndex = ok && Number.isInteger(result.current) ? result.current : -1;
            capsLock = ok && result.caps === true;
            numLock = ok && result.num === true;
            if (keyboardSent && keyboardDeadline) {
                if (Protocol.sameLayouts(liveLayouts, keyboardSent)) {
                    keyboardSent = null;
                    keyboardDeadline = 0;
                } else if (Date.now() >= keyboardDeadline) {
                    if (keyboardRewrites < keyboardMaxRewrites) {
                        ++keyboardRewrites;
                        keyboardPending = keyboardSent;
                    } else
                        keyboardFailed = true;
                    keyboardSent = null;
                    keyboardDeadline = 0;
                }
            } else if (keyboardFailed && !keyboardPending && keyboardTarget && Protocol.sameLayouts(liveLayouts, keyboardTarget)) {
                // niri runs the list after all (a late reload, or the list it already ran).
                keyboardFailed = false;
                keyboardUnavailable = false;
            } else if (!keyboardFailed && !keyboardPending && keyboardTarget && !Protocol.sameLayouts(liveLayouts, keyboardTarget)) {
                // niri dropped the confirmed list (an older write loaded late): write it again.
                keyboardPending = keyboardTarget;
                keyboardRewrites = 0;
            }
            // The keys typed before this report was asked for were typed in what it reports (or
            // in something that changed since): keep them only if that is what the field needs.
            if (keyboardReadCovers > secretChecked) {
                if (ok && Protocol.sameLayouts(liveLayouts, secretNeed) && liveIndex === 0 && layoutSwitches === secretSwitchMark)
                    secretChecked = keyboardReadCovers;
                else {
                    secretChecked = secretEdits;
                    keyboardDiscarded = true;
                    if (step === "encryption") {
                        diskPassword = "";
                        diskConfirmation = "";
                    }
                    secretsDiscarded();
                }
            }
            if (secretEdits > secretChecked)
                keyboardReadAgain = true;
            if (keyboardWantFirst && !keyboardPending && !keyboardSent && keyboardTarget && Protocol.sameLayouts(liveLayouts, keyboardTarget)) {
                keyboardWantFirst = false;
                keyboardFirstPending = liveIndex !== 0;
            }
        }
        pumpKeyboard();
        // After a refused trial, read which layout the person is really typing in.
        if (!keyboardBusy && keyboardFailed && keyboardOp === "trial")
            readLayouts();
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
    function acceptTimezoneGuess(name: var): void {
        if (!name || timezoneEdited || locked)
            return;
        if (session.inventory && session.inventory.tz_guess !== name) {
            session.inventory = Object.assign({}, session.inventory, {
                tz_guess: name
            });
            publish();
        }
        if (timezone !== name) {
            timezone = name;
            Protocol.invalidate(session);
            agreed = false;
            publish();
            if (step === "review") {
                clearPasswords();
                step = "you";
            }
        }
        if (timezoneInfo?.timezone !== timezone)
            applyTimezone();
    }
    function pollTimezoneGuess(): void {
        if (!timezoneGuessPending || timezoneGuessRequest || timezoneEdited || locked || !session.ready)
            return;
        timezoneGuessRequest = true;
        send("get_timezone_guess", {});
    }
    function chooseTimezone(name: string): void {
        if (locked)
            return;
        timezone = name;
        timezoneEdited = true;
        timezoneInfo = null;
        Protocol.invalidate(session);
        publish();
        applyTimezone();
    }
    function applyTimezone(): void {
        if (!session.ready || locked || applyingTimezone)
            return;
        applyingTimezone = timezone;
        timezoneInfo = null;
        timezoneMessage = "";
        send("set_timezone", {
            timezone: timezone
        });
    }
    function zoneClock(): string {
        const info = timezoneInfo;
        if (!info || info.timezone !== timezone)
            return "—:—";
        const date = new Date(info.unix_ms + clockMs - info.receivedMs + info.offset_seconds * 1000);
        return String(date.getUTCHours()).padStart(2, "0") + ":" + String(date.getUTCMinutes()).padStart(2, "0") + ":" + String(date.getUTCSeconds()).padStart(2, "0");
    }
    // The You page shows each field's message under that field; with one password for
    // everything the startup keyboard rule belongs to the password.
    function accountProblems(password: string, confirmation: string): var {
        const problems = Protocol.accountErrors(fullName, login, password, confirmation, hostname, session.reservedLogins);
        if (!problems.password && encryption === "encrypted" && encryptionPassword === "account")
            problems.password = Protocol.diskPasswordError(password);
        return problems;
    }
    function accountProblem(password: string, confirmation: string): string {
        return Protocol.accountError(fullName, login, password, confirmation, hostname, session.reservedLogins) || (encryption === "encrypted" && encryptionPassword === "account" ? Protocol.diskPasswordError(password) : "");
    }
    function stageAccount(password: string, confirmation: string): void {
        if (locked || !session.ready || !secretsChecked)
            return;
        helperMessage = "";
        helperFailed = false;
        if (accountProblem(password, confirmation))
            return;
        accountPassword = password;
        step = "software";
    }
    function reviewSoftware(): void {
        if (!accountPassword) {
            step = "you";
            helperMessage = "Enter your password again to prepare the review.";
            return;
        }
        plan(accountPassword, accountPassword);
        accountPassword = "";
    }
    function plan(password: string, confirmation: string): void {
        if (locked || !session.ready)
            return;
        if (!encryptedConfirmed) {
            step = "disk";
            return;
        }
        if (!encryptionReady) {
            step = "encryption";
            return;
        }
        const error = Protocol.accountError(fullName, login, password, confirmation, hostname, session.reservedLogins);
        if (error) {
            helperMessage = error;
            helperFailed = true;
            return;
        }
        helperMessage = "";
        const config = {
            mode: mode,
            disk_id: diskId,
            confirmed_encrypted: confirmedEncrypted,
            fs: filesystem,
            layouts: layouts.slice(),
            user: {
                name: fullName,
                login: login,
                password: password
            },
            hostname: hostname,
            timezone: timezone,
            software: software,
            encryption: encryption === "none" ? "none" : encryptionPassword,
            hibernation: hibernation,
            online_update: onlineUpdate,
            output_scales: catalog.output_scales || ({})
        };
        if (joinedWifiUuid)
            config.wifi_uuid = joinedWifiUuid;
        if (config.encryption === "separate")
            config.disk_password = diskPassword;
        if (mode === "manual")
            config.mounts = mounts;
        if (mode === "alongside") {
            if (!alongsideSizeValid) {
                helperMessage = "Windows shrink is unavailable. Refresh the disk list.";
                helperFailed = true;
                return;
            }
            config.partition_id = windowsPartition.id;
            config.shrink_bytes = shrinkBytes;
        }
        send("plan", {
            config: config
        });
        config.user.password = "";
        config.disk_password = "";
        clearPasswords();
    }
    function confirm(): void {
        if (locked || !session.ready || Object.values(session.pending).includes("renew") || !encryptedConfirmed || !session.plan || !session.plan.token || (needsAgreement && !agreed))
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
    function reboot(): void {
        if (Object.values(session.pending).includes("reboot"))
            return;
        if (!session.ready) {
            session.rebootMessage = "Could not restart. Try again.";
            publish();
            return;
        }
        send("reboot", {});
    }
    function requestReboot(): void {
        if (Object.values(session.pending).includes("prepare_reboot"))
            return;
        session.rebootMessage = "";
        send("prepare_reboot", {});
    }
    function retry(): void {
        if (!session.error || !session.error.retryable || session.running)
            return;
        const accountError = session.error.code === "login_name_reserved";
        focusLogin = accountError;
        session.outcome = "";
        session.error = null;
        session.jobId = "";
        session.cancelMessage = "";
        session.notice = accountError ? "Check your account settings and enter your password again for a new review." : "Check the disk and enter your password again for a new review.";
        publish();
        step = accountError ? "you" : "disk";
        if (!accountError)
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
        editorDisk = selectedDisk ? {
            id: selectedDisk.id,
            path: selectedDisk.path,
            size_bytes: selectedDisk.size_bytes
        } : null;
        editorProbing = true;
        probe();
    }
    // The fresh probe has answered (probed: with an inventory). GParted gets the chosen disk's path
    // only while the disk with the same id (its by-id link, or its path without one) is still there
    // under that path and size; the refreshed list is already on the Disk page.
    function startEditor(probed: bool): void {
        const chosen = editorDisk;
        const fresh = probed && chosen ? session.inventory.disks.find(d => d.id === chosen.id) || null : null;
        editorProbing = false;
        editorDisk = null;
        if (!probed || (chosen && !(fresh && fresh.path === chosen.path && fresh.size_bytes === chosen.size_bytes))) {
            helperMessage = probed ? "The chosen disk changed or was unplugged, so GParted was not opened. Check the disk list and open GParted again." : "The disk list could not be refreshed, so GParted was not opened. Try again.";
            helperFailed = true;
            return;
        }
        editorPath = fresh ? fresh.path : "";
        editorPending = true;
        gparted.running = true;
    }
    function gpartedExited(code: int): void {
        manualAssignments = true;
        helperMessage = code === 0 ? "Assign the partitions below. The disk list has been refreshed." : "GParted could not complete successfully. The disk list has been refreshed.";
        helperFailed = code !== 0;
        probe();
    }
    function clearJoinResult(): void {
        if (helperMessage === joinMessage)
            helperMessage = "";
        joinMessage = "";
        joinFailed = false;
    }
    function callHelper(op: string, fields: var): void {
        if (helper.running || mockTransport || !helpersEnabled)
            return;
        if (op === "join")
            clearJoinResult();
        helperOp = op;
        helperInput = JSON.stringify(Object.assign({
            op: op
        }, fields)) + "\n";
        helper.running = true;
    }
    function helperResult(result: var): void {
        helperFailed = !result.ok;
        if (helperOp === "join") {
            if (result.ok && result.wifi_uuid)
                joinedWifiUuid = result.wifi_uuid;
            joinFailed = !result.ok;
            joinMessage = result.message || "";
        }
        if (helperOp === "catalog" && result.ok) {
            catalog = result;
            if (result.boot_medium === false) {
                Protocol.bootMediumMissing(session);
                publish();
            }
        } else if (helperOp === "network")
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
        clearJoinResult();
        keyboardDiscarded = false;
        secretPasteRefused = false;
        if (step !== "software")
            accountPassword = "";
        if (step === "timezone")
            applyTimezone();
        if (step === "network")
            callHelper("network", {});
        if (step === "done" || step === "error")
            callHelper("media", {});
    }
    onModeChanged: {
        Protocol.invalidate(session);
        agreed = false;
        shrinkBytes = defaultAlongsideSize();
        publish();
    }
    onShrinkBytesChanged: {
        Protocol.invalidate(session);
        agreed = false;
        publish();
    }
    onLiveKeyboardChanged: {
        // Leaving the live session drops what was waiting; coming back writes the list again.
        keyboardPending = null;
        keyboardSent = null;
        keyboardDeadline = 0;
        keyboardTarget = null;
        keyboardFailed = false;
        Qt.callLater(syncKeyboard);
    }
    onWantedLayoutsChanged: Qt.callLater(syncKeyboard)
    Component.onCompleted: if (!mockTransport)
        callHelper("catalog", {})
    // niri reloads the trial file on its own; read its list back until it matches or time runs out.
    Timer {
        id: keyboardPoll
        interval: 100
        onTriggered: root.readLayouts()
    }
    // Super+Space can change the layout while a password is typed, and niri can load a list late;
    // keep the field's code and its readiness current.
    Timer {
        interval: 1000
        repeat: true
        triggeredOnStart: true
        running: root.liveKeyboard && root.secretFocus !== "" && !root.keyboardSwitching
        onTriggered: root.readLayouts()
    }
    Timer {
        interval: 1000
        running: true
        repeat: true
        onTriggered: {
            root.clockMs = Date.now();
            if (root.step === "review" && root.session.ready && root.session.plan?.token && root.session.deadline > root.clockMs && root.session.deadline - root.clockMs < 60000 && !Object.values(root.session.pending).includes("renew"))
                root.send("renew", {
                    plan_id: root.session.plan.plan_id,
                    token: root.session.plan.token
                });
            if (Protocol.expire(root.session, root.clockMs)) {
                root.agreed = false;
                root.publish();
                if (root.step === "review")
                    root.step = "you";
            }
        }
    }
    Timer {
        interval: root.timezoneGuessOffline ? 5000 : 500
        running: root.timezoneGuessPending && !root.timezoneEdited && !root.locked && root.session.ready
        repeat: true
        onTriggered: root.pollTimezoneGuess()
    }
    Timer {
        interval: 60000
        running: root.step === "timezone" && root.session.ready
        repeat: true
        onTriggered: root.applyTimezone()
    }
    Timer {
        interval: 20000
        running: root.session.rebootMessage === "Restarting…"
        onTriggered: {
            Protocol.rebootTimedOut(root.session);
            root.publish();
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
                    if (root.helperOp === "join") {
                        root.joinFailed = true;
                        root.joinMessage = root.helperMessage;
                    }
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
    // Keyboard operations have their own helper process: a network scan never delays a layout switch.
    Process {
        id: keyboardHelper
        command: helper.command
        stdinEnabled: true
        onStarted: {
            write(root.keyboardInput);
            root.keyboardInput = "";
        }
        stdout: StdioCollector {
            onStreamFinished: {
                let result = null;
                try {
                    result = JSON.parse(text);
                } catch (_) {}
                const serial = root.keyboardSerial;
                // Quickshell ends the stream inside its own exit handling; answer after it.
                Qt.callLater(function () {
                    if (serial === root.keyboardSerial)
                        root.keyboardReceive(result);
                });
            }
        }
        // A helper that could not start ends without output.
        onRunningChanged: if (!running) {
            const serial = root.keyboardSerial;
            Qt.callLater(function () {
                if (serial === root.keyboardSerial)
                    root.keyboardReceive(null);
            });
        }
    }
    // niri's layout switches as they happen (ui-helper layout_events), for the key checks; it
    // runs while the live keyboard does and is started again within a second after it ends.
    Process {
        id: layoutEvents
        command: helper.command
        stdinEnabled: true
        onStarted: write("{\"op\":\"layout_events\"}\n")
        stdout: SplitParser {
            onRead: line => {
                try {
                    if (JSON.parse(line).switched === true)
                        root.keyboardSwitched();
                } catch (_) {}
            }
        }
    }
    Timer {
        interval: 1000
        repeat: true
        triggeredOnStart: true
        readonly property bool wanted: root.liveKeyboard && root.helpersEnabled && !root.mockTransport
        running: wanted || layoutEvents.running
        onTriggered: {
            if (wanted !== layoutEvents.running)
                layoutEvents.running = wanted;
        }
    }
    Process {
        id: gparted
        command: root.partitionEditorArgv
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
