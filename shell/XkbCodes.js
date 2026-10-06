.pragma library
// Layout name (as niri reports it: the xkeyboard-config description) -> xkb code, uppercased.
// The table is /usr/share/X11/xkb/rules/evdev.lst: its "! layout" rows and its "! variant"
// rows (a variant's description names its layout's code). The lock/greeter helper
// (helpers/lock-environment.py) reads the same file the same way.

function parse(rules) {
    const table = {};
    let section = "";
    for (const raw of String(rules).split("\n")) {
        if (raw.startsWith("!")) {
            section = raw.slice(1).trim();
            continue;
        }
        const line = raw.trim();
        if (!line)
            continue;
        // The first row with a name wins (layout rows come before variant rows).
        let match;
        if (section === "layout" && (match = line.match(/^(\S+)\s+(.+)$/)) && !(match[2].trim() in table))
            table[match[2].trim()] = match[1].toUpperCase();
        else if (section === "variant" && (match = line.match(/^(\S+)\s+(\S+):\s*(.+)$/)) && !(match[3].trim() in table))
            table[match[3].trim()] = match[2].toUpperCase();
    }
    return table;
}

// A name outside the table (no rules file, a custom layout) keeps the old two-letter guess.
function code(name, table) {
    const known = table ? table[name] : undefined;
    return known !== undefined ? known : String(name).slice(0, 2).toUpperCase();
}
