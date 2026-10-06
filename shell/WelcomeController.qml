pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: controller
    property bool enabled: true
    property bool ready: false
    property bool loadWallpaper: true
    property bool pending: false
    property bool presentedThisRun: false
    property string shortcutError: ""
    readonly property WelcomeWindow window: windowLoader.item as WelcomeWindow
    readonly property alias store: store
    readonly property bool opened: windowLoader.active
    signal opening
    function maybePresent(): void {
        if (enabled && ready && store.restored && (pending || (!store.seen && !presentedThisRun)))
            present();
    }
    function present(): string {
        if (!enabled)
            return "unavailable";
        if (!ready) {
            pending = true;
            return "queued";
        }
        pending = false;
        shortcutError = "";
        opening();
        windowLoader.active = true;
        window?.present();
        return window ? "presented" : "unavailable";
    }
    function dismiss(): void {
        windowLoader.active = false;
    }
    function allShortcuts(): void {
        if (shortcutProcess.running)
            return;
        shortcutError = "";
        shortcutDeadline.restart();
        shortcutProcess.running = true;
    }
    onReadyChanged: Qt.callLater(maybePresent)
    onEnabledChanged: Qt.callLater(maybePresent)
    Component.onCompleted: Qt.callLater(maybePresent)
    WelcomeStore {
        id: store
        onRestoredChanged: Qt.callLater(controller.maybePresent)
    }
    Loader {
        id: windowLoader
        active: false
        sourceComponent: WelcomeWindow {
            loadWallpaper: controller.loadWallpaper
            error: controller.shortcutError || (store.saveFailed ? "Could not save welcome progress. This screen may appear again next login." : "")
            onBackingWindowVisibleChanged: {
                if (backingWindowVisible) {
                    controller.presentedThisRun = true;
                    store.markSeen();
                }
            }
            onDismissRequested: controller.dismiss()
            onClosed: controller.dismiss()
            onShortcutsRequested: controller.allShortcuts()
        }
    }
    Process {
        id: shortcutProcess
        command: ["niri", "msg", "action", "show-hotkey-overlay"]
        stdout: SplitParser {
            onRead: _line => {}
        }
        stderr: SplitParser {
            onRead: _line => {}
        }
        onExited: code => {
            shortcutDeadline.stop();
            if (code !== 0)
                controller.shortcutError = "Could not open the shortcut list. Try Super + Shift + /.";
        }
    }
    Timer {
        id: shortcutDeadline
        interval: 3000
        onTriggered: {
            if (shortcutProcess.running)
                shortcutProcess.signal(9);
            controller.shortcutError = "Could not open the shortcut list. Try Super + Shift + /.";
        }
    }
    IpcHandler {
        target: "welcome"
        function present(): string {
            return controller.present();
        }
    }
}
