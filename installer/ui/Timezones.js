// Data and presentation helpers shared by the picker and its regression tests.
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
function at(x, y) {
    const data = Zones.data;
    if (x < 0 || x >= 1 || y < 0 || y >= 1) return "";
    const row = data.rows[Math.floor(y * data.height)];
    for (let i = 0; i < row.length; i += 2)
        if (x * data.width < row[i]) return row[i + 1] ? data.names[row[i + 1]] : "";
    return "";
}
