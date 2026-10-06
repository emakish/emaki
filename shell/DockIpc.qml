pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

// `qs ipc call dock …`: shared by the production shell and the offscreen dock test.
Scope {
    id: wrapper
    required property ShellScene scene
    // With a settings profile the core owns dock.on/auto_hide: a direct store write
    // would be reverted by the next `settings list` (ShellScene mirrors the core), so the
    // IPC goes through the core key. pinned stays in dock.json.
    function apply(key: string, value: string, local: var): void {
        const settings = wrapper.scene.settings;
        if (settings.profile)
            settings.set(key, value);
        else
            local();
    }
    IpcHandler {
        target: "dock"
        function toggle(): bool {
            const on = !wrapper.scene.dockStore.on;
            wrapper.apply("dock.on", String(on), () => wrapper.scene.dockStore.on = on);
            return on;
        }
        function show(): void {
            wrapper.apply("dock.on", "true", () => wrapper.scene.dockStore.on = true);
        }
        function hide(): void {
            wrapper.apply("dock.on", "false", () => wrapper.scene.dockStore.on = false);
        }
        function pin(id: string): bool {
            return wrapper.scene.dockStore.pin(id);
        }
        function unpin(id: string): bool {
            return wrapper.scene.dockStore.unpin(id);
        }
        // Window identity: which desktop entry an app_id belongs to (apps.json).
        function assign(appId: string, id: string): bool {
            return wrapper.scene.identity.learn(appId, id);
        }
        function forget(appId: string): bool {
            return wrapper.scene.identity.forget(appId);
        }
        function apps(): string {
            return JSON.stringify(wrapper.scene.identity.learned);
        }
        function autoHide(value: bool): void {
            wrapper.apply("dock.auto_hide", String(value), () => wrapper.scene.dockStore.autoHide = value);
        }
        function status(): string {
            return wrapper.scene.status();
        }
    }
}
