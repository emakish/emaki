// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later

function minuteOfDay(value, fallback) {
    if (typeof value !== "string" || !/^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/.test(value))
        value = fallback;
    return Number(value.slice(0, 2)) * 60 + Number(value.slice(3, 5));
}

function isDark(theme, lightTime, darkTime, now) {
    if (theme !== "automatic")
        return theme === "dark";
    const light = minuteOfDay(lightTime, "07:00");
    const dark = minuteOfDay(darkTime, "19:00");
    const minute = now.getHours() * 60 + now.getMinutes();
    // A full local-clock evaluation handles resume, clock corrections and both
    // occurrences of a repeated hour. Equal boundaries deterministically stay light.
    if (light === dark)
        return false;
    const daylight = light < dark ? minute >= light && minute < dark : minute >= light || minute < dark;
    return !daylight;
}

function setting(rows, key, fallback) {
    const row = Array.from(rows || []).find(entry => entry.key === key);
    return row && typeof row.value === "string" ? row.value : fallback;
}
