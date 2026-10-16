// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import Quickshell
import Quickshell.Io

// Drives the real report controller and window offscreen against a stub emaki-report.
// Never match on display prefixes such as "[PLACEHOLDER]": only stable substrings and objectNames.
ShellRoot {
    id: root
    readonly property string mode: Quickshell.env("REPORT_MODE")
    readonly property string profile: Quickshell.env("REPORT_PROFILE")
    readonly property string sentinel: "REPORT_CLIPBOARD_SENTINEL"
    // The preview text on screen when Send was pressed, and how many url calls had run by then.
    property string snapshot: ""
    property int urlsBefore: 0
    NotificationStore {
        id: notes
    }
    ReportController {
        id: report
        store: notes
    }
    FileView {
        id: calls
        path: root.profile + "/calls.jsonl"
        blockLoading: true
        blockAllReads: true
        printErrors: false
    }
    FileView {
        id: stdins
        path: root.profile + "/stdin.jsonl"
        blockLoading: true
        blockAllReads: true
        printErrors: false
    }
    FileView {
        id: opened
        path: root.profile + "/opened"
        blockLoading: true
        blockAllReads: true
        printErrors: false
    }
    function find(item, name) {
        if (!item)
            return null;
        if (item.objectName === name)
            return item;
        for (const child of item.children || []) {
            const found = find(child, name);
            if (found)
                return found;
        }
        if (item.contentItem && item.contentItem !== item)
            return find(item.contentItem, name);
        return null;
    }
    function view() {
        return report.window ? report.window.view : null;
    }
    function el(name) {
        return find(view(), name);
    }
    function lines(file) {
        file.reload();
        return file.text().split("\n").filter(l => l).map(l => JSON.parse(l));
    }
    function callList() {
        return lines(calls);
    }
    function urlCount() {
        return callList().filter(c => c[0] === "url").length;
    }
    function openedText() {
        opened.reload();
        return opened.text();
    }
    function shot(name) {
        const dir = Quickshell.env("REPORT_SHOTS");
        if (dir && root.mode === "ok")
            root.view().grabToImage(result => result.saveToFile(dir + "/" + name + ".png"));
    }
    function check(ok, message) {
        if (!ok) {
            console.error("REPORT_FAILED", message);
            Qt.exit(1);
        }
    }
    function pass() {
        console.log("REPORT_PASS");
        Qt.quit();
    }
    function query(url, key) {
        for (const pair of (url.split("?")[1] || "").split("&"))
            if (pair.startsWith(key + "="))
                return decodeURIComponent(pair.slice(key.length + 1).replace(/\+/g, " "));
        return "";
    }
    function pressSend() {
        snapshot = el("report-preview-text").text;
        urlsBefore = urlCount();
        el("report-send").clicked();
    }
    function asksAgain(why) {
        check(report.previewOpen && report.message.indexOf("Send again") >= 0, why + ": open " + report.previewOpen + ", " + report.message);
    }
    function nothingSent(why) {
        check(openedText() === "" && Quickshell.clipboardText === sentinel && !report.sent, why + ": " + openedText() + " " + report.sent);
    }
    // Send hands over the reply of the preview on screen: the helper does not run again, and the
    // browser gets exactly the snapshot (outgoing(): optional "Title: <title>\n\n" + body).
    function sentIsSnapshot() {
        check(urlCount() === urlsBefore, "Send ran the helper again instead of using the shown preview: " + urlsBefore + " -> " + urlCount());
        const url = openedText().trim();
        check(url.startsWith("https://github.com/emakish/emaki/issues/new?"), "browser got the issue address: " + url);
        const title = query(url, "title"), body = query(url, "body"), cut = snapshot.indexOf("\n\n");
        if (title)
            check(cut >= 0 && snapshot.slice(0, cut).endsWith(title) && snapshot.slice(cut + 2) === body, "sent title and body equal the shown preview");
        else
            check(body === snapshot, "sent body equals the shown preview");
        check(report.sent && report.message.indexOf("open in your browser") >= 0, "sent message: " + report.message);
        return body;
    }
    Timer {
        id: untick
        interval: 150
        onTriggered: report.setChosen("logs", false)
    }
    // fail, bad: the helper fails or answers nonsense; noexec: it cannot even start.
    function failed(s) {
        if (s === 1) {
            check(report.state === "failed" && report.message.indexOf("could not be prepared") >= 0, "compose failure visible: " + report.state + " " + report.message);
            check(!report.busy && el("report-retry").visible && !el("report-send").enabled, "retry offered, send disabled, not busy");
            check(root.callList().every(c => c[0] === "compose"), "nothing but compose ran");
            check(root.openedText() === "", "browser never opened");
            el("report-retry").clicked();
        } else if (s === 2) {
            const composed = root.callList().filter(c => c[0] === "compose").length;
            check(composed === (root.mode === "noexec" ? 0 : 2), "retry composes again: " + composed);
            check(report.state === "failed" && !report.busy, "still failed, not stuck: " + report.state);
            check(root.openedText() === "", "browser never opened");
            pass();
        }
    }
    // A huge report: the document goes on stdin, the helper says too_big, Send copies the preview.
    function huge(s) {
        if (s === 1) {
            check(report.state === "ready", "huge report composed: " + report.state + " " + report.message);
            el("report-preview").clicked();
        } else if (s === 2) {
            const text = el("report-preview-text").text;
            check(text.indexOf("Emaki 0.8.0") >= 0 && /x{10000}/.test(text), "huge preview shows the body: " + text.slice(0, 200));
            const inputs = lines(stdins);
            check(inputs.length > 0 && inputs.every(i => i.stdin > 150000), "the document reached the helper on stdin: " + JSON.stringify(inputs));
            check(inputs.every(i => i.argv_max <= 4096 && i.parent_argv_max <= 4096), "the document is not in argv: " + JSON.stringify(inputs));
            pressSend();
        } else if (s === 3) {
            check(urlCount() === urlsBefore, "Send ran the helper again instead of using the shown preview");
            check(root.openedText() === "", "too big: no browser");
            check(report.message.indexOf("copied to the clipboard") >= 0, "too big message: " + report.message);
            check(Quickshell.clipboardText === snapshot && snapshot.length > 150000, "the shown preview copied exactly");
            pass();
        }
    }
    function limit(s) {
        const field = el("report-what"), counter = el("report-what-limit");
        if (s === 1) {
            field.text = "A".repeat(2000) + " FINAL IMPORTANT DETAIL";
        } else if (s === 2) {
            check(field.text.length === 2000, "the field holds at most 2000 characters: " + field.text.length);
            check(report.what === field.text, "what equals the visible text");
            check(counter && counter.visible && counter.text.indexOf("2000 / 2000") >= 0, "limit counter visible: " + (counter ? counter.text : "missing"));
            field.text = "";
        } else if (s === 3) {
            check(report.what === "" && /(^|\D)0 \/ 2000/.test(counter.text), "empty counter: " + counter.text);
            pass();
        }
    }
    // Send with the preview never opened opens it; the second Send sends what it shows.
    function unopened(s) {
        if (s === 1) {
            check(report.state === "ready" && !report.previewOpen, "ready, preview closed");
            el("report-send").clicked();
            asksAgain("Send with the preview never opened opens it instead of sending");
        } else if (s === 2) {
            nothingSent("nothing left before the preview was seen");
            check(el("report-preview-text").text.indexOf("Emaki 0.8.0") >= 0, "preview refreshed");
            pressSend();
        } else if (s === 3) {
            sentIsSnapshot();
            pass();
        }
    }
    // An edit in the same turn as Send makes the shown preview stale: refresh, ask again.
    function stale(s) {
        if (s === 1) {
            check(report.state === "ready", "ready");
            report.setWhat("First text");
            el("report-preview").clicked();
        } else if (s === 2) {
            check(el("report-preview-text").text.indexOf("First text") >= 0, "fresh preview shown");
            report.setWhat("UNREVIEWED replacement");
            el("report-send").clicked();
            asksAgain("Send right after an edit refreshes the preview instead of sending");
        } else if (s === 3) {
            nothingSent("the unreviewed edit was not sent");
            check(el("report-preview-text").text.indexOf("UNREVIEWED replacement") >= 0, "preview shows the edit");
            pressSend();
        } else if (s === 4) {
            check(sentIsSnapshot().indexOf("UNREVIEWED replacement") >= 0, "the reviewed edit was sent");
            pass();
        }
    }
    // ok, big, badurl, nobrowser: edit, preview, untick, Send at once (the preview is fresh).
    function main(s) {
        if (s === 1) {
            check(report.state === "ready", "composed: " + report.state + " " + report.message);
            check(report.items.length === 4, "items listed");
            check(report.chosenIds().join(",") === "emaki,kernel,logs", "defaults ticked as given: " + report.chosenIds());
            const removed = el("report-removed");
            check(removed && removed.text.indexOf("2 user names, 1 computer name, 1 IP address, 1 password or token") >= 0, "removed line: " + (removed ? removed.text : ""));
            const privacyNotice = "Known kinds of sensitive information are removed. Review the preview before sending.";
            check(removed && removed.text.indexOf(privacyNotice) >= 0, "known kinds and preview reminder after removals");
            check(root.view().removedText({}) === privacyNotice + " No known kinds of sensitive information were found.", "explicit no-removals notice and preview reminder");
            const sendNotice = el("report-send-notice");
            check(sendNotice && sendNotice.text === "Nothing leaves this computer until you choose Send. Send opens a new public GitHub issue form in your browser; it is posted only when you submit it there.", "public browser issue and separate submission explained");
            check(root.callList().map(c => c[0]).join() === "compose" && root.callList()[0].join(" ") === "compose --part update", "only compose ran before preview");
            check(root.openedText() === "", "nothing opened before Send");
            root.shot("1-opened");
            el("report-item-hardware").clicked();
            el("report-what").text = "It froze while updating";
            el("report-preview").clicked();
        } else if (s === 2 && root.mode === "badurl") {
            check(el("report-preview-text").text.indexOf("could not be prepared") >= 0, "non-web preview refused");
            check(root.openedText() === "", "nothing opened");
        } else if (s === 3 && root.mode === "badurl") {
            pressSend();
        } else if (s === 2) {
            check(report.chosenIds().join(",") === "emaki,kernel,hardware,logs", "hardware ticked");
            check(report.what === "It froze while updating", "what stored");
            const preview = el("report-preview-text");
            check(preview && preview.visible, "preview visible");
            check(preview.text.indexOf("It froze while updating") >= 0 && preview.text.indexOf("CPU: Example") >= 0 && preview.text.indexOf("line one") >= 0, "preview shows chosen items: " + preview.text);
            if (root.mode !== "big")
                check(preview.text.indexOf("Title: Problem: update") >= 0, "preview shows title");
            check(root.openedText() === "", "preview opened nothing");
            preview.forceActiveFocus();
            root.shot("2-preview");
            untick.start();
        } else if (s === 3) {
            const preview = el("report-preview-text");
            check(preview.text.indexOf("line one") < 0 && preview.text.indexOf("CPU: Example") >= 0, "unticked item left the preview");
            const urls = root.callList().filter(c => c[0] === "url");
            check(urls.length >= 2 && urls[urls.length - 1].join(" ") === "url --what It froze while updating --items emaki,kernel,hardware", "url args: " + JSON.stringify(urls));
            check(root.openedText() === "", "still nothing opened");
            pressSend();
        } else if (s === 4) {
            if (root.mode === "big") {
                check(urlCount() === urlsBefore, "Send ran the helper again instead of using the shown preview");
                check(root.openedText() === "", "too big: no browser");
                check(report.message.indexOf("copied to the clipboard") >= 0, "too big message: " + report.message);
                check(Quickshell.clipboardText === snapshot && snapshot.indexOf("It froze while updating") >= 0, "the shown preview copied exactly: " + Quickshell.clipboardText);
            } else if (root.mode === "badurl") {
                nothingSent("non-web address refused");
                check(el("report-preview-text").text.indexOf("could not be prepared") >= 0, "refusal visible");
            } else if (root.mode === "nobrowser") {
                check(urlCount() === urlsBefore, "Send ran the helper again instead of using the shown preview");
                check(report.message.indexOf("browser could not be opened") >= 0, "browser failure visible: " + report.message);
                check(!report.sent, "not marked sent");
            } else {
                const body = sentIsSnapshot();
                check(body.indexOf("line one") < 0 && body.indexOf("It froze") >= 0, "sent only what was chosen");
            }
            root.shot("3-sent");
            check(report.offer("other") === "unavailable" && report.offer("nope") === "unavailable", "only failure parts are offered");
            check(report.offer("install") === "offered", "install offered");
            check(report.offer("install") === "offered" && notes.entries.filter(e => e.actions.some(a => a.id === "report:install")).length === 1, "offer not duplicated");
            const entry = notes.entries[0];
            check(entry.actions.length === 1 && entry.actions[0].id === "report:install" && entry.actions[0].text.indexOf("Report") >= 0, "notice has the Report action");
            check(!notes.activate(entry.id, "default"), "default action is not the report");
            check(notes.activate(entry.id, "report:install"), "Report action accepted");
            check(!notes.entries.some(e => e.id === entry.id), "notice closed by its action");
        } else if (s === 5) {
            check(report.part === "install" && report.opened, "action opened the window for install");
            const last = root.callList().filter(c => c[0] === "compose").pop();
            check(last.join(" ") === "compose --part install", "composed install");
            check(report.what === (["nobrowser", "badurl"].includes(root.mode) ? "It froze while updating" : ""), "only a sent report clears its text: " + report.what);
            report.close();
        } else if (s === 6) {
            check(!report.opened && report.state === "idle" && report.preview === "", "closed and cleared");
            pass();
        }
    }
    Timer {
        property int step: 0
        property double pendingSince: 0
        interval: 700
        repeat: true
        running: true
        onTriggered: {
            // A timer tick is not completion of the refreshed helper request.
            const pending = report.busy || (report.previewOpen && report.previewStale) || untick.running;
            if (step > 0 && pending) {
                if (!pendingSince)
                    pendingSince = Date.now();
                check(Date.now() - pendingSince < 15000, "report helper did not settle");
                return;
            }
            pendingSince = 0;
            const s = step++;
            if (s === 0) {
                Quickshell.clipboardText = root.sentinel;
                check(report.open("bogus") === "unavailable" && !report.opened, "unknown part refused");
                check(report.open("update") === "opened" && report.opened, "window opened");
                check(report.part === "update", "part set");
                return;
            }
            const flows = {
                fail: root.failed,
                bad: root.failed,
                noexec: root.failed,
                huge: root.huge,
                limit: root.limit,
                unopened: root.unopened,
                stale: root.stale
            };
            (flows[root.mode] || root.main)(s);
        }
    }
}
