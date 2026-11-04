#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Render the real Appearance page and native cursor previews without a compositor."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import runpy
import shutil
import tempfile
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('render_shell', ROOT / 'tests/render-shell.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, default=ROOT / '.cache/evidence/cursors')
parser.add_argument('--write-previews', action='store_true',
                    help='regenerate the packaged PNG previews without rendering the page')
args = parser.parse_args()
decoder = runpy.run_path(str(ROOT / 'scripts/cursor-contact-sheet'))['decode']
for choice, theme in [('white', 'Emaki-White'), ('dark', 'Emaki-Dark')]:
    for size in (24, 48):
        expected = decoder(theme, 'default', size)[1][0][0]
        preview = ROOT / f'shell/settings/cursors/{choice}-{size}.png'
        if args.write_previews:
            # Generated pixels carry no metadata; the source keeps the licence header.
            expected.save(preview)
        with Image.open(preview) as actual:
            assert actual.size == expected.size and actual.tobytes() == expected.tobytes()
            assert not actual.info, f'{preview}: unexpected PNG metadata'
if args.write_previews:
    print('PASS: regenerated four native cursor previews without metadata')
    raise SystemExit(0)
args.output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='cursor-appearance-') as temporary:
    qml = Path(temporary) / 'shell'
    shutil.copytree(ROOT / 'shell', qml)
    (qml / 'CursorAppearance.qml').write_text('''import QtQuick
import "settings"
Rectangle {
    id: root
    width: 880; height: 470
    color: SettingsTheme.field
    property string theme: "light"
    readonly property bool translations: Translation.initialize()
    function collect(item) {
        let found = [];
        if (item.objectName.startsWith("cursor-preview-"))
            found.push({name: item.objectName, width: item.width, height: item.height,
                        scale: item.previewScale, ready: item.status === Image.Ready});
        for (const child of item.children || []) found = found.concat(collect(child));
        return found;
    }
    readonly property var renderStats: ({theme: Appearance.theme, previews: collect(page), bottom: page.y + page.height})
    Component.onCompleted: SettingsBridge.values = [{key: "appearance.theme", value: theme}]
    QtObject {
        id: catalog
        readonly property bool writing: false
        function value(key) { return row(key).value; }
        function row(key) {
            const values = {"appearance.theme": root.theme, "appearance.accent": "rose",
                            "appearance.cursor": "automatic", "appearance.light_time": "07:00", "appearance.dark_time": "19:00"};
            return {key: key, value: values[key], default: values[key], editable: true};
        }
    }
    Text {
        x: 30; y: 20; text: qsTranslate("SettingsPageRegistry", "Appearance")
        color: SettingsTheme.ink; font.family: SettingsTheme.fontFamily; font.pixelSize: 24
    }
    SettingsAppearancePage { id: page; x: 30; y: 68; width: root.width - 60; catalog: catalog }
}''')
    for theme in ('light', 'dark'):
        for scale in (1, 2):
            name = f'appearance-{theme}-scale-{scale}'
            stats = args.output / (name + '.json')
            log = renderer.render(qml / 'CursorAppearance.qml', png=args.output / (name + '.png'),
                                  stats=stats, properties={'theme': theme}, width=880, height=470,
                                  scale=scale, screen_scale=scale, warmup=400, milliseconds=1,
                                  extra_env={'EMAKI_LANGUAGE': 'en', 'EMAKI_LOCALE_DIR': str(ROOT / 'i18n/locale'),
                                             'QML_IMPORT_PATH': str(renderer.build_plugin()) + os.pathsep + str(ROOT / '.cache/i18n-native/qml'),
                                             'QT_QUICK_CONTROLS_STYLE': 'Basic'})
            (args.output / (name + '.log')).write_text(log)
            state = json.loads(stats.read_text())['fixture_end']
            assert state['theme'] == theme and state['bottom'] < 470, state
            assert len(state['previews']) == 3, state
            for preview in state['previews']:
                assert preview['ready'] and preview['scale'] == scale, preview
                theme_name = {'automatic': 'Emaki-Dark' if theme == 'dark' else 'Emaki-White', 'white': 'Emaki-White', 'dark': 'Emaki-Dark'}[preview['name'].removeprefix('cursor-preview-')]
                expected = decoder(theme_name, 'default', 24 * scale)[1][0][0]
                assert (preview['width'], preview['height']) == (expected.width / scale, expected.height / scale), preview
            assert not any(error in log for error in ('ReferenceError:', 'TypeError:', 'Binding loop', 'Cannot open:')), log
            print(f'PASS: {name}; native preview pixels and geometry verified')
