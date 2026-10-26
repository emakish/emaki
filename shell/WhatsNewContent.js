.pragma library
// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later

// Keep release copy together so it can be reviewed without changing the layout.
// The pages belong to the 1.0 series: someone who skips 1.0.0 and lands on 1.0.x still sees them once.
function available(version) { return /^1\.0(\.[0-9]+)?$/.test(version); }
function entryLabel() { return qsTranslate("WhatsNewContent", "What's new"); }
function title() { return qsTranslate("WhatsNewContent", "What's new in Emaki 1.0"); }
function closeLabel() { return qsTranslate("WhatsNewContent", "Close what's new"); }
function doneLabel() { return qsTranslate("WhatsNewContent", "Done"); }
function languageLabel() { return qsTranslate("WhatsNewContent", "Change language"); }
function saveError() { return qsTranslate("WhatsNewContent", "Could not save update progress. This screen may appear again next login."); }
function pages() {
    return [
        {
            id: "release", icon: "updates",
            title: qsTranslate("WhatsNewContent", "Emaki 1.0, the first beta"),
            text: qsTranslate("WhatsNewContent", "Emaki is ready for everyday use. Every update can still be undone from the boot menu, and on a computer we haven't tested something can still break: tell us with Report a problem."),
            note: ""
        },
        {
            id: "release", icon: "command",
            title: qsTranslate("WhatsNewContent", "A lighter launcher"),
            text: qsTranslate("WhatsNewContent", "The launcher keeps nothing loaded while it is closed and opens where you left it."),
            note: ""
        },
        {
            id: "release", icon: "fixes",
            title: qsTranslate("WhatsNewContent", "Fixes"),
            text: qsTranslate("WhatsNewContent", "• Settings can be found in the launcher by its name in your language\n• The account description in Settings is no longer cut short"),
            note: ""
        }
    ];
}
