// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
// English Qt translation semantics for pure JavaScript contract fixtures.
// Runtime catalogue loading and translated rendering are checked in the QML tests.
function qsTr(source, disambiguation, count) {
    if (count === undefined)
        return source;
    return source.replace(/\(s\)/g, count === 1 ? '' : 's')
        .replace(/\(ies\)/g, count === 1 ? 'y' : 'ies').replace(/%L?n/g, String(count));
}
function qsTranslate(context, source, disambiguation, count) {
    return qsTr(source, disambiguation, count);
}
Object.defineProperty(String.prototype, 'arg', {
    configurable: true,
    value(value) {
        const text = String(this);
        const numbers = Array.from(text.matchAll(/%L?([1-9][0-9]?)/g), match => Number(match[1]));
        if (!numbers.length)
            return text;
        const marker = Math.min(...numbers);
        return text.replace(new RegExp('%L?' + marker + '(?![0-9])', 'g'), () => String(value));
    }
});
