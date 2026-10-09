pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

// The production shell QML is copied beside this harness by the test. No window or Wayland
// backend, no substitute core executable; the system panel's keyboard page runs without one.
ShellRoot {
    NiriService {
        id: service
        binary: Quickshell.env("EMAKI_BIN")
    }
    SystemService {
        id: system
    }
    SystemBody {
        id: keyboard
        service: system
        niri: service
        page: "kb"
        opened: true
        width: 320
    }
    IpcHandler {
        target: "test"
        // A click on the keyboard page's row for layout `index`.
        function keyboardLayout(index: int): string {
            const row = keyboard.rows.find(r => r.value === index && r.action === "layout");
            if (!row)
                return "no_row";
            keyboard.activateRow(row);
            return keyboard.layoutState;
        }
        function close(id: int): bool {
            return service.closeWindow(id);
        }
        function window(id: int): bool {
            return service.focusWindow(id);
        }
        function workspace(id: int): bool {
            return service.focusWorkspace(id);
        }
        function layout(index: int): bool {
            return service.switchLayout(index);
        }
        // Requests in one JS call (one tick); replacement 0 issues no third request.
        function burst(workspace: int, window: int, replacement: int): string {
            const results = [service.focusWorkspace(workspace), service.focusWindow(window)];
            if (replacement)
                results.push(service.focusWindow(replacement));
            return JSON.stringify(results);
        }
        // Dock "Close all windows" on two windows, then a click on a third, in one JS call.
        function closeAllThenFocus(first: int, second: int, focus: int): string {
            return JSON.stringify([service.closeWindow(first), service.closeWindow(second), service.focusWindow(focus)]);
        }
        function supportsVersion(text: string): bool {
            return service.supportsLayerFocus(text.slice(1));
        }
        function reconnect(): void {
            const snapshot = {
                schema_version: 1,
                ipc_release: "26.04",
                generation: service.generation,
                connection: {
                    status: "connected",
                    reason: "fixture"
                },
                model: service.model
            };
            service.invalidate("fixture_disconnect");
            service.receive(JSON.stringify(snapshot));
        }
        function status(): string {
            return JSON.stringify({
                windows: service.windows.length,
                layer_focus: service.layerFocusSupported,
                keyboard_note: keyboard.note,
                connection: service.connection,
                connection_reason: service.reason,
                action: service.actionState,
                reason: service.actionReason,
                layout: service.layoutLabel
            });
        }
    }
}
