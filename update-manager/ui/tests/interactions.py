#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the combined manager with isolated controller fixtures and real key events."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

UI = Path(__file__).resolve().parents[1]
ROOT = UI.parents[1]
sys.path.insert(0, str(UI.parent))
import programs_engine  # noqa: E402  (Browse's picks as the engine serves them)
import programs_catalog  # noqa: E402  (the Installed inventory as the engine builds it)

HEADER = '// Copyright (C) 2026 Artur Yakymenko\n// SPDX-License-Identifier: GPL-3.0-or-later\n'
UPDATE = '''import QtQuick
QtObject {
    property bool externalBusy: false
    property var restartPackages: []
    property int notifications: 0
    property string restoreError: ""
    property string countError: ""
    function notifyRestart() { notifications++ }
    function accept(line) {
        const event = JSON.parse(line);
        if (event.type === "restoreFinished" && event.restart === true) { restorePending = true; restart = true; }
    }
    property bool busy: false
    property string phase: "idle"
    property bool restart: false
    property bool reminder: false
    property bool restorePending: false
    property bool signOut: false
    property var completedUpdates: []
    property var completedRelease: null
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
    property var data: ({foreignPackageCount:1,updates:[{source:"Emaki",name:"emaki-config",old:"0.6",new:"0.7",downloadSize:200000,aur:false},{source:"Arch core",name:"linux",old:"1",new:"2",downloadSize:150000000,aur:false},{source:"Flatpak",kind:"flatpak",name:"org.kde.krita",old:"1",new:"2",aur:false},{source:"AUR",name:"foreign",old:"1",new:"2",aur:true}],warnings:["The kernel will change. Restart the computer after updating."],news:[],downloadSize:150200000,checkedAt:"2026-10-11T12:00:00Z"})
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
        {key:"arch:inkscape",id:"org.inkscape.Inkscape",package:"inkscape",kind:"arch",name:"Inkscape",summary:"Vector graphics",categories:["Graphics"],version:"1.4",installed:true,actions:["remove","open","undo"],undo:{id:"20261012T120000123456Z-" + "b".repeat(32),kind:"arch",name:"inkscape",time:"2026-10-12T19:40:00+00:00",target:"inkscape",added:["inkscape","libinkscape"],snapshot:"17"}},
        {key:"arch:code",id:"code",package:"code",kind:"arch",name:"Code",summary:"Write code",categories:["Development"],actions:["install"]},
        {key:"aur:code-minimap",id:"code-minimap",package:"code-minimap",kind:"aur",name:"code-minimap",summary:"Code minimap",categories:[],actions:["install"],version:"0.6.8-1",votes:42,popularity:1.25,maintainer:"",orphan:true,outOfDate:"2026-09-30T10:00:00+00:00",firstSubmitted:"2021-03-04T10:00:00+00:00",lastModified:"2026-08-01T10:00:00+00:00",recipe:"https://aur.archlinux.org/cgit/aur.git/tree/PKGBUILD?h=code-minimap",page:"https://aur.archlinux.org/packages/code-minimap"},
        {key:"arch:gimp",id:"org.gimp.GIMP",package:"gimp",kind:"arch",name:"GNU Image Manipulation Program",summary:"Create images and edit photographs",categories:["Graphics"],actions:["install"],releaseDate:"2026-10-01"},
        {key:"arch:dolphin",id:"org.kde.dolphin",package:"dolphin",kind:"arch",name:"Dolphin",summary:"Files",categories:["System"],installed:true,protected:true,actions:[],releaseDate:"2026-09-01"}
    ].map(row => Object.assign({}, row, {icons: row.kind === "aur" ? [] : [{kind:"local", url:Quickshell.env("TEST_ICON")}], screenshots: row.package === "krita" ? [Quickshell.env("TEST_SCREENSHOT")] : []}))
    readonly property var allPrograms: Logic.merge(allRows.filter(row => row.kind !== "aur"))
    readonly property var programs: Logic.filter(query ? Logic.merge(allRows) : allPrograms, query, category)
    readonly property var installedPrograms: allRows.filter(row => row.installed)
    signal transactionFinished(bool ok)
    signal restoreFinished(var result)
    function selectCategory(value) { category = value; query = "" }
    function search(value) { query = value; category = "" }
    function refresh() {}
    function openProgram(row) { selectedProgram = Logic.merge(allRows).find(item => Logic.identity(item) === Logic.identity(row)); selectedEdition = selectedProgram.editions[0] }
    function selectEdition(kind) { selectedEdition = Logic.edition(selectedProgram, kind) }
    property bool aurKnown: true
    property bool aurEnabled: false
    property bool aurShowWarning: true
    property bool aurSnapshots: true
    property string aurQuery: ""
    property var aurRows: []
    property bool aurSearched: false
    property var recoveryPoint: null
    property int aurChanges: 0
    property var lastAur: null
    property var storefront: JSON.parse(Quickshell.env("TEST_STOREFRONT"))
    property var shelf: JSON.parse(Quickshell.env("TEST_SHELF")).map((item, index) => ({
        key:"package:emaki/" + item.package, id:item.package, package:item.package, kind:"arch", name:item.title,
        summary:item.summary, repository:"emaki", target:"emaki/" + item.package, version:"0.8.0-1",
        installed:index === 2, installedVersion:index === 2 ? "0.8.0-1" : "", actions:index === 2 ? [] : ["install"], packageOnly:true, shelf:true
    }))
    property var essentials: ({minimal: true, programs: ["gimp", "inkscape", "krita", "code"]})
    property bool catalogReceived: true
    property var aurPopular: []
    property bool aurPopularLoaded: false
    property int popularReads: 0
    function readPopular() { popularReads++; aurPopular = [
        {key:"aur:yay",id:"yay",package:"yay",kind:"aur",name:"yay",summary:"Yet another yogurt",categories:[],actions:["install"],version:"12.5-1",votes:2649,popularity:20.7,maintainer:"someone",orphan:false,outOfDate:"",icons:[]},
        allRows.find(row => row.kind === "aur")
    ].concat(Array.from({length: 40}, (_, index) => ({key:"aur:tool-" + index,id:"tool-" + index,package:"tool-" + index,kind:"aur",name:"tool-" + index,summary:"A tool",categories:[],actions:["install"],version:"1-1",votes:40 - index,popularity:1,maintainer:"someone",orphan:false,outOfDate:"",icons:[]}))); aurPopularLoaded = true }
    function setAur(accept, showWarning) { aurChanges++; lastAur = {accept: accept, showWarning: showWarning}; aurEnabled = accept; aurShowWarning = !accept || showWarning; if (!accept) aurRows = [] }
    function searchAur(text) { aurQuery = text; aurRows = text.length < 2 ? [] : allRows.filter(row => row.kind === "aur" && row.name.includes(text)); aurSearched = text.length >= 2 }
    function prepareUndo() { planAction = "undo"; plan = {ok:true,token:"test",key:selectedEdition.key,transaction:{receipt:selectedEdition.undo.id,target:"inkscape",remove:["inkscape","libinkscape"],packages:["inkscape","libinkscape"],keep:{"shared-fonts":"explicit"},upgraded:["linux"],snapshot:"17"}}; planRows = [{name:"inkscape",detail:""},{name:"libinkscape",detail:""}]; previewOpen = true }
    function prepareInstall() { if (selectedEdition && selectedEdition.kind === "aur") { planAction = "install"; plan = {ok:true,token:"test",transaction:{kind:"aur",target:"code-minimap",fullUpgrade:true,builds:[{packageBase:"rust-helper",packages:[{package:"rust-helper",version:"1.0-1"}],commit:"0123456789abcdef0123456789abcdef01234567",version:"1.0-1",purpose:"build",maintainer:"someone",orphan:false,outOfDate:"",recipe:"https://aur.archlinux.org/cgit/aur.git/tree/PKGBUILD?h=rust-helper&id=0123456789abcdef0123456789abcdef01234567"},{packageBase:"code-minimap",packages:[{package:"code-minimap",version:"0.6.8-1"}],commit:"89abcdef0123456789abcdef0123456789abcdef",version:"0.6.8-1",purpose:"run",maintainer:"",orphan:true,outOfDate:"2026-09-30T10:00:00+00:00",recipe:"https://aur.archlinux.org/cgit/aur.git/tree/PKGBUILD?h=code-minimap&id=89abcdef0123456789abcdef0123456789abcdef"}],packages:[{repository:"core",package:"base-devel",version:"1-2",purpose:"build"},{repository:"extra",package:"git",version:"2.51-1",purpose:"build"},{repository:"extra",package:"libgit2",version:"1.9-1",purpose:"run"}]}}; planRows = Logic.planRows(plan); previewOpen = true; return }
        planAction = "install"; plan ={ok:true,token:"test",transaction:{packages:[{package:"krita",downloadSize:1048576},{package:"linux",downloadSize:2097152}]}}; planRows = [{name:"linux",detail:"6.12"},{name:"krita",detail:"5.2"}]; previewOpen = true }
    function prepareRemove(row) { if(row) openProgram(row); planAction = "remove"; plan = {ok:true,token:"test"}; planRows = [{name:"inkscape",detail:"the program"},{name:"unused-library",detail:"nothing else needs it"}]; previewOpen = true }
    function cancelPlan() { previewOpen = false; plan = null }
    function executePlan() { executions++; previewOpen = false; mutating = true; busy = true; phase = planAction === "remove" ? "removing" : planAction === "undo" ? "undoing" : "installing" }
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
            programs.searchAur("code");
            windowLoader.active = false; root.present();
            const findAur = item => item.objectName === "aur-search" ? item : (item.children || []).reduce((found, child) => found || findAur(child), null);
            const aurField = findAur(windowLoader.item.manager);
            root.check(!!aurField && aurField.text === "code" && programs.aurQuery === "code", "recreated window shows the AUR search its results follow");
            programs.mutating = false;
            controller.busy = true; controller.phase = "checking";
            const beforePending = controller.checks;
            programs.transactionFinished(true); lifecycleInput.wait(30);
            root.check(root.checkPending && controller.checks === beforePending, "transaction recheck waits for active check");
            controller.busy = false; controller.phase = "idle"; lifecycleInput.wait(30);
            root.check(!root.checkPending && controller.checks === beforePending + 1, "queued transaction recheck runs after active check");
            for (const ok of [true, false]) {
                controller.restorePending = false; controller.restart = false;
                programs.recoveryPoint = {id: "71:verified", snapshot: "71", operation: "install"};
                programs.restoreFinished({ok: ok, restart: true});
                root.check(programs.recoveryPoint === null, "completed restore clears failed-install recovery");
                root.check(controller.restorePending && controller.restart && programs.externalBusy,
                    "snapshot Undo result reaches Updates and blocks further changes, including partial failure");
            }
            controller.restorePending = false;
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
# The shipped controller behind a scripted engine: its own JSON transport, refusal handling
# and Installed list, fed by the real inventory code (programs_catalog.programs).
REFUSAL = 'Building needs packages that could not be found: base-devel.'
ENGINE = r'''#!PYTHON
import json, os, sys
if sys.argv[1] == 'list':
    print(os.environ['TEST_INVENTORY']); sys.exit(0)
minimap = dict(key='aur:code-minimap', id='code-minimap', package='code-minimap', kind='aur', name='code-minimap',
               summary='Code minimap', categories=[], actions=['install'], version='0.6.8-1', votes=42, icons=[], screenshots=[])
dolphin = dict(key='arch:dolphin', id='org.kde.dolphin', package='dolphin', kind='arch', name='Dolphin', summary='Files',
               categories=['System'], installed=True, actions=['remove'], icons=[], screenshots=[])
for line in sys.stdin:
    request = json.loads(line)
    method, params = request['method'], request['params']
    value = dict(version=1, id=request['id'], type='result', ok=True)
    if method == 'plan':
        value.update(ok=False, code='missing-dependency', message=os.environ['TEST_REFUSAL'])
    elif method == 'aur':
        value.update(enabled=False, showWarning=True, accepted='', snapshots=True)
    elif method == 'catalog' and not params.get('source') and not params.get('scope'):
        value.update(programs=[minimap, dolphin], total=2, warnings=[], shelf=[])
    else:
        value.update(programs=[], total=0)
    print(json.dumps(value), flush=True)
'''


def inventory():
    """The engine's own `list` answer for Dolphin, which Emaki's app set needs: protected, kept."""
    apps = [{'id': 'org.kde.dolphin.desktop', 'name': 'Dolphin', 'filename': '/apps/org.kde.dolphin.desktop'}]
    owned = {'/apps/org.kde.dolphin.desktop': ('dolphin', '25.08-1')}
    details = {'dolphin': {'Required By': ['emaki-apps'], 'Optional For': []}}
    return json.dumps({'ok': True, 'programs': programs_catalog.programs(apps, owned, details)})


MUTANTS = {
    'aur-off-empty': ('ManagerUpdates.qml', ' && (root.controller.data.foreignPackageCount || 0) > 0', ''),
    'package-launcher': ('ManagerProgram.qml', 'visible: !page.program.packageOnly && page.program.kind !== "aur" || !!page.controller.selectedLauncher', 'visible: true'),
    'apply': ('ManagerUpdates.qml', 'onClicked: root.controller.apply()', 'onClicked: {}'),
    'later': ('ManagerUpdates.qml', 'onClicked: root.controller.dismissReminder()', 'onClicked: {}'),
    'remove-preview': ('ManagerView.qml', 'onClicked: rowItem.packageRow ? view.programs.preparePackage(rowItem.row, "remove") : view.programs.prepareRemove(rowItem.row)', 'onClicked: {}'),
    'busy': ('ManagerUpdates.qml', 'enabled: !root.locked && root.controller.managedCount', 'enabled: root.controller.managedCount'),
    'keyboard': ('ManagerButton.qml', 'focusPolicy: visible && enabled ? Qt.StrongFocus : Qt.NoFocus', 'focusPolicy: Qt.NoFocus'),
    'aur-stripe': ('ManagerView.qml', 'visible: view.aurOpen && view.page === "aur"', 'visible: false'),
    'back-control': ('ManagerView.qml', 'onClicked: view.page === "program" ? view.goBack() : view.navigate("browse")', 'onClicked: {}'),
    'part-of-emaki': ('ManagerLogic.js', 'function partOfEmaki(row) { return !!row && (row.protected === true || row.self === true); }', 'function partOfEmaki(row) { return false; }'),
    'update-details': ('ManagerUpdates.qml', 'visible: root.showLog && root.controller.output.trim().length > 0', 'visible: root.controller.output.trim().length > 0'),
    'browse-top': ('ManagerView.qml', 'visible: view.page === "browse" || view.page === "search"', 'visible: true'),
    'pinned-stretch': ('ManagerView.qml', 'objectName: "pinned"\n            Layout.fillHeight: false', 'objectName: "pinned"'),
    'unknown-facts': ('ManagerProgram.qml', '["Votes", page.known("votes") ? String(page.program.votes) : page.unknown, false]', '["Votes", String(page.program.votes || 0), false]'),
    'no-snapshots': ('ManagerProgram.qml', 'readonly property bool aurRefused: program.kind === "aur" && controller.aurKnown && !controller.aurSnapshots && allowed("install")', 'readonly property bool aurRefused: false'),
    'refused-plan': ('RealProgramsController.qml', 'if (previous === "planning") plan = null;', 'if (previous === "planning") { plan = null; previewOpen = false; }'),
    'long-heading': ('ManagerView.qml', 'Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.maximumWidth: implicitWidth', 'Layout.fillWidth: false'),
    'shelf-install':('ManagerView.qml', 'onClicked: view.programs.preparePackage(shelfItem.modelData, "install")', 'onClicked: {}'),
}

def run(width=1180, height=780, mutant=None, reopen=False, entry='InteractionTest.qml', source=UI, extra=None, timeout=30):
    """Runs a QML entry from tests/ against the UI files in `source`, with the fixture controllers."""
    with tempfile.TemporaryDirectory(prefix='emaki-manager-ui-') as directory:
        root = Path(directory)
        shutil.copytree(source, root / 'ui')
        for path in (root / 'ui').glob('*.qml'):
            path.write_text(path.read_text().replace('@EMAKI_DATADIR@', str(ROOT)))
        engine = root / 'engine'
        engine.write_text(ENGINE.replace('PYTHON', sys.executable, 1))
        engine.chmod(0o700)
        real = (root / 'ui/ProgramsController.qml').read_text()
        assert real.count('"emaki-programs"') >= 2, 'the shipped controller names its engine'
        (root / 'ui/RealProgramsController.qml').write_text(real.replace('"emaki-programs"', json.dumps(str(engine))))
        (root / 'ui/UpdateController.qml').write_text(HEADER + UPDATE)
        (root / 'ui/ProgramsController.qml').write_text(HEADER + PROGRAMS)
        if mutant:
            filename, before, after = MUTANTS[mutant]
            path = root / 'ui' / filename
            assert before in path.read_text(), mutant
            path.write_text(path.read_text().replace(before, after))
        entry = (UI / 'tests' / entry).read_text().replace('".." as UI', '"." as UI').replace('@EMAKI_DATADIR@', str(ROOT))
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
        evidence = Path(os.environ.get('EMAKI_UI_SHOTS') or ROOT / '.cache/evidence/W1b/screenshots')
        evidence.mkdir(parents=True, exist_ok=True)
        env['TEST_SHOTS'] = str(evidence) if not mutant else ''
        env['TEST_ICON'] = 'file:' + str(UI.parent / 'emaki-programs-updates.svg')
        env['TEST_ICON'] = str(UI.parent / 'emaki-programs-updates.svg')
        screenshot = root / 'program-screen.svg'
        screenshot.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="800" height="472"><!-- Copyright (C) 2026 Artur Yakymenko; SPDX-License-Identifier: GPL-3.0-or-later --><rect width="800" height="472" fill="#d2809a"/><rect x="48" y="40" width="704" height="400" rx="12" fill="#fff9f5"/><rect x="68" y="60" width="100" height="360" fill="#e4d9d2"/><rect x="188" y="80" width="544" height="320" fill="#eed0dd"/></svg>')
        env['TEST_SCREENSHOT'] = str(screenshot)
        env['TEST_STOREFRONT'] = json.dumps(programs_engine.storefront())
        env['TEST_SHELF'] = json.dumps(programs_engine.shelf_items())
        env['TEST_INVENTORY'] = inventory()
        env['TEST_REFUSAL'] = REFUSAL
        env.update(extra or {})
        with (root / 'runtime.log').open('w') as output:
            try:
                result = subprocess.run(['qs', '-p', str(root / 'ui/test.qml'), '--no-color'], env=env, stdout=output, stderr=subprocess.STDOUT, text=True, timeout=timeout)
            except subprocess.TimeoutExpired as error:
                raise AssertionError((root / 'runtime.log').read_text()) from error
        log = (root / 'runtime.log').read_text()
        if mutant:
            assert 'ASSERTION_FAILED' in log, log
        else:
            assert result.returncode == 0 and 'INTERACTION_OK' in log, log
            assert not any(word in log for word in ('ASSERTION_FAILED', 'ReferenceError', 'TypeError', 'Unable to assign', 'Binding loop')), log
        print('PASS', mutant or ('production reopen' if reopen else f'{width}x{height}'))
        return log

if __name__ == '__main__':
    run()
    run(960, 600)
    run(reopen=True)
    for mutation in MUTANTS:
        run(mutant=mutation)
