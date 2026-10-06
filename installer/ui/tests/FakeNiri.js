.pragma library
// A stand-in for niri behind the installer window's keyboard helper (ui-helper.py), for the
// window tests. niri names each layout by its xkeyboard-config description (NAMES, as
// evdev.lst writes them; test_helpers.py checks them against the system's evdev.lst); the helper
// turns a name back into a code through the layout rows, the first row with that description, so
// a layout the rows name differently never confirms. A written list is run with its first layout
// active: niri builds a fresh keymap state on every reload.

var NAMES = {
    us: "English (US)",
    gb: "English (UK)",
    de: "German",
    fr: "French",
    cz: "Czech",
    ru: "Russian",
    ua: "Ukrainian"
};

// mode: "follow" runs every written list, "stuck" keeps its list (a write niri never loads),
// "refuse" fails the write, "unavailable" never reads the file (the stock niri session).
function create(layouts) {
    return {
        layouts: (layouts || ["us"]).slice(),
        current: 0,
        mode: "follow",
        // The Caps Lock and Num Lock LEDs, which ui-helper layout_state reads with the layouts.
        caps: false,
        num: false
    };
}

function codes(rows, names) {
    const table = Array.from(rows);
    return names.map(function (name) {
        const row = table.find(function (x) {
            return x.label === name;
        });
        return row ? row.layout.toUpperCase() : name.slice(0, 32);
    });
}

function answer(niri, rows, request) {
    if (request.op === "trial") {
        if (niri.mode === "refuse")
            return {
                ok: false,
                message: "This operation is unavailable. Please try again."
            };
        if (niri.mode === "unavailable")
            return {
                ok: false,
                reloads: false,
                message: "This session does not read the keyboard trial."
            };
        if (niri.mode === "follow") {
            niri.layouts = request.layouts.slice();
            niri.current = 0;
        }
        return {
            ok: true
        };
    }
    // niri msg action switch-layout 0
    if (request.op === "first_layout") {
        niri.current = 0;
        return {
            ok: true
        };
    }
    if (request.op === "layout_state")
        return {
            ok: true,
            codes: codes(rows, niri.layouts.map(function (layout) {
                return NAMES[layout] || layout;
            })),
            current: niri.current,
            caps: niri.caps === true,
            num: niri.num === true
        };
    return {
        ok: false,
        message: "This operation is unavailable. Please try again."
    };
}
