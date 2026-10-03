# Emaki wallpaper: the ring

The concept: an elevated view from a hilltop over a long warm valley at sunset; a steam railway
runs through the whole looping strip at one fixed height; places follow one another and the end
flows back into the start. One easter egg: an old Japanese house (13).

Route: 01 forest, lake and peaks · 02 lake · 03 river village · 04 fields, distant megacity ·
05 gorge viaduct · 06 lighthouse coast · 07 seaside town · 08 dunes and shipwreck · 09 desert
station · 10 oasis ruins · 11 vineyard hills · 12 mountain pass · 13 Japanese house · 14 birch
grove · 15 forest that closes the ring into 01.

How it was made (15 source panels, 16:9, 1672x941; the panels themselves, ~2.3 MB each, are not
in this repository):
1. Panel 01 is the style anchor, in the colours of `palette.png`.
2. Every next panel continues the previous one: `make-canvas.py` puts the previous panel's right
   third on a key-green canvas. The closer: `make-canvas.py --close` with the first panel.
3. `remove-sun.py` paints the sun out of 01 (the sun belongs to a sky layer, not to a panel).
4. `stitch.py ... 15.png@0.30 --loop 0.30` cuts every overlap along the least-difference seam.
5. `pixelize.py loop.png --width 0 --height 1080 --palette art/wallpaper/palette-64.gpl
   --cells art/wallpaper/loop-cells.png` → 19396x1080 cells, one cell per physical pixel on a
   1920x1200 screen, every pixel an index into `palette-64.gpl` (64 colours that were taken from
   the ring by k-means in OKLab; the file keeps the indices fixed).
6. `touchup.py loop-cells.png ring.png` → `ring.png`, 19396x1200, the wallpaper itself: the
   painted train and smoke patched out (the train becomes a sprite), the one spruce the rails
   crossed put back in front of them, the sky extended by 120 rows for 16:10 screens.
7. Train sprite: drawn on key green in the colours of `palette.png` after `train-ref.png` (the
   painted train cut from 01), then `sprite.py train.png --palette palette-64.gpl --width 372`
   → `train.png`, 372x36, the palette plus one transparent index.
8. `train-anim.py train.png train-frames.png train-anim.js` → 16 frames per driving wheel turn
   (wheels, rods, crosshead) and the numbers a player needs.
9. `front.py ring.png ring-front.png` → what stands in front of the railway (spruces, birches,
   poles, the Japanese house with its pine), drawn over the train and its smoke.

niri-emaki draws the ring per workspace and runs the trains
(`packaging/niri-emaki/0005-emaki-wallpaper.patch`, enabled in `niri/fork-rules.kdl`).
Later, as updates: water, waterfalls and lights by palette cycling, grass sway, wildlife, a
cleanup pass by hand.
