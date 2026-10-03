pragma ComponentBehavior: Bound
import QtQuick
import Quickshell.Services.Pam

// A fresh object is created for every attempt. There is deliberately no PAM
// configDirectory, service, user, or environment-controlled authentication hook.
QtObject {
    id: root
    required property int attemptId
    property bool _terminal: false
    signal challenge(int id, string text, bool visible)
    signal notice(int id, string text, bool isError)
    signal finished(int id, string outcome)
    function begin(): void {
        if (!context.start())
            finish("technical");
    }
    function respond(value: string): void {
        if (!_terminal && context.responseRequired)
            context.respond(value);
    }
    function abort(): void {
        _terminal = true;
        context.abort();
    }
    function finish(outcome: string): void {
        if (_terminal)
            return;
        _terminal = true;
        finished(attemptId, outcome);
    }
    readonly property PamContext context: PamContext {
        config: "emaki-lock"
        onPamMessage: {
            if (root._terminal)
                return;
            if (responseRequired)
                root.challenge(root.attemptId, message, responseVisible);
            else
                root.notice(root.attemptId, message, messageIsError);
        }
        onError: root.finish("technical")
        onCompleted: result => {
            if (result === PamResult.Success)
                root.finish("success");
            else if (result === PamResult.Failed)
                root.finish("rejected");
            else if (result === PamResult.MaxTries)
                root.finish("locked");
            else
                root.finish("technical");
        }
    }
}
