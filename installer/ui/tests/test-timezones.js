// The production hit map, highlight rows and searchable geographic metadata.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const zones = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../assets/ZoneData.js'), 'utf8'), zones);
const helper = vm.createContext({Zones: zones});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../Timezones.js'), 'utf8').replace(/^\.import.*$/m, ''), helper);
const data = zones.data;
assert.equal(data.rows.length, data.height);
for (const row of data.rows) {
    assert.equal(row.length % 2, 0);
    assert.equal(row.at(-2), data.width);
    let end = 0;
    for (let i = 0; i < row.length; i += 2) {
        assert.ok(row[i] > end);
        assert.ok(row[i + 1] >= 0 && row[i + 1] < data.names.length);
        end = row[i];
    }
}
for (const [zone, lon, lat] of [
    ['Europe/Berlin', 13.4, 52.5], ['America/New_York', -74, 40.7],
    ['Asia/Tokyo', 139.7, 35.7], ['Asia/Kathmandu', 85.3, 27.7],
    ['Australia/Sydney', 151.2, -33.9], ['Pacific/Auckland', 174.8, -36.8],
    ['America/Sao_Paulo', -46.6, -23.5], ['Africa/Johannesburg', 28, -26.2],
]) assert.equal(helper.at((lon + 180) / 360, (90 - lat) / 180), zone);
// Ocean below the old rectangular polar sectors must not be clickable land.
for (const [lon, lat] of [[-160, -78], [-70, -68], [170, -70]])
    assert.equal(helper.at((lon + 180) / 360, (90 - lat) / 180), '');
assert.notEqual(helper.at((30 + 180) / 360, (90 + 80) / 180), '');
assert.equal(helper.at(-1, .5), '');
assert.equal(helper.at(1, .5), '');
assert.equal(helper.at(.5, -1), '');
assert.equal(helper.at(.5, 1), '');
assert.equal(helper.matches('America/New_York', 'new york'), true);
assert.equal(helper.matches('America/New_York', 'new_york'), true);
assert.equal(helper.matches('Europe/Berlin', 'germany'), true);
assert.equal(helper.matches('Asia/Kathmandu', 'nepal'), true);
assert.equal(helper.matches('Europe/Berlin', 'Europe/Berlin'), true);
assert.equal(helper.matches('Europe/Berlin', 'no such city'), false);
console.log('PASS map row coverage, eight regional hits, edges, city/region/zone search');
