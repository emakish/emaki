pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell
import "../../shell" as Shell

ShellRoot {
    Window {
        id: window
        visible: true
        width: 460
        height: 380
        property int keeps: 0
        property int dismissals: 0
        Shell.SnapshotPrompt {
            id: prompt
            anchors.fill: parent
            snapshot: "7"
            onKeepRequested: window.keeps++
            onDismissed: window.dismissals++
        }
        TestCase {
            name: "SnapshotPrompt"
            when: window.visible
            onCompletedChanged: if (completed)
                console.log("ROLLBACK_TEST_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
            function init() {
                prompt.busy = false;
                prompt.succeeded = false;
                prompt.error = "";
                window.keeps = 0;
                window.dismissals = 0;
                window.requestActivate();
                prompt.takeFocus();
                wait(50);
            }
            function test_keyboard() {
                compare(findChild(prompt, "snapshotLater").activeFocus, true);
                keyClick(Qt.Key_Tab);
                compare(findChild(prompt, "snapshotKeep").activeFocus, true);
                keyClick(Qt.Key_Return);
                compare(window.keeps, 1);
                keyClick(Qt.Key_Tab);
                keyClick(Qt.Key_Space);
                compare(window.dismissals, 1);
                keyClick(Qt.Key_Escape);
                compare(window.dismissals, 2);
            }
            function test_busy_and_error() {
                prompt.busy = true;
                keyClick(Qt.Key_Escape);
                mouseClick(findChild(prompt, "snapshotKeep"));
                compare(window.keeps, 0);
                compare(window.dismissals, 0);
                prompt.error = "Authentication was cancelled. Nothing changed.";
                prompt.busy = false;
                wait(50);
                compare(findChild(prompt, "snapshotLater").activeFocus, true);
                keyClick(Qt.Key_Tab);
                keyClick(Qt.Key_Space);
                compare(window.keeps, 1);
            }
            function test_success() {
                prompt.succeeded = true;
                compare(findChild(prompt, "snapshotKeep").visible, false);
                compare(findChild(prompt, "snapshotLater").label, "Done");
                keyClick(Qt.Key_Return);
                compare(window.dismissals, 1);
                compare(window.keeps, 0);
            }
        }
    }
}
