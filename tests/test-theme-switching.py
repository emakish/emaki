#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real isolated core transactions with tiny assets and the stock niri validator."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BINARY = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / 'target/debug/emaki').resolve()

with tempfile.TemporaryDirectory(prefix='emaki-theme-switch-') as temporary:
    base = Path(temporary)
    for name in ('home', 'config', 'state', 'runtime', 'cache', 'data', 'assets'):
        (base / name).mkdir(mode=0o700)
    styles = json.loads((ROOT / 'themes/catalog.json').read_text())
    styles['styles'] = [styles['styles'][0], styles['styles'][7], styles['styles'][8]]
    for style in styles['styles'][:2]:
        for key in ('wallpaper', 'overview_still', 'thumbnail'):
            path = base / 'assets' / style[key]
            path.parent.mkdir(exist_ok=True)
            Image.new('RGB', (64, 16), '#78aaf0').save(path)
    catalog = base / 'catalog.json'
    catalog.write_text(json.dumps(styles))
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_STATE_HOME=str(base / 'state'),
               XDG_RUNTIME_DIR=str(base / 'runtime'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_DATA_HOME=str(base / 'data'), EMAKI_THEME_CATALOG=str(catalog),
               EMAKI_THEME_ASSET_ROOT=str(base / 'assets'))
    for key in ('NIRI_SOCKET', 'WAYLAND_DISPLAY', 'DISPLAY', 'DBUS_SESSION_BUS_ADDRESS'):
        env.pop(key, None)

    def call(*args, status=None):
        result = subprocess.run([str(BINARY), 'settings', *args, '--profile-root', str(base), '--json'],
                                env=env, capture_output=True, text=True, timeout=20)
        reply = json.loads(result.stdout)
        assert result.returncode == (1 if status == 'rejected' else 0), reply
        if status:
            assert reply['status'] == status, reply
        return reply

    def values(reply):
        return {row['key']: row['value'] for row in reply['settings']}

    source = base / 'config/emaki/settings.toml'
    fresh = call('list')
    defaults = values(fresh)
    assert defaults['appearance.style'] == 'emaki'
    assert defaults['appearance.cursor'] == 'automatic'
    assert defaults['appearance.accent'] == 'rose'
    assert defaults['appearance.wallpaper'] == str(base / 'assets/emaki/wallpaper.png')
    assert defaults['appearance.overview_video'] == str(base / 'assets/emaki/overview.mp4')
    assert next(style for style in fresh['styles'] if style['id'] == 'emaki')['selected']
    assert all(row['override_value'] is None for row in fresh['settings']
               if row['key'] in ('appearance.style', 'appearance.cursor', 'appearance.wallpaper'))
    assert not source.exists(), 'Reading defaults must not seed personal settings'
    unrelated_config = base / 'unrelated-config'
    (unrelated_config / 'wpaperd').mkdir(parents=True)
    (unrelated_config / 'wpaperd/config.toml').write_text('[default]\npath = "/unrelated/wallpaper.png"\n')
    mismatched = subprocess.run([str(BINARY), 'settings', 'list', '--profile-root', str(base), '--json'],
                               env=dict(env, XDG_CONFIG_HOME=str(unrelated_config)),
                               capture_output=True, text=True)
    isolated = json.loads(mismatched.stdout)
    assert mismatched.returncode == 1 and isolated['reason'] == 'isolated_xdg_mismatch', isolated
    assert not source.exists()
    # Find the binary's packaged path independently of this fixture's theme assets.
    packaged_env = dict(env)
    packaged_env.pop('EMAKI_THEME_CATALOG', None)
    packaged_env.pop('EMAKI_THEME_ASSET_ROOT', None)
    packaged = subprocess.run([str(BINARY), 'settings', 'list', '--profile-root', str(base), '--json'],
                              env=packaged_env, capture_output=True, text=True, check=True)
    packaged_root = Path(values(json.loads(packaged.stdout))['appearance.wallpaper']).parents[2]
    legacy = base / 'config/wpaperd/config.toml'
    legacy.parent.mkdir()
    for selector, old_path in ((selector, old_path) for selector in ('any', 'default')
                               for old_path in ('fallback.png', 'ring.png')):
        legacy.write_text('[' + selector + ']\npath = '
                          + json.dumps(str(packaged_root / 'wallpaper' / old_path)) + '\n')
        skeleton_before = legacy.read_bytes()
        skeleton = call('list')
        assert values(skeleton)['appearance.style'] == 'emaki'
        assert values(skeleton)['appearance.cursor'] == 'automatic'
        assert values(skeleton)['appearance.wallpaper'] == defaults['appearance.wallpaper']
        session = subprocess.run([str(BINARY), 'settings', 'session-config'], env=env,
                                 capture_output=True, text=True, check=True)
        rendered = Path(session.stdout.strip()).read_text()
        assert 'mode "strip"' in rendered and defaults['appearance.wallpaper'] in rendered
        assert legacy.read_bytes() == skeleton_before
        assert not source.exists()
    laptop_wallpaper = base / 'home/Pictures/wallpapers/neo-pink.jpg'
    laptop_wallpaper.parent.mkdir(parents=True)
    Image.new('RGB', (64, 16), '#ff0099').save(laptop_wallpaper)
    legacy.write_text('[default]\npath = ' + json.dumps(str(laptop_wallpaper)) + '\n')
    assert not source.exists()
    assert values(call('list'))['appearance.wallpaper'] == defaults['appearance.wallpaper']
    session = subprocess.run([str(BINARY), 'settings', 'session-config'], env=env,
                             capture_output=True, text=True, check=True)
    assert defaults['appearance.wallpaper'] in Path(session.stdout.strip()).read_text()
    source.parent.mkdir(exist_ok=True)
    source.write_text('schema_version = 1\n[bar]\nclock_seconds_open = true\n')
    without_appearance = source.read_bytes()
    assert values(call('list'))['appearance.wallpaper'] == defaults['appearance.wallpaper']
    session = subprocess.run([str(BINARY), 'settings', 'session-config'], env=env,
                             capture_output=True, text=True, check=True)
    assert defaults['appearance.wallpaper'] in Path(session.stdout.strip()).read_text()
    assert source.read_bytes() == without_appearance
    source.unlink()
    personal = base / 'config/emaki/niri-emaki.kdl'
    personal.parent.mkdir(exist_ok=True)
    personal.write_text('emaki-wallpaper { mode "ring"; }\n')
    assert values(call('list'))['appearance.wallpaper'] is None
    session = subprocess.run([str(BINARY), 'settings', 'session-config'], env=env,
                             capture_output=True, text=True, check=True)
    rendered = Path(session.stdout.strip()).read_text()
    assert 'emaki-wallpaper' not in rendered
    assert not any(style['selected'] for style in call('list')['styles'])
    personal.unlink()
    assert not source.exists()
    legacy.unlink()
    chosen = base / 'chosen.png'
    Image.new('RGB', (64, 16), '#123456').save(chosen)
    call('set', 'appearance.wallpaper', str(chosen), status='committed')
    before = source.read_bytes()
    assert values(call('list'))['appearance.wallpaper'] == str(chosen)
    assert source.read_bytes() == before
    ribbon = str(base / 'assets' / styles['styles'][1]['wallpaper'])
    before_values = values(call('list'))
    plain = call('set', 'appearance.wallpaper', ribbon, status='committed')
    assert values(plain)['appearance.wallpaper'] == ribbon
    for key in ('appearance.accent', 'appearance.style', 'appearance.overview_video', 'appearance.overview_still'):
        assert values(plain)[key] == before_values[key], key
    session = subprocess.run([str(BINARY), 'settings', 'session-config'], env=env,
                             capture_output=True, text=True, check=True)
    rendered = Path(session.stdout.strip()).read_text()
    assert 'mode "strip"' in rendered and ribbon in rendered
    call('reset', 'appearance.wallpaper', status='committed')
    legacy = base / 'config/wpaperd/config.toml'
    legacy.parent.mkdir(exist_ok=True)
    legacy.write_text('[default]\npath = ' + json.dumps(str(chosen)) + '\n')
    legacy_before = legacy.read_bytes()
    inherited = call('list')
    assert values(inherited)['appearance.wallpaper'] == defaults['appearance.wallpaper']
    assert values(inherited)['appearance.style'] == 'emaki'
    assert next(style for style in inherited['styles'] if style['id'] == 'emaki')['selected']
    call('set', 'appearance.cursor', 'white', status='committed')
    assert values(call('list'))['appearance.wallpaper'] == defaults['appearance.wallpaper']
    assert legacy.read_bytes() == legacy_before
    plain_cube = call('set', 'appearance.wallpaper', defaults['appearance.wallpaper'], status='committed')
    assert values(plain_cube)['appearance.style'] == 'emaki'
    assert next(style for style in plain_cube['styles'] if style['id'] == 'emaki')['selected']
    explicit_theme = call('set', 'appearance.style', 'emaki', status='committed')
    assert values(explicit_theme)['appearance.style'] == 'emaki'
    assert next(style for style in explicit_theme['styles'] if style['id'] == 'emaki')['selected']
    call('reset', 'appearance.style', status='committed')
    assert legacy.read_bytes() == legacy_before
    legacy.unlink()
    call('set', 'appearance.cursor', 'dark', status='committed')
    call('set', 'appearance.theme', 'automatic', status='committed')
    first = call('set', 'appearance.style', 'emaki', status='committed')
    selected = values(first)
    assert selected['appearance.theme'] == 'automatic'
    assert selected['appearance.cursor'] == 'dark'
    assert selected['appearance.style'] == 'emaki' and selected['appearance.accent'] == 'rose'
    assert next(style for style in first['styles'] if style['id'] == 'emaki')['selected']
    assert not next(style for style in first['styles'] if style['id'] == 'emaki')['video_available']
    fragment = Path(first['generation_path'], 'niri.kdl').read_text()
    assert 'emaki-wallpaper' not in fragment and 'emaki-backdrop' not in fragment
    source = base / 'config/emaki/settings.toml'
    before = source.read_bytes()
    call('set', 'appearance.style', '08-violet-light', status='rejected')
    assert source.read_bytes() == before
    second = call('set', 'appearance.style', '07-blue-light', status='committed')
    assert values(second)['appearance.accent'] == 'blue'
    assert values(second)['appearance.cursor'] == 'dark'
    history = call('history')['history']
    entry = next(entry for entry in history if entry['id'] == second['change_id'])
    assert len(entry['changes']) == 5, entry
    cursor = call('set', 'appearance.cursor', 'white', status='committed')
    undone = call('undo', second['change_id'], status='committed')
    assert values(undone)['appearance.style'] == 'emaki'
    assert values(undone)['appearance.cursor'] == 'white'
    undone_cursor = call('undo', cursor['change_id'], status='committed')
    assert values(undone_cursor)['appearance.cursor'] == 'dark'
    assert values(undone_cursor)['appearance.style'] == 'emaki'
    assert values(undone_cursor)['appearance.accent'] == 'rose'
    reset = call('reset', 'appearance.style', status='committed')
    for key in ('style', 'wallpaper', 'overview_video', 'overview_still'):
        assert values(reset)['appearance.' + key] == defaults['appearance.' + key]
    assert values(reset)['appearance.accent'] == 'rose'
    assert values(reset)['appearance.cursor'] == 'dark'
    restored_bundle = call('undo', reset['change_id'], status='committed')
    assert values(restored_bundle)['appearance.style'] == 'emaki'
    assert values(restored_bundle)['appearance.cursor'] == 'dark'
    before_reset = values(call('set', 'appearance.style', '07-blue-light', status='committed'))
    reset = call('reset', 'appearance.wallpaper', status='committed')
    assert values(reset)['appearance.wallpaper'] == defaults['appearance.wallpaper']
    for key, value in before_reset.items():
        if key != 'appearance.wallpaper':
            assert values(reset)[key] == value, key
    assert values(reset)['appearance.cursor'] == 'dark'
    call('set', 'appearance.style', '07-blue-light', status='committed')
    call('set', 'appearance.accent', 'violet', status='committed')
    reset = call('reset', 'appearance.style', status='committed')
    assert values(reset)['appearance.accent'] == 'violet'
    assert values(reset)['appearance.cursor'] == 'dark'
    call('set', 'appearance.style', '07-blue-light', status='committed')
    reset = call('reset', 'appearance', status='committed')
    for key in ('style', 'wallpaper', 'overview_video', 'overview_still'):
        assert values(reset)['appearance.' + key] == defaults['appearance.' + key]
    assert values(reset)['appearance.accent'] == 'rose'
    assert values(reset)['appearance.cursor'] == 'automatic'
    restored = call('undo', reset['change_id'], status='committed')
    assert values(restored)['appearance.style'] == '07-blue-light'
    assert values(restored)['appearance.accent'] == 'blue'
    assert values(restored)['appearance.cursor'] == 'dark'
    before_values = values(restored)
    plain = call('set', 'appearance.wallpaper', defaults['appearance.wallpaper'], status='committed')
    assert values(plain)['appearance.wallpaper'] == defaults['appearance.wallpaper']
    for key, value in before_values.items():
        if key != 'appearance.wallpaper':
            assert values(plain)[key] == value, key
    entry = next(entry for entry in call('history')['history'] if entry['id'] == plain['change_id'])
    assert [change['key'] for change in entry['changes']] == ['appearance.wallpaper'], entry
    call('set', 'appearance.style', 'emaki', status='committed')
    overview = call('set', 'appearance.overview_background', 'catalog:07-blue-light', status='committed')
    assert next(style for style in overview['styles'] if style['id'] == 'emaki')['selected']
    reset_overview = call('reset', 'appearance.overview_background', status='committed')
    assert values(reset_overview)['appearance.overview_video'] == ''
    assert values(reset_overview)['appearance.overview_still'] == defaults['appearance.overview_still']
    print('PASS: fresh and skeleton Emaki/automatic-cursor defaults without overrides; chosen wallpaper preserved; wallpaper-only ribbon strip; theme transactions, reset, undo, schedule/cursor independence and stock validator')
