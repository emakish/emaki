// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls as C
import QtTest
import Quickshell
import ".." as UI
import "file:@EMAKI_DATADIR@/shell" as Shell

ShellRoot {
    id: test
    readonly property bool translationsReady: Shell.Translation.initialize()
    Component.onCompleted: Shell.SettingsBridge.values = [{key: "appearance.theme", value: Quickshell.env("TEST_STORE_THEME")}]
    UI.UpdateController { id: updates; restorePoints: [] }
    UI.ProgramsController { id: programs }
    function walk(item, predicate) {
        if (predicate(item)) return item;
        for (const child of item.children || []) { const result = walk(child, predicate); if (result) return result; }
        return null;
    }
    function named(name) { return walk(view, item => item.objectName === name); }
    function check(ok, message) {
        if (!ok) { console.error("ASSERTION_FAILED " + message); Qt.quit(); throw new Error(message); }
    }
    function settle() {
        let quiet = 0;
        for (let attempt = 0; attempt < 80 && quiet < 2; attempt++) {
            input.wait(40);
            const loading = walk(view, item => item instanceof Image && item.visible && item.status === Image.Loading);
            quiet = loading ? 0 : quiet + 1;
        }
        check(quiet === 2, "artwork finishes loading before capture");
    }
    function shot(suffix) {
        settle();
        // Repaint the full view after a scroll in the software renderer.
        view.opacity = 0; input.wait(30); view.opacity = 1; input.wait(80);
        let saved = false;
        check(view.grabToImage(result => {
            saved = result.saveToFile(Quickshell.env("TEST_SHOTS") + "/" + Quickshell.env("TEST_STORE_CASE") + "-" + suffix + ".png");
        }, Qt.size(view.width, view.height)), "capture starts");
        for (let attempt = 0; attempt < 100 && !saved; attempt++) input.wait(20);
        check(saved, "screenshot saved at device resolution");
    }
    function geometry(item) {
        if (!item.visible) return;
        if (item instanceof C.AbstractButton && item.width > 0) {
            const point = item.mapToItem(view, 0, 0);
            check(point.x >= -1 && point.x + item.width <= view.width + 1,
                  "control fits horizontally: " + item.objectName);
            check(item.height >= item.implicitHeight - 1, "control retains its height: " + item.objectName);
        }
        for (const child of item.children || []) geometry(child);
    }
    FloatingWindow {
        id: window
        implicitWidth: Number(Quickshell.env("TEST_WIDTH"))
        implicitHeight: Number(Quickshell.env("TEST_HEIGHT"))
        UI.ManagerView { id: view; anchors.fill: parent; controller: updates; programs: programs; brandIconSource: Quickshell.env("TEST_ICON"); discoveryDay: "2026-10-31" }
        TestCase { id: input; when: false; name: "Storefront" }
    }
    Timer {
        interval: 30000; running: true
        onTriggered: { console.error("ASSERTION_FAILED storefront timed out"); Qt.quit(); }
    }
    Timer {
        interval: 300; running: true
        onTriggered: {
            const availability = Quickshell.env("TEST_STORE_AVAILABILITY");
            const typical = Quickshell.env("TEST_STORE_INSTALLED") === "typical";
            const additions = [
                ["org.mozilla.firefox", "Firefox", "Network", "Browse the web"],
                ["org.videolan.VLC", "VLC", "AudioVideo", "Play videos and music"],
                ["org.libreoffice.LibreOffice", "LibreOffice", "Office", "Create documents, spreadsheets and presentations"],
                ["org.blender.Blender", "Blender", "Graphics", "Create 3D art and animation"]
            ].map(value => ({key: "flatpak:" + value[0], id: value[0], kind: "flatpak", name: value[1],
                categories: [value[2]], summary: value[3], installed: false, actions: ["install"]}));
            programs.allRows = programs.allRows.concat(additions).map((row, index) => Object.assign({}, row, {
                storefrontInventoryOnly: availability === "no-appstream",
                popularity: 100 - index,
                installed: typical && (row.package === "inkscape" || row.id === "org.mozilla.firefox"),
                icons: [{kind: "remote", url: "http://127.0.0.1:9/start-icon.png"}].concat(
                    availability === "cached-artwork" ? [{kind: "cached", url: Quickshell.env("TEST_SCREENSHOT")}] : []),
                screenshots: ["http://127.0.0.1:9/start-screenshot.png"]
            })).filter(row => availability !== "no-appstream" || row.installed);
            test.check(programs.installedPrograms.length === (typical ? 2 : 0), "installed fixture remains independent of catalog availability");
            view.navigate("browse"); input.wait(200); settle();
            const storefront = test.named("storefront");
            test.check(!!storefront && storefront.visible, "production storefront is visible");
            test.check(!!test.named("category-strip-page"), "categories remain available");
            test.check(!test.named("shelf-picked"), "placeholder picks are removed");
            test.check(!test.named("shelf-discovery-popular") && !test.named("see-all-discovery-popular"),
                       "the fixed popular shelf and its list control are absent");
            if (availability === "no-appstream") {
                test.check(test.named("browse-empty").visible && !test.named("storefront-empty").visible, "missing catalog has the connection and Refresh message");
                test.check(!test.named("daily-card").visible, "missing catalog does not invent a daily app");
            } else {
                test.check(test.named("daily-card").visible, "cached catalog has a daily app");
                test.check(test.named("daily-artwork-fallback").visible === (availability === "cached-offline"), "missing artwork uses the fallback");
                test.check(!!test.walk(storefront, item => item.visible && String(item.objectName).indexOf("collection-") === 0), "cached catalog has a collection");
                test.check(test.named("shelf-because").visible === typical, "personal shelf depends on local installed apps");
            }
            test.check(!test.walk(view, item => item instanceof Image && /^https?:/.test(String(item.source))),
                       "no image in the start screen requests a remote URL");
            test.check(storefront.localArtwork({icons: [{kind: "cached", url: "https://example.com/art.png"}]}) === ""
                       && storefront.localArtwork({icons: [{kind: "local", url: "file://remote/art.png"}]}) === "",
                       "artwork rejects web URLs and remote file authorities even with local kinds");
            test.check(!test.named("browse-empty").visible || availability === "no-appstream", "loaded catalogs do not show the offline message");
            if (availability !== "no-appstream") {
                test.check(test.named("shelf-popular").visible && test.named("shelf-essentials").visible
                           && test.named("shelf-fresh").visible && view.popularMeasured, "approved shelves remain below discovery with measured popularity");
                for (const collection of view.discovery.collections) {
                    const group = test.named("collection-" + collection.id);
                    test.check(group.Accessible.role === Accessible.Grouping && group.Accessible.name === collection.title,
                               "collection has a named accessible group");
                    test.check(test.named("see-all-" + collection.id).Accessible.name === view.discoveryData.seeAllLabel + ": " + collection.title,
                               "collection See all names its destination");
                }
            }
            test.geometry(storefront);
            view.resetScroll(); input.wait(50); test.shot("top");
            const flick = test.named("managerBody").contentItem;
            const lowerShelf = test.named("shelf-because").visible ? test.named("shelf-because") : test.named("shelf-popular");
            const lowerY = lowerShelf.visible ? lowerShelf.mapToItem(flick.contentItem, 0, 0).y - 12 : 0;
            flick.contentY = Math.max(0, Math.min(flick.contentHeight - flick.height, lowerY)); input.wait(50);
            test.geometry(storefront); test.shot("lower");
            const restored = test.named("shelf-popular");
            flick.contentY = restored.visible ? Math.max(0, Math.min(flick.contentHeight - flick.height,
                restored.mapToItem(flick.contentItem, 0, 0).y - 12)) : 0;
            input.wait(50); test.shot("restored");
            if (availability !== "no-appstream") {
                const originalRows = programs.allRows;
                const picked = view.discovery.daily;
                programs.allRows = programs.allRows.map(row => Object.assign({}, row, {
                    installed: row.id === picked.id || row.key === picked.key
                })); input.wait(50);
                test.check(view.discovery.daily.key === picked.key && test.named("daily-installed").visible,
                           "installing the daily pick keeps it selected and labels it Installed");
                // Program-page navigation has separate screenshot coverage; keep this fixture local.
                programs.allRows = originalRows.map(row => Object.assign({}, row, {screenshots: [],
                    icons: [{kind: "local", url: Quickshell.env("TEST_ICON")}]})); input.wait(50);
                const expectedDaily = view.discovery.daily;
                const expectedCollection = view.discovery.collections[0];
                view.resetScroll(); input.wait(50);
                test.named("daily-card").forceActiveFocus();
                input.keyClick(Qt.Key_Return); input.wait(80);
                test.check(view.page === "program" && programs.selectedProgram.key === expectedDaily.key,
                           "Enter on the daily card opens its exact program");
                test.check(view.returnTo.page === "browse" && !programs.previewOpen && programs.executions === 0,
                           "daily navigation preserves Browse without starting a transaction");
                view.goBack(); input.wait(50);
                test.check(view.page === "browse", "Back from the daily program returns to Browse");
                test.named("see-all-" + expectedCollection.id).clicked(); input.wait(50);
                test.check(view.page === "list" && view.listId === expectedCollection.id
                           && view.pageRows.length === expectedCollection.rows.length,
                           "collection See all opens its exact complete list");
                view.goBack(); input.wait(50);
                test.check(view.page === "browse" && !programs.previewOpen && programs.executions === 0,
                           "Back from a collection returns to Browse without a transaction");
            }
            console.log("INTERACTION_OK"); Qt.quit();
        }
    }
}
