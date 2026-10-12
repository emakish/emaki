#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the combined manager with isolated controller fixtures and real key events."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

UI = Path(__file__).resolve().parents[1]
ROOT = UI.parents[1]
HEADER = '// Copyright (C) 2026 Artur Yakymenko\n// SPDX-License-Identifier: GPL-3.0-or-later\n'
UPDATE = '''import QtQuick
QtObject {
    property bool externalBusy: false
    property var restartPackages: []
    property int notifications: 0
    property string restoreError: ""
    property string countError: ""
    function notifyRestart() { notifications++ }
    property bool busy: false
    property string phase: "idle"
    property bool restart: false
    property bool reminder: false
    property bool restorePending: false
    property bool signOut: false
    property var completedUpdates: []
    property string completedAt: ""
    property bool succeeded: false
    property string message: ""
    property string output: ""
    property int checks: 0
    property int applies: 0
    property int restores: 0
    property int reboots: 0
    property int dismissals: 0
    property int managedCount: 3
    property var selectedRestore: null
    property var restorePoints: [{id:"42:digest",timestamp:"2026-10-11T12:00:00Z",packages:"linux mesa"}]
    property var data: ({updates:[{source:"Emaki",name:"emaki-config",old:"0.6",new:"0.7",downloadSize:200000,aur:false},{source:"Arch core",name:"linux",old:"1",new:"2",downloadSize:150000000,aur:false},{source:"Flatpak",kind:"flatpak",name:"org.kde.krita",old:"1",new:"2",aur:false},{source:"AUR",name:"foreign",old:"1",new:"2",aur:true}],warnings:["The kernel will change. Restart the computer after updating."],news:[],downloadSize:150200000,checkedAt:"2026-10-11T12:00:00Z"})
    function refreshRestorePoints() {}
    function dismissReminder() { dismissals++; reminder = false }
    function reboot() { reboots++ }
    function check() { checks++ }
    function apply() { applies++; busy = true; phase = "applying"; output = "Updating linux" }
    function selectRestore(point) { selectedRestore = point }
    function cancelRestore() { selectedRestore = null }
    function restore() { restores++; busy = true; phase = "restoring" }
}
'''
PROGRAMS = '''import QtQuick
import Quickshell
import "ManagerLogic.js" as Logic
QtObject {
    property var launchEntries: [{id:"org.kde.dolphin",icon:"system-file-manager"}]
    property bool externalBusy: false
    property bool busy: false
    property bool mutating: false
    property string phase: "idle"
    property string message: ""
    property bool messageIsError: true
    property string inventoryError: ""
    property bool inventoryLoading: false
    property string output: ""
    property var warnings: []
    property bool previewOpen: false
    property real percent: -1
    property string planAction: ""
    property var plan: null
    property var planRows: []
    property int executions: 0
    property int opens: 0
    property string scope: "programs"
    property bool packagesReceived: true
    property var packageInventory: [
        {key:"package:extra/aom",id:"aom",package:"aom",kind:"arch",name:"aom",summary:"AV1 video codec",repository:"extra",version:"3.9",installed:true,installedVersion:"3.8",actions:["remove"],packageOnly:true},
        {key:"package:extra/gcc",id:"gcc",package:"gcc",kind:"arch",name:"gcc",summary:"The GNU Compiler Collection",repository:"extra",version:"14.2",installed:false,actions:["install"],packageOnly:true},
        {key:"package:extra/krita",id:"krita",package:"krita",kind:"arch",name:"krita",summary:"Digital painting",repository:"extra",version:"5.2",installed:false,actions:["install"],packageOnly:true,programKey:"arch:krita"}
    ].concat(Array.from({length: 1000}, (_, index) => ({key:"package:extra/library-" + index,id:"library-" + index,package:"library-" + index,kind:"arch",name:"library-" + index,summary:"A library",repository:"extra",version:"1",installed:false,actions:["install"],packageOnly:true})))
    readonly property var packageRows: Logic.filter(packageInventory, query, "")
    readonly property int packageTotal: packageRows.length
    function selectScope(value) { scope = value; category = "" }
    function openPackage(row) { openProgram(allRows.find(item => item.key === row.programKey)) }
    function preparePackage(row, action) { selectedEdition = row; if (action === "install") prepareInstall(); else prepareRemove() }
    property string query: ""
    property string category: ""
    property var selectedProgram: null
    property var selectedEdition: null
    readonly property var selectedLauncher: launchEntries.find(entry => Logic.launchIds(selectedEdition || {}).includes(entry.id)) || null
    property var allRows: [
        {key:"arch:krita",id:"org.kde.krita",package:"krita",kind:"arch",name:"Krita",summary:"Digital painting",description:"Create paintings and illustrations.",categories:["Graphics"],version:"5.2",installedSize:312000000,downloadSize:98000000,actions:["install"]},
        {key:"flatpak:org.kde.krita",id:"org.kde.krita",kind:"flatpak",name:"Krita",summary:"Digital painting",categories:["Graphics"],actions:["install"]},
        {key:"flatpak:md.obsidian.Obsidian",id:"md.obsidian.Obsidian",kind:"flatpak",name:"Obsidian",summary:"Linked notes",categories:["Office"],actions:["install"]},
        {key:"flatpak:net.supertuxkart.SuperTuxKart",id:"net.supertuxkart.SuperTuxKart",kind:"flatpak",name:"SuperTuxKart",summary:"Kart racing",categories:["Game"],actions:["install"]},
        {key:"arch:inkscape",id:"org.inkscape.Inkscape",package:"inkscape",kind:"arch",name:"Inkscape",summary:"Vector graphics",categories:["Graphics"],version:"1.4",installed:true,actions:["remove","open"]},
        {key:"arch:code",id:"code",package:"code",kind:"arch",name:"Code",summary:"Write code",categories:["Development"],actions:["install"]},
        {key:"aur:code-minimap",id:"code-minimap",package:"code-minimap",kind:"aur",name:"code-minimap",summary:"Code minimap",categories:[],actions:[]},
        {key:"arch:gimp",id:"org.gimp.GIMP",package:"gimp",kind:"arch",name:"GNU Image Manipulation Program",summary:"Create images and edit photographs",categories:["Graphics"],actions:["install"]}
    ].map(row => Object.assign({}, row, {icons: row.kind === "aur" ? [] : [{kind:"local", url:Quickshell.env("TEST_ICON")}], screenshots: row.package === "krita" ? [Quickshell.env("TEST_SCREENSHOT")] : []}))
    readonly property var allPrograms: Logic.merge(allRows.filter(row => row.kind !== "aur"))
    readonly property var programs: Logic.filter(query ? Logic.merge(allRows) : allPrograms, query, category)
    readonly property var installedPrograms: allRows.filter(row => row.installed)
    signal transactionFinished(bool ok)
    function selectCategory(value) { category = value; query = "" }
    function search(value) { query = value; category = "" }
    function refresh() {}
    function openProgram(row) { selectedProgram = Logic.merge(allRows).find(item => Logic.identity(item) === Logic.identity(row)); selectedEdition = selectedProgram.editions[0] }
    function selectEdition(kind) { selectedEdition = Logic.edition(selectedProgram, kind) }
    function prepareInstall() { planAction = "install"; plan = {ok:true,token:"test",transaction:{packages:[{package:"krita",downloadSize:1048576},{package:"linux",downloadSize:2097152}]}}; planRows = [{name:"linux",detail:"6.12"},{name:"krita",detail:"5.2"}]; previewOpen = true }
    function prepareRemove(row) { if(row) openProgram(row); planAction = "remove"; plan = {ok:true,token:"test"}; planRows = [{name:"inkscape",detail:"the program"},{name:"unused-library",detail:"nothing else needs it"}]; previewOpen = true }
    function cancelPlan() { previewOpen = false; plan = null }
    function executePlan() { executions++; previewOpen = false; mutating = true; busy = true; phase = planAction === "remove" ? "removing" : "installing" }
    function openInstalled() { opens++ }
}
'''
REOPEN = '''
    TestCase { id: lifecycleInput; when: false }
    property bool failed: false
    function check(ok, message) { if (!ok) { failed = true; console.error("ASSERTION_FAILED " + message); } }
    Timer {
        running: true; interval: 250
        onTriggered: {
            const initial = controller.checks;
            windowLoader.item.visible = false;
            root.check(root.present() && controller.checks === initial + 1, "idle reopen refreshes");
            controller.phase = "applying"; controller.busy = true; controller.output = "Updating linux";
            const calls = controller.checks;
            windowLoader.item.visible = false;
            root.check(root.present() && controller.checks === calls, "busy reopen never refreshes");
            windowLoader.active = false;
            root.check(root.present(), "closed window recreated");
            root.check(controller.busy && controller.output === "Updating linux" && controller.checks === calls, "controller survives recreated window");
            programs.mutating = true; programs.output = "Installing Krita";
            windowLoader.active = false; root.present();
            root.check(programs.mutating && programs.output === "Installing Krita", "program transaction survives recreated window");
            const sameWindow = windowLoader.item;
            root.check(root.presentUpdates() && windowLoader.item === sameWindow && sameWindow.manager.page === "updates", "Updates IPC presents same window and tab");
            sameWindow.manager.navigate("installed");
            windowLoader.active = false; root.present();
            root.check(windowLoader.item.manager.page === "installed", "recreated window retains tab");
            windowLoader.item.manager.showCategory(windowLoader.item.manager.categoryRows[2]);
            windowLoader.active = false; root.present();
            root.check(windowLoader.item.manager.page === "category" && windowLoader.item.manager.category === "Graphics" && windowLoader.item.manager.categoryName === "Graphics", "recreated window retains category and heading");
            windowLoader.item.manager.query = "code"; windowLoader.item.manager.page = "search"; programs.search("code");
            windowLoader.active = false; root.present();
            root.check(windowLoader.item.manager.page === "search" && windowLoader.item.manager.query === "code" && programs.query === "code", "recreated window retains search query and results");
            programs.mutating = false;
            controller.busy = true; controller.phase = "checking";
            const beforePending = controller.checks;
            programs.transactionFinished(true); lifecycleInput.wait(30);
            root.check(root.checkPending && controller.checks === beforePending, "transaction recheck waits for active check");
            controller.busy = false; controller.phase = "idle"; lifecycleInput.wait(30);
            root.check(!root.checkPending && controller.checks === beforePending + 1, "queued transaction recheck runs after active check");
            controller.restartPackages = ["linux"]; controller.reminder = true;
            root.check(controller.notifications === 0, "visible restart banner does not notify");
            root.present();
            root.check(controller.notifications === 0, "raising visible window does not notify");
            windowLoader.item.visible = false; lifecycleInput.wait(50);
            root.check(controller.notifications === 1, "hidden restart reminder notifies once: " + controller.notifications);
            root.notifyIfHidden(); root.present(); windowLoader.item.visible = false; lifecycleInput.wait(50);
            root.check(controller.notifications === 1, "same reminder is not repeated on reopen");
            controller.restartPackages = ["linux", "systemd"];
            root.check(controller.notifications === 2, "new restart packages notify while closed");
            if (!root.failed) console.log("INTERACTION_OK");
            Qt.quit();
        }
    }
'''
MUTANTS = {
    'package-launcher': ('ManagerProgram.qml', 'visible: !page.program.packageOnly || !!page.controller.selectedLauncher', 'visible: true'),
    'apply': ('ManagerUpdates.qml', 'onClicked: root.controller.apply()', 'onClicked: {}'),
    'later': ('ManagerUpdates.qml', 'onClicked: root.controller.dismissReminder()', 'onClicked: {}'),
    'remove-preview': ('ManagerView.qml', 'onClicked: rowItem.packageRow ? view.programs.preparePackage(rowItem.row, "remove") : view.programs.prepareRemove(rowItem.row)', 'onClicked: {}'),
    'busy': ('ManagerUpdates.qml', 'enabled: !root.locked && root.controller.managedCount', 'enabled: root.controller.managedCount'),
    'keyboard': ('ManagerButton.qml', 'focusPolicy: visible && enabled ? Qt.StrongFocus : Qt.NoFocus', 'focusPolicy: Qt.NoFocus'),
}

def run(width=1180, height=780, mutant=None, reopen=False):
    with tempfile.TemporaryDirectory(prefix='emaki-manager-ui-') as directory:
        root = Path(directory)
        shutil.copytree(UI, root / 'ui')
        for path in (root / 'ui').glob('*.qml'):
            path.write_text(path.read_text().replace('@EMAKI_DATADIR@', str(ROOT)))
        (root / 'ui/UpdateController.qml').write_text(HEADER + UPDATE)
        (root / 'ui/ProgramsController.qml').write_text(HEADER + PROGRAMS)
        if mutant:
            filename, before, after = MUTANTS[mutant]
            path = root / 'ui' / filename
            assert before in path.read_text(), mutant
            path.write_text(path.read_text().replace(before, after))
        entry = (UI / 'tests/InteractionTest.qml').read_text().replace('".." as UI', '"." as UI')
        if reopen:
            entry = (root / 'ui/shell.qml').read_text().replace('import QtQuick', 'import QtTest\nimport QtQuick', 1)
            entry = re.sub(r'Shell.WallpaperSource \{.*?\n            \}', 'QtObject { id: wallpaper; property string texture: "" }', entry, flags=re.S)
            entry = entry.rstrip()[:-1] + REOPEN + '\n}\n'
        (root / 'ui/test.qml').write_text(entry)
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1', QS_DISABLE_CRASH_HANDLER='1', TEST_WIDTH=str(width), TEST_HEIGHT=str(height))
        for name in ('runtime', 'cache', 'config', 'data', 'state'):
            (root / name).mkdir(mode=0o700)
            env['XDG_' + name.upper() + ('_DIR' if name == 'runtime' else '_HOME')] = str(root / name)
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'NIRI_SOCKET'):
            env.pop(name, None)
        evidence = ROOT / '.cache/evidence/W1b/screenshots'
        evidence.mkdir(parents=True, exist_ok=True)
        env['TEST_SHOTS'] = str(evidence) if not mutant else ''
        env['TEST_ICON'] = 'file:' + str(UI.parent / 'emaki-programs-updates.svg')
        env['TEST_ICON'] = str(UI.parent / 'emaki-programs-updates.svg')
        screenshot = root / 'program-screen.svg'
        screenshot.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="800" height="472"><!-- Copyright (C) 2026 Artur Yakymenko; SPDX-License-Identifier: GPL-3.0-or-later --><rect width="800" height="472" fill="#d2809a"/><rect x="48" y="40" width="704" height="400" rx="12" fill="#fff9f5"/><rect x="68" y="60" width="100" height="360" fill="#e4d9d2"/><rect x="188" y="80" width="544" height="320" fill="#eed0dd"/></svg>')
        env['TEST_SCREENSHOT'] = str(screenshot)
        with (root / 'runtime.log').open('w') as output:
            try:
                result = subprocess.run(['qs', '-p', str(root / 'ui/test.qml'), '--no-color'], env=env, stdout=output, stderr=subprocess.STDOUT, text=True, timeout=30)
            except subprocess.TimeoutExpired as error:
                raise AssertionError((root / 'runtime.log').read_text()) from error
        log = (root / 'runtime.log').read_text()
        if mutant:
            assert 'ASSERTION_FAILED' in log, log
        else:
            assert result.returncode == 0 and 'INTERACTION_OK' in log, log
            assert not any(word in log for word in ('ASSERTION_FAILED', 'ReferenceError', 'TypeError', 'Unable to assign', 'Binding loop')), log
        print('PASS', mutant or ('production reopen' if reopen else f'{width}x{height}'))

if __name__ == '__main__':
    run()
    run(960, 600)
    run(reopen=True)
    for mutation in MUTANTS:
        run(mutant=mutation)
