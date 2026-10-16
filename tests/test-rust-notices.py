#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise offline notice completeness, provenance and package installation."""
import importlib.util
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('rust_notices', ROOT / 'packaging/rust_notices.py')
notices = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notices)


class Notices(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'Cargo.toml').write_text('[package]\nname="local"\nversion="1.0.0"\n')
        (self.root / 'LICENSE').write_text('Local licence text\n')
        (self.root / 'Cargo.lock').write_text('''[[package]]
name="local"
version="1.0.0"
[[package]]
name="dependency"
version="2.0.0"
source="registry+https://example.invalid/index"
checksum="abc"
''')
        self.vendor = self.root / 'vendor'
        self.crate = self.vendor / 'dependency'
        self.crate.mkdir(parents=True)
        (self.crate / 'Cargo.toml').write_text('[package]\nname="dependency"\nversion="2.0.0"\nlicense="MIT"\n')
        (self.crate / '.cargo-checksum.json').write_text('{"package":"abc"}')
        (self.crate / 'LICENSE-MIT').write_text('Copyright (C) Example\nPermission to redistribute.\n')
        (self.crate / 'NOTICE').write_text('Additional attribution\n')
        (self.crate / 'lib.rs').write_text('// Copyright (C) Another holder\n')

    def render(self):
        return notices.render(self.root / 'Cargo.lock', self.vendor, self.root)

    def test_full_text_and_every_locked_crate(self):
        output = self.root / 'THIRD-PARTY-NOTICES'
        output.write_text(self.render())
        self.assertTrue(output.is_file())
        text = output.read_text()
        for package in notices.manifest(self.root / 'Cargo.lock')['package']:
            self.assertIn(f'===== {package["name"]} {package["version"]} =====', text)
        self.assertIn((self.crate / 'LICENSE-MIT').read_text(), text)
        self.assertIn('Additional attribution', text)
        self.assertIn('Another holder', text)
        self.assertEqual(self.render(), text)

    def test_missing_licence_fails_even_with_spdx(self):
        (self.crate / 'LICENSE-MIT').unlink()
        with self.assertRaisesRegex(ValueError, 'no licence text'):
            self.render()

    def test_missing_source_fails(self):
        (self.crate / 'Cargo.toml').unlink()
        with self.assertRaisesRegex(ValueError, 'matching vendored source'):
            self.render()

    def test_wrong_checksum_fails(self):
        (self.crate / '.cargo-checksum.json').write_text('{"package":"wrong"}')
        with self.assertRaisesRegex(ValueError, 'matching vendored source'):
            self.render()

    def test_nested_component_notices(self):
        component = self.crate / 'embedded'
        component.mkdir()
        (component / 'COPYING').write_text('Embedded component licence\n')
        self.assertIn('Embedded component licence', self.render())

    def test_complete_legal_block_in_source(self):
        (self.crate / 'LICENSE-MIT').unlink()
        text = '/* Copyright Holder\nPermission is hereby granted\nTHE SOFTWARE IS PROVIDED WITHOUT WARRANTY\n*/\n'
        (self.crate / 'lib.rs').write_text(text)
        self.assertIn(text, self.render())

    def test_standard_text_fallback_preserves_provenance(self):
        (self.crate / 'LICENSE-MIT').unlink()
        donor = self.vendor / 'donor'
        donor.mkdir()
        (donor / 'Cargo.toml').write_text('[package]\nname="donor"\nversion="1.0.0"\nlicense="MIT"\n')
        text = 'Copyright Donor\nPermission is hereby granted\nTHE SOFTWARE IS PROVIDED WITHOUT WARRANTY\n'
        (donor / 'LICENSE').write_text(text)
        output = self.render()
        self.assertIn('vendor/donor/LICENSE', output)
        self.assertIn('Another holder', output)
        self.assertIn(text, output)
        (donor / 'LICENSE-APACHE').write_text('Apache License\nVersion 2.0\nEND OF TERMS AND CONDITIONS\n')
        (self.crate / 'Cargo.toml').write_text('[package]\nname="dependency"\nversion="2.0.0"\nlicense="MIT/Apache-2.0"\n')
        output = self.render()
        self.assertIn('Declared MIT terms', output)
        self.assertIn('Declared Apache-2.0 terms', output)
        (self.crate / 'Cargo.toml').write_text('[package]\nname="dependency"\nversion="2.0.0"\nlicense="Unknown"\n')
        with self.assertRaisesRegex(ValueError, 'no licence text'):
            self.render()

    def test_exact_niri_publication_inherits_gpl(self):
        checksum = '6a4adbddf4037ce047854d36a60b5bf80a7990b8db2f0a0b9ede7534b0bae09a'
        lock = self.root / 'Cargo.lock'
        lock.write_text(lock.read_text().replace('dependency', 'niri-ipc').replace('2.0.0', '26.4.0').replace('abc', checksum))
        (self.crate / 'Cargo.toml').write_text('[package]\nname="niri-ipc"\nversion="26.4.0"\nlicense="GPL-3.0-or-later"\nauthors=["Upstream author"]\n')
        (self.crate / 'Cargo.toml.orig').write_text('[package]\nlicense.workspace=true\n')
        (self.crate / '.cargo-checksum.json').write_text('{"package":"' + checksum + '"}')
        (self.crate / 'LICENSE-MIT').unlink()
        (self.root / 'LICENSE').write_bytes((ROOT / 'LICENSE').read_bytes())
        text = self.render()
        self.assertIn('Upstream author', text)
        self.assertIn((ROOT / 'LICENSE').read_text(), text)
        (self.root / 'LICENSE').write_text('A different licence')
        with self.assertRaisesRegex(ValueError, 'no licence text'):
            self.render()

    def test_llvm_exception_or_choice_without_licence_file(self):
        (self.crate / 'LICENSE-MIT').unlink()
        donor = self.vendor / 'donor'
        donor.mkdir()
        (donor / 'Cargo.toml').write_text('[package]\nname="donor"\nversion="1.0.0"\n')
        mit = 'Copyright Donor\nPermission is hereby granted\nTHE SOFTWARE IS PROVIDED WITHOUT WARRANTY\n'
        apache = 'Apache License\nVersion 2.0\nEND OF TERMS AND CONDITIONS\n'
        (donor / 'LICENSE-MIT').write_text(mit)
        (donor / 'LICENSE-APACHE').write_text(apache)
        expression = 'Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT'
        manifest = self.crate / 'Cargo.toml'
        manifest.write_text('[package]\nname="dependency"\nversion="2.0.0"\nlicense="' + expression + '"\n')
        text = self.render()
        self.assertIn('Licence: ' + expression, text)
        self.assertIn(mit, text)
        self.assertIn(apache, text)
        self.assertIn('Another holder', text)
        self.assertNotIn('Declared Apache-2.0 WITH LLVM-exception terms', text)
        # An exception is not optional unless the expression offers another choice.
        manifest.write_text(manifest.read_text().replace(expression, 'Apache-2.0 WITH LLVM-exception'))
        with self.assertRaisesRegex(ValueError, 'no licence text'):
            self.render()

    def metadata(self):
        packages = notices.manifest(self.root / 'Cargo.lock')['package']
        for package, root in zip(packages, (self.root, self.crate)):
            package['id'] = package['name']
            package['manifest_path'] = str(root / 'Cargo.toml')
        return {'packages': packages, 'workspace_default_members': ['local'],
                'resolve': {'nodes': [{'id': 'local', 'deps': [
                    {'pkg': 'dependency', 'dep_kinds': [{'kind': None}]}]},
                    {'id': 'dependency', 'deps': []}]}}

    def test_host_metadata_uses_only_reachable_locked_sources(self):
        metadata = self.metadata()
        (self.root / 'Cargo.toml').write_text('[package]\nname="local"\nversion.workspace=true\n'
                                            'license.workspace=true\n[workspace.package]\n'
                                            'version="1.0.0"\nlicense="GPL-3.0-or-later"\n')
        # Metadata can describe packages outside the host's resolved closure.
        metadata['packages'].append({'name': 'wasm-only', 'version': '1.0.0',
                                     'id': 'wasm-only', 'source': 'registry+https://example.invalid/index',
                                     'manifest_path': str(self.root / 'absent/Cargo.toml')})
        metadata['resolve']['nodes'].append({'id': 'wasm-only', 'deps': []})
        metadata['resolve']['nodes'][0]['deps'].append(
            {'pkg': 'dev-only', 'dep_kinds': [{'kind': 'dev'}]})
        metadata['resolve']['nodes'].append({'id': 'dev-only', 'deps': []})
        # The dependency is needed at build time even without a normal edge.
        metadata['resolve']['nodes'][0]['deps'][0]['dep_kinds'] = [{'kind': 'build'}]
        with (self.root / 'Cargo.lock').open('a') as stream:
            stream.write('[[package]]\nname="wasm-only"\nversion="1.0.0"\nsource="registry+https://example.invalid/index"\n')
        # Cargo's fetched sources do not contain vendor checksum manifests.
        (self.crate / '.cargo-checksum.json').unlink()
        text = notices.render(self.root / 'Cargo.lock', None, self.root, metadata=metadata)
        self.assertIn('===== dependency 2.0.0 =====', text)
        self.assertIn('===== local 1.0.0 =====', text)
        self.assertIn('Licence: GPL-3.0-or-later', text)
        self.assertNotIn('wasm-only', text)
        self.assertNotIn('All packages recorded in Cargo.lock', text)

    def test_host_metadata_rejects_unlocked_source(self):
        metadata = self.metadata()
        metadata['packages'][1]['source'] = 'registry+https://other.invalid/index'
        with self.assertRaisesRegex(ValueError, 'not in Cargo.lock'):
            notices.render(self.root / 'Cargo.lock', None, self.root, metadata=metadata)

    def test_own_crates_declare_workspace_gpl(self):
        workspace = notices.manifest(ROOT / 'Cargo.toml')['workspace']['package']
        self.assertEqual(workspace.get('license'), 'GPL-3.0-or-later')
        for name in ('emaki-cli', 'emaki-core'):
            package = notices.manifest(ROOT / 'crates' / name / 'Cargo.toml')['package']
            self.assertEqual(package.get('license'), {'workspace': True})

    def test_niri_helper_is_a_checksummed_source(self):
        recipe = ROOT / 'packaging/niri-emaki/PKGBUILD'
        result = subprocess.run(['bash', '-c', 'source "$1"; for i in "${!source[@]}"; do '
                                 'printf "%s %s %s\\n" "${source[i]}" "${sha512sums[i]}" "${b2sums[i]}"; done',
                                 'test', str(recipe)], check=True, capture_output=True, text=True)
        entries = {name: (sha, b2) for name, sha, b2 in (line.split() for line in result.stdout.splitlines())}
        self.assertIn('rust_notices.py', entries)
        body = (recipe.parent / 'rust_notices.py').read_bytes()
        self.assertEqual(body, (ROOT / 'packaging/rust_notices.py').read_bytes())
        self.assertEqual(entries['rust_notices.py'], (hashlib.sha512(body).hexdigest(), hashlib.blake2b(body).hexdigest()))

    def test_niri_prepare_scopes_fetch_and_notices_to_host(self):
        recipe = (ROOT / 'packaging/niri-emaki/PKGBUILD').read_text()
        self.assertIn('cargo fetch --locked --target "$(rustc --print host-tuple)"', recipe)
        self.assertIn('cargo metadata --locked --offline --filter-platform "$(rustc --print host-tuple)"', recipe)
        self.assertIn('python "$srcdir/rust_notices.py"', recipe)
        self.assertIn('--metadata "$srcdir/rust-metadata.json"', recipe)
        self.assertNotIn('cargo vendor', recipe)

    def test_rust_recipes_install_notice(self):
        recipes = [p for p in (ROOT / 'packaging').glob('*/PKGBUILD')
                   if 'cargo ' in p.read_text()]
        self.assertEqual({p.parent.name for p in recipes}, {'emaki-config', 'niri-emaki'})
        for recipe in recipes:
            text = recipe.read_text()
            if recipe.parent.name == 'emaki-config':
                self.assertIn('_emaki_make prepare-core || return 1', text)
                self.assertLess(text.index('_emaki_make prepare-core'), text.index('cargo metadata'))
            self.assertIn('cargo metadata --locked --offline --filter-platform "$(rustc --print host-tuple)"', text)
            self.assertIn('--lock Cargo.lock --metadata "$srcdir/rust-metadata.json"', text)
            self.assertNotIn('cargo vendor', text)
            self.assertIn('"$pkgdir/usr/share/licenses/$pkgname/THIRD-PARTY-NOTICES"', text)
        self.assertIn('/usr/share/licenses/emaki-config/THIRD-PARTY-NOTICES',
                      (ROOT / 'packaging/emaki-config/expected-files.list').read_text())
        self.assertIn("'OFL-1.1'", (ROOT / 'packaging/emaki-config/PKGBUILD').read_text())


if __name__ == '__main__':
    unittest.main()
