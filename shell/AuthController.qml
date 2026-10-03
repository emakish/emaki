pragma ComponentBehavior: Bound
import QtQuick

// The shared, typed input contract. This object cannot authenticate by itself;
// each backend owns its conversation and success transition. The lock keeps its
// literal LockPam factory, with no production injection point.
QtObject {
    property string buffer: ""
    property int dotCount: 0
    property bool checking: false
    property bool queued: false
    property bool fieldReady: false
    property bool enabled: true
    property string prompt: "Password"
    property string message: ""
    property string messageKind: ""
    property int attemptId: 0
    readonly property bool awaitingResponse: _awaitingInput
    readonly property bool succeeded: _terminal
    readonly property int maximumLength: 1024
    property bool usernameMode: false
    property bool _awaitingInput: false
    property bool _terminal: false
    signal authenticated(int attemptId)
    signal rejected
    signal clearInput

    // Count code points, not UTF-16 units; reject instead of truncating. Qt's
    // TextInput supplies composed Unicode, including dead keys and AltGr.
    function length(text: string): int {
        let n = 0;
        for (let i = 0; i < text.length; ++i) {
            const c = text.charCodeAt(i);
            if (c >= 0xd800 && c <= 0xdbff && i + 1 < text.length) {
                const next = text.charCodeAt(i + 1);
                if (next >= 0xdc00 && next <= 0xdfff)
                    ++i;
            }
            ++n;
        }
        return n;
    }
    function edit(text: string): bool {
        return false;
    }
    function submit(): void {
    }
    function cancel(): void {
    }
    function suspend(): void {
    }
    function reset(): void {
    }
}
