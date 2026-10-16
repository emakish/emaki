// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
// The production map asset and Timezones.js: zone lookup for known places, offsets,
// the nearest-zone fallback, asset invariants and searchable geographic metadata.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const asset = path.join(__dirname, '../assets/ZoneData.js');
const qmlOnly = /^\.(pragma|import)\b.*$/gm;
const zones = vm.createContext({});
vm.runInContext(fs.readFileSync(asset, 'utf8').replace(qmlOnly, ''), zones);
const helper = vm.createContext({Zones: zones});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../Timezones.js'), 'utf8').replace(qmlOnly, ''), helper);
const data = zones.data;

// Small enough to embed (target: well under 1 MB), and internally consistent.
const size = fs.statSync(asset).size;
assert.ok(size < 400 * 1024, 'map asset is ' + size + ' bytes');
assert.equal(data.version, 2);
assert.equal(data.offsets.length, data.names.length);
assert.equal(data.summer.length, data.names.length);
assert.equal(data.zones.length, data.names.length);
assert.equal(data.kinds.length, data.arcs.length);
const used = new Set();
for (const rings of data.zones)
    for (const refs of rings)
        for (const ref of refs) {
            const arc = ref >= 0 ? ref : ~ref;
            assert.ok(arc < data.arcs.length, 'arc reference ' + ref);
            used.add(arc);
        }
assert.equal(used.size, data.arcs.length, 'every arc is used');
const map = helper.geometry();
for (const zone of map.zones)
    for (const flat of zone.rings) {
        assert.ok(flat.length >= 6, zone.name + ' ring has fewer than three points');
        for (let i = 0; i < flat.length; i += 2)
            assert.ok(flat[i] >= 0 && flat[i] <= data.width && flat[i + 1] >= 0 && flat[i + 1] <= data.height, zone.name);
    }
assert.ok(map.land.length > 100 && map.dots.length > 0);
// Shared borders are one arc used by both neighbours: no gaps or slivers between zones.
const arcsOf = name => new Set(data.zones[data.names.indexOf(name)].flat().map(ref => ref >= 0 ? ref : ~ref));
for (const [a, b] of [['Europe/Madrid', 'Europe/Paris'], ['Asia/Kolkata', 'Asia/Kathmandu'], ['America/Denver', 'America/Phoenix']])
    assert.ok([...arcsOf(a)].some(arc => arcsOf(b).has(arc)), a + ' and ' + b + ' share a border arc');

const at = (lon, lat, accept) => {
    const [x, y] = helper.project(lon, lat);
    return helper.zoneAt(x, y, accept);
};
const places = [
    // Spain and France, both sides of the Pyrenees and of the Bidasoa.
    ['Madrid', -3.70, 40.42, 'Europe/Madrid'], ['Barcelona', 2.17, 41.39, 'Europe/Madrid'],
    ['Figueres', 2.96, 42.27, 'Europe/Madrid'], ['Perpignan', 2.90, 42.70, 'Europe/Paris'],
    ['Pamplona', -1.64, 42.82, 'Europe/Madrid'], ['Bayonne', -1.47, 43.49, 'Europe/Paris'],
    ['Paris', 2.35, 48.86, 'Europe/Paris'], ['Lisbon', -9.14, 38.72, 'Europe/Lisbon'],
    ['London', -0.13, 51.51, 'Europe/London'], ['Berlin', 13.40, 52.52, 'Europe/Berlin'],
    ['Moscow', 37.62, 55.76, 'Europe/Moscow'], ['Istanbul', 28.98, 41.01, 'Europe/Istanbul'],
    ['Reykjavik', -21.94, 64.15, 'Atlantic/Reykjavik'],
    // India (+5:30) and Nepal (+5:45) on both sides of their border.
    ['Delhi', 77.21, 28.61, 'Asia/Kolkata'], ['Mumbai', 72.88, 19.08, 'Asia/Kolkata'],
    ['Kolkata', 88.36, 22.57, 'Asia/Kolkata'], ['Lucknow', 80.95, 26.85, 'Asia/Kolkata'],
    ['Kathmandu', 85.32, 27.72, 'Asia/Kathmandu'], ['Pokhara', 83.99, 28.21, 'Asia/Kathmandu'],
    ['Nepalgunj', 81.62, 28.05, 'Asia/Kathmandu'],
    ['Tehran', 51.39, 35.69, 'Asia/Tehran'], ['Kabul', 69.21, 34.56, 'Asia/Kabul'],
    ['Karachi', 67.01, 24.86, 'Asia/Karachi'], ['Dhaka', 90.41, 23.81, 'Asia/Dhaka'],
    ['Yangon', 96.20, 16.87, 'Asia/Yangon'], ['Bangkok', 100.50, 13.76, 'Asia/Bangkok'],
    ['Singapore', 103.82, 1.35, 'Asia/Singapore'], ['Jakarta', 106.85, -6.21, 'Asia/Jakarta'],
    ['Shanghai', 121.47, 31.23, 'Asia/Shanghai'], ['Urumqi', 87.62, 43.83, 'Asia/Urumqi'],
    ['Hong Kong', 114.17, 22.32, 'Asia/Hong_Kong'], ['Seoul', 126.98, 37.57, 'Asia/Seoul'],
    ['Tokyo', 139.69, 35.69, 'Asia/Tokyo'],
    ['Perth', 115.86, -31.95, 'Australia/Perth'], ['Eucla', 128.88, -31.68, 'Australia/Eucla'],
    ['Adelaide', 138.60, -34.93, 'Australia/Adelaide'], ['Darwin', 130.84, -12.46, 'Australia/Darwin'],
    ['Sydney', 151.21, -33.87, 'Australia/Sydney'], ['Auckland', 174.76, -36.85, 'Pacific/Auckland'],
    // The Chatham Islands (+12:45), on the other side of the antimeridian from Auckland.
    ['Chatham Islands', -176.56, -43.95, 'Pacific/Chatham'],
    ['Honolulu', -157.86, 21.31, 'Pacific/Honolulu'], ['Anchorage', -149.90, 61.22, 'America/Anchorage'],
    ['Los Angeles', -118.24, 34.05, 'America/Los_Angeles'], ['Phoenix', -112.07, 33.45, 'America/Phoenix'],
    ['Denver', -104.99, 39.74, 'America/Denver'], ['Winnipeg', -97.14, 49.90, 'America/Winnipeg'],
    ['New York', -74.01, 40.71, 'America/New_York'], ['Halifax', -63.57, 44.65, 'America/Halifax'],
    ["St. John's", -52.71, 47.56, 'America/St_Johns'], ['Mexico City', -99.13, 19.43, 'America/Mexico_City'],
    ['Bogota', -74.07, 4.71, 'America/Bogota'], ['Lima', -77.04, -12.05, 'America/Lima'],
    ['Sao Paulo', -46.63, -23.55, 'America/Sao_Paulo'], ['Buenos Aires', -58.38, -34.60, 'America/Argentina/Buenos_Aires'],
    ['Santiago', -70.67, -33.45, 'America/Santiago'],
    ['Cairo', 31.24, 30.04, 'Africa/Cairo'], ['Lagos', 3.38, 6.52, 'Africa/Lagos'],
    ['Nairobi', 36.82, -1.29, 'Africa/Nairobi'], ['Johannesburg', 28.05, -26.20, 'Africa/Johannesburg'],
];
for (const [city, lon, lat, zone] of places)
    assert.equal(at(lon, lat), zone, city);
for (const [zone, standard, summer] of [
    ['Asia/Kathmandu', 345, 345], ['Asia/Kolkata', 330, 330], ['Pacific/Chatham', 765, 825],
    ['Australia/Eucla', 525, 525], ['Australia/Adelaide', 570, 630], ['America/St_Johns', -210, -150],
    ['Europe/Madrid', 60, 120], ['Europe/Paris', 60, 120], ['Europe/Lisbon', 0, 60], ['Asia/Tehran', 210, 210],
]) {
    const index = data.names.indexOf(zone);
    assert.deepEqual([data.offsets[index], data.summer[index]], [standard, summer], zone);
}
assert.equal(helper.offsetLabel('Asia/Kathmandu'), 'UTC+5:45');
assert.equal(helper.offsetLabel('Pacific/Chatham'), 'UTC+12:45, UTC+13:45 in summer');
assert.equal(helper.offsetLabel('America/Los_Angeles'), 'UTC−8, UTC−7 in summer');
assert.equal(helper.offsetLabel('Africa/Abidjan'), 'UTC');
assert.equal(helper.place('Asia/Kathmandu'), 'Kathmandu, Nepal');
assert.equal(helper.place('America/Argentina/Buenos_Aires'), 'Buenos Aires, Argentina');
assert.equal(helper.place('UTC'), 'Coordinated Universal Time');

// Water falls back to the nearest zone; zones the system lacks are never offered.
assert.ok(['Europe/Paris', 'Europe/Madrid'].includes(at(-4, 45.5)), 'Bay of Biscay');
assert.equal(at(-28.0, 37.0), 'Atlantic/Azores');
assert.notEqual(at(-150, 0), '');
assert.notEqual(at(13.40, 52.52, name => name !== 'Europe/Berlin'), 'Europe/Berlin');
assert.equal(at(13.40, 52.52, name => name === 'Asia/Tokyo'), 'Asia/Tokyo');
assert.equal(at(13.40, 52.52, () => false), '');
// Every shaped zone is found at a point inside its own shape.
for (const zone of map.hitOrder) {
    const flat = zone.rings[0];
    assert.ok(helper.insideZone(flat[0], flat[1], zone) || helper.zoneAt(flat[0], flat[1]) !== '', zone.name);
}
const [x0, y0] = helper.anchor('Asia/Kathmandu');
assert.equal(helper.zoneAt(x0, y0), 'Asia/Kathmandu');
assert.equal(helper.anchor('No/Such_Zone'), null);

assert.equal(helper.matches('America/New_York', 'new york'), true);
assert.equal(helper.matches('America/New_York', 'new_york'), true);
assert.equal(helper.matches('Europe/Berlin', 'germany'), true);
assert.equal(helper.matches('Asia/Kathmandu', 'nepal'), true);
assert.equal(helper.matches('Europe/Berlin', 'Europe/Berlin'), true);
assert.equal(helper.matches('Europe/Berlin', 'no such city'), false);
console.log('PASS map asset ' + size + ' bytes, ' + data.arcs.length + ' shared arcs, ' + places.length +
            ' places incl. Spain/France, India/Nepal, Chatham; offsets, water fallback, search');
