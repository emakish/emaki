// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
.pragma library
.import "PolkitText.js" as Common

// Shared authentication prompt text.
var unavailable = Common.unavailable;
var requestUnavailable = Common.requestUnavailable;
var requesterUnavailable = Common.requesterUnavailable;
var redacted = Common.redacted;

// Unapproved typed amount text stays visibly marked until wording is supplied.
var keyMinutesInput = "[PLACEHOLDER] Number of minutes";
var keyTurnsChoice = "[PLACEHOLDER] For a number of turns";
var keyTurnsInput = "[PLACEHOLDER] Number of turns";
var keyMinutesError = "At least 5 minutes";
var keyTurnsError = "[PLACEHOLDER] Enter a whole number of turns from 1 to 1000.";

var keyWarningRequired = "Read and accept the warning in Settings › AI Assistants first.";
var keyWarningTitle = "A sudo key lets an AI agent act as administrator";
var keyWarningBody = "While a key is active, the agent and everything it starts can run any command as administrator without asking you. It can change or delete system files and your files. Any program that can control the agent can use the key too. Emaki never stores your password.";
var keyWarningLifetime = "You choose how long: a number of minutes (at least 5), a number of the agent's turns, or the agent's whole session. The key stays through screen lock and sleep. It ends when its time or turns run out, when the agent stops, when you sign out, or when you revoke it: click the key in the panel or press Super+Ctrl+Esc.";
var keyTimestampPolicy = "After a key ends, sudo's usual five minutes still apply.";
var keyWarningRisk = "Use it at your own risk. Without snapguard, an agent's mistake can't be rolled back.";
var keySnapguardUnavailable = "Your system can roll back changes with snapguard. Set it up before you continue.";
var keySnapguardOffer = "Your system can roll back changes with snapguard. Set it up before you continue.";
var keyAcknowledge = "I understand the risk";
var keyNotNow = "Not now";
var keyReview = "Read the warning";
var keySettings = "Read the risks before you give an AI agent administrator rights.";
var keySessionRemaining = "until the agent stops";
var keyRevoke = "Revoke sudo key";
var sudoProcessChain = "Asked by";
var sudoCommand = "Command";
var sudoWorkingFolder = "Folder";
var sudoUser = "Runs as";

function keyTurns(count) {
    return count + (count === 1 ? " turn" : " turns");
}
function keyTurnsRemaining(count) {
    return keyTurns(Math.max(0, count)) + " left";
}
