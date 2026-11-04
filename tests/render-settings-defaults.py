#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture fresh-profile defaults and installed wallpaper/overview controls offscreen."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('render_shell', ROOT / 'tests/render-shell.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=ROOT / '.cache/target/debug/emaki')
    parser.add_argument('--cube', type=Path, required=True)
    parser.add_argument('--ribbons', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--overview-video', type=Path,
                        help='optional local video for personal overview and failure captures')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='settings-defaults-') as temporary:
        profile = Path(temporary)
        for name in ('home', 'config', 'data', 'cache', 'state', 'runtime'):
            (profile / 'fresh' / name).mkdir(parents=True, mode=0o700)
        themes = profile / 'themes'
        styles = json.loads((ROOT / 'themes/catalog.json').read_text())['styles']
        for style in styles:
            source = args.cube if style['id'] == 'emaki' else args.ribbons / (style['id'] + '-2160.png')
            assert source.is_file(), source
            target = themes / style['wallpaper']
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source.resolve())
            (themes / style['thumbnail']).symlink_to(ROOT / 'themes/previews' / (style['id'] + '.jpg'))
            # Still-only catalog fixture; production thumbnails identify each bundle.
            (themes / style['overview_still']).symlink_to(ROOT / 'themes/previews' / (style['id'] + '.jpg'))
        qml = profile / 'shell'
        shutil.copytree(ROOT / 'shell', qml)
        fixture = (ROOT / 'tests/fixtures/SettingsVisualTest.qml').read_text()
        start = fixture.index('    QtObject {\n        id: catalog')
        end = fixture.index('    SystemBackend {', start)
        fixture = fixture[:start] + '''    SettingsCatalog {
        id: catalog
        active: false
        installedWallpapers: [{name: "Emaki landscape", path: Quickshell.env("EMAKI_FIXTURE_PICTURE")}]
    }
''' + fixture[end:]
        fixture = fixture.replace('navigationChecked && aboutReady &&',
                                  'navigationChecked && catalog.values.length > 0 && catalog.wallpapers.length === 20 && thumbnailsReady(view) && aboutReady &&')
        fixture = fixture.replace('    property bool navigationChecked: false', '''    property bool navigationChecked: false
    property bool showOverviewFailure: false
    property bool failureApplied: false
    Connections {
        target: catalog
        function onValuesChanged() {
            if (root.showOverviewFailure && !root.failureApplied && catalog.values.length > 0) {
                root.failureApplied = true;
                Qt.callLater(() => catalog.overviewChooser.completed({state: "invalid_overview_background"}));
            }
        }
    }''')
        fixture = fixture.replace('    readonly property var renderStats:', '''    function thumbnailsReady(item) {
        if (item.objectName.startsWith("wallpaper-thumbnail-") && item.status !== Image.Ready)
            return false;
        return (item.children || []).every(child => thumbnailsReady(child));
    }
    function findNamed(item, name) {
        if (item.objectName === name)
            return item;
        for (const child of item.children || []) {
            const found = findNamed(child, name);
            if (found)
                return found;
        }
        return null;
    }
    readonly property var renderStats:''')
        fixture = fixture.replace('requested_page: requestedPage,', '''requested_page: requestedPage,
            wallpaper_count: catalog.wallpapers.length,
            style_count: catalog.styles.length,
            wallpaper: catalog.value("appearance.wallpaper"),
            cursor: catalog.value("appearance.cursor"),
            automatic_selected: requestedPage === "appearance" ? findNamed(view, "cursor-automatic")?.selected : null,
            overview_group: findNamed(view, "setting-appearance.overview_background") !== null,
            overview_choices: catalog.styles.filter(style => style.available).length,
            overview_video: catalog.value("appearance.overview_video"),
            overview_still: catalog.value("appearance.overview_still"),
            overview_error: catalog.overviewChooserError,
            accent: catalog.value("appearance.accent"),
            thumbnails_ready: thumbnailsReady(view),''')
        fixture_path = qml / 'SettingsVisualTest.qml'
        fixture_path.write_text(fixture)
        env = dict(EMAKI_SETTINGS_PROFILE=str(profile / 'fresh'), EMAKI_BIN=str(args.binary.resolve()),
                   EMAKI_THEME_ASSET_ROOT=str(themes), EMAKI_THEME_CATALOG=str(ROOT / 'themes/catalog.json'),
                   EMAKI_FIXTURE_WALLPAPER=args.cube.resolve().as_uri(),
                   EMAKI_FIXTURE_PICTURE=str(ROOT / 'art/wallpaper/ring.png'),
                   EMAKI_FIXTURE_ACCOUNT='{}', QT_QUICK_CONTROLS_STYLE='Basic')
        for name, page, height in [('overview-background', 'wallpaper', 810),
                                   ('wallpaper-collection', 'wallpaper', 2300),
                                   ('appearance', 'appearance', 810)]:
            stats = args.output / (name + '.json')
            log = renderer.render(fixture_path, png=args.output / (name + '.png'), stats=stats,
                                  properties={'requestedPage': page, 'height': height}, width=1240, height=height,
                                  warmup=700, milliseconds=100, ready_property='ready',
                                  shader_dir=ROOT / '.cache/shell-shaders', extra_env=env)
            (args.output / (name + '.log')).write_text(log)
            for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign',
                          'Cannot assign', 'Cannot create delegate', 'Required property',
                          'Failed to deserialize', 'Failed to find shader'):
                assert error not in log, log
            result = json.loads(stats.read_text())
            state = result['fixture_end']
            assert result['readiness_reached'] and 'llvmpipe' in result['renderer'], result
            assert state['actual_page'] == page and state['graphics_api'] == 3, state
            assert state['cursor'] == 'automatic' and state['accent'] == 'rose', state
            assert state['wallpaper'] == str(themes / 'emaki/wallpaper.png'), state
            assert state['style_count'] == 19 and state['wallpaper_count'] == 20, state
            assert state['thumbnails_ready'], state
            if page == 'appearance':
                assert state['automatic_selected'] is True, state
            else:
                assert state['overview_group'] and state['overview_choices'] == 19, state
            assert not (profile / 'fresh/config/emaki/settings.toml').exists()
            print(f'PASS: {name}: fresh core defaults, production catalog and Qt shader capture')

        if args.overview_video:
            assert args.overview_video.is_file(), args.overview_video
            picture = profile / 'personal overview.png'
            picture.symlink_to(ROOT / 'art/wallpaper/fallback.png')
            video = profile / 'personal overview.mp4'
            video.symlink_to(args.overview_video.resolve())
            cli_env = dict(os.environ, **env, HOME=str(profile / 'fresh/home'),
                           XDG_CONFIG_HOME=str(profile / 'fresh/config'),
                           XDG_STATE_HOME=str(profile / 'fresh/state'),
                           XDG_RUNTIME_DIR=str(profile / 'fresh/runtime'),
                           XDG_CACHE_HOME=str(profile / 'fresh/cache'),
                           XDG_DATA_HOME=str(profile / 'fresh/data'))
            for name, video_path, broken in [('overview-picture', '', False),
                                              ('overview-video', str(video), False),
                                              ('overview-broken', str(video), True)]:
                selection = json.dumps(dict(video=video_path, still=str(picture)))
                changed = subprocess.run([str(args.binary.resolve()), 'settings', 'set',
                                'appearance.overview_background', selection, '--profile-root',
                                str(profile / 'fresh'), '--json'], env=cli_env,
                               capture_output=True, text=True, timeout=20)
                assert changed.returncode == 0, changed.stdout + changed.stderr
                stats = args.output / (name + '.json')
                log = renderer.render(fixture_path, png=args.output / (name + '.png'), stats=stats,
                                      properties={'requestedPage': 'wallpaper', 'showOverviewFailure': broken},
                                      width=1240, height=810, warmup=1000, milliseconds=500,
                                      ready_property='ready', shader_dir=ROOT / '.cache/shell-shaders', extra_env=env)
                (args.output / (name + '.log')).write_text(log)
                result = json.loads(stats.read_text())
                state = result['fixture_end']
                assert result['readiness_reached'] and state['overview_group'], result
                assert state['overview_still'] == str(picture), state
                assert state['overview_video'] == ('' if broken else video_path), state
                assert bool(state['overview_error']) == broken, state
                assert not any(error in log for error in ('ReferenceError', 'TypeError', 'Binding loop')), log
                print(f'PASS: {name}: personal settings and chooser failure fallback captured')


if __name__ == '__main__':
    main()
