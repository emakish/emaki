// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
.pragma library
.import "PolkitText.js" as Copy

// Unicode 16 format characters, including supplementary-plane ranges. The QML
// engine does not implement Unicode property escapes, so spell out the ranges.
var formatCharacters = /[\u00ad\u0600-\u0605\u061c\u06dd\u070f\u0890-\u0891\u08e2\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff\ufff9-\ufffb]|\ud804\udcbd|\ud804\udccd|\ud80d[\udc30-\udc3f]|\ud82f[\udca0-\udca3]|\ud834[\udd73-\udd7a]|\udb40\udc01|\udb40[\udc20-\udc7f]/g;

function stripFormat(value) {
    return value.replace(formatCharacters, "");
}

// These are display hints supplied by polkit, never proof of a process identity.
function clean(value) {
    return String(value === null || value === undefined ? "" : value)
        // Joiners form real text in several scripts; other format controls separate words.
        .replace(formatCharacters, character => character === "\u200c" || character === "\u200d" ? character : " ")
        .replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/g, " ").slice(0, 2048);
}

// Fields and options are explicit; positional values are ambiguous, so leave
// plain lowercase words readable in prose (for example, "password to change").
function redact(value) {
    const keyword = '(?:password|passwd|passphrase|pwd|token|secret|api[-_]?key|credential|authorization|cookie)';
    const quoted = '"(?:\\\\.|[^"\\\\])*"|\'(?:\\\\.|[^\'\\\\])*\'';
    const bare = '[^\\s"\'`}\\]]+(?:["\'`}\\]]+[^\\s"\'`}\\]]+)*';
    const secret = '(?:(?:' + quoted + ')(?:' + bare + ')?|' + bare + ')';
    const headerBare = '[^\\s"\'`;,}\\]]+(?:["\'`}\\]]+[^\\s"\'`;,}\\]]+)*';
    const headerValue = '(?:(?:' + quoted + ')(?:' + headerBare + ')?|' + headerBare + ')';
    const field = '((?:\\b|_)(?:[a-z0-9]+[-_])*';
    const assignment = '["\']?\\s*[:=]\\s*)';
    const pair = '[a-z0-9_-]+\\s*=\\s*' + headerValue;
    // Headers can contain a scheme or several cookie/digest pairs. Hide their
    // complete credential value before the general single-value field rule.
    return clean(value)
        .replace(new RegExp(field + 'authorization' + assignment
            + '(?:[a-z][a-z0-9_-]*\\s+)?(?:' + pair + '|' + headerValue + ')'
            + '(?:\\s*,\\s*' + pair + ')*', 'gi'), '$1' + Copy.redacted())
        .replace(new RegExp(field + 'cookie' + assignment
            + '(?:' + pair + '|' + headerValue + ')'
            + '(?:\\s*;\\s*(?:' + pair + '|' + headerValue + '))*', 'gi'), '$1' + Copy.redacted())
        .replace(new RegExp('(\\b(?:Bearer|Basic)\\s+)' + secret, 'gi'), '$1' + Copy.redacted())
        .replace(new RegExp(field + keyword + assignment + secret, 'gi'), '$1' + Copy.redacted())
        .replace(new RegExp('((?:^|[\\s`\'"])--?(?:[a-z0-9]+[-_])*' + keyword
            + '\\s+)' + secret, 'gi'), '$1' + Copy.redacted())
        .replace(new RegExp('(\\b(?:password|passwd|passphrase|pwd|token|secret)\\s+)'
            + '(?![:=])(' + secret + ')', 'gi'), function(match, prefix, token) {
                return /^[a-z]+$/.test(token) ? match : prefix + Copy.redacted();
            })
        .replace(/(https?:\/\/)[^\s/@]+@/gi, "$1" + Copy.redacted() + "@");
}

// Only fixed installed helpers named by our policy actions may expose argv.
// Some authorities may supply "program" and "command_line"; polkit 127 sends
// only caller/subject pids. exec.path is an action annotation, not request metadata.
// These remain display hints, never an authorization check.
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
        "org.emaki.programs.aur": "aur",
        "org.emaki.programs.undo": "undo",
        "org.emaki.programs.flatpak.install": "install",
        "org.emaki.programs.flatpak.remove": "remove",
        "org.emaki.programs.flatpak.undo": "undo"
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
    const receipt = /^[0-9]{8}T[0-9]{12}Z-[0-9a-f]{32}$/;
    const packageName = /^[a-z0-9@_+][a-z0-9@._+-]{0,127}$/;
    let target;
    if (flatpak) {
        const undo = operation === "undo";
        if (words.length !== (undo ? 6 : 5) || words[1] !== operation
                || !/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(words[2])
                || words[3].length > 512
                || !/^app\/[A-Za-z0-9_][A-Za-z0-9_.-]*\/[A-Za-z0-9_-]+\/[A-Za-z0-9_.-]+$/.test(words[3])
                || (undo && !receipt.test(words[4]))
                || !digest.test(words[words.length - 1]))
            return null;
        target = words[3].split("/")[1];
    } else if (operation === "aur") {
        if (words.length !== 3 || !packageName.test(words[1]) || !digest.test(words[2]))
            return null;
        // The source comes first: nobody checked what the AUR builds.
        target = "AUR · " + words[1];
    } else if (operation === "undo") {
        if (words.length !== 4 || !receipt.test(words[1]) || !packageName.test(words[2]) || !digest.test(words[3]))
            return null;
        target = words[2];
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
    const wording = operation === "remove" ? qsTranslate("Shell", "Remove program")
                   : operation === "undo" ? qsTranslate("Shell", "Undo the install of this program")
                   : operation === "aur" ? qsTranslate("Shell", "Build and install program and update repository packages")
                   : flatpak ? qsTranslate("Shell", "Install program") : qsTranslate("Shell", "Install program and update repository packages");
    return {headline: target + " — " + wording, command: command};
}

// Details must name one complete absolute path, never a shell command.
function executableName(value) {
    if (typeof value !== "string" || !value.startsWith("/")
            || /[\s\u0000-\u001f\u007f-\u009f]/.test(value))
        return "";
    return stripFormat(value).split("/").pop().slice(0, 64).replace(/[\ud800-\udbff]$/, "");
}

// Only pkexec's own fixed template may supply a basename when details are absent.
// The rest of the command is discarded, including a shortened command's tail.
function pkexecRequest(message) {
    if (typeof message !== "string")
        return null;
    const template = /^Authentication is needed to run `([\s\S]*)' as (?:the super user|user ([\s\S]+))$/.exec(message);
    if (!template)
        return null;
    const word = /^([^\s"'`\\]+)(?:\s|$)/.exec(template[1]);
    const user = template[2] ? clean(template[2]).slice(0, 64).replace(/[\ud800-\udbff]$/, "").trim() : "";
    return {program: word ? executableName(word[1]) : "", user: user};
}

function requestText(action, message, details, request, fallback) {
    if (request)
        return request.headline;
    // Own policies are plain sentences; never run secret heuristics over them.
    if (typeof action === "string" && action.startsWith("org.emaki."))
        return clean(message) || fallback;
    const fields = details && typeof details === "object" && !Array.isArray(details) ? details : {};
    if (action === "org.freedesktop.policykit.exec") {
        const template = pkexecRequest(message);
        const program = Object.prototype.hasOwnProperty.call(fields, "program")
            ? executableName(fields.program) : template && template.program;
        return Copy.runProgram(program, template && template.user);
    }
    const hasCommand = Object.keys(fields).some(key => /command|cmdline|argv|arguments/i.test(key))
        || (typeof fields.program === "string" && /\s/.test(fields.program));
    if (hasCommand)
        return Copy.runProgram(executableName(fields.program));
    return redact(message) || fallback;
}

function headline(action, message, details, libexecDir) {
    return requestText(action, message, details, programRequest(action, details, libexecDir), Copy.requestUnavailable());
}

function rows(action, message, details, requester, paths) {
    const libexecDir = typeof paths === "string" ? paths : paths && paths.libexecDir;
    const request = programRequest(action, details, libexecDir);
    const result = [
        {label: qsTranslate("Shell", "Asked by"), value: requester ? redact(requester) : Copy.requesterUnavailable()},
        {label: qsTranslate("Shell", "Action"), value: redact(action) || Copy.unavailable()},
        {label: Copy.messageLabel(), value: requestText(action, message, details, request, Copy.unavailable())}
    ];
    if (!details || typeof details !== "object" || Array.isArray(details) || !Object.keys(details).length) {
        result.push({label: Copy.detailsLabel(), value: Copy.unavailable()});
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
            value = sensitive ? Copy.redacted() : redact(value);
        result.push({label: clean(key).slice(0, 128), value: value.slice(0, 2048) || Copy.unavailable()});
    }
    return result;
}
