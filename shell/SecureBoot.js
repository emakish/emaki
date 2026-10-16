// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
.pragma library

// The answer of `emaki-secureboot status --json`, written by root at boot and after every
// signing. Machines without the Secure Boot machine key (key false or unknown) show nothing at
// all. `signed` maps each startup file to whether it verifies with the key.
function valid(status) {
    return !!status && typeof status === "object" && status.schema_version === 1
        && [true, false, null].includes(status.secure_boot) && [true, false, null].includes(status.key)
        && ["enrolled", "pending", "missing", null].includes(status.enrollment)
        && !!status.signed && typeof status.signed === "object" && !Array.isArray(status.signed)
        && Object.values(status.signed).every(value => value === true || value === false);
}

function shown(status) {
    return valid(status) && status.key === true;
}

// Root has not written its copy yet (the session started before the boot unit finished): the
// answer is well-formed but does not know whether a key exists. Ask again later.
function unpublished(status) {
    return valid(status) && status.key === null;
}

// The kernels in /boot that do not verify with the key, such as one an update could not sign.
// The firmware switch does not matter: with it off now, Emaki stops starting once it is on.
// fwupd's loader is left out: unsigned, it stops firmware updates, not Emaki.
function unsigned(status) {
    if (!shown(status))
        return [];
    return Object.keys(status.signed).filter(name => name.startsWith("/boot/") && status.signed[name] === false).sort();
}

// A new confirmation helps when the key is not in shim's list, or when a request waits but
// Secure Boot is off (its password may be lost; a new request replaces it).
function offer(status) {
    return shown(status) && (status.enrollment === "missing"
        || (status.enrollment === "pending" && status.secure_boot === false));
}

// The session notice: Emaki runs with Secure Boot off and the key is not confirmed.
function notice(status) {
    return offer(status) && status.secure_boot === false;
}

// Which notice the session shows: unsigned files first, then the unconfirmed key, or none.
function noticeKind(status) {
    if (unsigned(status).length > 0)
        return "unsigned";
    return notice(status) ? "confirm" : "";
}

function stateText(status) {
    if (unsigned(status).length > 0)
        return "Some startup files are not signed, so Emaki will not start with Secure Boot on. Free some disk space, then choose Sign again.";
    if (!shown(status) || status.enrollment === null)
        return "The Secure Boot state could not be read.";
    if (status.enrollment === "pending")
        return "A confirmation is waiting for the next restart.";
    if (status.enrollment === "missing")
        return "This computer's key is not confirmed yet. Until it is, Emaki cannot start with Secure Boot on.";
    if (status.secure_boot === true)
        return "Secure Boot is on. Emaki signs its startup files with this computer's key.";
    if (status.secure_boot === false)
        return "Secure Boot is off. This computer's key is confirmed, so you can turn Secure Boot on in your firmware settings.";
    return "This computer's key is confirmed.";
}

function validPassword(answer) {
    return !!answer && answer.schema_version === 1 && answer.status === "queued"
        && typeof answer.password === "string" && /^[0-9]{8}$/.test(answer.password);
}

var title = "Secure Boot";
var statusTitle = "Status";
var confirmTitle = "Confirm the key";
var confirmExplanation = "Get a one-time password, then confirm the key on the blue screen at the next restart.";
var confirmButton = "Get password";
var passwordTitle = "One-time password";
var steps = "Restart the computer. When the blue screen appears, press any key within 10 seconds, choose Enroll MOK, then Continue and Yes, type this password and choose Reboot.";
var afterSteps = "After that, turn Secure Boot on in your firmware settings.";
var cancelled = "Authentication was cancelled.";
var failed = "A new confirmation could not be set up. Try again.";
// The session notices' headlines fit one line of the preview beside "Secure Boot · 59 min";
// the bodies wrap in full.
var noticeSummary = "Finish setting up Secure Boot.";
var noticeBody = "This computer's key is not confirmed, so Emaki cannot start with Secure Boot on. Open Settings to get a one-time password, then confirm the key at the next restart.";
var noticeAction = "Open Settings";
var signTitle = "Sign the startup files";
var signExplanation = "Sign the startup files again with this computer's key.";
var signButton = "Sign again";
var signFailed = "The startup files could not be signed. Free some disk space and try again.";
var unsignedSummary = "Startup files are not signed.";
var unsignedBody = "An update could not sign them, so Emaki will not start with Secure Boot on. Free some disk space, then open Settings and choose Sign again.";
