pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

Scope {
    LockPam {
        id: production
        attemptId: 0
    }
    Timer {
        interval: 10
        running: true
        onTriggered: {
            // Read the real PamContext default without assigning configDirectory and
            // without attempting authentication against an administrator's PAM stack.
            if (production.context.configDirectory !== "/etc/pam.d" || production.context.config !== "emaki-lock") {
                console.error("LOCK_PAM_PRODUCTION_PATH_FAILED");
                Qt.exit(1);
                return;
            }
            console.log("LOCK_PAM_PRODUCTION_PATH /etc/pam.d/emaki-lock");
            Qt.quit();
        }
    }
}
