pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

// `qs ipc call dock …`: shared by the production shell and the offscreen dock test.
Scope {
    id: wrapper
    required property ShellScene scene
    property ShellServices shared: null
    readonly property SettingsCatalog settings: shared?.settings ?? scene?.settings ?? null
    readonly property DockStore store: shared?.dockStore ?? scene?.dockStore ?? null
    readonly property AppIdentity identity: shared?.identity ?? scene?.identity ?? null
    IpcHandler {
        target: "dock"
        function focus(): void {
            wrapper.scene.closeAll();
            wrapper.scene.dock.takeFocus();
        }
        function toggle(): bool {
            const on = !wrapper.store.on;
            wrapper.settings.set("dock.on", String(on));
            return on;
        }
        function show(): void {
            wrapper.settings.set("dock.on", "true");
        }
        function hide(): void {
            wrapper.settings.set("dock.on", "false");
        }
        function pin(id: string): bool {
            return wrapper.store.pin(id);
        }
        function unpin(id: string): bool {
            return wrapper.store.unpin(id);
        }
        // Window identity: which desktop entry an app_id belongs to (apps.json).
        function assign(appId: string, id: string): bool {
            return wrapper.identity.learn(appId, id);
        }
        function forget(appId: string): bool {
            return wrapper.identity.forget(appId);
        }
        function apps(): string {
            return JSON.stringify(wrapper.identity.learned);
        }
        function autoHide(value: bool): void {
            wrapper.settings.set("dock.auto_hide", String(value));
        }
        function status(): string {
            return wrapper.scene?.status() ?? "{}";
        }
    }
}
