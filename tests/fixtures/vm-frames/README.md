<!-- Copyright (C) 2026 Artur Yakymenko -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# Recorded VM frames

These include host captures from disposable Emaki VM acceptance runs and
the explicitly identified offscreen render and composite below. `G-` means
visually labelled good, `B-` means visually labelled bad for the tested stage.
The test runs actual host OCR; it does not replay saved OCR responses.

- `G-clean017-prompt`: graphical encrypted-disk prompt, resized to 800x500.
- `G-clean019-menu-enc`: normal GRUB menu, native 1024x768 geometry.
- `G-eyes57-snaprow-enc`: snapshot submenu with truncated highlighted title.
- `G-eyes58-kernel-enc`: snapshot kernel submenu without an Emaki text row.
- `G-clean021-greeter`: 640x360 center crop of a 1920x1080 greeter.
- `G-key11-greeter2560`: 640x360 center crop of a 2560x1600 greeter.
- `G-audit-f01-cream`: center crop of `f01-first-boot-t020s`, 1920x1080.
- `G-hw37-cream`: center crop of `r3-hw37-0-greeter`, 1376x768.
  These two cream greeters retain the pointer covering the placeholder.
  The former `G-audit-i15-cream` crop duplicated `G-audit-f01-cream` byte for byte
  and was removed; the full i15 capture remains in the external 94-frame checks.
- `B-audit-g19-lock-wrongpw`: center crop of `g19-lock-wrong-password`.
- `B-audit-i07-greeter-wrongpw`: center crop of `i07-greeter-wrong-password`.
  Both 1920x1080 captures show red password feedback below the field and must
  fail specifically for the visible password error.
- `F1-g18-lock-crop640`: the recorded g18 lock with the actual Qt-rendered
  refusal plate pasted at its shared shell coordinates, then center-cropped to
  640x360 without scaling. Source: `u7f-review/frames-false-pass/`; construction
  is recorded in that evidence directory’s `scripts/repro_crop.py`.
- `B-current-greeter-wrongpw`: unmodified 640x480 offscreen Qt render from
  `tests/test-greeter-visual.py` at b80fbd2, with the shipped wallpaper and refusal
  plate. Source: `u7f-review/qt-renders/b80-realwall.png`. These last two fixtures
  must fail for dark-red authentication feedback even when OCR misses its text.
- `B-n2full-installed-prompt`: firmware text password prompt. It must fail as
  an illustrated prompt, menu and greeter despite containing Emaki and password.
- `B-clean018-wrongpw`: illustrated prompt with a password error, resized to 800x500.
- `B-key12-display-inactive`: the recorded inactive display after VM resume;
  fails greeter assessment and is explicitly NOT TESTED by the resume check.

Prompt images use a 64-color palette; menu images use 32 colors. Greeter crops
retain original pixels and include the old baked-in pointer over the field.
Coordinates and OCR boxes always refer to decoded fixture pixels. These fixtures
prove raster recognition, not physical display readability or a new VM run.

The four retained audit/hw37 crops are 640x360 with no scaling or pixel changes. The
`audit` captures come from `audit-eyes-2026-10-04/shots`; `hw37` comes from
`night-desktop-checks/shots`. Each crop contains only the disposable VM display.
