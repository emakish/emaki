// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
// Supply the Qt translation primitives to the isolated JavaScript test contexts.
const vm = require('node:vm');
exports.install = function(context) {
    vm.runInContext(`
        function qsTr(source, disambiguation, count) {
            return count === undefined ? source : source.replace(/%n/g, String(count)).replaceAll("(s)", count === 1 ? "" : "s");
        }
        function qsTranslate(context, source, disambiguation, count) {
            return qsTr(source, disambiguation, count);
        }
        String.prototype.arg = function(value) {
            return this.replace(/%[1-9][0-9]*/g, token =>
                Number(token.slice(1)) === 1 ? String(value) : '%' + (Number(token.slice(1)) - 1));
        };
    `, context);
};
