pragma ComponentBehavior: Bound
import QtQuick

// Test-only replacement, copied into an isolated shell directory. Production
// LockAuth has no factory injection, and this file is never installed.
QtObject {
    id: root
    required property int attemptId
    property int responses: 0
    property int responseLength: 0
    property bool began: false
    property bool aborted: false
    signal challenge(int id, string text, bool visible)
    signal notice(int id, string text, bool isError)
    signal finished(int id, string outcome)
    function begin(): void {
        began = true;
    }
    function respond(value: string): void {
        ++responses;
        responseLength = value.length;
    }
    function abort(): void {
        aborted = true;
    }
}
