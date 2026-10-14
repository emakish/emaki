<!-- Copyright (C) 2026 Artur Yakymenko -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# Light pixel references

These 1536 × 960 PNGs freeze the released 0.8.0 light/orange surfaces (light was the
default then; the default is now dark, so the captures choose light explicitly) using
`ThemeShots.qml`, the shipped wallpaper and base Regular/Bold/Italic/BoldItalic
font faces, Qt 6.11.2 and llvmpipe (LLVM 22.1.8). The scene uses the real Programs & Updates
application icon and no invented Settings application entry.

Strict pixel references use Qt's native glyph rasterizer
(`QML_DISABLE_DISTANCEFIELD=1`) on both the released and current surfaces.
Distance-field glyph atlas allocation can change an antialiased text-edge pixel
between otherwise identical runs; waiting longer or retrying does not eliminate
that variation. The native glyph path removes this allocation-dependent rounding
without masking pixels or allowing a colour tolerance. Normal presentation
screenshots retain the production distance-field text path. The JSON renderer
statistics record which text rasterizer each capture used.

Run `make check-theme-pixels` with the shell render dependencies installed.
The check waits for settled surfaces and retries one mismatch before failing.
Only two approved changes are excluded from comparison: the newly enabled Appearance
navigation entry in Settings and the launcher's Settings button, where the grey gear
replaces the released sun.
No repository history is required. Update references only after an intentional
default-design change has been reviewed; never overwrite them during the check.

`programs-browse.png` was updated once, in 0.8.1: the From Emaki shelf now offers
snapguard, so its card takes the place of the NVIDIA card at the bottom of the page.
Every pixel outside that card's row (788,879 to 1444,910) is unchanged from 0.8.0.

`launcher.png` was updated once, in 0.8.1, for the keyboard round's fix of the first
launcher open: 0.8.0 drew the mode drop as an 8 × 8 drop at the plate's top-left corner
(-4,-4) and left the "Apps" tab unmarked; now the drop sits on the Apps tab. The changed
pixels lie in (0,0 to 64,46), the corner where the stray drop was, (340,1 to 518,123), the
Apps tab with its drop, and three pixels of the plate's right rim at (729,22 to 729,24);
11129 pixels in all. The Settings button (the grey gear) keeps 0.8.0's pixels here, since it
is compared as an approved change.

`settings.png` was updated once, in 0.8.1, for the panel visibility choices: "Hide the panel
automatically" is a menu ("In Full Screen Only") instead of a switch, with a new explanation
under it. The changed pixels lie in (346,186 to 721,198), the explanation, and (918,164 to
1097,197), the menu; 8318 pixels in all. The Appearance navigation entry keeps 0.8.0's
pixels, since it is compared as an approved change.
