pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io
import "../../shell" as Shell

ShellRoot {
    Shell.WelcomeController {
        id: controller
        loadWallpaper: false
        ready: Quickshell.env("WELCOME_READY") !== "0"
    }
    IpcHandler {
        target: "welcomeTest"
        function status(): string {
            return JSON.stringify({
                opened: controller.opened,
                seen: controller.store.seen,
                restored: controller.store.restored,
                saveFailed: controller.store.saveFailed,
                error: controller.shortcutError
            });
        }
        function dismiss(): void {
            controller.dismiss();
        }
        function ready(): void {
            controller.ready = true;
        }
        function shortcuts(): void {
            controller.allShortcuts();
        }
    }
}
