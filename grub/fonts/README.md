# Installed GRUB menu faces

`menu-24.pf2`, `menu-32.pf2` and `menu-46.pf2` are DejaVu Sans Mono Regular at 24, 32
and 46 pixels with every glyph of the face (3452). They are installed to
`/usr/share/emaki/grub/fonts/` by emaki-config. The licence is
`installer/assets/grub/LICENSE-DejaVu.txt`, installed as
`/usr/share/licenses/emaki-config/LICENSE-DejaVu.txt`.

GRUB scripts cannot read the video mode GRUB selected, so the face is chosen where
the menu is generated: `menu_font()` in `installer/emaki_installer/boot.py` takes the
framebuffer GRUB handed to Linux (`/sys/kernel/boot_params/data`) and picks the
largest face that still leaves 100x30 gfxterm cells. Smaller modes, such as the
menu's preferred 1024x768, keep GRUB's own 16 px Unifont (128x48 cells there).

| face | cell (px) | capital E (px) | chosen for, e.g. |
|---|---|---|---|
| 24 | 14x29 | 18 | 1920x1080, 1600x900 |
| 32 | 19x38 | 23 | 2560x1600, 2560x1440, 1920x1200 |
| 46 | 28x55 | 34 | 3840x2160, 3000x2000, 2880x1800 |

gfxterm draws each row at the face's maximum glyph height and puts the baseline at
its ascent. The ascent and descent are therefore set to the vertical box line's
extent (U+2502), so the menu frame is continuous; taller accent stacks may reach a
few pixels into the row above. 48 px is not used: four quadrant and bracket glyphs
round one pixel taller than the box line there, which leaves gaps in the frame.

`MENU_FONTS` in `boot.py` repeats the cell sizes; `tests/test-menu-font.py` reads
them and the frame alignment from these files. Rebuild with grub 2:2.16-1,
ttf-dejavu 2.37+18+g9b5d1b2f-8 and freetype2 2.14.3-1, then rerun that test:

```sh
ttf=/usr/share/fonts/TTF/DejaVuSansMono.ttf
grub-mkfont -s 24 -c 23 -d 6 -o grub/fonts/menu-24.pf2 $ttf
grub-mkfont -s 32 -c 30 -d 8 -o grub/fonts/menu-32.pf2 $ttf
grub-mkfont -s 46 -c 43 -d 12 -o grub/fonts/menu-46.pf2 $ttf
```
