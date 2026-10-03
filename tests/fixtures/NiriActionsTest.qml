pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

// The production NiriService.qml is copied beside this harness by the test.
// No visual items or Wayland backend, no substitute core executable.
ShellRoot {
    NiriService {
        id: service
        binary: Quickshell.env("EMAKI_BIN")
    }
    IpcHandler {
        target: "test"
        function window(id: int): bool {
            return service.focusWindow(id);
        }
        function workspace(id: int): bool {
            return service.focusWorkspace(id);
        }
        function layout(index: int): bool {
            return service.switchLayout(index);
        }
        function status(): string {
            return JSON.stringify({
                connection: service.connection,
                connection_reason: service.reason,
                action: service.actionState,
                reason: service.actionReason,
                layout: service.layoutLabel
            });
        }
    }
}
