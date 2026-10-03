pragma Singleton
import QtQuick
import Quickshell

QtObject {
    // Scope setup (1 s) + worker (6 s), plus clipboard decode and scheduling margin.
    readonly property int timeoutMs: 15000
    property bool warned: false
    function command(id: string, argv: var): var {
        return [Quickshell.env("EMAKI_PYTHON") || "python3", "-B", Quickshell.shellPath("helpers/app_scope.py"), "--exec", id].concat(argv);
    }
    function diagnostic(line: string): bool {
        if (line !== "emaki: app scope unavailable; launching without cgroup isolation")
            return false;
        if (!warned) {
            warned = true;
            console.warn(line);
        }
        return true;
    }
}
