// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
.pragma library
.import "PolkitText.js" as Copy

// These are display hints supplied by polkit, never proof of a process identity.
function clean(value) {
    return String(value === null || value === undefined ? "" : value)
        .replace(/[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/g, " ").slice(0, 2048);
}

function redact(value) {
    return clean(value)
        .replace(/\b(Bearer|Basic)\s+\S+/gi, "$1 " + Copy.redacted)
        .replace(/(\b(?:password|passwd|passphrase|pwd|token|secret|api[-_]?key|credential|authorization|cookie)\b\s*(?:[:=]\s*|\s+))(?:"[^"]*"|'[^']*'|\S+)/gi, "$1" + Copy.redacted)
        .replace(/(https?:\/\/)[^\s/@]+:[^\s/@]+@/gi, "$1" + Copy.redacted + "@");
}

// Only fixed installed helpers named by our policy actions may expose argv.
// pkexec supplies "program" and "command_line"; exec.path is an action annotation,
// not request metadata. These remain display hints, never an authorization check.
function emakiHelper(action, details, paths) {
    const helpers = {
        "org.emaki.rollback": ["binDir", "emaki-rollback"],
        "org.emaki.channel.change": ["binDir", "emaki-update-channel"],
        "org.emaki.wifi-recovery": ["libDir", "emaki-wifi-recover"],
        "org.emaki.wifi-password": ["libexecDir", "emaki-wifi-password"],
        "org.emaki.charge-limit": ["libexecDir", "emaki-charge-limit"],
        "org.emaki.update.apply": ["libexecDir", "emaki-update-apply"],
        "org.emaki.update.restore": ["libexecDir", "emaki-update-restore"],
        "org.emaki.programs.remove": ["libexecDir", "emaki-programs-remove"]
    };
    if (!Object.prototype.hasOwnProperty.call(helpers, action) || !paths || !details)
        return false;
    const helper = helpers[action];
    const directory = paths[helper[0]];
    if (typeof directory !== "string" || !directory.startsWith("/")
            || directory.split("/").some(part => part === "." || part === "..")
            || /[\u0000-\u0020\u007f]/.test(directory))
        return false;
    return details.program === directory + "/" + helper[1];
}

// Match only the fixed program-manager argv contracts. This is a display hint,
// not an authorization check; policy and helpers validate the request separately.
function programRequest(action, details, libexecDir) {
    if (!details || typeof details !== "object" || Array.isArray(details)
            || typeof libexecDir !== "string" || !libexecDir.startsWith("/"))
        return null;
    const operations = {
        "org.emaki.programs.install": "install",
        "org.emaki.programs.remove": "remove",
        "org.emaki.programs.flatpak.install": "install",
        "org.emaki.programs.flatpak.remove": "remove"
    };
    if (!Object.prototype.hasOwnProperty.call(operations, action))
        return null;
    const operation = operations[action];
    const flatpak = action.indexOf(".flatpak.") >= 0;
    const helper = libexecDir + "/emaki-programs-" + (flatpak ? "flatpak" : operation);
    const command = details.command_line;
    if (details.program !== helper || typeof command !== "string"
            || command.length > helper.length + 8706 || /[^\x20-\x7e]/.test(command))
        return null;
    const words = command.split(" ");
    if (words[0] !== helper)
        return null;
    const digest = /^[0-9a-f]{64}$/;
    let target;
    if (flatpak) {
        if (words.length !== 5 || words[1] !== operation
                || !/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(words[2])
                || words[3].length > 512
                || !/^app\/[A-Za-z0-9_][A-Za-z0-9_.-]*\/[A-Za-z0-9_-]+\/[A-Za-z0-9_.-]+$/.test(words[3])
                || !digest.test(words[4]))
            return null;
        target = words[3].split("/")[1];
    } else {
        if (words.length !== 3 || words[1].length > 512)
            return null;
        const name = operation === "install" ? /^[A-Za-z0-9][A-Za-z0-9@._+-]*\/[A-Za-z0-9][A-Za-z0-9@._+-]*$/
                                            : /^[a-z0-9@_+][a-z0-9@._+-]{0,127}$/;
        const token = operation === "install" ? digest : /^[a-z0-9@_+][a-z0-9@._+,-]{0,8191}$/;
        if (!name.test(words[1]) || !token.test(words[2]))
            return null;
        target = words[1];
    }
    const wording = operation === "remove" ? "Remove program"
                   : flatpak ? "Install program" : "Install program and update repository packages";
    return {headline: target + " — [PLACEHOLDER] " + wording, command: command};
}

function headline(action, message, details, libexecDir) {
    const request = programRequest(action, details, libexecDir);
    return request ? request.headline : redact(message) || Copy.requestUnavailable;
}

function rows(action, message, details, requester, paths) {
    const libexecDir = typeof paths === "string" ? paths : paths && paths.libexecDir;
    const request = programRequest(action, details, libexecDir);
    const result = [
        {label: "Asked by", value: requester ? redact(requester) : Copy.requesterUnavailable},
        {label: "Action", value: redact(action) || Copy.unavailable},
        {label: Copy.messageLabel, value: redact(message) || Copy.unavailable}
    ];
    if (!details || typeof details !== "object" || Array.isArray(details) || !Object.keys(details).length) {
        result.push({label: Copy.detailsLabel, value: Copy.unavailable});
        return result;
    }
    const showArguments = emakiHelper(action, details, paths);
    for (const key of Object.keys(details).sort().slice(0, 32)) {
        // Unclassified command fields stay hidden, even for a recognized helper.
        const argumentsField = /^(command_line|argv|arguments)$/.test(key);
        const sensitive = /password|passwd|passphrase|pwd|token|secret|key|credential|authorization|cookie/i.test(key)
            || (/command|cmdline|argv|arguments/i.test(key) && !(showArguments && argumentsField));
        // A validated program request keeps its complete fixed-contract command.
        if (key === "command_line" && request) {
            result.push({label: key, value: request.command});
            continue;
        }
        let value = details[key];
        // Do not mistake a helper basename ending in "password" for a secret option.
        if (!sensitive && showArguments && argumentsField && typeof value === "string"
                && value.startsWith(details.program + " "))
            value = clean(details.program) + " " + redact(value.slice(details.program.length + 1));
        else
            value = sensitive ? Copy.redacted : redact(value);
        result.push({label: clean(key).slice(0, 128), value: value.slice(0, 2048) || Copy.unavailable});
    }
    return result;
}
