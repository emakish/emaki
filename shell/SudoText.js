// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
.pragma library
.import "PolkitText.js" as Common

// Shared authentication prompt text.
function unavailable() { return Common.unavailable(); }
function requestUnavailable() { return Common.requestUnavailable(); }
function requesterUnavailable() { return Common.requesterUnavailable(); }
function redacted() { return Common.redacted(); }

// Unapproved typed amount text stays visibly marked until wording is supplied.
function keyMinutesInput() { return qsTranslate("Shell", "Number of minutes"); }
function keyTurnsChoice() { return qsTranslate("Shell", "For a number of turns"); }
function keyTurnsInput() { return qsTranslate("Shell", "Number of turns"); }
function keyMinutesError() { return qsTranslate("Shell", "At least 5 minutes"); }
function keyTurnsError() { return qsTranslate("Shell", "Enter a whole number of turns from 1 to 1000."); }

function keyWarningRequired() { return qsTranslate("Shell", "Read and accept the warning in Settings › AI Assistants first."); }
function keyWarningTitle() { return qsTranslate("Shell", "A sudo key lets an AI agent act as administrator"); }
function keyWarningBody() { return qsTranslate("Shell", "While a key is active, the agent and everything it starts can run any command as administrator without asking you. It can change or delete system files and your files. Any program that can control the agent can use the key too. Emaki never stores your password."); }
function keyWarningLifetime() { return qsTranslate("Shell", "You choose how long: a number of minutes (at least 5), a number of the agent's turns, or the agent's whole session. The key stays through screen lock and sleep. It ends when its time or turns run out, when the agent stops, when you sign out, or when you revoke it: click the key in the panel or press Super+Ctrl+Esc."); }
function keyTimestampPolicy() { return qsTranslate("Shell", "After a key ends, sudo's usual five minutes still apply."); }
function keyWarningRisk() { return qsTranslate("Shell", "Use it at your own risk. Without snapguard, an agent's mistake can't be rolled back."); }
function keySnapguardUnavailable() { return qsTranslate("Shell", "Your system can roll back changes with snapguard. Set it up before you continue."); }
function keySnapguardOffer() { return qsTranslate("Shell", "Your system can roll back changes with snapguard. Set it up before you continue."); }
function keyAcknowledge() { return qsTranslate("Shell", "I understand the risk"); }
function keyNotNow() { return qsTranslate("Shell", "Not now"); }
function keyReview() { return qsTranslate("Shell", "Read the warning"); }
function keySettings() { return qsTranslate("Shell", "Read the risks before you give an AI agent administrator rights."); }
function keySessionRemaining() { return qsTranslate("Shell", "until the agent stops"); }
function keyRevoke() { return qsTranslate("Shell", "Revoke sudo key"); }
function sudoProcessChain() { return qsTranslate("Shell", "Asked by"); }
function sudoCommand() { return qsTranslate("SudoCommand", "Command"); }
function sudoWorkingFolder() { return qsTranslate("Shell", "Folder"); }
function sudoUser() { return qsTranslate("Shell", "Runs as"); }

function keyTurns(count) {
    return qsTranslate("Shell", "%n turn(s)", "", count).replace(/\(s\)/g, count === 1 ? "" : "s");
}
function keyTurnsRemaining(count) {
    return qsTranslate("Shell", "%n turn(s) left", "", Math.max(0, count)).replace(/\(s\)/g, Math.max(0, count) === 1 ? "" : "s");
}
