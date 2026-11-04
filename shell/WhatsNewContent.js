.pragma library
// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later

// Draft release copy for approval; keep it together without changing the layout.
// The pages belong to the 1.1 series: someone who skips 1.1.1 and lands on 1.1.x still sees them once.
function available(version) { return /^1\.1(\.[0-9]+)?$/.test(version); }
function entryLabel() { return qsTranslate("WhatsNewContent", "What's new"); }
function title() { return qsTranslate("WhatsNewContent", "What's new in Emaki 1.1"); }
function closeLabel() { return qsTranslate("WhatsNewContent", "Close what's new"); }
function doneLabel() { return qsTranslate("WhatsNewContent", "Done"); }
function languageLabel() { return qsTranslate("WhatsNewContent", "Change language"); }
function saveError() { return qsTranslate("WhatsNewContent", "Could not save update progress. This screen may appear again next login."); }
function pages() {
    return [
        {
            id: "release", icon: "updates",
            title: qsTranslate("WhatsNewContent", "Themes for your desktop"),
            text: qsTranslate("WhatsNewContent", "Choose a theme in Settings to change your wallpaper, overview background and accent together."),
            note: ""
        },
        {
            id: "release", icon: "command",
            title: qsTranslate("WhatsNewContent", "A clearer launcher"),
            text: qsTranslate("WhatsNewContent", "Follow the moving highlight with the arrow keys, and choose icons for launcher modes in Settings."),
            note: ""
        },
        {
            id: "release", icon: "updates",
            title: qsTranslate("WhatsNewContent", "Discover programs"),
            text: qsTranslate("WhatsNewContent", "Browse recommendations and categories on the new Programs start screen, using the catalog already on your computer."),
            note: ""
        },
        {
            id: "release", icon: "fixes",
            title: qsTranslate("WhatsNewContent", "Fixes"),
            text: qsTranslate("WhatsNewContent", "• Wallpaper changes reach the lock and login screens reliably\n• Installing, removing and updating programs blocks sleep and shutdown until the operation finishes"),
            note: ""
        }
    ];
}
