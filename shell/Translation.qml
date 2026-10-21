// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma Singleton
import QtQml

QtObject {
    id: translation
    // A broken or missing optional catalogue loader must not prevent a window from opening.
    readonly property var bridge: load()
    function load(): var {
        try {
            return Qt.createQmlObject('import QtQml; import Emaki.I18n; QtObject { function identify(window, appId) { return WindowIdentity.identify(window, appId); } }', translation, "TranslationBridge");
        } catch (_) {
            return null;
        }
    }
    // Without the optional catalogue loader, Qt returns the English source markers.
    // Translated text has no markers and passes through unchanged.
    function englishPlural(text: string, count: int): string {
        if (text.indexOf("(s)") < 0 && text.indexOf("(es)") < 0 && text.indexOf("(ies)") < 0)
            return text;
        const singular = count === 1;
        return text.replace("need(s) what it added.", singular ? "needs what it added." : "need what it added.")
            .replace("update(s) are ready to install.", singular ? "update is ready to install." : "updates are ready to install.")
            .replace(/\(s\)/g, singular ? "" : "s")
            .replace(/\(es\)/g, singular ? "" : "es")
            .replace(/\(ies\)/g, singular ? "y" : "ies");
    }
    function initialize(): bool { return bridge !== null; }
    function identify(window: var, appId: string): bool {
        return bridge !== null && window !== null && bridge.identify(window, appId);
    }
}
