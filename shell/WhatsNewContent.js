.pragma library
// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later

// Keep release copy together so it can be reviewed without changing the layout.
// The pages belong to the 0.12 series: someone who skips 0.12.0 and lands on 0.12.x still sees them once.
function available(version) { return /^0\.12(\.[0-9]+)?$/.test(version); }
function entryLabel() { return qsTranslate("WhatsNewContent", "What's new"); }
function title() { return qsTranslate("WhatsNewContent", "What's new in Emaki 0.12"); }
function closeLabel() { return qsTranslate("WhatsNewContent", "Close what's new"); }
function doneLabel() { return qsTranslate("WhatsNewContent", "Done"); }
function languageLabel() { return qsTranslate("WhatsNewContent", "Change language"); }
function saveError() { return qsTranslate("WhatsNewContent", "Could not save update progress. This screen may appear again next login."); }
function pages() {
    return [
        {
            id: "release", icon: "language", action: "language",
            title: qsTranslate("WhatsNewContent", "Emaki speaks German, French and Russian"),
            text: qsTranslate("WhatsNewContent", "German, French and Russian are available across the desktop, Programs & Updates, the installer and Notes. Choose your language in Settings › Region & Time."),
            note: ""
        },
        {
            id: "release", icon: "command",
            title: qsTranslate("WhatsNewContent", "A command menu and emoji"),
            text: qsTranslate("WhatsNewContent", "Super+K opens every system action in the launcher: settings pages, appearance, Night Light, restart and more. Super+; finds an emoji by name and copies it."),
            note: ""
        },
        {
            id: "release", icon: "capture",
            title: qsTranslate("WhatsNewContent", "Copy text and QR codes from the screen"),
            text: qsTranslate("WhatsNewContent", "In Capture, choose Text or QR code and select an area: Emaki copies what it reads. Recognition runs on your computer."),
            note: ""
        },
        {
            id: "release", icon: "programs",
            title: qsTranslate("WhatsNewContent", "Developer kits and websites as apps"),
            text: qsTranslate("WhatsNewContent", "Programs & Updates installs Python, Node.js, Rust, Go and Docker in one step, and turns a website into its own app in Firefox."),
            note: ""
        },
        {
            id: "release", icon: "reset",
            title: qsTranslate("WhatsNewContent", "Reset Emaki's settings"),
            text: qsTranslate("WhatsNewContent", "Settings › Updates & Recovery returns Emaki's own settings to their defaults. Your files and app settings stay, and Undo brings everything back."),
            note: ""
        },
        {
            id: "release", icon: "fixes",
            title: qsTranslate("WhatsNewContent", "Fixes"),
            text: qsTranslate("WhatsNewContent", "• Numbers, sizes and dates follow your language\n• Translated buttons show their whole label\n• Every launcher tab stays within reach"),
            note: ""
        }
    ];
}
