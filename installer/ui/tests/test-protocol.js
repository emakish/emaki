// Replay the same transcript files served by mock-worker.py through production JS.
const assert = require("node:assert/strict");
const {execFileSync} = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const candidateVersion = fs.readFileSync(path.join(__dirname, "../../../iso/VERSION"), "utf8").trim();
// Rendering fixtures must not silently preserve an old release title.
function checkVersions(value, file) {
    if (!value || typeof value !== "object") return;
    if (value.type === "hello") assert.equal(value.emaki_version, candidateVersion, file);
    for (const child of Object.values(value)) checkVersions(child, file);
}
for (const file of fs.readdirSync(path.join(__dirname, "transcripts")).filter(f => f.endsWith(".json"))) {
    checkVersions(JSON.parse(fs.readFileSync(path.join(__dirname, "transcripts", file))), file);
}

const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../Protocol.js"), "utf8"), context);
const P = context;
// A login refusal keeps the worker's recovery instruction on the main page.
const loginRefusal = "This login name belongs to the system. Choose another name; the disk has not been changed.";
assert.equal(P.errorPresentation({code: "login_name_reserved", message: loginRefusal, retryable: true}).sentence, loginRefusal);
assert.equal(P.errorPresentation({code: "login_name_reserved", message: loginRefusal, retryable: true}).action, "");
const timedOutReboot = P.initial();
P.request(timedOutReboot, "reboot");
P.rebootTimedOut(timedOutReboot);
assert.equal(timedOutReboot.rebootMessage, "Could not restart. Try again.");
assert.equal(Object.values(timedOutReboot.pending).includes("reboot"), false);
const disconnectedReboot = P.initial();
P.request(disconnectedReboot, "reboot");
P.disconnected(disconnectedReboot);
assert.equal(disconnectedReboot.rebootMessage, "Could not restart. Try again.");
// The removal dialog gets immediate progress and the service's failure text.
const rebootState = P.initial();
const rebootRequest = P.request(rebootState, "reboot");
assert.equal(rebootState.rebootMessage, "Restarting…");
P.receive(rebootState, {type: "reply", id: rebootRequest.id, ok: false, msg: "Could not restart. Try again."}, 1);
assert.equal(rebootState.rebootMessage, "Could not restart. Try again.");
assert.equal(Object.values(rebootState.pending).includes("reboot"), false);

const files = fs.readdirSync(path.join(__dirname, "transcripts")).filter(f => f.endsWith(".json") && f !== "render.json");
// As mock-worker.py's transcript(): {"base", "confirm"} is the base's rows with other job events.
function transcript(file) {
    const data = JSON.parse(fs.readFileSync(path.join(__dirname, "transcripts", file)));
    if (Array.isArray(data))
        return data;
    const rows = transcript(data.base + ".json");
    assert.equal(rows[rows.length - 1].expect, "confirm");
    rows[rows.length - 1] = Object.assign({}, rows[rows.length - 1], {responses: data.confirm});
    return rows;
}
for (const file of files) {
    const rows = transcript(file);
    const s = P.initial();
    let seq = 0, queue = [], resumeCursor = 0;
    for (const row of rows) {
        if (row.expect === "disconnect") {
            resumeCursor = s.lastLogSeq;
            P.disconnected(s);
            assert.equal(s.ready, false);
            continue;
        }
        if (file === "error-retry.json" && row.expect === "probe" && s.outcome === "error") {
            assert.equal(s.error.retryable, true);
            s.outcome = ""; s.jobId = ""; s.error = null;
        }
        // Open GParted leaves a review: its probe (before GParted starts) drops the plan.
        if (row.action === "gparted_open") assert.ok(s.plan.token);
        const fields = row.expect === "hello" ? {proto: 1} : row.expect === "confirm" ? {plan_id: s.plan.plan_id, token: s.plan.token} : row.expect === "plan" ? {config: {software: file === "choices.json" ? "minimal" : "rich", user: {password: "in-memory-" + Math.random()}}} : row.fields || {};
        const request = queue.shift() || P.request(s, row.expect, fields);
        assert.equal(request.type, row.expect);
        if (["probe", "plan", "set_timezone"].includes(row.expect)) assert.equal(s.plan, null);
        if (row.expect === "resume") assert.equal(request.since_seq, resumeCursor);
        for (const response of row.responses) {
            const event = ["state", "progress", "log", "done", "error", "cancel_ack"].includes(response.type);
            const message = {...response, seq: ++seq, id: event ? "" : request.id};
            if (message.type === "reply") message.for_id = request.id;
            queue.push(...P.receive(s, message, 10000 + seq));
            if (message.code === "token_expired") assert.equal(s.plan, null);
            if (message.type === "plan_ack" && !message.errors.length) assert.equal(s.deadline, 610000 + seq);
            if (message.type === "log") assert.ok(s.logs.includes(message.line));
        }
        if (row.expect === "plan") assert.ok(!JSON.stringify(s).includes(request.config.user.password));
    }
    if (file === "plan-errors.json") {
        assert.equal(s.plan.errors[0].code, "manual_layout"); assert.ok(!s.plan.token);
    } else if (file === "cancel.json") {
        assert.equal(s.outcome, "error"); assert.match(s.cancelMessage, /not been undone/);
    } else assert.equal(s.outcome, "done");
    console.log("PASS " + file);
}
// Expiry before confirm; hello's global seq must not advance the log cursor.
const s = P.initial();
s.plan = {token: "fixture"}; s.deadline = 100;
assert.equal(P.expire(s, 99), false);
assert.equal(P.expire(s, 100), true);
assert.equal(s.plan, null);
s.jobId = "known"; s.running = true; s.lastLogSeq = 9;
const res = P.receive(s, {type: "hello", proto: 1, emaki_version: candidateVersion, busy_job: "known", seq: 900}, 1000);
assert.equal(res[0].since_seq, 9);
P.receive(s, {type: "log", job_id: "known", seq: 10, line: "replayed"}, 1001);
P.receive(s, {type: "log", job_id: "known", seq: 10, line: "replayed"}, 1002);
assert.equal(s.logs.length, 1);
P.receive(s, {type: "done", job_id: "unrelated", seconds: 5}, 1003);
assert.equal(s.outcome, "");
// A refused plan has no token and so no deadline: it never expires, and its own sentence stays
// on the Review page (VM, night e4: "The review expired" replaced it after one second).
const refused = P.initial();
refused.pending["ui-1"] = "plan";
refused.planning = true;
P.receive(refused, {type: "plan_ack", id: "ui-1", seq: 1, plan_id: "p", expires_s: 600, summary: [], warnings: [],
    errors: [{code: "manual_layout", msg: "/home cannot be FAT; choose ext4 or btrfs."}]}, 5000);
assert.equal(refused.deadline, 0);
assert.equal(P.expire(refused, 5000 + 3000), false);
assert.equal(refused.plan.errors[0].msg, "/home cannot be FAT; choose ext4 or btrfs.");
assert.equal(refused.notice, "");
// The worker's done carries warnings for the done page (installer/emaki_installer/worker.py).
assert.deepEqual(Array.from(P.initial().doneWarnings), []);
const partial = "The online update stopped part-way; some packages may be newer than others. Run `sudo pacman -Syu` after the first login.";
P.receive(s, {type: "done", job_id: "known", seconds: 5, warnings: [partial]}, 1004);
assert.equal(s.outcome, "done");
assert.deepEqual(Array.from(s.doneWarnings), [partial]);
P.receive(s, {type: "done", job_id: "known", seconds: 5}, 1005);
assert.deepEqual(Array.from(s.doneWarnings), []);
// A failed first connect is not a lost connection; only a session that got a hello reconnects.
const fresh = P.initial();
P.disconnected(fresh);
assert.equal(fresh.notice, "Connecting to the installer…");
P.receive(fresh, {type: "hello", proto: 1, emaki_version: "", seq: 1}, 1);
P.disconnected(fresh);
assert.equal(fresh.notice, "Connection lost. Reconnecting…");
// Copied to RAM: the boot medium is gone, the worker never starts, and the window says what to do
// instead of "Connecting…" forever; failed connects keep that sentence; a worker that answers clears it.
const copied = "Restart from the USB stick without the 'copy to RAM' option to install.";
const ram = P.initial();
P.bootMediumMissing(ram);
assert.equal(ram.notice, copied);
P.disconnected(ram);
P.disconnected(ram);
assert.equal(ram.notice, copied);
P.receive(ram, {type: "hello", proto: 1, emaki_version: "", seq: 1}, 1);
assert.equal(ram.notice, "");
P.bootMediumMissing(ram);
assert.equal(ram.notice, "", "a worker that answered has its boot medium");
console.log("PASS a missing boot medium replaces the endless connecting notice");
// The worker's count for a long step of a phase (worker.py Worker.activity) is shown until
// the phase's next state event, which carries none.
const job = P.initial();
job.pending["ui-9"] = "confirm";
P.receive(job, {type: "reply", id: "ui-9", for_id: "ui-9", ok: true, job_id: "j", seq: 1}, 1);
assert.equal(job.activity, null);
assert.equal(P.activityText(job.activity), "");
P.receive(job, {type: "progress", job_id: "j", seq: 2, phase: "prepare_disk", phase_pct: 0, total_pct: 0, indeterminate: true,
    activity: {name: "signatures", done: 312, total: 871}}, 2);
assert.equal(job.phase, "prepare_disk");
assert.equal(P.activityText(job.activity), "Checking package signatures… 312 of 871");
P.receive(job, {type: "state", job_id: "j", seq: 3, phase: "prepare_disk", phase_pct: 0, total_pct: 0, indeterminate: true}, 3);
assert.equal(job.activity, null);
assert.equal(P.activityText({name: "something-new", done: 1, total: 2}), "");
assert.equal(P.activityText({name: "signatures", done: null, total: null}), "Checking package signatures…");
console.log("PASS the window shows the worker's count for the signature check");
// The update's downloads: the package lists without a count, then each started download;
// a progress event without activity ends it (worker.py DownloadCount, Worker.download).
P.receive(job, {type: "progress", job_id: "j", seq: 4, phase: "update", phase_pct: 0, total_pct: 93, indeterminate: true,
    activity: {name: "downloads", done: null, total: null}}, 4);
assert.equal(P.activityText(job.activity), "Downloading updates…");
P.receive(job, {type: "progress", job_id: "j", seq: 5, phase: "update", phase_pct: 0, total_pct: 93, indeterminate: true,
    activity: {name: "downloads", done: 12, total: 144}}, 5);
assert.equal(P.activityText(job.activity), "Downloading updates… 12 of 144");
P.receive(job, {type: "progress", job_id: "j", seq: 6, phase: "update", phase_pct: 0, total_pct: 93, indeterminate: true, activity: null}, 6);
assert.equal(job.activity, null);
assert.equal(P.activityText(job.activity), "");
console.log("PASS the window shows what pacman reports while the update downloads");
// A running sub-step survives unrelated logs and reconnect replay, then clears at
// the explicit boundary. Its text appears even while the bar is determinate.
const step = {id: "copy-17", name: "initramfs", state: "running",
    text: "Building the startup image (mkinitcpio: linux-lts: default)."};
P.receive(job, {type: "progress", job_id: "j", phase: "copy_packages", phase_pct: 20,
    total_pct: 15, indeterminate: false, step}, 7);
assert.equal(P.stepText(job.step), step.text);
P.receive(job, {type: "log", job_id: "j", seq: 10, line: "work continues"}, 8);
assert.equal(P.stepText(job.step), step.text);
P.disconnected(job);
assert.equal(P.stepText(job.step), step.text);
P.receive(job, {type: "state", job_id: "j", phase: "copy_packages", phase_pct: 20,
    total_pct: 15, indeterminate: false, step}, 9);
assert.equal(P.stepText(job.step), step.text);
P.receive(job, {type: "progress", job_id: "j", phase: "copy_packages", step: null}, 10);
assert.equal(P.stepText(job.step), "");
P.receive(job, {type: "state", job_id: "j", phase: "bootloader"}, 11);
assert.equal(job.step, null);
assert.equal(P.stepText({id: "copy-18", text: "wrong", state: "done"}), "");
console.log("PASS running installation steps persist until a proven boundary");
assert.equal(P.loginFromName("Alex Morgan"), "alex-morgan");
const secret = "memory-" + Math.random();
assert.equal(P.accountError("Alex", "alex", secret, secret, "emaki"), "");
assert.equal(P.accountError("😀".repeat(128), "alex", secret, secret, "emaki"), "");
assert.ok(P.accountError("😀".repeat(129), "alex", secret, secret, "emaki"));
for (const login of ["root", "live", "greeter", "nobody", "9alex", "Alex", "a".repeat(33)]) assert.ok(P.accountError("Alex", login, secret, secret, "emaki"));
assert.ok(P.accountError("A:lex", "alex", secret, secret, "emaki"));
assert.ok(P.accountError("Alex", "alex", secret, "different", "emaki"));
assert.ok(P.accountError("Alex", "alex", "é".repeat(513), "é".repeat(513), "emaki"));
assert.ok(P.accountError("Alex", "alex", secret, secret, "-emaki"));
// One message per field, the same rules and texts; accountError is the first of them.
const loginRule = "Use 1–32 lowercase letters, digits, _ or - for your login, starting with a letter or _. This login must not be reserved.";
const nameRule = "Your name must be at most 128 characters and contain no colon or control characters.";
const passwordRule = "Enter a password without line breaks, tabs or other control characters (at most 1024 UTF-8 bytes).";
const hostnameRule = "Computer name: 1–63 letters, digits or hyphens, beginning and ending with a letter or digit.";
// An empty name is allowed (the planner uses the login) and two empty passwords match: no new rules.
assert.deepEqual(JSON.parse(JSON.stringify(P.accountErrors("", "", "", "", ""))), {name: "", login: loginRule, password: passwordRule, confirm: "", hostname: hostnameRule});
assert.deepEqual(JSON.parse(JSON.stringify(P.accountErrors("A:lex", "Alex", "é".repeat(513), "x", "-emaki"))),
    {name: nameRule, login: loginRule, password: passwordRule, confirm: "The passwords do not match.", hostname: hostnameRule});
assert.deepEqual(JSON.parse(JSON.stringify(P.accountErrors("Alex", "alex", secret, secret, "emaki"))), {name: "", login: "", password: "", confirm: "", hostname: ""});
// A tab, an escape or DEL cannot be typed at the login screen: the planner refuses them too
// (installer/ui/tests/test_planner_limits.py compares the two on every character to U+00FF).
for (const char of ["\t", "\x01", "\x1b", "\x1f", "\x7f", "\n", "\r", "\0"])
    assert.equal(P.accountErrors("Alex", "alex", "Zebra" + char, "Zebra" + char, "emaki").password, passwordRule, JSON.stringify(char));
for (const char of [" ", " ", "€", "ф", "’"])
    assert.equal(P.accountErrors("Alex", "alex", "Zebra" + char, "Zebra" + char, "emaki").password, "", JSON.stringify(char));
// The disk password is printable ASCII only (GRUB reads US key positions): no control character.
for (const char of ["\t", "\x01", "\x1b", "\x7f", " ", "€"])
    assert.ok(P.diskPasswordError("Zebra" + char), JSON.stringify(char));
assert.equal(P.diskPasswordError("Zebra Yacht-1~"), "");
assert.equal(P.accountError("", "", "", "", ""), loginRule);
assert.equal(P.accountError("A:lex", "alex", secret, "x", "-emaki"), nameRule);
assert.equal(P.accountError("Alex", "alex", secret, "x", "-emaki"), "The passwords do not match.");
// Reserved logins come from the planner over hello (installer/tests/test_protocol.py checks
// that hello carries planner.RESERVED_LOGINS); the window refuses every one of them.
const planner = fs.readFileSync(path.join(__dirname, "../../emaki_installer/planner.py"), "utf8");
const reserved = Array.from(planner.match(/^RESERVED_LOGINS = \(([\s\S]*?)\n\)/m)[1].matchAll(/'([^']+)'/g), match => match[1]);
assert.ok(reserved.length >= 70 && reserved.includes("wheel"), reserved.length);
const greeted = P.initial();
P.receive(greeted, {type: "hello", proto: 1, emaki_version: "", seq: 1, reserved_logins: reserved}, 1);
for (const login of reserved) assert.ok(P.accountError("Alex", login, secret, secret, "emaki", greeted.reservedLogins), login);
assert.equal(P.accountError("Alex", "alex", secret, secret, "emaki", greeted.reservedLogins), "");
// A worker without the list (recorded transcripts) keeps the window's own four names.
const older = P.initial();
P.receive(older, {type: "hello", proto: 1, emaki_version: "", seq: 1}, 1);
for (const login of ["root", "greeter", "live", "nobody"]) assert.ok(P.accountError("Alex", login, secret, secret, "emaki", older.reservedLogins), login);
P.receive(older, {type: "hello", proto: 1, emaki_version: "", seq: 2, reserved_logins: ["ok", 7]}, 2);
assert.ok(P.accountError("Alex", "root", secret, secret, "emaki", older.reservedLogins));
console.log("PASS the window refuses all " + reserved.length + " reserved logins the worker sends");
// The console table comes in the worker's hello (installer/tests/test_protocol.py: it is
// latin_layouts.CONSOLE_CHARS; installer/ui/tests/test_console_chars.py holds the window's check
// to planner.console_unsafe_chars on the real table). Only five-field records are taken.
const consoleTable = {us: [true, "", "", "", ""], de: [false, "~", "äöüß€", "yz~", ""],
    ru: [true, ".abc", "", ".", "abc"]};
const withTable = P.initial();
assert.equal(withTable.consoleChars, null);
P.receive(withTable, {type: "hello", proto: 1, emaki_version: "", seq: 1, console_chars: consoleTable}, 1);
assert.deepEqual(JSON.parse(JSON.stringify(withTable.consoleChars)), consoleTable);
assert.equal(P.consoleUnsafeChars(withTable.consoleChars, ["de"], "Grüße~~"), "~");
assert.equal(P.consoleUnsafeChars(withTable.consoleChars, ["us", "ru"], "пароль1"), "алопрь");
assert.equal(P.consoleUnsafeChars(withTable.consoleChars, ["ru", "us"], "abc"), "");
assert.equal(P.consoleUnsafeChars(withTable.consoleChars, ["ru", "us"], "a.c"), ".");
assert.equal(P.consoleUnsafeChars(withTable.consoleChars, ["zz"], "naïve 😀"), "ï😀");
assert.equal(P.consoleWarning(withTable.consoleChars, ["de"], "a~€"),
    "The text console types these characters differently: ~. Choose a password without them if you may need the console.");
assert.equal(P.consoleWarning(withTable.consoleChars, ["us", "ru"], "да"),
    "The text console types these characters differently: а д. Choose a password without them if you may need the console.");
assert.equal(P.consoleWarning(withTable.consoleChars, ["de"], "Grüße"), "");
// A character the list cannot show is named: a no-break space, a tab, an unassigned code point.
assert.equal(P.consoleWarning(withTable.consoleChars, ["us"], "a b\t៽"),
    "The text console types these characters differently: U+0009 tab U+00A0 no-break space U+17FD. Choose a password without them if you may need the console.");
assert.equal(P.consoleWarning(withTable.consoleChars, ["de"], ""), "");
// A worker without the table, or with another shape, leaves the window without the warning.
for (const value of [undefined, null, [], {us: [true, "", ""]}, {us: [1, "", "", "", ""]}, {us: "x"}]) {
    const other = P.initial();
    P.receive(other, {type: "hello", proto: 1, emaki_version: "", seq: 1, console_chars: value}, 1);
    assert.equal(other.consoleChars, null, JSON.stringify(value));
    assert.equal(P.consoleWarning(other.consoleChars, ["us"], "пароль"), "");
}
console.log("PASS the window names the password characters the text console types differently");
// What one edit of a password field brought in: a typed key brings one character (also over a
// selection or next to the same letter), a paste or an input method's string can bring more.
assert.equal(P.insertedLength("", "a"), 1);
assert.equal(P.insertedLength("abc", "abbc"), 1);
assert.equal(P.insertedLength("abc", "d"), 1);
assert.equal(P.insertedLength("abc", "ab"), 0);
assert.equal(P.insertedLength("abc", ""), 0);
assert.equal(P.insertedLength("ab", "a😀b"), 1);
assert.equal(P.insertedLength("a", "aPa€ф \t1"), 7);
assert.equal(P.insertedLength("abc", "xy"), 2);
assert.equal(P.insertedLength("aaa", "aaaa"), 1);
console.log("PASS one edit's inserted characters are counted by code point");
assert.equal(P.diskReason({reason: "x"}), "x");
// The disk card names a system found on the disk (inventory os_hint), once per system.
assert.equal(P.diskContents({partitions: [{os_hint: "windows"}, {os_hint: "windows"}, {os_hint: null}]}), " · contains Windows");
assert.equal(P.diskContents({partitions: [{os_hint: "macos"}]}), " · contains macOS");
assert.equal(P.diskContents({partitions: [{os_hint: "macos"}, {os_hint: "windows"}]}), " · contains Windows · contains macOS");
assert.equal(P.diskContents({partitions: [{os_hint: "emaki"}, {}]}), "");
assert.equal(P.diskContents({}), "");
assert.equal(P.alongside({partitions: [{os_hint: "windows"}], shrink: null}), false);
assert.equal(P.alongside({partitions: [{os_hint: "macos"}], shrink: {min_bytes: 1}}), false);
assert.equal(P.alongside({partitions: [{os_hint: "windows"}], shrink: {min_bytes: 1}}), false);
const windows = {id: "win", os_hint: "windows", fs: "ntfs", size_bytes: 96 * 1073741824,
    shrink: {min_bytes: 20 * 1073741824, max_free_bytes: 74 * 1073741824}};
const disk = {size_bytes: 100 * 1073741824, partitions: [windows]};
assert.equal(P.alongside(disk), true);
assert.equal(P.alongsideDefault(windows), 37 * 1073741824);
assert.match(P.alongsideReview(windows, 40 * 1073741824), /^Windows keeps 60.13 GB, Emaki gets 42.95 GB; back up your files first\.$/);
windows.shrink.max_free_bytes = 32 * 1073741824;
assert.equal(P.alongsideDefault(windows), 32 * 1073741824);
windows.shrink.reason = "Windows is hibernated";
assert.equal(P.alongside(disk), false);
delete windows.shrink.reason;
windows.mountpoint = "/media/windows";
assert.equal(P.alongside(disk), false);
console.log("PASS expiry, replay cursor, duplicate logs, job isolation, account validation, alongside gating");
// The window's unlock layout is the worker's: GRUB reads US key positions.
const render = fs.readFileSync(path.join(__dirname, "../../emaki_installer/render.py"), "utf8");
assert.match(render, /def unlock_layout\(layouts, prompt\):[\s\S]*?if prompt == 'grub':\n\s+return 'us'\n/);
// The worker's own function, run, for the same lists: a JS constant alone proves nothing.
const lists = [["us"], ["de"], ["cz", "us"], ["ru", "us"], ["fr", "de", "cz", "ua"], ["dvorak"]];
const grub = JSON.parse(execFileSync("python3", ["-I", "-c", "import json, sys; sys.path.insert(0, sys.argv[1]); " +
    "from emaki_installer.render import unlock_layout; print(json.dumps([unlock_layout(x, 'grub') for x in json.loads(sys.argv[2])]))",
    path.join(__dirname, "../.."), JSON.stringify(lists)], {encoding: "utf8"}));
assert.deepEqual(lists.map(layouts => P.unlockLayout(layouts)), grub);
assert.equal(P.sameLayouts(["de", "us"], ["de", "us"]), true);
assert.equal(P.sameLayouts(["us", "de"], ["de", "us"]), false);
assert.equal(P.sameLayouts(["de"], ["de", "us"]), false);
assert.equal(P.sameLayouts(null, []), false);
console.log("PASS unlock layout matches render.unlock_layout for GRUB; layout lists compare in order");
// Every worker error code has plain words on the error page; the worker's message goes to the details.
const codes = Array.from(fs.readFileSync(path.join(__dirname, "../../emaki_installer/errors.py"), "utf8")
    .matchAll(/^\s+[A-Z_]+ = '([a-z_]+)'$/gm), match => match[1]);
assert.equal(codes.length, 32);
const ownSentence = ["bad_config", "login_name_reserved", "secure_boot", "clock_skew"];
for (const code of codes) {
    const words = P.errorPresentation({code: code, message: "raw worker message"});
    if (code === "disk_busy" || ownSentence.includes(code))
        assert.equal(words.sentence, "raw worker message");
    else
        assert.ok(words.sentence && words.sentence !== "raw worker message" && /\.$/.test(words.sentence), code);
    if (!ownSentence.includes(code)) assert.ok(words.action && /\.$/.test(words.action), code);
    assert.equal(words.details, ownSentence.includes(code) ? "" : "raw worker message", code);
}
// Confirmation instructions belong to Disk; the error details keep only the worker's facts.
const encryptedWarning = "/dev/vda contains an encrypted volume. Type ERASE to confirm that all its contents will be lost.";
const encryptedFailure = P.errorPresentation({code: "encrypted_confirmation", message: encryptedWarning, retryable: true});
assert.equal(encryptedFailure.sentence, "The installer could not confirm permission to erase the chosen volume.");
assert.equal(encryptedFailure.action, "Check the choices on the Disk step.");
assert.equal(encryptedFailure.details, "/dev/vda contains an encrypted volume.");
assert.ok(!encryptedFailure.sentence.includes("ERASE") && !encryptedFailure.action.includes("ERASE"));
assert.ok(!encryptedFailure.details.includes("ERASE"));
const volumeFacts = "/dev/vda2 contains a locked encrypted Linux system (LUKS). " +
    "The installer cannot check its contents; they may belong to a set of several disks.";
for (const retryable of [true, false]) {
    const error = {code: "encrypted_confirmation", retryable,
        message: volumeFacts + " Type ERASE to confirm that this volume and any connected data can be erased."};
    assert.equal(P.errorPresentation(error).details, volumeFacts);
    assert.ok(error.message.includes("Type ERASE"), "presentation preserves the original diagnostic");
}
assert.equal(P.errorPresentation({code: "encrypted_confirmation", message: encryptedWarning, retryable: false}).action,
    "Save the log below; it records what the installer did.");
const missingVolumeId = "/dev/vda has no readable volume ID, so the installer cannot erase this device safely. " +
    "Choose another target or wipe this device in the partition editor. " +
    "If this is a partition, you can also leave it unformatted in Manual mode.";
const missingVolumeFailure = P.errorPresentation({code: "encrypted_confirmation", message: missingVolumeId, retryable: true});
assert.equal(missingVolumeFailure.sentence, encryptedFailure.sentence);
assert.equal(missingVolumeFailure.action, encryptedFailure.action);
assert.equal(missingVolumeFailure.details, missingVolumeId);
const raw = "Offline repository preflight failed (Rich software requires emaki-apps); disk untouched: pacman exited with status 1.";
const offline = P.errorPresentation({code: "offline_repo_incomplete", message: raw, retryable: true});
assert.notEqual(offline.sentence, raw);
assert.equal(offline.details, raw);
assert.ok(!offline.sentence.includes("pacman") && !offline.sentence.includes("preflight"));
assert.equal(offline.action, "Check that the USB stick is still connected.");
// A step the person can only take through "Try again" is not offered without it.
assert.equal(P.errorPresentation({code: "manual_layout", message: "x", retryable: true}).action, "Check the choices on the Disk step.");
assert.equal(P.errorPresentation({code: "manual_layout", message: "x", retryable: false}).action, "Save the log below; it records what the installer did.");
const unknown = P.errorPresentation({code: "not_a_code", message: "kept as sent"});
assert.deepEqual([unknown.sentence, unknown.action, unknown.details], ["kept as sent", "", ""]);
assert.equal(P.errorPresentation({message: "The worker no longer has this job."}).sentence, "The worker no longer has this job.");
assert.equal(P.errorPresentation(null).sentence, "The installer could not continue.");
console.log("PASS plain words for all " + codes.length + " worker error codes; unknown codes keep the worker's message");

// Renewal keeps the reviewed plan and rotates its token without resending credentials.
{
    const state = P.initial();
    state.plan = {plan_id: "review", token: "old"};
    state.deadline = 600000;
    const request = P.request(state, "renew", {plan_id: "review", token: "old"});
    assert.equal(state.plan.token, "old");
    P.receive(state, {type: "plan_ack", id: request.id, plan_id: "review", token: "fresh", expires_s: 600, summary: [], warnings: []}, 550000);
    assert.equal(state.plan.token, "fresh");
    assert.equal(state.deadline, 1150000);
    assert.equal(P.expire(state, 600001), false);
}

{
    const message = "/dev/vda1 is mounted at /run/media/live/Files. Close its windows, unmount it, then refresh disks.";
    assert.equal(P.errorPresentation({code: "disk_busy", message, retryable: true}).sentence, message);
}
