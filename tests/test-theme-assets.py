#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise media staging using tiny generated inputs and isolated directories."""
import argparse
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('theme_assets', str(ROOT / 'scripts/prepare-theme-assets'))
spec = importlib.util.spec_from_loader(loader.name, loader)
assets = importlib.util.module_from_spec(spec)
loader.exec_module(assets)


@unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg is required for tiny media fixtures')
class Staging(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='emaki-theme-assets-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.row = {'id': 'emaki', 'name': 'Emaki', 'accent': 'rose', 'mode': 'light',
                    'wallpaper': 'emaki/wallpaper.png', 'overview_video': 'emaki/overview.mp4',
                    'overview_still': 'emaki/overview.jpg', 'thumbnail': 'emaki/thumbnail.jpg'}
        self.catalog = self.root / 'catalog.json'
        self.catalog.write_text(json.dumps({'schema_version': 1, 'styles': [self.row]}))
        (self.root / 'renders').mkdir()
        (self.root / 'videos').mkdir()
        self.wallpaper = self.root / 'renders/variant-a-fixed-8k-13500x2160.png'
        self.video = self.root / 'videos/final-probe.mp4'
        self.ffmpeg('color=c=red:s=160x90', ['-frames:v', '1', str(self.wallpaper)])
        self.ffmpeg('color=c=blue:s=160x90:r=2', ['-t', '1', '-c:v', 'libx264', str(self.video)])
        self.args = argparse.Namespace(catalog=self.catalog, theme=None, cubes=self.root,
                                      ribbons=self.root, videos=self.root / 'videos', height=2160,
                                      allow_probes=False, output=self.root / 'output')

    def ffmpeg(self, source, output):
        subprocess.run(['nice', '-n', '19', 'ffmpeg', '-nostdin', '-v', 'error',
                        '-f', 'lavfi', '-i', source, '-filter_threads', '1', '-threads', '1',
                        *output], check=True)

    def test_probe_is_explicit_and_release_refuses_it(self):
        with self.assertRaisesRegex(ValueError, 'Missing media'):
            assets.stage(self.args)
        self.assertFalse(self.args.output.exists())
        self.args.allow_probes = True
        assets.stage(self.args)
        assets.verify(self.args.output)
        with self.assertRaisesRegex(ValueError, 'Preview videos'):
            assets.verify(self.args.output, release=True)
        record = json.loads((self.args.output / 'assets.json').read_text())
        self.assertEqual(record['probe_themes'], ['emaki'])

    def test_final_is_preferred_and_tampering_rejected(self):
        shutil.copyfile(self.video, self.root / 'videos/final-fixed-ship.mp4')
        assets.stage(self.args)
        assets.verify(self.args.output, release=True)
        with self.assertRaisesRegex(ValueError, 'already exists'):
            assets.stage(self.args)
        (self.args.output / 'emaki/overview.mp4').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed staged asset'):
            assets.verify(self.args.output)

    def test_final_video_name_is_exact(self):
        for name in ('final-fixed.mp4', 'final-fixed-vs-probe.mp4'):
            shutil.copyfile(self.video, self.root / 'videos' / name)
        with self.assertRaisesRegex(ValueError, 'Missing media.*final-fixed-ship.mp4'):
            assets.stage(self.args)
        self.args.allow_probes = True
        self.assertEqual(assets.sources(self.row, self.args)[1], self.video)
        final = self.root / 'videos/final-fixed-ship.mp4'
        final.write_bytes(b'final video selection')
        self.assertEqual(assets.sources(self.row, self.args), (self.wallpaper, final, False))

    def test_variant_a_shipping_directory_is_accepted_unchanged(self):
        row = dict(self.row, id='17-silver', accent='silver')
        for field in assets.FIELDS:
            row[field] = row[field].replace('emaki/', '17-silver/')
        self.catalog.write_text(json.dumps({'schema_version': 1, 'styles': [row]}))
        self.args.ribbons = self.root / 'ship-a-2160'
        self.args.ribbons.mkdir()
        wallpaper = self.args.ribbons / '17-silver-2160.png'
        shutil.copyfile(self.wallpaper, wallpaper)
        video = self.args.videos / 'r2-final-17-silver-3840x2160-hevc10.mp4'
        shutil.copyfile(self.video, video)
        self.assertEqual(assets.sources(row, self.args), (wallpaper, video, False))
        assets.stage(self.args)
        assets.verify(self.args.output, release=True)
        self.assertEqual((self.args.output / row['wallpaper']).read_bytes(), wallpaper.read_bytes())
        self.assertEqual((self.args.output / row['overview_video']).read_bytes(), video.read_bytes())

    def assert_public_modes(self, root):
        for path in (root, *root.rglob('*')):
            self.assertEqual(path.stat().st_mode & 0o777,
                             0o755 if path.is_dir() else 0o644, str(path))

    def complete_stage(self):
        """Reuse tiny media for all shipped catalog paths without more encoders."""
        catalog = json.loads((ROOT / 'themes/catalog.json').read_text())
        stage = self.args.output
        (stage / 'catalog.json').write_text(json.dumps(catalog))
        for row in catalog['styles']:
            for field in assets.FIELDS:
                destination = stage / row[field]
                if not destination.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(stage / self.row[field], destination)
        paths = ['catalog.json'] + [row[field] for row in catalog['styles'] for field in assets.FIELDS]
        record = {'schema_version': 1, 'themes': [row['id'] for row in catalog['styles']],
                  'probe_themes': [], 'sha256': {name: assets.digest(stage / name) for name in paths}}
        (stage / 'assets.json').write_text(json.dumps(record))

    def test_package_payload_uses_verified_external_stage(self):
        shutil.copyfile(self.video, self.root / 'videos/final-fixed-ship.mp4')
        previous_umask = os.umask(0o077)
        try:
            assets.stage(self.args)
        finally:
            os.umask(previous_umask)
        self.assert_public_modes(self.args.output)
        self.complete_stage()
        # Older stages and a restrictive build umask must remain safe to package.
        for path in (self.args.output, *self.args.output.rglob('*')):
            path.chmod(0o700 if path.is_dir() else 0o600)
        source = self.root / 'src'
        package = self.root / 'pkg'
        source.mkdir()
        package.mkdir()
        env = dict(os.environ, EMAKI_THEME_STAGE=str(self.args.output),
                   startdir=str(ROOT / 'packaging/emaki-themes'), srcdir=str(source),
                   pkgdir=str(package))
        subprocess.run(['bash', '-euc', 'umask 077; source "$startdir/PKGBUILD"; prepare; check; package'],
                       env=env, check=True)
        self.assert_public_modes(source / 'themes')
        media = package / 'usr/share/emaki/themes'
        self.assert_public_modes(media)
        self.assertTrue((media / 'emaki/overview.mp4').is_file())
        self.assertTrue((media / 'assets.json').is_file())
        self.assertFalse((media / 'catalog.json').exists())

        # The prepared-source archive must rebuild without the checkout or source stage.
        recipe = self.root / 'standalone'
        recipe.mkdir()
        for name in ('PKGBUILD', 'expected-files.list'):
            shutil.copyfile(ROOT / 'packaging/emaki-themes' / name, recipe / name)
        rebuilt = self.root / 'rebuilt'
        rebuilt.mkdir()
        env.update(startdir=str(recipe), pkgdir=str(rebuilt))
        env.pop('EMAKI_THEME_STAGE')
        shutil.rmtree(self.args.output)
        subprocess.run(['bash', '-euc', 'source "$startdir/PKGBUILD"; check; package'],
                       env=env, check=True)
        for row in json.loads((ROOT / 'themes/catalog.json').read_text())['styles']:
            for field in assets.FIELDS:
                path = Path('usr/share/emaki/themes') / row[field]
                self.assertEqual((rebuilt / path).read_bytes(), (package / path).read_bytes())

    def test_partial_stage_is_valid_for_development_but_not_the_package(self):
        shutil.copyfile(self.video, self.root / 'videos/final-fixed-ship.mp4')
        assets.stage(self.args)
        assets.verify(self.args.output, release=True)
        source = self.root / 'src'
        source.mkdir()
        env = dict(os.environ, EMAKI_THEME_STAGE=str(self.args.output),
                   startdir=str(ROOT / 'packaging/emaki-themes'), srcdir=str(source))
        result = subprocess.run(['bash', '-euc', 'source "$startdir/PKGBUILD"; prepare; check'],
                                env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('must contain every bundled theme', result.stderr)

    def test_selection_and_paths_are_validated_before_publication(self):
        self.args.theme = ['missing']
        with self.assertRaisesRegex(ValueError, 'Unknown theme'):
            assets.stage(self.args)
        for name in ('../outside', '/absolute', 'emaki/../../outside'):
            with self.assertRaisesRegex(ValueError, 'Unsafe catalog asset path'):
                assets.local_path(self.root, name)
        (self.root / 'escape').symlink_to('/tmp', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'escapes staging'):
            assets.local_path(self.root, 'escape/test')


class Delivery(unittest.TestCase):
    def test_default_media_is_a_runtime_dependency_and_build_input(self):
        # The package's own check() runs this in an extracted tree without .git: the recipe then needs its
        # SOURCE-COMMIT file instead of asking git, so read the recipe from a copy that has one.
        with tempfile.TemporaryDirectory() as temporary:
            recipe = Path(temporary) / 'PKGBUILD'
            shutil.copyfile(ROOT / 'packaging/emaki-config/PKGBUILD', recipe)
            (Path(temporary) / 'SOURCE-COMMIT').write_text('0' * 40 + '\n')
            dependencies = subprocess.check_output(
                ['bash', '-euc', 'source "$1"; printf "%s\n" "${depends[@]}"', 'dependencies', str(recipe)],
                text=True, env=dict(os.environ, startdir=temporary)).splitlines()
        self.assertIn('emaki-themes', dependencies)
        import re
        order = re.search(r'ORDER=\(([^)]+)\)', (ROOT / 'packaging/build.sh').read_text()).group(1).split()
        self.assertLess(order.index('emaki-themes'), order.index('emaki-config'))
        import runpy
        self.assertIn('emaki-themes', runpy.run_path(str(ROOT / 'packaging/source_archive.py'))['PACKAGES'])


if __name__ == '__main__':
    unittest.main()
