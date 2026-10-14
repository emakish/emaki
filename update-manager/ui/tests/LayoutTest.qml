// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as C
import QtTest
import Quickshell
import ".." as UI
import "file:@EMAKI_DATADIR@/shell" as Shell

// Every page of the window at every width from a niri third of a small screen up to a full
// one: nothing drawn outside its parent, no two neighbours overlapping, no text cut, no page
// scrolling sideways, and room left for the page under its pinned top.
ShellRoot {
    id: test
    property bool failed: false
    property int checked: 0
    property int failures: 0
    property string where: ""
    // [[width, height], …]: the window sizes to walk.
    readonly property var sizes: JSON.parse(Quickshell.env("TEST_SIZES") || "[[1180, 780]]")
    // Only screenshots (the pixel comparison with an earlier commit), no assertions.
    readonly property bool shotsOnly: Quickshell.env("TEST_SHOTS_ONLY") === "1"
    // The pixel comparison is with 0.8.0's light look: choose light, since nothing saved is dark.
    Component.onCompleted: Shell.SettingsBridge.values = [{key: "appearance.theme", value: "light"}]
    UI.UpdateController { id: updates }
    UI.ProgramsController { id: programs; externalBusy: updates.busy || updates.restorePending }
    function find(item, predicate) {
        if (predicate(item)) return item;
        for (const child of item.children || []) { const result = find(child, predicate); if (result) return result; }
        return null;
    }
    function named(name) { return find(view, item => item.objectName === name) || find(view.C.Overlay.overlay, item => item.objectName === name); }
    // Popups live in their item's data, not its children.
    function popup(item, name) {
        for (const child of item.data || []) {
            if (child.objectName === name && child instanceof C.Popup) return child;
            if (child instanceof Item) { const found = popup(child, name); if (found) return found; }
        }
        return null;
    }
    function fail(message) {
        if (shotsOnly) return;
        failed = true; failures++;
        if (failures <= 60) console.error("ASSERTION_FAILED " + window.width + "x" + window.height + " " + where + ": " + message);
    }
    function describe(item) {
        const text = typeof item.text === "string" && item.text !== "" ? " “" + item.text.slice(0, 40) + "”" : "";
        return (item.objectName || String(item).replace(/\(0x[0-9a-f]+.*$/, "")) + text;
    }
    function layout(item) {
        return item instanceof RowLayout || item instanceof ColumnLayout || item instanceof GridLayout
            || item instanceof Row || item instanceof Column || item instanceof Flow;
    }
    // A strip of screenshots scrolls sideways by design, inside the page; the page itself never does.
    function sideways(flick) { return flick.objectName === "screenshots" || !!flick.parent && flick.parent.objectName === "screenshots"; }
    function shown(item) { return item.visible && item.opacity > 0 && item.width > 0.5 && item.height > 0.5; }
    function walk(item) {
        if (!item || !item.visible || item.opacity === 0) return;
        const parent = item.parent;
        const content = parent instanceof Flickable && parent.contentItem === item;
        const inList = !!parent && parent.parent instanceof Flickable && parent.parent.contentItem === parent;
        // A progress fill slides through its clipped track.
        if (parent && !content && shown(item) && item.objectName !== "progress-fill") {
            checked++;
            // A control's label is measured against the control, whose padding it may use.
            const control = !!parent.parent && !(parent.parent instanceof Flickable) && parent.parent.contentItem === parent ? parent.parent : null;
            const box = control || parent;
            const rect = control ? item.mapToItem(control, 0, 0, item.width, item.height) : Qt.rect(item.x, item.y, item.width, item.height);
            // Inside a scrolling list the bound is the list's own width.
            const room = inList ? Math.max(parent.width, parent.parent.width) : box.width;
            if (rect.x < -0.5 || rect.x + rect.width > room + 0.5)
                fail(describe(item) + " is outside " + describe(box) + " sideways (x " + rect.x.toFixed(1) + ", width " + rect.width.toFixed(1) + ", parent width " + room.toFixed(1) + ")");
            if (!inList && (rect.y < -0.5 || rect.y + rect.height > box.height + 0.5))
                fail(describe(item) + " is outside " + describe(box) + " vertically (y " + rect.y.toFixed(1) + ", height " + rect.height.toFixed(1) + ", parent height " + box.height.toFixed(1) + ")");
        }
        if (item instanceof Text && item.text !== "" && shown(item)) {
            if (item.contentWidth > item.width + 1) fail("text cut sideways: " + describe(item) + " (" + item.contentWidth.toFixed(1) + " in " + item.width.toFixed(1) + ")");
            if (item.contentHeight > item.height + 1) fail("text cut at the bottom: " + describe(item) + " (" + item.contentHeight.toFixed(1) + " in " + item.height.toFixed(1) + ")");
        }
        if (item instanceof Text && item.text !== "" && item.visible && item.opacity > 0 && item.width < 0.5) fail("text squeezed to nothing: " + describe(item));
        if (item instanceof Flickable && shown(item) && !sideways(item) && item.contentWidth > item.width + 0.5)
            fail(describe(item) + " scrolls sideways (" + item.contentWidth.toFixed(1) + " in " + item.width.toFixed(1) + ")");
        if (layout(item)) {
            const children = item.children.filter(child => shown(child));
            for (let first = 0; first < children.length; first++)
                for (let second = first + 1; second < children.length; second++) {
                    const a = children[first], b = children[second];
                    const across = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x);
                    const down = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y);
                    if (across > 0.5 && down > 0.5) fail(describe(a) + " overlaps " + describe(b) + " in " + describe(item));
                }
        }
        // A list builds rows ahead of its view asynchronously; a row it has not placed yet is not drawn.
        const list = item.parent instanceof ListView && item.parent.contentItem === item ? item.parent : null;
        const placed = list ? placedRows(list) : null;
        for (const child of item.children || []) if (!placed || placed.includes(child)) walk(child);
    }
    function placedRows(list) {
        const rows = [list.headerItem, list.footerItem, list.highlightItem];
        for (let index = 0; index < list.count; index++) rows.push(list.itemAtIndex(index));
        return rows.filter(Boolean);
    }
    // Lets the window lay out what changed, so rows a list has just created are checked where they are drawn.
    function settle(ms) {
        input.wait(ms);
        const drawn = view.Window.window;
        if (!drawn || !input.waitForPolish(drawn, 3000)) fail("the window did not finish laying out");
    }
    function images(item, found) {
        if (item instanceof Image) found.push(item);
        for (const child of item.children || []) images(child, found);
        return found;
    }
    function loading() { return images(window.contentItem, []).concat(images(view.C.Overlay.overlay, [])).filter(image => image.status === Image.Loading); }
    // Images load in a thread: a page is checked and captured only when every one is loaded or has
    // failed (a program icon then tries its next source, so it must stay quiet twice in a row).
    function settleImages() {
        for (let quiet = 0, tries = 0; quiet < 2 && tries < 500; tries++) {
            input.wait(10);
            quiet = loading().length ? 0 : quiet + 1;
        }
        settle(0);
        const pending = loading();
        if (pending.length) {
            failed = true; failures++;
            console.error("ASSERTION_FAILED " + window.width + "x" + window.height + " " + where + ": " + pending.length + " images still loading: " + pending.map(image => String(image.source)).join(", "));
        }
    }
    function scroller() { return view.packagesPage ? named("package-list") : view.listPage ? named("row-list") : named("managerBody"); }
    function inspect(name, roomy) {
        where = name;
        settle(60);
        settleImages();
        if (Quickshell.env("TEST_SHOTS")) {
            const image = input.grabImage(window.contentItem);
            image.save(Quickshell.env("TEST_SHOTS") + "/" + name + "-" + window.width + "x" + window.height + ".png");
        }
        walk(window.contentItem);
        walk(view.C.Overlay.overlay);
        // The pinned top leaves the page room to scroll in.
        if (roomy !== false) {
            const page = scroller();
            // A list opens with its first row, or a heading that scrolls with its rows, in sight.
            if (page instanceof ListView && page.contentY > page.originY + 0.5)
                fail("the list opens scrolled to " + page.contentY.toFixed(1) + ", its first item starts at " + page.originY.toFixed(1));
            if (page.height < 72) fail("the page has " + page.height.toFixed(1) + " px under its pinned top");
            // Scrolled to its end, the page's last rows are checked too.
            const flick = page.contentItem instanceof Flickable ? page.contentItem : page;
            flick.contentY = Math.max(flick.originY || 0, flick.contentHeight - flick.height + (flick.originY || 0));
            settle(40);
            walk(window.contentItem);
            view.resetScroll(); input.wait(20);
        }
    }
    function reset() {
        programs.previewOpen = false; programs.plan = null; programs.mutating = false; programs.busy = false; programs.phase = "idle";
        programs.percent = -1; programs.output = ""; programs.message = "";
        updates.busy = false; updates.phase = "idle"; updates.output = ""; updates.restart = false; updates.reminder = false; updates.restorePending = false;
        if (programs.scope !== "programs") programs.selectScope("programs");
        view.navigate("browse");
        input.wait(30);
    }
    function cancel() {
        const button = find(view.C.Overlay.overlay, item => typeof item.clicked === "function" && item.text === "Cancel" && item.visible);
        if (!button) fail("no Cancel in the open dialog"); else button.clicked();
        input.wait(30);
    }
    function program(key) { return programs.allRows.find(row => row.key === key); }
    function states() {
        reset();
        // As a real catalog has them: New and updated with its See all, Popular with its note, and ratings
        // with a count on cards and rows.
        programs.allRows = programs.allRows.map((row, index) => Object.assign({}, row, {releaseDate: row.releaseDate || "2026-09-1" + index, popularity: 90 - index,
            rating: row.kind === "aur" ? undefined : {average: 3.8, count: 495 + 1000 * index}}));
        input.wait(30);
        if (!view.popularMeasured || view.fresh.length <= 4) fail("the fixture shows New and updated with See all and Popular with its note");
        if (!find(view, item => item.objectName === "card-rating" && item.visible)) fail("the fixture shows ratings on cards");
        inspect("browse");
        view.showCategory(view.categoryRows[2]); inspect("category");
        view.query = "k"; view.page = "search"; programs.search("k"); named("program-search").text = "k"; inspect("search");
        reset(); view.showList("popular"); inspect("list");
        reset(); view.showProgram(program("arch:krita"));
        const reviews = named("program-reviews");
        reviews.loading = false; reviews.loaded = true; reviews.rating = {average: 4.6, count: 1804};
        reviews.items = [
            {stars: 5, summary: "Works well for drawing on a tablet with pressure", text: "Brushes feel natural and the canvas stays smooth even with large files and many layers open at once.", name: "Someone with a long display name", date: 1790000000, version: "5.2.9"},
            {stars: 3, summary: "Good", text: "Fine.", name: "", date: 1780000000, version: ""},
            {stars: 4, summary: "Fast", text: "Starts quickly.", name: "Ann", date: 1770000000, version: "5.2"},
            {stars: 4, summary: "More", text: "Another review.", name: "Bo", date: 1760000000, version: "5.1"}
        ];
        inspect("program");
        named("reviews-write").clicked(); inspect("review-dialog", false);
        popup(view, "review-dialog").close();
        reset(); view.showProgram(program("aur:code-minimap")); inspect("program-aur");
        reset(); view.showProgram(program("arch:inkscape")); inspect("program-installed");
        // An installed AUR package with a long development version on its update button.
        reset(); view.showProgram(Object.assign({}, program("aur:code-minimap"), {installed: true, installedVersion: "0.4.1.r12.g3f2a1bc-1", version: "0.4.1.r15.g9d0e2aa-1"}));
        inspect("program-aur-installed");
        reset(); view.showProgram(program("arch:krita")); programs.prepareInstall(); inspect("install-preview", false);
        programs.executePlan(); programs.percent = 42; programs.output = "downloading krita-5.2.9-1-x86_64.pkg.tar.zst\ninstalling krita";
        const details = named("program-details"); if (details) details.clicked();
        inspect("program-progress");
        reset(); view.showProgram(program("aur:code-minimap")); programs.prepareInstall(); inspect("aur-preview", false);
        reset(); view.navigate("aur"); inspect("aur-entry");
        popup(view, "aur-warning").open(); inspect("aur-warning", false);
        // Reopened, the warning starts at the top of its text again.
        const warningText = named("aur-warning-body").contentItem;
        warningText.contentY = Math.max(0, warningText.contentHeight - warningText.height);
        popup(view, "aur-warning").close(); popup(view, "aur-warning").open(); input.wait(30);
        if (warningText.contentY > 0.5) fail("the reopened AUR warning starts scrolled to " + warningText.contentY.toFixed(1));
        popup(view, "aur-warning").close();
        programs.setAur(true, false); view.aurAcknowledged = true; view.navigate("aur"); input.wait(30); inspect("aur");
        programs.searchAur("code"); inspect("aur-search");
        programs.searchAur(""); programs.setAur(false, true);
        // A computer without system snapshots: every AUR page carries the long notice.
        reset(); programs.aurSnapshots = false; view.navigate("aur"); inspect("aur-entry-no-snapshots");
        popup(view, "aur-warning").open(); inspect("aur-warning-no-snapshots", false); popup(view, "aur-warning").close();
        programs.setAur(true, false); view.aurAcknowledged = true; view.navigate("aur"); input.wait(30); inspect("aur-no-snapshots");
        programs.searchAur("code"); inspect("aur-search-no-snapshots");
        programs.searchAur(""); programs.setAur(false, true); programs.aurSnapshots = true;
        reset(); view.navigate("installed"); inspect("installed");
        reset(); programs.selectScope("packages"); view.page = "browse"; inspect("packages");
        reset();
        updates.restart = true; updates.reminder = true;
        updates.restorePoints = [{id: "42:digest", timestamp: "2026-10-11T12:00:00Z", packages: "linux mesa"}, {id: "41:digest", timestamp: "2026-10-04T09:30:00Z", packages: "mesa"}];
        updates.data = Object.assign({}, updates.data, {release: {version: "0.8.1", url: "https://github.com/emakish/emaki/releases/tag/v0.8.1", published: true,
            items: ["The Programs & Updates window fits a third of the screen.", "Undo this install takes back exactly what an install added."]},
            updates: updates.data.updates.concat([{source: "Arch core", name: "linux-lts", old: "6.12.48-1", new: "6.12.49-1", downloadSize: 150000000, restart: true, aur: false}]),
            news: [{title: "Manual intervention for pacman 7.0.0 and local repositories required", link: "https://archlinux.org/news/manual-intervention-for-pacman-700-and-local-repositories-required/"}]});
        view.navigate("updates"); inspect("updates");
        // The restart and restore questions, each closed with its Cancel.
        const restart = find(view, item => typeof item.clicked === "function" && item.text === "Restart now" && item.visible);
        if (!restart) fail("no Restart now button"); else { restart.clicked(); inspect("restart-dialog", false); cancel(); }
        find(view, item => typeof item.confirmRestore === "function").confirmRestore(updates.restorePoints[0]);
        inspect("restore-dialog", false); cancel();
        updates.apply(); updates.output = ":: Synchronizing package databases...\n(1/3) upgrading linux-lts-6.12.49-1-x86_64.pkg.tar.zst"; input.wait(30);
        const log = find(view, item => item.objectName === "update-details" && item.visible); if (log) log.clicked();
        inspect("updates-running");
        reset();
    }
    FloatingWindow {
        id: window
        implicitWidth: test.sizes[0][0]
        implicitHeight: test.sizes[0][1]
        UI.ManagerView { id: view; anchors.fill: parent; controller: updates; programs: programs; brandIconSource: Quickshell.env("TEST_ICON") }
        TestCase { id: input; when: false; name: "ManagerLayout" }
    }
    Timer {
        running: true; interval: 300
        onTriggered: {
            try {
                for (const size of test.sizes) {
                    window.implicitWidth = size[0]; window.implicitHeight = size[1];
                    input.wait(80);
                    if (window.width !== size[0] || window.height !== size[1]) test.fail("the window did not take " + size[0] + "x" + size[1] + " (" + window.width + "x" + window.height + ")");
                    test.states();
                }
            } catch (error) {
                test.failed = true;
                console.error("ASSERTION_FAILED " + test.where + ": " + error + "\n" + error.stack);
            }
            if (!test.shotsOnly && test.checked < 1000) test.fail("only " + test.checked + " items checked");
            console.log("LAYOUT_CHECKED " + test.checked + " FAILURES " + test.failures);
            if (!test.failed) console.log("INTERACTION_OK");
            Qt.quit();
        }
    }
}
