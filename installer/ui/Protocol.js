// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
// Shared by the QML client and the transcript tests. No secret is retained here.
function initial() {
    return { connected: false, ready: false, serial: 0, pending: {}, version: "", greeted: false,
        inventory: null, plan: null, deadline: 0, jobId: "", lastLogSeq: 0, reservedLogins: null, consoleChars: null, availableLocales: ["en_US.UTF-8"],
        phase: "", phasePct: 0, totalPct: 0, indeterminate: false, activity: null, step: null, skippedPhases: [],
        running: false, confirming: false, planning: false, probing: false,
        outcome: "", error: null, restarting: false, restartState: "", forcedDeadline: 0, preparingSince: 0, rebootMessage: "", notice: qsTranslate("Protocol", "Connecting to the installer…"),
        logs: [], cancelPending: false, cancelMessage: "", started: 0, seconds: 0, doneWarnings: [], secureBoot: null };
}
function invalidate(s) { s.plan = null; s.deadline = 0; }
function request(s, type, fields) {
    if (type === "plan" || type === "probe" || type === "set_timezone") invalidate(s);
    if (type === "plan") s.planning = true;
    if (type === "probe") s.probing = true;
    if (type === "confirm") s.confirming = true;
    if (type === "cancel") s.cancelPending = true;
    if (type === "prepare_reboot") {
        s.restartState = "preparing";
        s.preparingSince = Date.now();
        s.notice = qsTranslate("Protocol", "Preparing to restart. Keep the USB stick connected. This can take up to two and a half minutes. If preparation cannot finish, the computer restarts by itself.");
    }
    if (type === "reboot") { s.restarting = true; s.rebootMessage = qsTranslate("Protocol", "Restarting…"); }
    const id = "ui-" + (++s.serial);
    s.pending[id] = type;
    return Object.assign({ type: type, id: id }, fields || {});
}
function rebootTimedOut(s) {
    s.restarting = false;
    for (const id of Object.keys(s.pending))
        if (s.pending[id] === "reboot") delete s.pending[id];
    s.rebootMessage = recoveryText();
    s.notice = s.rebootMessage;
}
function recoveryText() {
    return qsTranslate("Protocol", "Your installation is safe. If the computer has not restarted by itself, hold the power button to turn it off, then start it again.");
}
function forcedTick(s, now) {
    if (!s.forcedDeadline) return;
    if (now >= s.forcedDeadline + 20000) {
        s.forcedDeadline = 0;
        s.rebootMessage = recoveryText();
        return;
    }
    const seconds = Math.max(0, Math.ceil((s.forcedDeadline - now) / 1000));
    s.rebootMessage = qsTranslate("Protocol", "Restart preparation could not finish. Your installation is safe. Remove the USB stick if it is still connected. ") +
        (seconds ? qsTranslate("Protocol", "Restarting in %n second(s)… Press Enter to restart now.", "", seconds).replace(/\(s\)/g, seconds === 1 ? "" : "s") : qsTranslate("Protocol", "Restarting…"));
}
function disconnectedReboot(s) {
    if (s.restartState !== "forced") s.rebootMessage = recoveryText();
}
function preparationTimedOut(s) {
    // Preserve the pending request so a late preparation reply can still open the prompt.
    s.preparingSince = 0;
    s.notice = qsTranslate("Protocol", "Restart preparation has not been confirmed. ") + recoveryText();
}
function disconnected(s) {
    if (s.restarting && s.restartState !== "forced") rebootTimedOut(s);
    s.connected = false; s.ready = false;
    s.planning = false; s.probing = false;
    s.pending = {};
    // Confirm can have reached the worker before the connection was lost.
    // Keep that intent until hello tells us whether there is a running job.
    invalidate(s);
    if (s.greeted) s.notice = qsTranslate("Protocol", "Connection lost. Reconnecting…");

}
// Copied to RAM, the live system has no boot medium: the worker's unit starts only while it is
// mounted (installer/systemd/emaki-installerd.service), and the offline packages live on it.
// Without this the window says "Connecting to the installer…" forever.
function bootMediumMissing(s) {
    if (!s.greeted) s.notice = qsTranslate("Protocol", "Restart from the USB stick without the 'copy to RAM' option to install.");
}
// Only a plan with a token has a deadline (0 otherwise): a refused plan stays on Review.
function expire(s, now) {
    if (s.plan && s.deadline > 0 && now >= s.deadline) {
        invalidate(s);
        s.notice = qsTranslate("Protocol", "The review expired. Enter your password again to prepare a new plan.");
        return true;
    }
    return false;
}
function receive(s, m, now) {
    const out = [];
    const pending = s.pending[m.for_id || m.id];
    if (m.job_id && s.jobId && m.job_id !== s.jobId && pending !== "confirm") return out;
    switch (m.type) {
    case "hello":
        if (m.proto !== 1) { s.notice = qsTranslate("Protocol", "The installer and this window are incompatible."); break; }
        s.connected = true; s.ready = true; s.greeted = true; s.version = m.emaki_version;
        s.label = typeof m.emaki_label === "string" ? m.emaki_label : "";
        s.notice = "";
        if (Array.isArray(m.available_locales) && m.available_locales.length && m.available_locales.every(x => typeof x === "string"))
            s.availableLocales = m.available_locales.slice();
        // The planner's list (planner.RESERVED_LOGINS); a worker without it keeps the window's own.
        if (Array.isArray(m.reserved_logins) && m.reserved_logins.every(x => typeof x === "string"))
            s.reservedLogins = m.reserved_logins.slice();
        // The console table (latin_layouts.CONSOLE_CHARS) for consoleUnsafeChars; a worker without
        // it leaves the You page without the console sentence.
        if (m.console_chars && typeof m.console_chars === "object" && !Array.isArray(m.console_chars) &&
            Object.values(m.console_chars).every(r => Array.isArray(r) && r.length === 5 && typeof r[0] === "boolean" && r.slice(1).every(x => typeof x === "string")))
            s.consoleChars = m.console_chars;
        if (m.restart && m.restart.state) {
            s.restartState = m.restart.state;
            s.jobId = m.restart.job_id;
            s.running = false; s.outcome = "done"; s.probing = false;
            s.preparingSince = 0;
            if (s.restartState === "forced") {
                s.forcedDeadline = now + m.restart.remaining_s * 1000;
                forcedTick(s, now);
            } else {
                s.forcedDeadline = 0;
                s.rebootMessage = "";
                if (s.restartState === "failed") s.notice = recoveryText();
            }
            break;
        }
        if (m.busy_job || s.jobId) {
            if (m.busy_job && m.busy_job !== s.jobId) {
                s.jobId = m.busy_job; s.lastLogSeq = 0; s.logs = [];
                s.skippedPhases = [];
                s.started = now; s.running = true; s.outcome = "";
            }
            out.push(request(s, "resume", {job_id: s.jobId, since_seq: s.lastLogSeq}));
        } else {
            if (s.confirming) s.notice = qsTranslate("Protocol", "The confirmation could not be recovered. Check the installation log before preparing a new plan.");
            s.confirming = false;
            out.push(request(s, "probe"));
        }
        break;
    case "inventory":
        if (pending !== "probe") break;
        s.inventory = m; s.probing = false;
        break;
    case "plan_ack":
        if (pending !== "plan" && pending !== "renew") break;
        s.planning = false; s.plan = m;
        s.deadline = m.token ? now + m.expires_s * 1000 : 0;
        s.notice = "";
        break;
    case "reply":
        if (pending === "prepare_reboot") {
            s.preparingSince = 0;
            s.restartState = m.ok ? (m.forced_reboot ? "forced" : "ready") : "failed";
            if (m.ok && m.forced_reboot === true) {
                s.forcedDeadline = now + (typeof m.remaining_s === "number" ? m.remaining_s : 15) * 1000;
                forcedTick(s, now);
            }
        }
        if (pending === "reboot") {
            s.restarting = m.ok === true;
            if (m.ok) s.rebootMessage = qsTranslate("Protocol", "Restarting…");
            else if (m.restart_state === "forced") {
                s.restartState = "forced";
                s.forcedDeadline = now + m.remaining_s * 1000;
                forcedTick(s, now);
            } else {
                s.restartState = m.restart_state || "ready";
                s.forcedDeadline = 0;
                // A generic worker message still gets the way out; the target is already safe here.
                const recovery = qsTranslate("Protocol", "Try again, or hold the power button to turn the computer off, then start it again. Your installation is safe.");
                s.rebootMessage = !m.msg ? qsTranslate("Protocol", "Could not restart. ") + recovery
                    : m.recovery_included === true ? m.msg : m.msg + " " + recovery;
            }
        }
        if (pending === "confirm") {
            s.confirming = false;
            if (m.ok) {
                s.jobId = m.job_id; s.lastLogSeq = 0; s.logs = [];
                s.running = true; s.outcome = ""; s.error = null;
                s.cancelPending = false; s.cancelMessage = ""; s.started = now;
                s.phase = "prepare_disk"; s.phasePct = null; s.totalPct = 0; s.indeterminate = true;
                s.activity = null;
                s.step = null;
                s.skippedPhases = [];
                invalidate(s);
            }
        }
        if (pending === "probe") s.probing = false;
        if (pending === "plan") s.planning = false;
        if (pending === "cancel" && !s.running) s.cancelPending = false;
        if (!m.ok && pending !== "set_timezone") {
            s.notice = m.msg || m.code || qsTranslate("Protocol", "The request failed.");
            if (m.code === "token_expired" || m.code === "token_invalid") {
                invalidate(s);
                s.notice += qsTranslate("Protocol", " Enter your password again to prepare a new plan.");
            }
            if (m.code === "job_not_found") {
                s.running = false; s.confirming = false; s.jobId = "";
                s.outcome = "error";
                s.error = {message: qsTranslate("Protocol", "The installer no longer has a record of this installation. Check the installation log before starting again."), retryable: false};
            }
            if (m.code === "busy" && pending !== "save_log") out.push(request(s, "hello", {proto: 1}));
        } else if (pending === "save_log") s.notice = qsTranslate("Protocol", "Log saved to the selected removable medium.");
        else if (pending === "reboot") s.notice = qsTranslate("Protocol", "Restart requested…");
        else if (pending === "prepare_reboot") s.notice = "";
        break;
    case "state": case "progress":
        if (!m.job_id) break;
        if (s.jobId !== m.job_id) s.skippedPhases = [];
        s.jobId = m.job_id; s.running = true; s.confirming = false;
        s.phase = m.phase; s.phasePct = m.phase_pct; s.totalPct = m.total_pct;
        s.indeterminate = m.indeterminate;
        if (Array.isArray(m.skipped_phases)) s.skippedPhases = m.skipped_phases.slice();
        else if (m.phase_pct === null && m.indeterminate === false && !phaseSkipped(s, m.phase))
            s.skippedPhases = s.skippedPhases.concat([m.phase]);
        if (m.notice !== undefined) s.notice = m.notice;
        // A state event has none: the count ends with the step it counted.
        s.activity = m.activity || null;
        s.step = m.step || null;
        if (!s.started) s.started = now;
        break;
    case "log":
        if (!m.job_id || m.seq <= s.lastLogSeq) break;
        s.lastLogSeq = m.seq;
        s.logs = s.logs.concat([m.line]).slice(-100);
        break;
    case "cancel_ack":
        s.cancelPending = false;
        s.cancelMessage = m.disk_changed ? qsTranslate("Protocol", "Cancelled at a phase boundary. Disk changes have not been undone.") : qsTranslate("Protocol", "Cancelled before any disk changes.");
        break;
    case "error":
        s.error = m; s.running = false; s.confirming = false;
        s.outcome = "error"; s.cancelPending = false;
        break;
    case "done":
        s.running = false; s.confirming = false; s.outcome = "done";
        s.seconds = m.seconds; s.totalPct = 100; s.cancelPending = false;
        s.doneWarnings = m.warnings || [];
        // Only a well-formed one-time password is shown. esp_label names the EFI partition in
        // MokManager's "Enroll key from disk" list: a FAT label the installer gave it, else null.
        s.secureBoot = m.secure_boot && /^[0-9]{8}$/.test(m.secure_boot.password || "") ? {
            password: m.secure_boot.password,
            espLabel: typeof m.secure_boot.esp_label === "string" && /^[A-Z0-9]{1,11}$/.test(m.secure_boot.esp_label) ? m.secure_boot.esp_label : null
        } : null;
        break;
    }
    if (m.id) delete s.pending[m.id];
    return out;
}
// The worker retains one running operation until its proven boundary. Log events
// and elapsed-time ticks cannot replace it or invent progress.
function stepText(step) {
    return step && step.state === "running" && typeof step.id === "string"
        && typeof step.text === "string" ? step.text : "";
}
// The current phase row while a long step runs (worker.py Worker.activity); "" for a step this
// window does not know, which then shows its busy word.
function activityText(a) {
    if (!a || typeof a !== "object")
        return "";
    const counted = Number.isInteger(a.done) && Number.isInteger(a.total) && a.total > 0;
    if (a.name === "signatures")
        return counted ? qsTranslate("Protocol", "Checking package signatures… %1 of %2").arg(number(a.done, 0)).arg(number(a.total, 0)) : qsTranslate("Protocol", "Checking package signatures…");
    // Without a count while pacman downloads the package lists.
    if (a.name === "downloads")
        return counted ? qsTranslate("Protocol", "Downloading updates… %1 of %2").arg(number(a.done, 0)).arg(number(a.total, 0)) : qsTranslate("Protocol", "Downloading updates…");
    return "";
}
// The error page in plain words: what happened and one thing to do, per worker error code
// (installer/emaki_installer/errors.py). The worker's diagnostic goes under "Show details";
// a code missing here shows that message as it was sent.
function errorPresentation(error) {
    if (error && ["bad_config", "login_name_reserved", "secure_boot", "clock_skew"].indexOf(error.code) >= 0 && error.message)
        return {sentence: error.message, action: "", details: ""};
    const save = qsTranslate("Protocol", "Save the log below; it records what the installer did.");
    const usb = qsTranslate("Protocol", "Check that the USB stick is still connected.");
    const disk = qsTranslate("Protocol", "Check the choices on the Disk step.");
    const review = qsTranslate("Protocol", "Prepare a new review.");
    const another = qsTranslate("Protocol", "Choose another disk.");
    const larger = qsTranslate("Protocol", "Choose a larger disk.");
    const table = {
        bad_request: [qsTranslate("Protocol", "The installer could not read a request from this window."), save],
        bad_config: [qsTranslate("Protocol", "The installer could not use one of the chosen settings."), save],
        login_name_reserved: [qsTranslate("Protocol", "This login name belongs to the system."), qsTranslate("Protocol", "Choose another name; the disk has not been changed.")],
        bad_dest: [qsTranslate("Protocol", "The log could not be written to the chosen removable medium."), qsTranslate("Protocol", "Choose another medium from the list.")],
        busy: [qsTranslate("Protocol", "The installer was busy with another task."), save],
        restart_pending: [qsTranslate("Protocol", "A previous restart is still pending."), qsTranslate("Protocol", "Wait for it to finish, then try again.")],
        unsupported_mode: [qsTranslate("Protocol", "This type of installation is not available in this version."), disk],
        unsupported_version: [qsTranslate("Protocol", "The installer's parts on this USB stick do not match each other."), save],
        uefi_required: [qsTranslate("Protocol", "Installing needs a computer started in 64-bit UEFI mode."), qsTranslate("Protocol", "If the computer's boot menu offers a UEFI entry for the USB stick, choose it there.")],
        disk_not_found: [qsTranslate("Protocol", "The chosen disk is no longer there."), qsTranslate("Protocol", "Check that the disk is connected.")],
        boot_medium: [qsTranslate("Protocol", "The chosen disk is the USB stick Emaki is running from."), another],
        disk_busy: [qsTranslate("Protocol", "The chosen disk or one of its partitions is in use."), qsTranslate("Protocol", "Close any program or window that uses it.")],
        encrypted_confirmation: [qsTranslate("Protocol", "The installer could not confirm permission to erase the chosen volume."), disk],
        disk_too_small: [qsTranslate("Protocol", "The chosen disk is smaller than the 24 GiB Emaki needs."), larger],
        disk_changed: [qsTranslate("Protocol", "The disk changed after the review."), review],
        unsafe_disk: [qsTranslate("Protocol", "A safety check of the disk did not pass."), save],
        manual_layout: [qsTranslate("Protocol", "The partitions cannot be used as they were assigned."), disk],
        esp_space: [qsTranslate("Protocol", "The disk's startup partition cannot be used."), disk],
        root_too_small: [qsTranslate("Protocol", "There is not enough room for Emaki on the chosen partition."), disk],
        shrink_bounds: [qsTranslate("Protocol", "Windows cannot be made smaller as planned."), disk],
        token_invalid: [qsTranslate("Protocol", "The confirmation did not match the review."), review],
        token_expired: [qsTranslate("Protocol", "The review expired before the installation started."), review],
        job_not_found: [qsTranslate("Protocol", "The installer no longer has a record of this installation."), save],
        offline_repo: [qsTranslate("Protocol", "The installer could not use the package source on the USB stick."), usb],
        offline_repo_incomplete: [qsTranslate("Protocol", "Some packages on the USB stick are missing or could not be verified."), usb],
        command_failed: [qsTranslate("Protocol", "A program the installer runs stopped with an error."), save],
        boot_verify: [qsTranslate("Protocol", "The startup files of the new system did not pass their check."), save],
        fstab_verify: [qsTranslate("Protocol", "The list of disks the new system mounts at startup did not pass its check."), save],
        cleanup_failed: [qsTranslate("Protocol", "The installer could not detach the new system's disks."), save],
        cancelled: [qsTranslate("Protocol", "The installation was cancelled."), save],
        internal: [qsTranslate("Protocol", "The installer stopped because of an internal error."), save]
    };
    const words = error && Object.prototype.hasOwnProperty.call(table, error.code) ? table[error.code] : null;
    if (!words)
        return {sentence: (error && error.message) || qsTranslate("Protocol", "The installer could not continue."), action: "", details: ""};
    // Without "Try again" the person cannot return to the Disk step or prepare a new review.
    const action = error.retryable || [disk, review, another, larger].indexOf(words[1]) < 0 ? words[1] : save;
    // The ERASE field belongs to Disk. Keep the facts here, without instructions to type into it.
    const details = error.code === "encrypted_confirmation"
        ? (error.message || "").replace(qsTranslate("Protocol", "Type ERASE to confirm that this volume and any connected data can be erased."), "").replace(/\s*Type ERASE to confirm[^.]*\./g, "").trim()
        : error.message || "";
    return {sentence: error.code === "disk_busy" && error.message ? error.message : words[0], action: action, details: details};
}
function loginFromName(name) {
    return name.toLowerCase().replace(/[^a-z0-9 _-]/g, "").trim().replace(/\s+/g, "-").replace(/^[^a-z_]+/, "").slice(0, 32);
}
// The layout a disk passphrase is typed in at startup: GRUB reads US key positions
// (installer/emaki_installer/render.py, unlock_layout(layouts, 'grub')).
function unlockLayout(layouts) {
    return "us";
}
function sameLayouts(a, b) {
    return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((x, i) => x === b[i]);
}
function diskPasswordError(password) {
    if (!password || /[^\x20-\x7e]/.test(password) || password.length > 1024)
        return qsTranslate("Protocol", "Use characters available on an English (US) keyboard for the startup password (at most 1024 characters).");
    if (password.length < 8)
        return qsTranslate("Protocol", "Use at least 8 characters for the startup password.");
    return "";
}

// One message per field ("" when the field is fine). reserved: the worker's list from hello
// (session.reservedLogins).
function accountErrors(name, login, password, confirm, hostname, reserved) {
    return {
        name: Array.from(name).length > 128 || /[\x00-\x1f:\x7f]/.test(name) ? qsTranslate("Protocol", "Your name must be at most 128 characters and contain no colon or control characters.") : "",
        login: !/^[a-z_][a-z0-9_-]{0,31}$/.test(login) || (reserved || ["root", "greeter", "live", "nobody"]).indexOf(login) !== -1
            ? qsTranslate("Protocol", "Use 1–32 lowercase letters, digits, _ or - for your login, starting with a letter or _. This login must not be reserved.") : "",
        // New login passwords use the same printable keyboard characters as startup passwords.
        password: !password || unescape(encodeURIComponent(password)).length > 1024
            ? qsTranslate("Protocol", "Enter a password without line breaks, tabs or other control characters (at most 1024 UTF-8 bytes).")
            : /[^\x20-\x7e]/.test(password)
                ? qsTranslate("Protocol", "Use only letters, digits and symbols of the English (US) keyboard.") : "",
        confirm: password !== confirm ? qsTranslate("Protocol", "The passwords do not match.") : "",
        hostname: !/^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$/.test(hostname) ? qsTranslate("Protocol", "Computer name: 1–63 letters, digits or hyphens, beginning and ending with a letter or digit.") : ""
    };
}
// The password's characters the text console types differently, each once in code point order;
// "" without the worker's table. planner.console_unsafe_chars says why; installer/ui/tests/
// test_console_chars.py holds the two to the same answers. The password never leaves the window.
function consoleUnsafeChars(table, layouts, password) {
    if (!table || !layouts || !layouts.length)
        return "";
    const record = name => Object.prototype.hasOwnProperty.call(table, name) ? table[name] : null;
    const has = (text, c) => Array.from(text).indexOf(c) >= 0;
    const first = record(layouts[0]);
    function safe(c) {
        const printable = c >= " " && c <= "~";
        if (!first)
            return printable;
        if (!printable)
            return has(first[2], c);
        if (!has(first[1], c))
            return true;
        if (!first[0] || !has(first[4], c))
            return false;
        // Typed in a later layout; its keys must be the English (US) ones the console types.
        for (const name of Array.from(layouts).slice(1)) {
            const later = record(name);
            if (!later)
                return true;
            if (!has(later[4], c))
                return !has(later[3], c);
        }
        return false;
    }
    return Array.from(new Set(Array.from(password))).filter(c => !safe(c)).sort((a, b) => a.codePointAt(0) - b.codePointAt(0)).join("");
}
// Characters a list cannot show as themselves, by their Unicode names ("" for unassigned and
// private-use code points): the tab and every separator, format character, unassigned or
// private-use code point an offered layout types. installer/ui/tests/test_console_chars.py
// holds the names to Unicode's and the list to what the layouts type.
function unseenNames() { return {
    0x0009: qsTranslate("Protocol", "tab"), 0x00a0: qsTranslate("Protocol", "no-break space"), 0x00ad: qsTranslate("Protocol", "soft hyphen"), 0x061c: qsTranslate("Protocol", "arabic letter mark"),
    0x09b3: "", 0x09bb: "", 0x0f48: "", 0x17fb: "", 0x17fc: "", 0x17fd: "", 0x17fe: "", 0x17ff: "",
    0x200b: qsTranslate("Protocol", "zero width space"), 0x200c: qsTranslate("Protocol", "zero width non-joiner"), 0x200d: qsTranslate("Protocol", "zero width joiner"),
    0x200e: qsTranslate("Protocol", "left-to-right mark"), 0x200f: qsTranslate("Protocol", "right-to-left mark"), 0x202a: qsTranslate("Protocol", "left-to-right embedding"),
    0x202b: qsTranslate("Protocol", "right-to-left embedding"), 0x202c: qsTranslate("Protocol", "pop directional formatting"),
    0x202d: qsTranslate("Protocol", "left-to-right override"), 0x202e: qsTranslate("Protocol", "right-to-left override"),
    0x202f: qsTranslate("Protocol", "narrow no-break space"), 0x2066: qsTranslate("Protocol", "left-to-right isolate"),
    0x2067: qsTranslate("Protocol", "right-to-left isolate"), 0x2068: qsTranslate("Protocol", "first strong isolate"),
    0x2069: qsTranslate("Protocol", "pop directional isolate"), 0xe60b: "", 0xe656: "", 0xe659: ""
}; }
// One character of the console sentence: itself, or "U+00A0 no-break space" for one a list
// cannot show (and any other control character, by its code point).
function shownCharacter(c) {
    const UNSEEN_NAMES = unseenNames();
    const code = c.codePointAt(0);
    const hex = "U+" + code.toString(16).toUpperCase().padStart(4, "0");
    if (Object.prototype.hasOwnProperty.call(UNSEEN_NAMES, code))
        return UNSEEN_NAMES[code] ? hex + " " + UNSEEN_NAMES[code] : hex;
    return code < 0x20 || (code >= 0x7f && code < 0xa0) ? hex : c;
}
// The sentence under the account password on the You page; it does not block Continue.
function consoleWarning(table, layouts, password) {
    const chars = consoleUnsafeChars(table, layouts, password);
    return chars ? qsTranslate("Protocol", "The text console types these characters differently: %1. Choose a password without them if you may need the console.").arg(Array.from(chars).map(shownCharacter).join(" ")) : "";
}
// How many characters one edit of a field brought in: the text between what before and after
// share at the start and at the end. One typed key brings one; a paste or an input method's
// string can bring more.
function insertedLength(before, after) {
    const a = Array.from(before), b = Array.from(after);
    let start = 0;
    while (start < a.length && start < b.length && a[start] === b[start])
        ++start;
    let end = 0;
    while (end < a.length - start && end < b.length - start && a[a.length - 1 - end] === b[b.length - 1 - end])
        ++end;
    return b.length - start - end;
}
// The first problem, in the order the rules were always checked.
function accountError(name, login, password, confirm, hostname, reserved) {
    const errors = accountErrors(name, login, password, confirm, hostname, reserved);
    return errors.login || errors.name || errors.password || errors.confirm || errors.hostname;
}
function diskReason(d) {
    if (d.reason) return typeof d.reason === "string" ? d.reason : d.reason.msg;
    if (d.refusal_reason) return d.refusal_reason;
    if (d.is_boot_medium) return qsTranslate("Protocol", "The live boot medium cannot be a target.");
    if ((d.partitions || []).some(p => !!p.mountpoint)) return qsTranslate("Protocol", "A partition on this disk is mounted.");
    if (d.size_bytes < 24 * 1073741824) return qsTranslate("Protocol", "The disk must be at least 24 GiB.");
    return "";
}
// The systems the inventory found on a disk (os_hint), for its card: a fact, not an offer.
function diskContents(d) {
    const hints = (d.partitions || []).map(p => p.os_hint);
    return [["windows", qsTranslate("Protocol", "Windows")], ["macos", "macOS"]].filter(row => hints.indexOf(row[0]) >= 0).map(row => qsTranslate("Protocol", " · contains %1").arg(row[1])).join("");
}
// Keep the worker's specific refusal on the disk card before any selection.
function alongsideRefusal(d) {
    if (!d || alongside(d) || !(d.partitions || []).some(p => p.os_hint === "windows")) return "";
    const partition = (d.partitions || []).find(p => p.shrink && p.shrink.reason);
    const reason = d.shrink?.reason || partition?.shrink.reason || "";
    return reason ? qsTranslate("Protocol", "\nInstall alongside Windows is not possible here. %1").arg(reason) : "";
}
// planner.manual_partitions refuses every table but GPT; an empty disk gets a new GPT table in the
// partition editor (planner.partition_edits new_table).
function manualReason(d) {
    return d && d.partition_table && d.partition_table !== "gpt" && (d.partitions || []).length ? qsTranslate("Protocol", "Manual installation needs a GPT disk.") : "";
}

// The partition editor (manual mode): the rules of planner.partition_edits, for the window. Nothing
// is written until Install: the edits travel in the plan as config.partitioning.
var MIB = 1048576;
var GIB = 1073741824;
// planner.ESP_FORMAT_MIN, SWAP_MIN, DATA_MIN; root has its own minimum (rootMinimum).
var EDITOR_MINIMUM = { "/efi": 512 * MIB, "swap": 256 * MIB, "/home": GIB };
var EDITOR_KINDS = { "/": "linux", "/home": "linux", "/efi": "esp", "swap": "swap" };
function emptyEdits() {
    return { new_table: false, delete: [], create: [] };
}
// The config's partitioning, or null when nothing changes.
function editsConfig(e) {
    if (!e || !(e.new_table || e.delete.length || e.create.length)) return null;
    const value = { delete: e.delete.slice(), create: e.create.slice() };
    if (e.new_table) value.new_table = true;
    return value;
}
// planner.usable_end: 1 MiB stays for the backup GPT.
function usableEnd(d) {
    return Math.floor((d.size_bytes - MIB) / MIB) * MIB;
}
// An empty disk (no partitions) can get a new GPT table; one with GPT already needs none, and a
// locked volume over the whole disk is cleared by Erase, with its typed ERASE (planner.partition_edits).
function canNewTable(d) {
    return !!d && !(d.partitions || []).length && d.partition_table !== "gpt" && !(d.closed_encrypted || []).length;
}
// The GPT numbers new partitions get: the lowest entries no kept partition uses, in order.
function newNumbers(d, e) {
    const gone = e.delete.map(row => row.partition_id);
    const used = (d.partitions || []).filter(p => gone.indexOf(p.id) < 0).map(p => p.number);
    const numbers = [];
    for (let n = 1; n <= 128 && numbers.length < e.create.length; ++n)
        if (used.indexOf(n) < 0) numbers.push(n);
    return numbers;
}
// The disk as the editor shows it, in disk order: kept partitions, deleted ones (still listed, their
// space free), new ones and the free extents left (inventory._extents: whole MiB, past 1 MiB).
function editorLayout(d, e) {
    if (!d) return [];
    e = e || emptyEdits();
    const gone = e.delete.map(row => row.partition_id);
    const numbers = newNumbers(d, e);
    const rows = (d.partitions || []).map(p => ({
        kind: gone.indexOf(p.id) >= 0 ? "deleted" : "partition", id: p.id, number: p.number,
        start: p.start_bytes, size: p.size_bytes, partition: p
    }));
    e.create.forEach((c, i) => rows.push({ kind: "new", id: c.id, number: numbers[i], start: c.start_bytes, size: c.size_bytes, create: c }));
    const taken = rows.filter(r => r.kind !== "deleted").sort((a, b) => a.start - b.start);
    let cursor = MIB;
    const end = usableEnd(d);
    const free = [];
    for (const r of taken) {
        const start = Math.floor(r.start / MIB) * MIB;
        if (start > cursor) free.push({ kind: "free", id: "free-" + cursor, start: cursor, size: start - cursor });
        cursor = Math.max(cursor, Math.ceil((r.start + r.size) / MIB) * MIB);
    }
    if (end > cursor) free.push({ kind: "free", id: "free-" + cursor, start: cursor, size: end - cursor });
    return rows.concat(free).sort((a, b) => a.start - b.start || (a.kind === "deleted" ? -1 : 1));
}
// Whole MiB from a size typed in GiB ("12", "12.5", "0.5"); NaN for anything else.
function sizeFromGiB(text) {
    const value = String(text).trim();
    if (!/^[0-9]+([.,][0-9]+)?$/.test(value)) return NaN;
    return Math.round(Number(value.replace(",", ".")) * 1024) * MIB;
}
// Why a new partition cannot be made in a free extent: "" when it can. root: the root minimum.
function newPartitionError(free, use, size, root) {
    if (!free) return qsTranslate("Protocol", "Choose free space first.");
    if (!EDITOR_KINDS[use]) return qsTranslate("Protocol", "Choose what the new partition is for.");
    if (!(size > 0)) return qsTranslate("Protocol", "Type the size in GiB, for example 40 or 0.5.");
    if (size > free.size) return qsTranslate("Protocol", "Only %1 GiB of free space is here.").arg(number(free.size / GIB, 2));
    const least = use === "/" ? root : EDITOR_MINIMUM[use];
    if (size < least) return (use === "/" ? qsTranslate("Protocol", "Root needs at least %1 GiB.") : use === "/efi" ? qsTranslate("Protocol", "An EFI system partition needs at least %1 GiB.") : use === "swap" ? qsTranslate("Protocol", "Swap needs at least %1 GiB.") : qsTranslate("Protocol", "A new partition needs at least %1 GiB.")).arg(number(least / GIB, 2));
    return "";
}
// What a deletion of another system's partition must confirm (planner.deletion_identity).
function deletionIdentity(p) {
    return { path: p.path, start_bytes: p.start_bytes, size_bytes: p.size_bytes, system: p.other_system };
}
function systemLabel(value) {
    if (value === "another system") return qsTranslate("Protocol", "another system");
    if (value === "files Emaki cannot check") return qsTranslate("Protocol", "files Emaki cannot check");
    return value || "";
}
// One line per partition row: what it is now.
function partitionText(p) {
    return [p.path, qsTranslate("Protocol", "%1 GiB").arg(number(p.size_bytes / GIB, 1)), p.fs || qsTranslate("Protocol", "unformatted"), p.esp ? "EFI" : "", systemLabel(p.other_system)].filter(x => x).join(" · ");
}
// planner.storage_layout: 20 GiB, plus the RAM-sized hibernation file, plus the encryption header.
function rootMinimum(memory, hibernation, encrypted) {
    return 20 * 1073741824 + (hibernation ? memory : 0) + (encrypted ? 16 * 1048576 : 0);
}
function alongside(d) {
    return !!alongsidePartition(d);
}
function alongsidePartition(d) {
    if (!d || diskReason(d)) return null;
    return (d.partitions || []).find(p => p.os_hint === "windows" &&
        ["ntfs", "ntfs3"].indexOf(p.fs) >= 0 && p.shrink && !p.shrink.reason &&
        Number.isSafeInteger(p.shrink.min_bytes) && p.shrink.min_bytes > 0 &&
        Number.isSafeInteger(p.shrink.max_free_bytes) && p.shrink.max_free_bytes >= 32 * 1073741824) || null;
}
function alongsideDefault(p) {
    return p ? Math.min(p.shrink.max_free_bytes, Math.max(32 * 1073741824,
        Math.floor(p.shrink.max_free_bytes / 2 / 1048576) * 1048576)) : 0;
}
function alongsideReview(p, freed) {
    return p ? qsTranslate("Protocol", "Windows keeps %1 GB, Emaki gets %2 GB; back up your files first.").arg(number((Math.floor(p.size_bytes / 1048576) * 1048576 - freed) / 1e9, 2)).arg(number(freed / 1e9, 2)) : qsTranslate("Protocol", "Windows shrink is unavailable. Refresh the disk list.");
}

function phaseSkipped(s, phase) {
    return (s.skippedPhases || []).indexOf(phase) >= 0;
}

// Compare the public identity and table, not transient probe offers or free-space estimates.
// This preserves only editor input; every review still needs a fresh server plan.
function diskLayoutKey(d) {
    if (!d || !d.layout_id) return "";
    const parts = (d.partitions || []).map(p => [p.id, p.path, p.number, p.uuid,
        p.start_bytes, p.size_bytes, p.fs, p.esp, p.label]);
    parts.sort((a, b) => String(a[0]).localeCompare(String(b[0])));
    return JSON.stringify([d.layout_id, d.id, d.path, d.model, d.size_bytes, d.bus, d.partition_table, parts]);
}

function skippedPhaseText(phase) {
    return phase === "snapshot" ? qsTranslate("Protocol", "Skipped: snapshots need btrfs") : qsTranslate("Protocol", "Skipped");
}

// Preserve the ungrouped decimal sizes used by the installer.
function number(value, digits) {
    return value.toFixed(digits);
}
