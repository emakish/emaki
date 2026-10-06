# Installer time zone map

`timezone-map.png` and `ZoneData.js` are generated, offline map assets, together
under 165 KiB. Map rendering and search use no network lookup or location service.

Source: [Timezone Boundary Builder, release 2026b](https://github.com/evansiroky/timezone-boundary-builder/releases/tag/2026b),
the comprehensive `timezones.geojson.zip` dataset. Contains information from
Timezone Boundary Builder and © OpenStreetMap contributors, available under
the [Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
The upstream [data licence](https://github.com/evansiroky/timezone-boundary-builder/blob/2026b/DATA_LICENSE)
is included as `ODbL-1.0.txt`. Verified on 2026-10-03.

The generated raster index, zone identifiers and map stay under ODbL-1.0.
They are separate data shipped with the GPL-3.0-or-later installer, not relicensed
as GPL code. ODbL section 2.3(a) excludes the programs operating the database;
sections 4.2–4.6 require the notices, share-alike data and machine-readable source
or alteration method supplied here. The UI displays the map attribution.

City coordinates, country names and region comments come from `zone.tab`,
`zone1970.tab` and `iso3166.tab` in
[IANA tzdata2026b](https://data.iana.org/time-zones/releases/tzdata2026b.tar.gz).
Those data files are public domain under that archive's `LICENSE`.
The installed tzdata tables, not the bundled map, determine which choices the
worker accepts and which UTC offsets and daylight-saving rules it uses.

Rebuild from the repository root with Python and Pillow (tested with Pillow
12.3.0; no build dependency is needed to install the committed assets):

```sh
python installer/ui/assets/generate-timezones.py --cache /tmp/emaki-zone-source
python installer/ui/assets/generate-timezones.py --cache /tmp/emaki-zone-source --check
```

The script pins download URLs and SHA-256 checksums, sorts features, rasterizes
polygons and holes, renders the warm palette and zone edges, then encodes the
same raster as row runs. Those runs drive both hit testing and selection
highlighting; there is no independently drawn hit map. `ZoneData.js` is the full
machine-readable derivative, and the generator is the method of alteration.

South of 60° S, the timezone sectors are clipped to the
[public-domain](https://www.naturalearthdata.com/about/terms-of-use/)
[Natural Earth 1:110m land coastline, v5.1.2](https://github.com/nvkelso/natural-earth-vector/blob/v5.1.2/geojson/ne_110m_land.geojson).
The generator pins its SHA-256 alongside the other sources. This removes the
rectangular territorial sectors over the Southern Ocean from both the picture
and hit map. Antarctic land is drawn in one colour: station timekeeping sectors are not
geographic subdivisions. The clipped index still provides zone hit testing and
selection highlights; the city list remains authoritative. Other zone boundaries
on land remain approximate at this scale.

Projection: equirectangular, the whole globe, 960 × 480 samples. Longitude and
latitude map directly to image coordinates, without a projection dependency or
Mercator's polar singularity. The UI fits that map to its available area. The
land dataset avoids offering fixed-offset ocean zones as substitutes for cities
with daylight-saving rules. Ocean clicks do nothing; UTC shows the zero meridian.
The search list remains authoritative for small islands and boundary pixels.
At this scale borders are approximate and are not intended as political claims.
