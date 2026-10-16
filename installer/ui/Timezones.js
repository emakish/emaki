// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
// Map geometry, hit testing and labels shared by the picker and its regression tests.
.pragma library
.import "assets/ZoneData.js" as Zones

function label(name) { return name.replace(/_/g, " ").replace(/\//g, " / "); }
function searchKey(name) {
    const point = Zones.data.points[name] || [];
    return (label(name) + " " + name + " " + (point[2] || "") + " " + (point[3] || "")).toLowerCase();
}
function matches(name, query) {
    const key = searchKey(name);
    return query.toLowerCase().trim().replace(/_/g, " ").split(/\s+/).every(word => key.indexOf(word) >= 0);
}

// "Kathmandu, Nepal": the zone's city and the country it belongs to.
function place(name) {
    if (name === "UTC") return "Coordinated Universal Time";
    const city = name.split("/").pop().replace(/_/g, " ");
    const country = (Zones.data.points[name] || [])[2];
    return country && country !== city ? city + ", " + country : city;
}
function offsetText(minutes) {
    if (!minutes) return "UTC";
    const size = Math.abs(minutes);
    return "UTC" + (minutes < 0 ? "−" : "+") + Math.floor(size / 60) + (size % 60 ? ":" + String(size % 60).padStart(2, "0") : "");
}
// "UTC+5:45", or "UTC−8, UTC−7 in summer" for zones with daylight saving time.
function offsetLabel(name) {
    const index = Zones.data.names.indexOf(name);
    if (index < 0) return "";
    const standard = Zones.data.offsets[index], summer = Zones.data.summer[index];
    return offsetText(standard) + (summer !== standard ? ", " + offsetText(summer) + " in summer" : "");
}

// Mercator, in the asset's map units (y grows southwards).
function project(lon, lat) {
    const data = Zones.data;
    const mercator = l => Math.log(Math.tan(Math.PI / 4 + l * Math.PI / 360));
    return [(lon + 180) / 360 * data.width, (mercator(data.north) - mercator(lat)) / (2 * Math.PI) * data.width];
}

let cached = null;
// Decoded once per process: arcs, zone rings as flat [x0, y0, x1, y1, …] arrays with
// their bounding boxes, and the land rings.
function geometry() {
    if (cached) return cached;
    const data = Zones.data;
    const decode = encoded => {
        const flat = new Array(encoded.length);
        let x = 0, y = 0;
        for (let i = 0; i < encoded.length; i += 2) {
            flat[i] = x += encoded[i];
            flat[i + 1] = y += encoded[i + 1];
        }
        return flat;
    };
    const arcs = data.arcs.map(decode);
    const bounds = flat => {
        const box = [Infinity, Infinity, -Infinity, -Infinity];
        for (let i = 0; i < flat.length; i += 2) {
            box[0] = Math.min(box[0], flat[i]); box[1] = Math.min(box[1], flat[i + 1]);
            box[2] = Math.max(box[2], flat[i]); box[3] = Math.max(box[3], flat[i + 1]);
        }
        return box;
    };
    const zones = data.zones.map((rings, index) => {
        const flats = rings.map(refs => {
            const flat = [];
            for (const ref of refs) {
                const arc = arcs[ref >= 0 ? ref : ~ref];
                const count = arc.length / 2;
                for (let k = flat.length ? 1 : 0; k < count; ++k) {
                    const j = ref >= 0 ? k : count - 1 - k;
                    flat.push(arc[2 * j], arc[2 * j + 1]);
                }
            }
            return flat;
        });
        let area = 0;
        for (const flat of flats)
            for (let i = 0, n = flat.length; i < n; i += 2) {
                const j = (i + n - 2) % n;
                area += (flat[j] * flat[i + 1] - flat[i] * flat[j + 1]) / 2;
            }
        const boxes = flats.map(bounds);
        const box = boxes.reduce((a, b) => [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])], [Infinity, Infinity, -Infinity, -Infinity]);
        return {index: index, name: data.names[index], offset: data.offsets[index], rings: flats, boxes: boxes, box: box, area: area};
    });
    const shaped = zones.filter(zone => zone.rings.length);
    const byName = {};
    for (const zone of zones)
        byName[zone.name] = zone;
    cached = {
        width: data.width,
        height: data.height,
        arcs: arcs,
        kinds: data.kinds,
        zones: zones,
        byName: byName,
        // Smallest first: an enclave wins over the zone around it.
        hitOrder: shaped.slice().sort((a, b) => a.area - b.area || a.index - b.index),
        // Largest first: small zones are painted over large ones.
        paintOrder: shaped.slice().sort((a, b) => b.area - a.area || a.index - b.index),
        land: data.land.map(decode),
        dots: data.dots
    };
    return cached;
}

function insideRing(x, y, flat) {
    let result = false;
    for (let i = 0, n = flat.length, j = n - 2; i < n; j = i, i += 2) {
        const ay = flat[j + 1], by = flat[i + 1];
        if ((ay > y) !== (by > y) && x < flat[j] + (y - ay) * (flat[i] - flat[j]) / (by - ay))
            result = !result;
    }
    return result;
}
function insideZone(x, y, zone) {
    let result = false;
    for (let r = 0; r < zone.rings.length; ++r) {
        const box = zone.boxes[r];
        if (x >= box[0] && x <= box[2] && y >= box[1] && y <= box[3] && insideRing(x, y, zone.rings[r]))
            result = !result;
    }
    return result;
}
// Squared distance from a point to a zone's outline.
function distance(x, y, zone, limit) {
    let best = limit;
    for (const flat of zone.rings)
        for (let i = 0, n = flat.length, j = n - 2; i < n; j = i, i += 2) {
            const ax = flat[j], ay = flat[j + 1], dx = flat[i] - ax, dy = flat[i + 1] - ay;
            const length = dx * dx + dy * dy;
            const t = length ? Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / length)) : 0;
            const ex = ax + t * dx - x, ey = ay + t * dy - y;
            best = Math.min(best, ex * ex + ey * ey);
        }
    return best;
}

// The zone under a point in map units: the shape that contains it, otherwise the
// nearest shape. `accept(name)` limits the answer to zones the system offers.
function zoneAt(x, y, accept) {
    const map = geometry();
    const allowed = zone => !accept || accept(zone.name);
    for (const zone of map.hitOrder) {
        const box = zone.box;
        if (x >= box[0] && x <= box[2] && y >= box[1] && y <= box[3] && insideZone(x, y, zone) && allowed(zone))
            return zone.name;
    }
    return nearest(x, y, accept);
}
function nearest(x, y, accept) {
    const map = geometry();
    let best = Infinity, found = "";
    for (const zone of map.hitOrder) {
        if (accept && !accept(zone.name))
            continue;
        const box = zone.box;
        const bx = Math.max(box[0] - x, 0, x - box[2]), by = Math.max(box[1] - y, 0, y - box[3]);
        if (bx * bx + by * by >= best)
            continue;
        const d = distance(x, y, zone, best);
        if (d < best) {
            best = d;
            found = zone.name;
        }
    }
    return found;
}
// Where a zone is on the map: its city from zone.tab, else the centre of its largest ring.
function anchor(name) {
    const point = Zones.data.points[name];
    if (point)
        return project(point[0], point[1]);
    const zone = geometry().byName[name];
    if (!zone || !zone.rings.length)
        return null;
    const box = zone.boxes.reduce((a, b) => (b[2] - b[0]) * (b[3] - b[1]) > (a[2] - a[0]) * (a[3] - a[1]) ? b : a);
    return [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2];
}
