# Installer time zone map

`ZoneData.js` is the generated, offline vector map behind the installer's time zone
picker: 213 392 bytes, drawn and hit-tested by `../TimezoneMap.qml` and `../Timezones.js`.
Map rendering, picking and search use no network lookup or location service.

## Sources

All downloads are pinned by URL and SHA-256 in `generate-timezones.py`.

- Zone boundaries: [Timezone Boundary Builder, release 2026b](https://github.com/evansiroky/timezone-boundary-builder/releases/tag/2026b),
  the `timezones.geojson.zip` dataset (zones on land, with territorial waters). Contains
  information from Timezone Boundary Builder and © OpenStreetMap contributors
  ([copyright and attribution](https://www.openstreetmap.org/copyright)), available
  under the [Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
  The upstream [data licence](https://github.com/evansiroky/timezone-boundary-builder/blob/2026b/DATA_LICENSE)
  is included as `ODbL-1.0.txt`. Verified on 2026-10-15.
- Coastlines: [Natural Earth 1:50m land, v5.1.2](https://github.com/nvkelso/natural-earth-vector/blob/v5.1.2/geojson/ne_50m_land.geojson),
  [public domain](https://www.naturalearthdata.com/about/terms-of-use/).
- City coordinates, country names, region comments and UTC offsets: `zone.tab`,
  `zone1970.tab`, `iso3166.tab` and the zone rules in
  [IANA tzdata2026b](https://data.iana.org/time-zones/releases/tzdata2026b.tar.gz),
  public domain under that archive's `LICENSE`.

The generated map data stays under ODbL-1.0. It is separate data shipped with the
GPL-3.0-or-later installer, not relicensed as GPL code. ODbL section 2.3(a) excludes the
programs operating the database; sections 4.2–4.6 require the notices, share-alike data
and machine-readable source or alteration method supplied here: `ZoneData.js` is the full
machine-readable derivative and `generate-timezones.py` is the method of alteration. The
installer shows the map attribution under the map; activating it opens this notice and
the full ODbL text locally, with no network request.

The installed tzdata tables, not the bundled map, decide which zones the worker accepts
and which UTC offsets and daylight-saving rules apply. The map's offsets only colour the
zones and label them.

## Method

1. Clip every zone ring to 75° N … 60° S. Further north Mercator inflates the Arctic;
   further south there is only Antarctica, whose zones are research-station sectors.
2. Project with Mercator to map units: 3600 across 360° of longitude (0.1° each),
   1916 from top to bottom.
3. Split the rings into arcs where neighbouring zones meet. A border two zones share
   is one arc used by both, so simplification can never open a gap or an overlap.
4. Simplify every arc once (Douglas-Peucker, 1 map unit) and round to whole map units;
   drop rings under 2 square units unless a ring is its zone's largest.
5. Simplify the Natural Earth land the same way. The picker draws water over everything
   outside it, because zone shapes include territorial seas (Hudson Bay would be land).
   Zones stay whole for picking, so a click just off a coast picks that coast's zone.
6. Zones whose land the land layer does not show at all (atolls, small archipelagos)
   get a dot at the centre of each of their rings.
7. Compile the pinned tzdata with `zic` and record each zone's standard and summer UTC
   offsets: the smaller and the larger offset on 15 January and 15 July 2026. Arcs are
   classed as coast, border inside one offset, or border between offsets.

Zone colours follow the standard offset: four tones by whole hour, a fifth for offsets
with :30 or :45. A click inside a zone picks it; over water, or over a zone the running
system does not offer, the nearest offered zone is picked. Zones too small for the map
at this scale (Vatican City, Monaco, Gibraltar, Büsingen) and zones beyond the map's
latitudes (Svalbard, northern Greenland, Antarctica) have no shape; the searchable list
offers every zone. Borders are approximate at this scale and are not political claims.

## Rebuild

From the repository root, with Python 3 and `zic` (tzcode); no Python packages are needed:

```sh
python installer/ui/assets/generate-timezones.py --cache /tmp/emaki-zone-source
python installer/ui/assets/generate-timezones.py --cache /tmp/emaki-zone-source --check
```

The build is deterministic: the same sources give the same bytes (checked on Python
3.14.7, glibc, x86-64; `--check` compares byte for byte).
