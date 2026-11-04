#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture launcher review states using production QML and CPU-only glass rendering."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
spec = importlib.util.spec_from_file_location('pictures', ROOT / 'tests/theme-pictures.py')
pictures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pictures)
SCENARIOS = {
    '01-launcher': ('launcher', '', {}),
    '02-category-keyboard': ('launcher', 'body.focusControl(body.categoryControls()[2]);', {}),
    '04-app-keyboard': ('launcher', 'body.focusResults(); body.selectAt(1);', {}),
    '05-mode-icons': ('launcher', '', {'launcher.mode_icons': True}),
    '16-mode-tooltip': ('launcher', 'const tab = reviewInput.findChild(body, \"launcher-tab-all\"); reviewInput.mouseMove(tab, tab.width / 2, tab.height / 2);', {'launcher.mode_icons': True}),
    '10-commands': ('launcher', 'body.setMode("Commands");', {}),
    '11-emoji': ('launcher', 'body.setMode("Emoji");', {}),
    '12-emoji-search': ('launcher', 'body.setMode("Emoji"); body.setQuery("smile");', {}),
    '06-search-optics': ('launcher', 'body.setQuery("Editor");', {}),
    '03-clock-closed-default': ('bar', '', {}),
    '03-clock-closed-seconds': ('bar', '', {'bar.clock_seconds_closed': True}),
    '03-clock-open-default': ('clock', '', {}),
    '03-clock-open-seconds': ('clock', '', {'bar.clock_seconds_open': True}),
    '14-mode-keyboard': ('launcher', 'body.focusControl(body.headerControls().find(item => item.objectName === "launcher-tab-windows"));', {}),
    '15-search-keyboard': ('launcher', 'body.setQuery("Editor"); body.focusControl(body.headerControls()[1]);', {}),
    '13-settings': ('settings-launcher', '', {
        'bar.clock_seconds_closed': False, 'bar.clock_seconds_open': False,
        'launcher.mode_icons': False, 'bar.autohide_mode': 'fullscreen',
        'bar.overview_workspaces': True, 'dock.on': True, 'dock.auto_hide': True}),
}


def stage(source, target, action, settings):
    pictures.stage(source, target)
    scene = target / 'ShellRender.qml'
    scene.write_text(scene.read_text().replace('new Date(2026, 8, 23, 14, 32, 0)',
                                              'new Date(2026, 8, 23, 14, 32, 23)'))
    for path in (target / "manager").glob("*.js"):
        path.write_text(path.read_text().replace("file:@EMAKI_DATADIR@/shell", ".."))
    fixture = target / 'ThemeShots.qml'
    text = fixture.read_text()
    text = text.replace('import QtQuick\n', 'import QtQuick\nimport QtTest\n')
    text = text.replace('id: root\n', 'id: root\n    TestCase { id: reviewInput; when: false }\n', 1)
    text = text.replace('const rows = [', 'const rows = ' + json.dumps(
        [{'key': key, 'value': value, 'default': value, 'editable': True}
         for key, value in settings.items()]) + '.concat([')
    text = text.replace('].filter(row => row.value !== "");', ']).filter(row => row.value !== "");')
    text = text.replace('property bool configured: false',
                        'property bool configured: false\n    property bool reviewConfigured: false')
    text = text.replace('readonly property bool ready: configured',
                        'readonly property bool ready: reviewConfigured && configured')
    text = text.replace('const scene = desktop.fixtureScene;',
                        'const scene = desktop.fixtureScene; root.Window.window.requestActivate();')
    text = text.replace('Component.onCompleted: applyPalette()',
        'Component.onCompleted: applyPalette()\n'
        '    Timer { interval: 200; repeat: true; running: !root.reviewConfigured; onTriggered: {\n'
        '        if (!surface.item || !surface.item.configured) return;\n'
        '        const scene = surface.item.fixtureScene; const body = scene.input;\n'
        '        if (root.shot === "launcher" && (!body.opened || !body.results.length)) return;\n'
        + ('        if (body.categoryControls().length < 3) return;\n' if 'focusControl' in action else '')
        + action + '\n'
        '        root.reviewConfigured = true;\n    }}')
    text = text.replace('theme: theme,',
        'theme: theme,\n'
        '            reviewTooltipVisible: reviewInput.findChild(surface.item?.fixtureScene?.input, \"launcher-mode-tooltip\")?.visible ?? false,\n'
        '            reviewTooltipLabel: reviewInput.findChild(surface.item?.fixtureScene?.input, \"launcher-mode-tooltip\")?.label ?? \"\",\n'
        '            reviewSelection: surface.item?.fixtureScene?.input.selection !== null,\n'
        '            reviewClosedTime: surface.item?.fixtureScene?.bar.clockIsland.time ?? "",\n'
        '            reviewOpenTime: surface.item?.fixtureScene?.clockPanel.openTime ?? surface.item?.fixtureScene?.clockPanel.time ?? "",\n'
        '            reviewMode: surface.item?.fixtureScene?.input.modeIndex ?? -1,\n'
        '            reviewQuery: surface.item?.fixtureScene?.input.queryText ?? "",\n'
        '            reviewResults: surface.item?.fixtureScene?.input.results.length ?? 0,\n'
        '            reviewKeyboard: surface.item?.fixtureScene?.input.keyboardMode ?? false,\n'
        '            reviewGrid: surface.item?.fixtureScene?.input.gridMode ?? false,\n'
        '            reviewCategoryFocus: surface.item?.fixtureScene?.input.categoryControls().indexOf(root.Window.window.activeFocusItem) ?? -1,\n'
        '            reviewFocus: surface.item?.fixtureScene?.input.Window.window?.activeFocusItem?.objectName ?? "",\n'
        '            reviewEmojiState: surface.item?.fixtureScene?.input.emoji.state ?? "",', 1)
    fixture.write_text(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--skip-existing', action='store_true', help='Preserve completed PNG, metadata and log triples.')
    parser.add_argument('--phase', required=True, choices=('before', 'after'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scenario', action='append', choices=SCENARIOS)
    parser.add_argument('--scale', action='append', type=int, choices=(1, 2))
    parser.add_argument('--theme', action='append', choices=('light', 'dark'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='launcher-review-') as temporary:
        pictures.WORK = Path(temporary)
        environment = pictures.environment()
        environment['EMAKI_SHELL_CLIPBOARD_RECORDER'] = '0'
        wallpaper = pictures.wallpaper()
        for name in args.scenario or SCENARIOS:
            shot, action, settings = SCENARIOS[name]
            target = Path(temporary) / 'scene'
            stage(args.source, target, action, settings)
            for theme in args.theme or ('light', 'dark'):
                for scale in args.scale or (1, 2):
                    prefix = args.output / f'{name}-{theme}-s{scale}-{args.phase}'
                    if args.skip_existing and all(prefix.with_suffix(ext).exists() for ext in ('.png', '.json', '.log')):
                        print('SKIP', prefix.name, flush=True)
                        continue
                    log = pictures.RENDER.render(
                        target / 'ThemeShots.qml', png=prefix.with_suffix('.png'),
                        stats=prefix.with_suffix('.json'),
                        properties=dict(shot=shot, theme=theme, accent='orange', wallpaper=wallpaper),
                        width=1536, height=960, scale=scale, screen_scale=scale,
                        warmup=1500, milliseconds=100, ready_property='ready',
                        shader_dir=ROOT / '.cache/shell-shaders', extra_env=environment)
                    prefix.with_suffix('.log').write_text(log)
                    stats = json.loads(prefix.with_suffix('.json').read_text())
                    assert stats['readiness_reached'] and 'llvmpipe' in stats['renderer'], stats
                    assert not any(error in log for error in ('ReferenceError', 'TypeError', 'Binding loop',
                        'Unable to assign', 'Cannot assign', 'Cannot create delegate', 'Required property',
                        'Failed to deserialize', 'Failed to find shader')), log
                    state = stats['fixture_end']
                    assert state['dark'] == (theme == 'dark'), state
                    expected_mode = 5 if name == '10-commands' else 6 if 'emoji' in name else 1
                    assert state['reviewMode'] == expected_mode, state
                    if 'emoji' in name:
                        assert state['reviewEmojiState'] == 'ready' and state['reviewResults'] > 0, state
                        if args.phase == 'after':
                            assert state['reviewGrid'], state
                    if name == '12-emoji-search':
                        assert state['reviewQuery'] == 'smile', state
                    if name in ('06-search-optics', '15-search-keyboard'):
                        assert state['reviewQuery'] == 'Editor', state
                    if name == '02-category-keyboard':
                        assert state['reviewCategoryFocus'] == 2, state
                    if name == '14-mode-keyboard':
                        assert state['reviewFocus'] == 'launcher-tab-windows' and state['reviewKeyboard'], state
                    if name == '15-search-keyboard':
                        assert state['reviewFocus'] == 'launcher-search' and state['reviewKeyboard'], state
                    if name == '16-mode-tooltip':
                        assert state['reviewTooltipVisible'] and state['reviewTooltipLabel'] == 'All', state
                    if name == '15-search-keyboard' and args.phase == 'after':
                        assert state['reviewSelection'], state
                    if name == '04-app-keyboard':
                        assert state['reviewFocus'] == 'launcher-results', state
                    if args.phase == 'after' and name.startswith('03-clock'):
                        field = 'reviewOpenTime' if '-open-' in name else 'reviewClosedTime'
                        assert state[field].count(':') == (2 if name.endswith('seconds') else 1), state
                    print('PASS', prefix.name, flush=True)


if __name__ == '__main__':
    main()
