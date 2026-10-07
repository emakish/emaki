# GRUB unlock-screen artwork

The visible text is Adwaita Sans, rendered with antialiasing into opaque RGB PNGs.
Each variant has initial, retry and checking masters at 2560×1600. GRUB stretches
the master to its automatically selected firmware mode, so even an unlisted GOP
mode fills the screen. The four sentences stay centred as one block, with the
last line reserved on the initial screen. Each sentence stays on one line, with
even spacing and at least 12% side margins. Adwaita Sans is rasterized at 90 px:
at 1280 pixels wide the text is equivalent to 45 px; at 2560 it remains 90 px.
Different aspect ratios stretch both the picture and glyphs; sharpness above the
master resolution and physical size on unusual displays still need hardware checks.

`VARIANT` in `emaki_installer/grub_screen.py` selects A, B or C in one line:

- A: the historical cream text on the warm dark background.
- B: the historical more spacious layout, with an orange retry line.
- C: the boot menu's `art/grub/background.png`, with a soft darker band behind
  the text and a thin orange rule above the instructions.

Text colors come from `tokens.toml`. C resamples the 1920×1200 menu picture to
2560×1600 without cropping or changing its aspect ratio. The generator measures
contrast against the brightest pixel in each complete text rectangle and refuses
anything below 4.5:1. Solid letter interiors therefore pass even over the brightest
part of the wave; antialiased edges blend normally.

The top 128 master rows remain black to conceal the terminal cursor even when
stretched down to 480 pixels high; the fade ends at row 240. The checking sentence
is the single `CHECKING` constant. Its picture is ready, but stock cryptomount has
no script hook between Enter and key derivation, so the current script cannot
display it during checking. The generator writes lossless PNG Up filters to keep
the three embedded RGB pictures small; the offline tests decode those filters
without Pillow or access to the root artwork.

The PNGs are committed under `installer/emaki_installer/grub_artwork/` and copied
beside the installed Python module by the package recipe. There is no image
renderer or UI font dependency at package build time or on the live ISO. Only
regeneration needs Arch's `python-pillow` (Pillow/FreeType) and `adwaita-fonts`:

```sh
python installer/assets/grub/generate-screens.py
python installer/assets/grub/generate-screens.py --check
```

Regeneration uses the installed Adwaita Sans Regular file and the root palette;
`--font` can name that font at another location. The generator rejects a different
font family. Font or renderer upgrades may change antialiasing; regenerate and
inspect all states before committing updated pictures. The current files were
rendered with Pillow 12.3.0. No font file is embedded in the PNGs.

The installer carries the script, pictures and fonts inside its EFI image's
memdisk. The early config uses `source` to enter the embedded normal parser in
batch mode; an unbounded loop handles wrong passwords, empty input and Escape.
All files needed before unlocking explicitly name `(memdisk)`. Hidden colors are
enabled only after gfxterm and the initial picture both succeed. Failure of
graphics or either displayed picture restores light console instructions; GRUB's
native prompt and diagnostics are then visible. Successful unlock drains queued
input, clears the old picture, and loads an embedded visible font before the menu.
The early EFI image uses the firmware GOP driver so it can restore the EFI console;
firmware without usable GOP takes the plain-text path.

The prototype `unlock-24.pf2`, `unlock-32.pf2` and `unlock-48.pf2` remain available.
Only the 24 px font is packaged and embedded as `visible.pf2`, for menu fallback
when the installed Unicode font is unavailable. These are DejaVu Sans Mono from `ttf-dejavu 2.37+18+g9b5d1b2f-8`,
rendered by `grub-mkfont` from `grub 2:2.16-1`, then reduced to printable ASCII
(U+0020..U+007E) by `subset-pf2.py`. The licence is `LICENSE-DejaVu.txt`.

To rebuild a prototype font:

```sh
grub-mkfont -s 32 -o full-32.pf2 /usr/share/fonts/TTF/DejaVuSansMono.ttf
python installer/assets/grub/subset-pf2.py --family 'DejaVu Sans Mono' full-32.pf2 installer/assets/grub/unlock-32.pf2
```
