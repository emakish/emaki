// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

// Emaki's own reminder that updates are waiting; it replaced Discover's notifier in 0.11. It speaks
// once when a check first finds updates and again only when more arrive; installing some of them is
// not news. Its one action opens Programs & Updates. Per-app notification rules apply to it as to any
// program, so a person can turn it off in Settings.
Scope {
    id: notice
    required property NotificationStore store
    property bool ready: false
    property int pending: 0
    property int announced: 0
    // The reminder's card in the store; a newer count replaces it instead of stacking a second one
    // (at login the cached count and the fresh check arrive seconds apart).
    property int shownId: 0
    readonly property string action: "emaki-updates-open"
    signal openRequested
    function show(): void {
        if (!ready || pending <= announced)
            return;
        announced = pending;
        if (shownId !== 0 && store.entries.some(entry => entry.id === shownId))
            store.dismiss([shownId]);
        // Transient like the Secure Boot notices: never saved with the history, since the next login raises it
        // again while updates are waiting; a saved copy would only pile up as an inert card per login.
        shownId = store.transientLocal("Programs & Updates", qsTranslate("UpdatesNotice", "Updates available"), Translation.englishPlural(qsTranslate("UpdatesNotice", "%n update(s) are ready to install.", "", pending), pending), [
            {
                id: action,
                text: qsTranslate("UpdatesNotice", "Open")
            }
        ]);
    }
    onPendingChanged: {
        // Fewer pending updates lower the mark without a notice, so the next arrival is news again.
        if (pending < announced)
            announced = pending;
        show();
    }
    onReadyChanged: show()
    Connections {
        target: notice.store
        function onLocalActionRequested(action: string): void {
            if (action === notice.action)
                notice.openRequested();
        }
    }
}
