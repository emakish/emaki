pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import QtTest
import Quickshell
import Quickshell.Io

ShellRoot {
    id: root
    NiriService {
        id: niri
        binary: ""
    }
    Window {
        id: window
        visible: true
        width: 1536
        height: 960
        MouseArea {
            anchors.fill: parent
            onClicked: mouse => scene.outsidePress(mouse.x, mouse.y)
        }
        ShellScene {
            id: scene
            anchors.fill: parent
            niri: niri
            borderMode: "soft"
            reservedSpace: 52
            headless: true
            testWidth: 1536
            testHeight: 960
        }
        TestCase {
            id: pointer
            when: false
        }
    }
    function publish(overview: bool, full: bool): void {
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
                overview_open: overview,
                links_pending: false,
                keyboard_layouts: {
                    names: ["English (US)"],
                    current_idx: 0
                },
                outputs: {
                    Fixture: {
                        logical: {
                            width: 1536,
                            height: 960
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
                    }
                },
                windows: full ? {
                    "8": {
                        id: 8,
                        workspace_id: 1,
                        layout: {
                            tile_size: [1536, 960]
                        }
                    }
                } : {}
            }
        }));
    }
    Loader {
        id: fixtureSystem
        active: Quickshell.env("EMAKI_TEST_SYSTEM") === "1"
        source: active ? "SystemFixture.qml" : ""
        onLoaded: {
            scene.services.backend = item;
            scene.services.helpersEnabled = true;
        }
    }
    DockIpc {
        scene: scene
    }
    IpcHandler {
        target: "test"
        function btPairOutcome(value: string): void {
            if (fixtureSystem.item)
                fixtureSystem.item.pairOutcome = value;
        }
        function btDetail(index: int): string {
            return scene.systemBody.rows[index].detail;
        }
        function connectivity(value: string): void {
            if (fixtureSystem.item)
                fixtureSystem.item.connectivity = value;
        }
        function wifiFail(value: string): void {
            if (fixtureSystem.item)
                fixtureSystem.item.failWith = value;
        }
        function hidden(ssid: string, password: string, secured: bool): void {
            scene.systemBody.hiddenOpen = true;
            scene.systemBody.hiddenSecured = secured;
            scene.systemBody.hiddenSsidText(ssid, password);
            scene.systemBody.joinHidden();
        }
        function wifiMessage(): string {
            return scene.systemBody.message;
        }
        function night(on: bool): bool {
            return scene.services.night.setOn(on);
        }
        function nightWarmth(value: int): bool {
            return scene.services.night.setWarmth(value);
        }
        function captures(value: string): void {
            if (fixtureSystem.item)
                fixtureSystem.item.captures = JSON.parse(value).list; // qs ipc mangles a bare JSON array argument
        }
        function casts(count: int): void {
            const casts = {};
            for (let i = 1; i <= count; ++i)
                casts[String(i)] = {
                    stream_id: i,
                    session_id: 1,
                    kind: "pipewire",
                    target: i === 1 ? "output" : "window",
                    output: i === 1 ? "Fixture" : null,
                    window_id: i === 1 ? null : 8,
                    is_active: true
                };
            niri.model = Object.assign({}, niri.model, {
                casts: casts
            });
        }
        // The shell's own live capture for the glass (core marks it by_parent).
        function shellCast(): void {
            niri.model = Object.assign({}, niri.model, {
                casts: {
                    "1": {
                        stream_id: 1,
                        session_id: 1,
                        kind: "wlr_screencopy",
                        target: "output",
                        output: "Fixture",
                        window_id: null,
                        is_active: true,
                        by_parent: true
                    }
                }
            });
        }
        function clickPrivacy(): void {
            const item = scene.privacy;
            const point = item.mapToItem(scene, item.width / 2, item.height / 2);
            pointer.mouseClick(scene, point.x, point.y, Qt.LeftButton);
        }
        // A cell of the system island by page ("sound", "light", "power", …).
        function wheelSystem(page: string, delta: int): void {
            const item = scene.bar.systemGlass.row.cell(page);
            const point = item.mapToItem(scene, item.width / 2, item.height / 2);
            pointer.mouseWheel(scene, point.x, point.y, 0, delta);
        }
        function hoverSystem(page: string): void {
            const item = scene.bar.systemGlass.row.cell(page);
            const point = item.mapToItem(scene, item.width / 2, item.height / 2);
            pointer.mouseMove(scene, point.x, point.y);
        }
        function barAuto(value: bool): void {
            scene.barPolicy.autoHide = value;
        }
        function ovWs(value: bool): void {
            scene.barPolicy.overviewWorkspaces = value;
        }
        function hover(x: real, y: real): void {
            pointer.mouseMove(scene, x, y);
        }
        function tipText(): string {
            return JSON.stringify({
                text: scene.tip.label,
                key: scene.tip.shortcut
            });
        }
        function trayMenuCount(): int {
            return scene.systemBody.trayMenu.children.values.length;
        }
        function trayMenuFirst(): void {
            scene.systemBody.trayMenu.children.values[0].triggered();
        }
        function trayOpenSub(index: int): void {
            scene.systemBody.selectedSub = scene.systemBody.trayMenu.children.values[index];
        }
        function traySubCount(): int {
            return scene.systemBody.traySubMenu.children.values.length;
        }
        function traySubFirst(): void {
            scene.systemBody.traySubMenu.children.values[0].triggered();
        }
        function systemRow(index: int): void {
            scene.systemBody.activateRow(scene.systemBody.rows[index]);
        }
        function systemAbsent(): void {
            scene.services.backend = null;
        }
        function system(page: string): void {
            scene.openSystem(page);
        }
        function systemAction(kind: string, value: string): bool {
            return scene.services.act(kind, JSON.parse(value));
        }
        function systemDeny(value: bool): void {
            if (fixtureSystem.item)
                fixtureSystem.item.deny = value;
        }
        function battery(value: int, charging: bool): void {
            if (fixtureSystem.item) {
                fixtureSystem.item.charging = charging;
                fixtureSystem.item.percent = value / 100;
            }
        }
        function session(value: string): void {
            scene.systemBody.session(value);
        }
        function confirmSession(): void {
            scene.systemBody.confirmSession();
        }
        function status(): string {
            return scene.status();
        }
        function page(name: string): void {
            scene.input.openPage(name);
        }
        function shortcut(): void {
            pointer.keyClick(Qt.Key_V, Qt.MetaModifier | Qt.ShiftModifier);
        }
        function saveShortcut(): void {
            scene.input.saveRecorded();
        }
        function undoNewest(): void {
            const rows = scene.input.settings.history;
            if (rows.length)
                scene.input.settings.undo(rows[rows.length - 1].id);
        }
        function mode(name: string): bool {
            return scene.input.setMode(name);
        }
        function query(value: string): void {
            scene.input.setQuery(value);
        }
        function down(): void {
            pointer.keyClick(Qt.Key_Down);
        }
        function up(): void {
            pointer.keyClick(Qt.Key_Up);
        }
        function select(index: int): void {
            scene.input.selectAt(index);
        }
        function enter(): void {
            pointer.keyClick(Qt.Key_Return);
        }
        function resultKinds(): string {
            return scene.input.results.map(r => r.kind).join(",");
        }
        function clipRecording(value: bool): bool {
            return scene.input.clipboard.setRecording(value);
        }
        function deleteClip(): void {
            scene.input.deleteSelectedClip();
        }
        function media(action: string): bool {
            return scene.clockBody.mediaAction(action);
        }
        function open(): void {
            scene.openDrawer();
        }
        function launcher(): void {
            scene.openLauncher();
        }
        function close(): void {
            scene.closeAll();
        }
        function esc(): void {
            pointer.keyClick(Qt.Key_Escape);
        }
        function click(x: real, y: real): void {
            pointer.mouseClick(scene, x, y, Qt.LeftButton);
        }
        function dnd(value: bool): void {
            scene.notifications.dnd = value;
        }
        function environment(overview: bool, full: bool): void {
            root.publish(overview, full);
        }
        function expand(): void {
            const g = scene.notifications.groups().find(g => g.count > 1);
            if (g)
                scene.notifications.expand(g.key, true);
        }
        function collapse(): void {
            for (const key of Object.keys(scene.notifications.expanded))
                scene.notifications.expand(key, false);
        }
        function ageRows(): void {
            scene.notifications.now = Date.now();
            scene.notifications.entries = scene.notifications.entries.map((entry, i) => Object.assign({}, entry, {
                    time: scene.notifications.now - [0, 40, 800][i % 3] * 60000
                }));
        }
        function calendarCheck(): bool {
            const cal = scene.clockBody.calendar;
            const year = cal.year, month = cal.month;
            cal.year = 2024;
            cal.month = 1;
            const leap = cal.days === 29 && cal.start === 3 && cal.cellCount === 35;
            cal.year = 2026;
            cal.month = 11;
            cal.shift(1);
            const rollover = cal.year === 2027 && cal.month === 0;
            cal.year = year;
            cal.month = month;
            return leap && rollover;
        }
        function unknown(): void {
            niri.invalidate("fixture_disconnect");
        }
        function clearGroup(): void {
            const g = scene.notifications.groups().find(g => g.count > 1);
            if (g)
                scene.notifications.dismiss(g.ids);
        }
        function clear(): void {
            scene.notifications.dismiss(scene.notifications.entries.map(n => n.id));
        }
        function action(id: int, name: string): bool {
            return scene.notifications.activate(id, name);
        }
        function shift(delta: int): void {
            scene.clockBody.calendar.shift(delta);
        }
    }
    Component.onCompleted: {
        Quickshell.watchFiles = false;
        Qt.callLater(() => root.publish(false, false));
        window.requestActivate();
    }
}
