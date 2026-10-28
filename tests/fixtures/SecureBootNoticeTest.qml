// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell

// Run only through tests/test-secureboot-notice.py: the session's Secure Boot notice in an
// offscreen scene, with a fake `emaki-secureboot status --json` on PATH.
ShellRoot {
    id: root
    readonly property string mode: Quickshell.env("NOTICE_FIXTURE_MODE")
    readonly property string shots: Quickshell.env("NOTICE_FIXTURE_SHOTS")
    readonly property string theme: Quickshell.env("NOTICE_FIXTURE_THEME") || "dark"
    readonly property bool longWait: Quickshell.env("NOTICE_FIXTURE_LONG") === "1"
    property int phase: 0
    property int ticks: 0
    property int settingsRequests: 0
    property int noticeId: 0
    property bool busy: false
    Component.onCompleted: SettingsBridge.values = [
        {
            key: "appearance.theme",
            value: root.theme
        }
    ]
    NiriService {
        id: niri
        binary: ""
    }
    Window {
        visible: true
        width: 1280
        height: 800
        color: "transparent"
        Item {
            id: canvas
            anchors.fill: parent
            Image {
                anchors.fill: parent
                source: Quickshell.env("NOTICE_FIXTURE_WALLPAPER")
                fillMode: Image.PreserveAspectCrop
            }
            ShellScene {
                id: scene
                anchors.fill: parent
                niri: niri
                borderMode: "soft"
                reservedSpace: 52
                headless: true
                testWidth: 1280
                testHeight: 800
                skipIntro: true
                // Welcome is open when the session starts. Set from script, so the same fixture
                // also runs (and fails) on a shell from before held notices.
                Component.onCompleted: {
                    try {
                        scene.firstRunPresent = root.mode === "welcome";
                    } catch (_) {}
                }
            }
        }
    }
    property bool published: false
    SecureBootNotice {
        store: scene.notifications
        // As in the session: the notice asks once the panel knows the screen.
        ready: root.published && root.mode !== "wrap"
        onSettingsRequested: root.settingsRequests += 1
    }
    function check(ok: bool, message: string): void {
        if (!ok)
            throw new Error(message);
    }
    function publish(): void {
        niri.receive(JSON.stringify({
            schema_version: 1,
            ipc_release: "26.04",
            generation: 1,
            connection: {
                status: "connected",
                reason: "synchronized"
            },
            model: {
                focused_output: "Fixture",
                overview_open: false,
                links_pending: false,
                keyboard_layouts: {
                    names: ["English (US)"],
                    current_idx: 0
                },
                outputs: {
                    Fixture: {
                        logical: {
                            width: 1280,
                            height: 800
                        }
                    }
                },
                workspaces: {
                    "1": {
                        id: 1,
                        idx: 1,
                        is_active: true,
                        is_focused: true,
                        output: "Fixture"
                    },
                    "2": {
                        id: 2,
                        idx: 2,
                        is_active: false,
                        is_focused: false,
                        output: "Fixture"
                    }
                },
                windows: {},
                casts: {}
            }
        }));
        published = true;
    }
    function find(item: var, predicate: var): var {
        if (predicate(item))
            return item;
        for (const child of item.children ?? []) {
            const result = find(child, predicate);
            if (result)
                return result;
        }
        return null;
    }
    function held(): var {
        return scene.notifications.entries.find(e => e.app === "Secure Boot") ?? null;
    }
    function heldShown(): bool {
        return scene.peekOpen && scene.peekIds.length === 1 && scene.peekIds[0] === noticeId;
    }
    function row(prefix: string, id: int): var {
        return find(scene.clockBody, item => item.key === prefix + id && item.visible);
    }
    // The card shows the whole body: wrapped, nothing cut, inside the panel.
    function wholeText(card: var, entry: var): void {
        check(card !== null, "card missing");
        const summary = find(card, item => item.text === entry.summary);
        check(summary !== null && !summary.truncated, "summary cut");
        const label = find(card, item => item.text === entry.app + " · now");
        check(label !== null && summary.x + summary.width + 8 <= label.x + 0.5, "summary runs under the label");
        const message = find(card, item => item.text === entry.body);
        check(message !== null, "body text missing");
        check(message.lineCount >= 2, "body on one line: " + message.lineCount);
        check(!message.truncated, "body cut");
        check(message.height + 0.5 >= message.contentHeight, "body clipped: " + message.height + " < " + message.contentHeight);
        const position = card.mapToItem(scene.clockBody, 0, 0);
        check(position.y + card.height <= scene.clockPanel.targetHeight + 0.5, "card clipped by the panel");
        const action = find(card, item => item.key === card.key + ":a0" && item.visible);
        check(action !== null && action.label === "Open Settings", "Open Settings missing");
        const close = find(card, item => item.key === card.key + ":x");
        check(close !== null, "close missing");
        console.log("NOTICE CARD " + card.key.replace(/-?[0-9]+$/, "") + " lines " + message.lineCount + " height " + card.height);
    }
    function shoot(name: string): void {
        if (!shots) {
            next();
            return;
        }
        busy = true;
        const accepted = canvas.grabToImage(result => {
            check(result.saveToFile(shots + "/" + (mode === "welcome" ? "confirm" : mode) + "-" + name + "-" + theme + ".png"), "shot not saved");
            busy = false;
            next();
        });
        check(accepted, "grab refused");
    }
    function next(): void {
        ticks = 0;
        phase++;
    }
    function done(): void {
        console.log("NOTICE PASS " + mode);
        Qt.quit();
    }
    // A critical notice's title: whole, beside its label, the body under it, inside the panel.
    function wholeTitle(card: var, entry: var): void {
        check(card !== null && entry !== null, "desktop update card missing");
        const summary = find(card, item => item.text === entry.summary);
        check(summary !== null && !summary.truncated && summary.lineCount <= 2, "title cut: " + (summary ? summary.lineCount + " lines, truncated " + summary.truncated : "missing"));
        check(summary.height + 0.5 >= summary.contentHeight, "title clipped: " + summary.height + " < " + summary.contentHeight);
        const label = find(card, item => item.text === scene.notifications.displayApp(entry.app) + " · now");
        check(label !== null && summary.x + summary.width + 8 <= label.x + 0.5, "title runs under the label");
        const message = find(card, item => item.text === entry.body);
        // As in a one-line row (title 7..27, body from 25): the body follows the title's last line.
        check(message !== null && message.y - (summary.y + summary.height) >= -2.5, "body over the title: title " + summary.y + "+" + summary.height + ", body " + (message ? message.y : "missing"));
        const position = card.mapToItem(scene.clockBody, 0, 0);
        check(position.y + card.height <= (scene.drawerOpen ? scene.clockBody.drawerHeight : scene.clockPanel.targetHeight) + 0.5, "card clipped by the panel");
        check(message.y + message.height <= card.height + 0.5, "body outside its card");
        console.log("NOTICE CARD " + card.key.split("-")[0] + " title " + summary.lineCount + " lines, card " + card.height);
    }
    // Two critical rows built in one tick: both titles whole on two lines, each body under its
    // title, the cards apart.
    function pairCards(): void {
        const cards = [];
        for (const app of ["Desktop update", "System update"]) {
            const note = scene.notifications.entries.find(e => e.app === app) ?? null;
            check(note !== null, app + " notice missing");
            const card = row("note-", note.id);
            wholeTitle(card, note);
            check(find(card, item => item.text === note.summary).lineCount === 2, app + " title not wrapped");
            cards.push(card.mapToItem(scene.clockBody, 0, 0).y, card.height);
        }
        const [firstY, firstHeight, secondY, secondHeight] = cards;
        check(firstY + firstHeight <= secondY + 0.5 || secondY + secondHeight <= firstY + 0.5, "cards overlap: " + cards);
    }
    // Ordinary notices in the drawer: a body that fits keeps its one-line row; a longer one
    // wraps to three lines at most, its line breaks run together.
    function wrapCases(): void {
        const now = Date.now();
        const make = (id, body) => ({
                    id: id,
                    app: ["Chat", "Mail", "Files"][id - 701] ?? "",
                    summary: "Fixture notice",
                    body: body,
                    time: now,
                    object: null,
                    deadline: 0,
                    actions: []
                });
        const named = make(704, "A short line");
        named.app = "A synthetic application with a long name";
        named.summary = "A synthetic summary long enough to reach the label";
        scene.notifications.entries = [make(701, "A short line"), make(702, "This synthetic notice is long enough to need several lines in the card: it keeps going past the first line, then past the second line, and still keeps going so that a third line is not enough for all of it."), make(703, "First line\nSecond line"), named];
        scene.notifications.expand(JSON.stringify(["Now", named.app]), true);
    }
    function checkWrap(): void {
        const short = row("note-", 701), long = row("note-", 702), broken = row("note-", 703), named = row("note-", 704);
        check(short && long && broken && named, "drawer rows missing");
        const text = card => find(card, item => item.text === card.note.body.replace(/\s+/g, " "));
        const shortText = text(short), longText = text(long), brokenText = text(broken);
        check(shortText && longText && brokenText, "bodies missing");
        check(short.height === 50 && shortText.height === 20 && shortText.lineCount === 1 && !shortText.truncated, "one-line row changed: " + short.height + " " + shortText.height);
        check(shortText.verticalAlignment === Text.AlignVCenter, "one-line body not centred");
        check(brokenText.text === "First line Second line" && brokenText.lineCount === 1 && broken.height === 50, "line breaks not run together");
        check(longText.lineCount === 3 && longText.truncated, "long body not three lines: " + longText.lineCount);
        check(long.height === 30 + longText.height && longText.height >= longText.contentHeight - 0.5, "long row height " + long.height);
        // The summary keeps its width beside a short label and stops before a long one.
        const summary = card => find(card, item => item.text === card.note.summary);
        check(summary(short).width === short.width - 130, "summary width changed beside a short label");
        const label = find(named, item => item.text === named.note.app + " · now");
        check(summary(named).x + summary(named).width + 8 <= label.x + 0.5 && summary(named).truncated, "summary under a long label");
        console.log("NOTICE WRAP short " + short.height + " long " + long.height + " lines " + longText.lineCount);
    }
    Timer {
        interval: 250
        repeat: true
        running: true
        onTriggered: {
            if (root.busy)
                return;
            root.ticks++;
            try {
                if (root.ticks > 80)
                    throw new Error("phase " + root.phase + " timed out");
                const entry = root.held();
                if (root.mode === "wrap") {
                    switch (root.phase) {
                    case 0:
                        root.publish();
                        root.wrapCases();
                        scene.openDrawer();
                        root.next();
                        break;
                    case 1:
                        if (root.ticks < 4)
                            return;
                        root.checkWrap();
                        root.done();
                    }
                    return;
                }
                if (root.mode === "session") {
                    // The desktop update notice (FR11 P3-a): its title is whole, wrapped, in the
                    // preview and in the drawer; this shell is the old one during the next update.
                    const update = scene.notifications.entries.find(e => e.app === "Desktop update") ?? null;
                    switch (root.phase) {
                    case 0:
                        root.publish();
                        root.noticeId = scene.notifications.localNotice("Desktop update", "Sign out and sign in again to finish updating the desktop.", "Save your work first.", false, true);
                        root.next();
                        break;
                    case 1:
                        if (!scene.peekOpen || root.ticks < 4)
                            return;
                        root.wholeTitle(root.row("peek-", root.noticeId), update);
                        scene.openDrawer();
                        root.next();
                        break;
                    case 2:
                        if (root.ticks < 4)
                            return;
                        root.wholeTitle(root.row("note-", root.noticeId), update);
                        root.done();
                    }
                    return;
                }
                if (root.mode === "pair") {
                    // Two critical notices arrive together, the drawer opened before their rows
                    // exist: every row's title height counts, not only the last one reported.
                    switch (root.phase) {
                    case 0:
                        root.publish();
                        scene.notifications.localNotice("Desktop update", "Sign out and sign in again to finish updating the desktop.", "Save your work first.", false, true);
                        scene.notifications.localNotice("System update", "Restart the computer to finish updating the system and its kernel.", "Save your work first.", false, true);
                        scene.openDrawer();
                        root.next();
                        break;
                    case 1:
                        if (root.ticks < 4)
                            return;
                        root.pairCards();
                        root.done();
                    }
                    return;
                }
                if (root.mode === "fixed") {
                    if (root.phase === 0) {
                        root.publish();
                        root.next();
                    } else if (root.ticks >= 12) {
                        // The problem is gone: no notice, and no stale copy from the last login.
                        root.check(scene.notifications.entries.every(e => e.app !== "Secure Boot"), "stale Secure Boot notice");
                        root.check(scene.notifications.entries.some(e => e.summary === "Older notice"), "unrelated history lost");
                        root.check(scene.notifications.applications.every(a => a.id !== "name:Secure Boot"), "Secure Boot listed under notification rules");
                        root.check(!scene.peekOpen, "preview open");
                        root.done();
                    }
                    return;
                }
                switch (root.phase) {
                case 0:
                    root.publish();
                    if (root.mode === "dismiss")
                        scene.notifications.dnd = true;
                    root.next();
                    break;
                case 1:
                    // The notice arrives.
                    if (!entry)
                        return;
                    root.noticeId = entry.id;
                    root.check(scene.notifications.entries.filter(e => e.app === "Secure Boot").length === 1, "more than one Secure Boot notice");
                    root.check(scene.notifications.applications.every(a => a.id !== "name:Secure Boot"), "Secure Boot listed under notification rules");
                    // The store drops old copies by this name: it must be the notices' app name.
                    root.check(entry.app === "Secure Boot" && (scene.notifications.heldApps ?? []).includes(entry.app), "held app name differs: " + entry.app);
                    root.next();
                    break;
                case 2:
                    if (root.mode === "welcome") {
                        // Welcome is open: the notice waits in the drawer, no preview over Welcome.
                        root.check(!scene.peekOpen, "preview over Welcome");
                        if (root.ticks < 6)
                            return;
                        scene.firstRunPresent = false;
                    }
                    root.next();
                    break;
                case 3:
                    // The preview appears (at once without Welcome, when Welcome closes with it).
                    if (!root.heldShown())
                        return;
                    if (root.ticks < 4)
                        return;
                    root.wholeText(root.row("peek-", root.noticeId), entry);
                    root.check(root.find(root.row("peek-", root.noticeId), item => item.key === "peek-" + root.noticeId + ":x").visible, "preview has no close");
                    if (root.mode === "login") {
                        // Signing out without acting on it.
                        root.done();
                        return;
                    }
                    root.next();
                    break;
                case 4:
                    // It stays: no timer takes it away, the panel never folds (16 s when asked,
                    // the critical preview timer is 15 s).
                    root.check(root.heldShown() && scene.clockExpansion > .99, "preview went away after " + root.ticks / 4 + " s");
                    if (root.ticks < (root.longWait ? 64 : 4))
                        return;
                    root.shoot("popup");
                    break;
                case 5:
                    if (root.mode === "dismiss") {
                        // Do not disturb holds an ordinary arrival back; the held preview stays.
                        scene.notifications.local("Fixture", "Fixture notice", "");
                        root.check(root.heldShown(), "a quiet arrival replaced the held preview");
                        const close = root.find(root.row("peek-", root.noticeId), item => item.key === "peek-" + root.noticeId + ":x");
                        close.clicked();
                        root.phase = 20;
                        root.ticks = 0;
                        return;
                    }
                    // Welcome opens again: the preview steps aside, and comes back when it closes.
                    scene.firstRunPresent = true;
                    root.next();
                    break;
                case 6:
                    root.check(!scene.peekOpen && !!root.held(), "preview over Welcome, or the notice lost");
                    if (root.ticks < 3)
                        return;
                    scene.firstRunPresent = false;
                    root.next();
                    break;
                case 7:
                    if (!root.heldShown())
                        return;
                    // The drawer shows the whole text too.
                    scene.openDrawer();
                    root.next();
                    break;
                case 8:
                    if (root.ticks < 5)
                        return;
                    root.check(scene.drawerOpen && scene.clockPanel.opened, "drawer not open");
                    root.wholeText(root.row("note-", root.noticeId), entry);
                    root.shoot("shade");
                    break;
                case 9:
                    // Closing the drawer brings the preview back.
                    scene.closeAll();
                    root.next();
                    break;
                case 10:
                    if (!root.heldShown())
                        return;
                    // A battery warning takes the preview; when it ends the notice comes back.
                    scene.notifications.systemBattery(5);
                    root.next();
                    break;
                case 11:
                    root.check(scene.batteryPeekOpen, "battery warning not shown");
                    scene.endPeek();
                    root.next();
                    break;
                case 12:
                    if (!root.heldShown())
                        return;
                    // The explicit end of a preview (outside click, timer) brings it back too.
                    scene.endPeek();
                    root.next();
                    break;
                case 13:
                    // After the preview cooldown of the last end.
                    if (!root.heldShown() || root.ticks < 4)
                        return;
                    // An ordinary arrival takes the preview for its few seconds.
                    scene.notifications.local("Fixture", "Fixture notice", "");
                    root.check(scene.peekOpen && !root.heldShown(), "ordinary arrival not previewed");
                    scene.endPeek();
                    root.next();
                    break;
                case 14:
                    if (!root.heldShown())
                        return;
                    // The launcher (Settings opens in it) and the system panel cover nothing:
                    // the preview waits for them to close.
                    scene.openLauncher();
                    root.next();
                    break;
                case 15:
                    root.check(!scene.peekOpen, "preview over the launcher");
                    if (root.ticks < 4)
                        return;
                    root.check(scene.launcherOpen, "launcher not open");
                    scene.closeAll();
                    root.next();
                    break;
                case 16:
                    if (!root.heldShown())
                        return;
                    scene.openSystem("sound");
                    root.next();
                    break;
                case 17:
                    root.check(!scene.peekOpen, "preview over the system panel");
                    if (root.ticks < 4)
                        return;
                    root.check(scene.systemOpen, "system panel not open");
                    scene.closeAll();
                    root.next();
                    break;
                case 18:
                    if (!root.heldShown())
                        return;
                    // The 200-entry history limit never drops it (quiet arrivals, no previews).
                    scene.notifications.dnd = true;
                    for (let i = 0; i < 205; ++i)
                        scene.notifications.local("Fixture", "Fixture notice " + i, "");
                    root.check(!!root.held() && root.heldShown(), "history limit dropped the notice");
                    root.check(scene.notifications.entries.length === 201, "history limit: " + scene.notifications.entries.length);
                    scene.notifications.dnd = false;
                    root.next();
                    break;
                case 19:
                    if (!root.heldShown())
                        return;
                    // Acting on it: Open Settings.
                    root.find(root.row("peek-", root.noticeId), item => item.key === "peek-" + root.noticeId + ":a0").clicked();
                    root.phase = 20;
                    root.ticks = 0;
                    break;
                case 20:
                    root.check(!root.held(), "notice still there");
                    root.check(root.settingsRequests === (root.mode === "dismiss" ? 0 : 1), "Settings requests: " + root.settingsRequests);
                    if (root.ticks < 8)
                        return;
                    // Gone for this session.
                    root.check(!scene.peekOpen, "preview came back");
                    root.done();
                }
            } catch (error) {
                console.error("NOTICE FAIL " + root.mode + ": " + error);
                Qt.quit();
            }
        }
    }
}
