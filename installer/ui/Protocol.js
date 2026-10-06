// Shared by the QML client and the transcript tests. No secret is retained here.
function initial() {
    return { connected: false, ready: false, serial: 0, pending: {}, version: "", greeted: false,
        inventory: null, plan: null, deadline: 0, jobId: "", lastLogSeq: 0, reservedLogins: null, consoleChars: null,
        phase: "", phasePct: 0, totalPct: 0, indeterminate: false, activity: null,
        running: false, confirming: false, planning: false, probing: false,
        outcome: "", error: null, notice: "Connecting to the installer…",
        logs: [], cancelPending: false, cancelMessage: "", started: 0, seconds: 0, doneWarnings: [] };
}
function invalidate(s) { s.plan = null; s.deadline = 0; }
function request(s, type, fields) {
    if (type === "plan" || type === "probe" || type === "set_timezone") invalidate(s);
    if (type === "plan") s.planning = true;
    if (type === "probe") s.probing = true;
    if (type === "confirm") s.confirming = true;
    if (type === "cancel") s.cancelPending = true;
    const id = "ui-" + (++s.serial);
    s.pending[id] = type;
    return Object.assign({ type: type, id: id }, fields || {});
}
function disconnected(s) {
    s.connected = false; s.ready = false;
    s.planning = false; s.probing = false;
    s.pending = {};
    // Confirm can have reached the worker before the connection was lost.
    // Keep that intent until hello tells us whether there is a running job.
    invalidate(s);
    if (s.greeted) s.notice = "Connection lost. Reconnecting…";
}
// Copied to RAM, the live system has no boot medium: the worker's unit starts only while it is
// mounted (installer/systemd/emaki-installerd.service), and the offline packages live on it.
// Without this the window says "Connecting to the installer…" forever.
function bootMediumMissing(s) {
    if (!s.greeted) s.notice = "Restart from the USB stick without the 'copy to RAM' option to install.";
}
// Only a plan with a token has a deadline (0 otherwise): a refused plan stays on Review.
function expire(s, now) {
    if (s.plan && s.deadline > 0 && now >= s.deadline) {
        invalidate(s);
        s.notice = "The review expired. Enter your password again to prepare a new plan.";
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
        if (m.proto !== 1) { s.notice = "This worker uses an unsupported protocol."; break; }
        s.connected = true; s.ready = true; s.greeted = true; s.version = m.emaki_version;
        s.label = typeof m.emaki_label === "string" ? m.emaki_label : "";
        s.notice = "";
        // The planner's list (planner.RESERVED_LOGINS); a worker without it keeps the window's own.
        if (Array.isArray(m.reserved_logins) && m.reserved_logins.every(x => typeof x === "string"))
            s.reservedLogins = m.reserved_logins.slice();
        // The console table (latin_layouts.CONSOLE_CHARS) for consoleUnsafeChars; a worker without
        // it leaves the You page without the console sentence.
        if (m.console_chars && typeof m.console_chars === "object" && !Array.isArray(m.console_chars) &&
            Object.values(m.console_chars).every(r => Array.isArray(r) && r.length === 5 && typeof r[0] === "boolean" && r.slice(1).every(x => typeof x === "string")))
            s.consoleChars = m.console_chars;
        if (m.busy_job || s.jobId) {
            if (m.busy_job && m.busy_job !== s.jobId) {
                s.jobId = m.busy_job; s.lastLogSeq = 0; s.logs = [];
                s.started = now; s.running = true; s.outcome = "";
            }
            out.push(request(s, "resume", {job_id: s.jobId, since_seq: s.lastLogSeq}));
        } else {
            if (s.confirming) s.notice = "The confirmation could not be recovered. Check the worker log before preparing a new plan.";
            s.confirming = false;
            out.push(request(s, "probe"));
        }
        break;
    case "inventory":
        if (pending !== "probe") break;
        s.inventory = m; s.probing = false;
        break;
    case "plan_ack":
        if (pending !== "plan") break;
        s.planning = false; s.plan = m;
        s.deadline = m.token ? now + m.expires_s * 1000 : 0;
        s.notice = "";
        break;
    case "reply":
        if (pending === "confirm") {
            s.confirming = false;
            if (m.ok) {
                s.jobId = m.job_id; s.lastLogSeq = 0; s.logs = [];
                s.running = true; s.outcome = ""; s.error = null;
                s.cancelPending = false; s.cancelMessage = ""; s.started = now;
                s.phase = "prepare_disk"; s.phasePct = null; s.totalPct = 0; s.indeterminate = true;
                s.activity = null;
                invalidate(s);
            }
        }
        if (pending === "probe") s.probing = false;
        if (pending === "plan") s.planning = false;
        if (pending === "cancel" && !s.running) s.cancelPending = false;
        if (!m.ok && pending !== "set_timezone") {
            s.notice = m.msg || m.code || "The request failed.";
            if (m.code === "token_expired" || m.code === "token_invalid") {
                invalidate(s);
                s.notice += " Enter your password again to prepare a new plan.";
            }
            if (m.code === "job_not_found") {
                s.running = false; s.confirming = false; s.jobId = "";
                s.outcome = "error";
                s.error = {message: "The worker no longer has this job. Inspect the installation log before starting again.", retryable: false};
            }
            if (m.code === "busy") out.push(request(s, "hello", {proto: 1}));
        } else if (pending === "save_log") s.notice = "Log saved to the selected removable medium.";
        else if (pending === "reboot") s.notice = "Restart requested…";
        break;
    case "state": case "progress":
        if (!m.job_id) break;
        s.jobId = m.job_id; s.running = true; s.confirming = false;
        s.phase = m.phase; s.phasePct = m.phase_pct; s.totalPct = m.total_pct;
        s.indeterminate = m.indeterminate;
        // A state event has none: the count ends with the step it counted.
        s.activity = m.activity || null;
        if (!s.started) s.started = now;
        break;
    case "log":
        if (!m.job_id || m.seq <= s.lastLogSeq) break;
        s.lastLogSeq = m.seq;
        s.logs = s.logs.concat([m.line]).slice(-100);
        break;
    case "cancel_ack":
        s.cancelPending = false;
        s.cancelMessage = m.disk_changed ? "Cancelled at a phase boundary. Disk changes have not been undone." : "Cancelled before any disk changes.";
        break;
    case "error":
        s.error = m; s.running = false; s.confirming = false;
        s.outcome = "error"; s.cancelPending = false;
        break;
    case "done":
        s.running = false; s.confirming = false; s.outcome = "done";
        s.seconds = m.seconds; s.totalPct = 100; s.cancelPending = false;
        s.doneWarnings = m.warnings || [];
        break;
    }
    if (m.id) delete s.pending[m.id];
    return out;
}
// The current phase row while a long step runs (worker.py Worker.activity); "" for a step this
// window does not know, which then shows its busy word.
function activityText(a) {
    if (!a || typeof a !== "object")
        return "";
    const count = Number.isInteger(a.done) && Number.isInteger(a.total) && a.total > 0 ? " " + a.done + " of " + a.total : "";
    if (a.name === "signatures")
        return "Checking package signatures…" + count;
    // Without a count while pacman downloads the package lists.
    if (a.name === "downloads")
        return "Downloading updates…" + count;
    return "";
}
// The error page in plain words: what happened and one thing to do, per worker error code
// (installer/emaki_installer/errors.py). The worker's diagnostic goes under "Show details";
// a code missing here shows that message as it was sent.
function errorPresentation(error) {
    const save = "Save the log below; it records what the installer did.";
    const usb = "Check that the USB stick is still connected.";
    const disk = "Check the choices on the Disk step.";
    const review = "Prepare a new review.";
    const another = "Choose another disk.";
    const larger = "Choose a larger disk.";
    const table = {
        bad_request: ["The installer service could not read a request from this window.", save],
        bad_config: ["The installer could not use one of the chosen settings.", save],
        bad_dest: ["The log could not be written to the chosen removable medium.", "Choose another medium from the list."],
        busy: ["The installer service was busy with another task.", save],
        unsupported_mode: ["This type of installation is not available in this version.", disk],
        unsupported_version: ["The installer's parts on this USB stick do not match each other.", save],
        uefi_required: ["Installing needs a computer started in 64-bit UEFI mode.", "If the computer's boot menu offers a UEFI entry for the USB stick, choose it there."],
        disk_not_found: ["The chosen disk is no longer there.", "Check that the disk is connected."],
        boot_medium: ["The chosen disk is the USB stick Emaki is running from.", another],
        disk_busy: ["The chosen disk or one of its partitions is in use.", "Close any program or window that uses it."],
        encrypted_confirmation: ["The installer could not confirm permission to erase the chosen volume.", disk],
        disk_too_small: ["The chosen disk is smaller than the 24 GiB Emaki needs.", larger],
        disk_changed: ["The disk changed after the review.", review],
        unsafe_disk: ["A safety check of the disk did not pass.", save],
        manual_layout: ["The partitions cannot be used as they were assigned.", disk],
        esp_space: ["The disk's EFI system partition cannot be used.", disk],
        root_too_small: ["There is not enough room for Emaki on the chosen partition.", disk],
        shrink_bounds: ["Windows cannot be made smaller as planned.", disk],
        token_invalid: ["The confirmation did not match the review.", review],
        token_expired: ["The review expired before the installation started.", review],
        job_not_found: ["The installer service no longer has this installation.", save],
        offline_repo: ["The installer could not use the package source on the USB stick.", usb],
        offline_repo_incomplete: ["Some packages on the USB stick are missing or could not be verified.", usb],
        command_failed: ["A program the installer runs stopped with an error.", save],
        boot_verify: ["The startup files of the new system did not pass their check.", save],
        fstab_verify: ["The list of disks the new system mounts at startup did not pass its check.", save],
        cleanup_failed: ["The installer could not detach the new system's disks.", save],
        cancelled: ["The installation was cancelled.", save],
        internal: ["The installer stopped because of an internal error.", save]
    };
    const words = error && Object.prototype.hasOwnProperty.call(table, error.code) ? table[error.code] : null;
    if (!words)
        return {sentence: (error && error.message) || "The installer could not continue.", action: "", details: ""};
    // Without "Try again" the person cannot return to the Disk step or prepare a new review.
    const action = error.retryable || [disk, review, another, larger].indexOf(words[1]) < 0 ? words[1] : save;
    // The ERASE field belongs to Disk. Keep the facts here, without instructions to type into it.
    const details = error.code === "encrypted_confirmation"
        ? (error.message || "").replace(/\s*Type ERASE to confirm[^.]*\./g, "").trim()
        : error.message || "";
    return {sentence: words[0], action: action, details: details};
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
        return "Use characters available on an English (US) keyboard for the startup password (at most 1024 characters).";
    return "";
}

// One message per field ("" when the field is fine). reserved: the worker's list from hello
// (session.reservedLogins).
function accountErrors(name, login, password, confirm, hostname, reserved) {
    return {
        name: Array.from(name).length > 128 || /[\x00-\x1f:\x7f]/.test(name) ? "Your name must be at most 128 characters and contain no colon or control characters." : "",
        login: !/^[a-z_][a-z0-9_-]{0,31}$/.test(login) || (reserved || ["root", "greeter", "live", "nobody"]).indexOf(login) !== -1
            ? "Use 1–32 lowercase letters, digits, _ or - for your login, starting with a letter or _. This login must not be reserved." : "",
        // No key types a control character at the login screen (planner.validate_config).
        password: !password || /[\x00-\x1f\x7f]/.test(password) || unescape(encodeURIComponent(password)).length > 1024
            ? "Enter a password without line breaks, tabs or other control characters (at most 1024 UTF-8 bytes)." : "",
        confirm: password !== confirm ? "The passwords do not match." : "",
        hostname: !/^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$/.test(hostname) ? "Computer name: 1–63 letters, digits or hyphens, beginning and ending with a letter or digit." : ""
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
var UNSEEN_NAMES = {
    0x0009: "tab", 0x00a0: "no-break space", 0x00ad: "soft hyphen", 0x061c: "arabic letter mark",
    0x09b3: "", 0x09bb: "", 0x0f48: "", 0x17fb: "", 0x17fc: "", 0x17fd: "", 0x17fe: "", 0x17ff: "",
    0x200b: "zero width space", 0x200c: "zero width non-joiner", 0x200d: "zero width joiner",
    0x200e: "left-to-right mark", 0x200f: "right-to-left mark", 0x202a: "left-to-right embedding",
    0x202b: "right-to-left embedding", 0x202c: "pop directional formatting",
    0x202d: "left-to-right override", 0x202e: "right-to-left override",
    0x202f: "narrow no-break space", 0x2066: "left-to-right isolate",
    0x2067: "right-to-left isolate", 0x2068: "first strong isolate",
    0x2069: "pop directional isolate", 0xe60b: "", 0xe656: "", 0xe659: ""
};
// One character of the console sentence: itself, or "U+00A0 no-break space" for one a list
// cannot show (and any other control character, by its code point).
function shownCharacter(c) {
    const code = c.codePointAt(0);
    const hex = "U+" + code.toString(16).toUpperCase().padStart(4, "0");
    if (Object.prototype.hasOwnProperty.call(UNSEEN_NAMES, code))
        return UNSEEN_NAMES[code] ? hex + " " + UNSEEN_NAMES[code] : hex;
    return code < 0x20 || (code >= 0x7f && code < 0xa0) ? hex : c;
}
// The sentence under the account password on the You page; it does not block Continue.
function consoleWarning(table, layouts, password) {
    const chars = consoleUnsafeChars(table, layouts, password);
    return chars ? "The text console types these characters differently: " + Array.from(chars).map(shownCharacter).join(" ") + ". Choose a password without them if you may need the console." : "";
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
    if (d.is_boot_medium) return "The live boot medium cannot be a target.";
    if ((d.partitions || []).some(p => !!p.mountpoint)) return "A partition on this disk is mounted.";
    if (d.size_bytes < 24 * 1073741824) return "The disk must be at least 24 GiB.";
    return "";
}
// The systems the inventory found on a disk (os_hint), for its card: a fact, not an offer.
function diskContents(d) {
    const hints = (d.partitions || []).map(p => p.os_hint);
    return [["windows", "Windows"], ["macos", "macOS"]].filter(row => hints.indexOf(row[0]) >= 0).map(row => " · contains " + row[1]).join("");
}
// planner.manual_partitions refuses every table but GPT; a disk without a table gets one in GParted.
function manualReason(d) {
    return d && d.partition_table && d.partition_table !== "gpt" ? "Manual installation needs a GPT disk." : "";
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
    return p ? "Windows keeps " + ((p.size_bytes - freed) / 1e9).toFixed(2) +
        " GB, Emaki gets " + (freed / 1e9).toFixed(2) + " GB; back up your files first." : "Windows shrink is unavailable. Refresh the disk list.";
}
