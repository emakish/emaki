// Arithmetic only. No JavaScript evaluation, names, strings or function calls.
function calculate(input) {
    if (input.length > 1024 || !/[+\-*/%^]/.test(input)) return null;
    const source = input.replace(/,/g, '.');
    if (!/^[\d\s.+\-*/%^()]+$/.test(source)) return null;
    let pos = 0, depth = 0, steps = 0;
    function space() { while (/\s/.test(source[pos] || '') && pos < source.length) pos++; }
    function peek() { space(); return source[pos] || ''; }
    function enter() { if (++depth > 32 || ++steps > 256) throw new Error('limit'); }
    function atom() {
        enter();
        let value;
        if (peek() === '(') {
            pos++; value = sum();
            if (peek() !== ')') throw new Error('parenthesis');
            pos++;
        } else {
            space();
            const match = /^(?:\d+(?:\.\d*)?|\.\d+)/.exec(source.slice(pos));
            if (!match) throw new Error('number');
            pos += match[0].length; value = Number(match[0]);
        }
        depth--; return value;
    }
    function power() { const left = atom(); if (peek() === '^') { pos++; return Math.pow(left, unary()); } return left; }
    function unary() {
        enter(); let value;
        if (peek() === '+' || peek() === '-') { const op = source[pos++]; value = unary() * (op === '-' ? -1 : 1); }
        else value = power();
        depth--; return value;
    }
    function product() {
        let value = unary();
        while (['*', '/', '%'].includes(peek())) {
            const op = source[pos++], right = unary();
            value = op === '*' ? value * right : op === '/' ? value / right : value % right;
        }
        return value;
    }
    function sum() {
        let value = product();
        while (['+', '-'].includes(peek())) { const op = source[pos++], right = product(); value += op === '+' ? right : -right; }
        return value;
    }
    try { const value = sum(); return peek() === '' && Number.isFinite(value) ? Number(value.toFixed(8)) : null; }
    catch (_) { return null; }
}
