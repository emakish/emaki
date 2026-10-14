// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

// "Report a problem": composes a report locally with emaki-report, lets the person edit
// and review it, and only on Send hands the prefilled issue address to the browser.
// Nothing here posts anything; notices only offer to open this window.
Scope {
    id: controller
    property NotificationStore store: null
    property bool enabled: true
    readonly property var parts: ["shell", "update", "install", "other"]
    readonly property int whatLimit: 2000
    property string part: "other"
    // "idle" | "composing" | "ready" | "failed"
    property string state: "idle"
    property var report: null
    property var items: []
    property var chosen: ({})
    property string what: ""
    property bool previewOpen: false
    property string preview: ""
    // The helper reply behind the displayed preview, and the choices it was made from.
    // Send hands over exactly this reply and never runs the helper again.
    property var shown: null
    property string shownKey: ""
    property int generation: 0
    readonly property bool previewStale: previewer.running || shownKey !== choiceKey()
    property string message: ""
    property bool sent: false
    readonly property bool busy: state === "composing" || browser.running
    readonly property ReportWindow window: windowLoader.item as ReportWindow
    readonly property bool opened: windowLoader.active
    readonly property string unprepared: "The report could not be prepared. Nothing was sent. Try again."
    signal opening

    function validPart(name: string): bool {
        return parts.includes(name);
    }
    function open(name: string): string {
        if (!enabled || !validPart(name))
            return "unavailable";
        opening();
        const fresh = !windowLoader.active;
        windowLoader.active = true;
        if (fresh || name !== part || state === "failed" || sent)
            compose(name, fresh || sent);
        window?.present();
        return window ? "opened" : "unavailable";
    }
    function close(): void {
        windowLoader.active = false;
        generation++;
        composer.running = false;
        previewer.again = false;
        previewer.running = false;
        state = "idle";
        report = null;
        items = [];
        chosen = {};
        what = "";
        preview = "";
        shown = null;
        shownKey = "";
        previewOpen = false;
        message = "";
        sent = false;
    }
    // A notice that offers the window; choosing it opens the window with the failed part.
    function offer(name: string): string {
        if (!enabled || !store || name === "other" || !validPart(name))
            return "unavailable";
        const action = "report:" + name;
        if (store.entries.some(e => !e.object && e.actions?.some(a => a.id === action)))
            return "offered";
        const summary = name === "update" ? "The update did not finish." : name === "install" ? "A program could not be installed." : "The panel stopped and was restarted.";
        store.offer(name === "shell" ? "Panel recovery" : "Programs & Updates", summary, "You can report the problem. Nothing is sent until you review the report and choose Send.", action, "Report…");
        return "offered";
    }
    function compose(name: string, clearText: bool): void {
        part = name;
        if (clearText)
            what = "";
        generation++;
        state = "composing";
        report = null;
        items = [];
        chosen = {};
        preview = "";
        shown = null;
        shownKey = "";
        message = "";
        sent = false;
        previewer.again = false;
        previewer.running = false;
        composer.received = false;
        composer.command = ["emaki-report", "compose", "--part", name];
        composer.running = false;
        composer.running = true;
    }
    function setChosen(id: string, value: bool): void {
        const next = Object.assign({}, chosen);
        next[id] = value;
        chosen = next;
        invalidate();
    }
    function setWhat(text: string): void {
        if (text === what)
            return;
        what = text.slice(0, whatLimit);
        invalidate();
    }
    function chosenIds(): var {
        return items.filter(i => chosen[i.id] === true).map(i => i.id);
    }
    // Everything the outgoing report is made from; the preview is fresh while it matches.
    function choiceKey(): string {
        return JSON.stringify([generation, what, chosenIds()]);
    }
    function invalidate(): void {
        sent = false;
        if (previewOpen)
            previewDelay.restart();
    }
    function togglePreview(): void {
        previewOpen = !previewOpen;
        if (previewOpen && previewStale)
            refreshPreview();
    }
    // The preview runs the helper's local `url` step and shows its reply decoded back to
    // text; Send hands over that same reply. Running the helper locally sends nothing.
    // Only the ticked items reach the helper; the report goes on stdin, so any size works
    // and an oversized one comes back as too_big for the clipboard.
    function refreshPreview(): void {
        if (state !== "ready")
            return;
        if (previewer.running) {
            previewer.again = true;
            return;
        }
        const ids = chosenIds();
        previewer.key = choiceKey();
        previewer.document = JSON.stringify(Object.assign({}, report, {
            items: items.filter(i => ids.includes(i.id))
        }));
        previewer.received = false;
        previewer.command = ["emaki-report", "url", "--what", what, "--items", ids.join(",")];
        previewer.stdinEnabled = true;
        previewer.running = true;
    }
    function send(): void {
        if (state !== "ready" || browser.running)
            return;
        // Never send text the person has not seen: show the current preview first.
        if (!previewOpen || previewStale) {
            previewOpen = true;
            message = "Check the preview, then choose Send again.";
            refreshPreview();
            return;
        }
        if (!shown) {
            message = unprepared;
            refreshPreview();
            return;
        }
        message = "";
        if (shown.too_big === true) {
            Quickshell.clipboardText = shown.body;
            sent = true;
            message = "The report is too long to fill in the issue form by itself, so it was copied to the clipboard. Open a new issue on GitHub and paste it there.";
            return;
        }
        browser.completed = false;
        browser.command = ["xdg-open", shown.url];
        browser.running = true;
    }
    // Text of a query field, decoded as the browser form will show it.
    function field(url: string, key: string): string {
        const query = url.indexOf("?") >= 0 ? url.slice(url.indexOf("?") + 1) : "";
        for (const pair of query.split("&")) {
            const at = pair.indexOf("=");
            if (at > 0 && pair.slice(0, at) === key)
                return decodeURIComponent(pair.slice(at + 1).replace(/\+/g, " "));
        }
        return "";
    }
    function outgoing(reply: var): string {
        if (reply.too_big === true && typeof reply.body === "string")
            return reply.body;
        const title = field(reply.url, "title");
        const body = field(reply.url, "body");
        return (title ? "Title: " + title + "\n\n" : "") + body;
    }
    function parseUrlReply(text: string): var {
        const reply = JSON.parse(text);
        if (reply && reply.too_big === true && typeof reply.body === "string")
            return reply;
        if (reply && typeof reply.url === "string" && reply.url.startsWith("https://") && !/[\s"'<>\\]/.test(reply.url))
            return reply;
        throw new Error("invalid reply");
    }
    function parseReport(text: string): var {
        const value = JSON.parse(text);
        if (!value || value.version !== 1 || !Array.isArray(value.items))
            throw new Error("invalid report");
        for (const item of value.items)
            if (!item || typeof item.id !== "string" || !/^[a-z0-9_-]{1,40}$/.test(item.id) || typeof item.label !== "string" || typeof item.text !== "string" || typeof item.default !== "boolean")
                throw new Error("invalid item");
        return value;
    }

    Process {
        id: composer
        property bool received: false
        stdout: StdioCollector {
            onStreamFinished: {
                if (controller.state !== "composing")
                    return;
                try {
                    const value = controller.parseReport(text);
                    composer.received = true;
                    controller.report = value;
                    controller.items = value.items;
                    const next = {};
                    for (const item of value.items)
                        next[item.id] = item.default;
                    controller.chosen = next;
                    controller.state = "ready";
                    if (controller.previewOpen)
                        controller.refreshPreview();
                } catch (_) {}
            }
        }
        stderr: StdioCollector {}
        // Covers a helper that exits without a report and one that cannot start at all.
        onRunningChanged: {
            if (!running)
                Qt.callLater(() => {
                    if (!composer.running && !composer.received && controller.state === "composing") {
                        controller.state = "failed";
                        controller.message = controller.unprepared;
                    }
                });
        }
    }
    Timer {
        id: previewDelay
        interval: 250
        onTriggered: controller.refreshPreview()
    }
    Process {
        id: previewer
        property bool received: false
        property bool again: false
        property string key: ""
        property string document: ""
        onStarted: {
            write(document);
            stdinEnabled = false;
        }
        stdout: StdioCollector {
            onStreamFinished: {
                if (previewer.key.length === 0 || JSON.parse(previewer.key)[0] !== controller.generation)
                    return;
                try {
                    const reply = controller.parseUrlReply(text);
                    previewer.received = true;
                    controller.shown = reply;
                    controller.preview = controller.outgoing(reply);
                    controller.shownKey = previewer.key;
                } catch (_) {}
            }
        }
        stderr: StdioCollector {}
        onRunningChanged: {
            if (!running)
                Qt.callLater(() => {
                    if (previewer.running)
                        return;
                    if (!previewer.received && previewer.key.length > 0 && JSON.parse(previewer.key)[0] === controller.generation) {
                        controller.shown = null;
                        controller.preview = "The preview could not be prepared.";
                        controller.shownKey = previewer.key;
                    }
                    previewer.document = "";
                    if (previewer.again) {
                        previewer.again = false;
                        controller.refreshPreview();
                    }
                });
        }
    }
    Process {
        id: browser
        property bool completed: false
        stderr: StdioCollector {}
        onRunningChanged: {
            if (!running)
                Qt.callLater(() => {
                    if (!browser.completed)
                        controller.message = "The browser could not be opened. Nothing was sent.";
                });
        }
        onExited: code => {
            completed = true;
            if (code !== 0) {
                controller.message = "The browser could not be opened. Nothing was sent.";
            } else {
                controller.sent = true;
                controller.message = "The issue form is open in your browser with this report filled in. It is posted only when you submit it there.";
            }
        }
    }
    Loader {
        id: windowLoader
        active: false
        sourceComponent: ReportWindow {
            controller: controller
            onClosed: controller.close()
        }
    }
    Connections {
        target: controller.store
        function onLocalActionRequested(action: string): void {
            if (action.startsWith("report:"))
                controller.open(action.slice(7));
        }
    }
    IpcHandler {
        target: "report"
        function open(part: string): string {
            return controller.open(part || "other");
        }
        function offer(part: string): string {
            return controller.offer(part);
        }
        function status(): string {
            return JSON.stringify({
                opened: controller.opened,
                part: controller.part,
                state: controller.state,
                chosen: controller.chosenIds(),
                previewOpen: controller.previewOpen,
                sent: controller.sent
            });
        }
    }
}
