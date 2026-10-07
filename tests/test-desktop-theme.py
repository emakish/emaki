#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Token-derived GTK theme and the administrator prompt’s scoped theme."""
from pathlib import Path
from runtime_fixture import runtime_path
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ThemeTests(unittest.TestCase):
    def test_administrator_prompt_selects_emaki_only_for_its_process(self):
        self.assertIn('spawn-at-startup "emaki-autostart" "authentication" "env" "GTK_THEME=Emaki" '
                      '"/usr/lib/polkit-gnome/polkit-gnome-authentication-agent-1"',
                      (ROOT / 'niri/default.kdl').read_text())

    def test_popup_surfaces_and_selection_have_readable_contrast(self):
        # Broadway gives GTK a real display without opening a window on the
        # desktop. Keep all settings, sockets and caches in a temporary home.
        daemon = shutil.which('broadwayd') or shutil.which('gtk3-broadwayd')
        self.assertIsNotNone(daemon, 'GTK3 Broadway is required for colour checks')
        with tempfile.TemporaryDirectory(prefix='emaki-gtk-') as directory:
            home = Path(directory)
            env = dict(os.environ, HOME=directory, GDK_BACKEND='broadway',
                       BROADWAY_DISPLAY=':0', GTK_THEME='Emaki',
                       GSETTINGS_BACKEND='memory', DBUS_SESSION_BUS_ADDRESS='disabled:')
            for key in ('CONFIG', 'DATA', 'CACHE', 'STATE', 'RUNTIME'):
                path = runtime_path(home) if key == 'RUNTIME' else home / key.lower()
                path.mkdir(mode=0o700, exist_ok=True)
                env[f'XDG_{key}_' + ('DIR' if key == 'RUNTIME' else 'HOME')] = str(path)
            (home / 'data/themes').mkdir()
            (home / 'data/themes/Emaki').symlink_to(ROOT / 'gtk/Emaki')
            with (home / 'broadway.log').open('w+') as log:
                process = subprocess.Popen([daemon, '--unixsocket=' + str(runtime_path(home) / 'http'), ':0'],
                                           env=env, stdout=log, stderr=log)
                try:
                    deadline = time.monotonic() + 5
                    # Broadway uses an abstract Unix address: no socket file
                    # appears in XDG_RUNTIME_DIR, despite the printed path.
                    address = '\0' + str(runtime_path(home) / 'broadway1.socket')
                    ready = False
                    while process.poll() is None and time.monotonic() < deadline:
                        try:
                            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                                probe.connect(address)
                            ready = True
                            break
                        except OSError:
                            time.sleep(.02)
                    if process.poll() is not None or not ready:
                        log.seek(0)
                        reason = 'Headless GTK display could not start: ' + log.read().strip()
                        if os.environ.get('EMAKI_TEST_SANDBOX') == '1' and 'Operation not permitted' in reason:
                            self.skipTest(reason)
                        self.fail(reason)
                    result = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                             '--gtk-colours'], env=env, text=True,
                                            capture_output=True, timeout=15)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    samples = json.loads(result.stdout)
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
        self.assertEqual(set(samples), {'menu normal', 'menu hover',
                                        'popover normal', 'popover hover'})
        for name, sample in samples.items():
            with self.subTest(widget=name):
                print(f"{name}: {sample['foreground']} on {sample['background']}, "
                      f"contrast {sample['contrast']:.2f}")
                self.assertGreaterEqual(sample['contrast'], 4.5)

    def test_gtk_theme_parses_and_uses_current_tokens(self):
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk
        css = (ROOT / 'gtk/Emaki/gtk-3.0/gtk.css').read_text()
        tokens = tomllib.loads((ROOT / 'tokens.toml').read_text())
        for key in ('background', 'surface', 'surface_high', 'text', 'accent', 'on_accent'):
            self.assertIn('#' + tokens['color'][key], css)
        self.assertIn('resource:///org/gtk/libgtk/theme/Adwaita/gtk-contained.css', css)
        errors = []
        provider = Gtk.CssProvider()
        provider.connect('parsing-error', lambda _p, _s, error: errors.append(str(error)))
        # GTK's built-in Adwaita import resolves icons through a display. Parse
        # our overrides alone here; the installed authentication window is a VM check.
        provider.load_from_data('\n'.join(line for line in css.splitlines()
                                         if not line.startswith('@import ')).encode())
        self.assertEqual(errors, [])


def gtk_colours():
    """Read the final cascade, including Adwaita, from real widget contexts."""
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk, Gdk
    Gtk.init([])
    Gtk.Settings.get_default().set_property('gtk-enable-animations', False)
    # The theme is selected the way the session starts the prompt (GTK_THEME=Emaki, found in
    # the temporary XDG_DATA_HOME/themes), so GTK applies it exactly as on an installed system.
    # GTK_THEME does not change the gtk-theme-name setting; the colours below show it applied.
    window = Gtk.Window()
    anchor = Gtk.Button(label='Test')
    window.add(anchor)
    menu = Gtk.Menu()
    menu.attach_to_widget(anchor, None)
    menu_item = Gtk.MenuItem.new_with_label('Test')
    menu.append(menu_item)
    popover = Gtk.Popover.new(anchor)
    popover_item = Gtk.ModelButton(text='Test')
    popover.add(popover_item)
    window.show_all()
    menu.show_all()
    popover.show_all()

    def rgba(colour):
        return (colour.red, colour.green, colour.blue, colour.alpha)

    def composite(foreground, background):
        alpha = foreground[3]
        return tuple(foreground[i] * alpha + background[i] * (1 - alpha) for i in range(3)) + (1,)

    def luminance(colour):
        linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4
                  for c in colour[:3]]
        return sum(c * weight for c, weight in zip(linear, (.2126, .7152, .0722)))

    def colour_text(colour):
        return '#' + ''.join(f'{round(c * 255):02x}' for c in colour[:3])

    samples = {}
    for name, surface, item in (('menu', menu, menu_item),
                                ('popover', popover, popover_item)):
        surface_colour = rgba(surface.get_style_context().get_background_color(Gtk.StateFlags.NORMAL))
        if surface_colour[3] != 1:
            raise AssertionError(f'{name} surface is not opaque: {surface_colour}')
        for state_name, state in (('normal', Gtk.StateFlags.NORMAL),
                                  ('hover', Gtk.StateFlags.PRELIGHT)):
            item.set_state_flags(state, True)
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            context = item.get_style_context()
            background = composite(rgba(context.get_background_color(state)), surface_colour)
            foreground = composite(rgba(context.get_color(state)), background)
            levels = sorted((luminance(foreground), luminance(background)))
            samples[name + ' ' + state_name] = dict(
                foreground=colour_text(foreground), background=colour_text(background),
                contrast=(levels[1] + .05) / (levels[0] + .05))
    print(json.dumps(samples))


if __name__ == '__main__':
    if sys.argv[1:] == ['--gtk-colours']:
        gtk_colours()
    else:
        unittest.main()
