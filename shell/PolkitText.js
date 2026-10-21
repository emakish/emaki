// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
.pragma library

// Approved authentication prompt text.
function unavailable() { return qsTranslate("Shell", "Not available"); }
function requesterUnavailable() { return qsTranslate("Shell", "Unknown program"); }
function requestUnavailable() { return qsTranslate("Shell", "A program asks for administrator rights."); }
function runProgram(name, user) {
    return qsTranslate("Shell", "Enter an administrator password to run %1 as %2.").replace(/%([12])/g, (_, index) => index === "1" ? (name || qsTranslate("Shell", "a program")) : (user || qsTranslate("Shell", "administrator")));
}
function messageLabel() { return qsTranslate("Shell", "Message"); }
function detailsLabel() { return qsTranslate("Shell", "Details from the program"); }
function redacted() { return qsTranslate("Shell", "Hidden"); }
function cancelledBody() { return qsTranslate("Shell", "Nothing was done. The program was told you said no."); }
function lockedTitle() { return qsTranslate("Shell", "Not done — the screen was locked"); }
function blockedTitle() { return qsTranslate("Shell", "Not done — password requests are paused"); }
function unsupported() { return qsTranslate("Shell", "This request can't be answered with a password."); }
function keyUnavailable() { return qsTranslate("Shell", "A sudo key can't be given right now."); }
function sudoInterruptedTitle() { return qsTranslate("Shell", "Sudo request cancelled"); }
function sudoInterruptedBody() { return qsTranslate("Shell", "This sudo request ended before authorization was completed."); }
