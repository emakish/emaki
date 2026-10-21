.pragma library
// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later

// DRAFT English copy: owner approval is required before release.
// Keep release copy together so it can be reviewed without changing the layout.
// The pages belong to the 0.11 series: someone who skips 0.11.0 and lands on 0.11.x still sees them once.
function available(version) { return /^0\.11(\.[0-9]+)?$/.test(version); }
function entryLabel() { return qsTranslate("WhatsNewContent", "What's new"); }
function title() { return qsTranslate("WhatsNewContent", "What's new in Emaki 0.11"); }
function closeLabel() { return qsTranslate("WhatsNewContent", "Close what's new"); }
function doneLabel() { return qsTranslate("WhatsNewContent", "Done"); }
function languageLabel() { return qsTranslate("WhatsNewContent", "Change language"); }
function saveError() { return qsTranslate("WhatsNewContent", "Could not save update progress. This screen may appear again next login."); }
function pages() {
    return [
        {
            id: "release", icon: "clock",
            title: qsTranslate("WhatsNewContent", "Clocks in the clock shade"),
            text: qsTranslate("WhatsNewContent", "A timer with a Pomodoro preset, a stopwatch, alarms and world clocks are now in the clock shade."),
            note: ""
        },
        {
            id: "release", icon: "language", action: "language",
            title: qsTranslate("WhatsNewContent", "Emaki speaks Spanish and Portuguese"),
            text: qsTranslate("WhatsNewContent", "Latin American Spanish and Brazilian Portuguese are available across the desktop, Programs & Updates, the installer and Notes. Choose your language in Settings › Region & Time, then sign out and back in."),
            note: ""
        },
        {
            id: "release", icon: "updates",
            title: qsTranslate("WhatsNewContent", "Update reminders from Emaki"),
            text: qsTranslate("WhatsNewContent", "Emaki tells you when updates are ready and opens Programs & Updates."),
            note: qsTranslate("WhatsNewContent", "Discover is no longer part of the Rich set.")
        },
        {
            id: "release", icon: "fixes",
            title: qsTranslate("WhatsNewContent", "Fixes"),
            text: qsTranslate("WhatsNewContent", "• Small downloads show their real size\n• The password card no longer flashes an error while it closes\n• Install buttons stay available while the catalogue refreshes"),
            note: ""
        }
    ];
}
