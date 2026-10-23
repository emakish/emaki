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
    Component.onCompleted: Shell.SettingsBridge.values = [{key: "appearance.theme", value: "light"}]
    UI.UpdateController { id: updates; restorePoints: [] }
    UI.ProgramsController { id: programs }
    function find(item, predicate) {
        if (predicate(item)) return item;
        for (const child of item.children || []) { const match = find(child, predicate); if (match) return match; }
        return null;
    }
    function named(name) { return find(view.C.Overlay.overlay, item => item.objectName === name) || find(view, item => item.objectName === name); }
    function label(text) { return find(view.C.Overlay.overlay, item => item.visible && item.text === text) || find(view, item => item.visible && item.text === text); }
    function check(ok, message) { if (!ok) { console.error("ASSERTION_FAILED " + message); Qt.quit(); throw new Error(message); } }
    FloatingWindow {
        id: window
        implicitWidth: 960; implicitHeight: 900
        UI.ManagerView { id: view; anchors.fill: parent; controller: updates; programs: programs; brandIconSource: Quickshell.env("TEST_ICON") }
        TestCase { id: input; when: false; name: "ProgramsPages" }
    }
    Timer {
        interval: 300; running: true
        onTriggered: {
            const scenario = Quickshell.env("TEST_PAGE_CASE");
            view.navigate("websites"); input.wait(80);
            const address = test.named("website-address");
            const name = test.named("website-name");
            const add = test.named("website-add");
            if (scenario === "confirmation") {
                const remove = test.label("Remove");
                input.mouseDoubleClickSequence(remove); input.wait(50);
                test.check(programs.websiteRemoves === 0, "double click never deletes website data");
                test.check(test.label("Cancel").activeFocus, "confirmation starts focused on Cancel");
                input.keyClick(Qt.Key_Return); input.wait(30);
                test.check(programs.websiteRemoves === 0 && remove.activeFocus, "Enter cancels and restores card focus");
                remove.clicked(); input.wait(30); input.keyClick(Qt.Key_Escape); input.wait(30);
                test.check(!test.label("Remove app and its data"), "Escape clears confirmation");
                remove.clicked(); input.wait(30); view.navigate("browse"); view.navigate("websites"); input.wait(30);
                test.check(!test.label("Remove app and its data"), "leaving the page clears confirmation");
                remove.forceActiveFocus();
                for (let repeat = 0; repeat < 10; repeat++) { input.keyClick(Qt.Key_Return); input.wait(10); }
                test.check(programs.websiteRemoves === 0, "repeated Enter never reaches destructive confirmation");
                if (test.label("Remove app and its data")) input.keyClick(Qt.Key_Escape);
                input.wait(30);
                remove.clicked(); input.wait(Qt.styleHints.mouseDoubleClickInterval + 30);
                test.label("Remove app and its data").clicked(); input.wait(30);
                test.check(programs.websiteRemoves === 1 && address.activeFocus, "separate confirmation removes once and restores address focus");
            } else if (scenario === "add") {
                address.text = "https://example.com"; name.text = "Example";
                address.forceActiveFocus(); input.keyClick(Qt.Key_Return); input.wait(30);
                test.check(programs.websiteAdds === 1, "Enter in address submits Add");
                test.check(test.label("Adding…") && !add.enabled, "Add gives progress and blocks repeated submission");
                programs.finishWebsite(true); input.wait(30);
                test.check(!address.text && !name.text && !add.enabled && address.activeFocus, "success clears fields and restores address focus");
                address.text = "https://second.example"; name.forceActiveFocus(); input.keyClick(Qt.Key_Return); input.wait(30);
                test.check(programs.websiteAdds === 2, "Enter in name submits Add");
                programs.finishWebsite(false); input.wait(30);
                test.check(address.text === "https://second.example", "failed Add keeps input for retry");
            } else if (scenario === "guidance") {
                test.check(!!test.label("Links to other sites open in this app too. Point at the top edge or press Ctrl+L to see the address."), "website explains address reveal");
                test.check(!!test.label("When you press Add, Emaki loads the page and its icon to read the site name and save its icon."), "fetch guidance names explicit consent");
                view.navigate("development"); input.wait(30);
                test.check(!!test.label("Members of the Docker group can control this computer as root."), "Docker risk appears beside opt-in");
            } else if (scenario === "style") {
                test.check(address.implicitHeight === 40 && address.font.family === "Noto Sans" && address.font.pixelSize === 14 && address.background.radius === 12, "website fields match Emaki field metrics");
                view.navigate("development"); input.wait(30);
                const access = test.label("Allow Docker without an administrator password");
                test.check(access.indicator.radius === 5 && access.indicator.implicitWidth === 18, "Docker checkbox uses Emaki indicator");
                test.check(test.named("development-progress-fill"), "kit progress uses Emaki progress track");
            } else if (scenario === "loading") {
                programs.websiteApps = []; programs.websitesLoaded = true; input.wait(30);
                test.check(!!test.label("No website apps yet."), "empty website list is explained");
                programs.websitesLoaded = false; input.wait(30);
                test.check(!!test.label("Loading…"), "website initial load is visible");
                view.navigate("development"); programs.developmentLoaded = false; input.wait(30);
                test.check(!!test.label("Loading…") && !!test.label("Development kits"), "kits have loading and consistent title");
            } else if (scenario === "present") {
                let before = programs.websiteReads; view.present();
                test.check(programs.websiteReads === before + 1, "present refreshes website inventory");
                view.navigate("development"); before = programs.developmentReads; view.present();
                test.check(programs.developmentReads === before + 1, "present refreshes development inventory");
            } else if (scenario === "finish") {
                programs.developmentKits = [{id:"rust", name:"Rust", packages:["rustup"], installed:false, packagesInstalled:true, actions:["finish"]}];
                view.navigate("development"); input.wait(30);
                test.check(!!test.label("Finish setup"), "partial Rust install offers user setup");
                test.label("Finish setup").clicked(); input.wait(30);
                test.check(programs.planAction === "finish", "Finish setup uses finish action");
            }
            console.log("INTERACTION_OK"); Qt.quit();
        }
    }
}
