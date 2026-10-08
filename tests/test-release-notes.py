#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise release-note collection against disposable, local Git histories."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/release-notes'


class ReleaseNotesTest(unittest.TestCase):
    def setUp(self):
        evidence = ROOT / '.cache/evidence'
        evidence.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='release-notes-', dir=evidence)
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo'
        self.repo.mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith('GIT_')}
        self.env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_TERMINAL_PROMPT='0', LC_ALL='C',
                        GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
                        GIT_COMMITTER_NAME='Fixture',
                        GIT_COMMITTER_EMAIL='fixture@example.invalid',
                        GIT_AUTHOR_DATE='2026-01-01T00:00:00+00:00',
                        GIT_COMMITTER_DATE='2026-01-01T00:00:00+00:00')
        self.git('init', '--quiet', '--initial-branch=main', '--template=')
        self.git('config', 'core.hooksPath', os.devnull)
        self.git('config', 'commit.gpgsign', 'false')
        self.git('config', 'merge.gpgsign', 'false')
        self.base = self.commit('Initial fixture', {'README': 'fixture\n'})

    def git(self, *args):
        result = subprocess.run(['git', *args], cwd=self.repo, env=self.env,
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def commit(self, message, changes=None):
        for name, content in (changes or {}).items():
            path = self.repo / name
            if content is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding='utf-8')
        self.git('add', '--all')
        self.git('commit', '--quiet', '--allow-empty', '-m', message)
        return self.git('rev-parse', 'HEAD')

    def notes(self, old=None, new='HEAD', check=False, cwd=None):
        return subprocess.run([sys.executable, str(SCRIPT),
                               *(['--check'] if check else []),
                               old or self.base, new], cwd=cwd or self.repo,
                              env=self.env, text=True, capture_output=True, timeout=15)

    def assert_result(self, result, code=0, output=''):
        self.assertEqual(result.returncode, code, result.stderr)
        self.assertEqual(result.stdout, output)
        if code:
            self.assertTrue(result.stderr.strip())
            self.assertNotIn('Traceback', result.stderr)
        else:
            self.assertEqual(result.stderr, '')

    def test_multiple_trailers_order_and_exact_deduplication(self):
        self.commit('One\n\nNote: Removed: Old shortcut is removed.\n'
                    'Note: Fixed: Windows open reliably.\n'
                    'Note: New: Search is available.\n'
                    'Note: Changed: Shortcuts are clearer.')
        self.commit('Two\n\nNote: New: Search is available.\n'
                    'Note: New: Search is faster.\n'
                    'Note: Changed: Search is available.')
        self.assert_result(self.notes(), output=(
            '## New\n\n- Search is available.\n- Search is faster.\n\n'
            '## Fixed\n\n- Windows open reliably.\n\n'
            '## Changed\n\n- Shortcuts are clearer.\n- Search is available.\n\n'
            '## Removed\n\n- Old shortcut is removed.\n'))

    def test_branch_and_merge_notes_are_both_collected(self):
        self.git('checkout', '--quiet', '-b', 'feature')
        self.commit('Feature\n\nNote: New: Search is available.', {'shell/search': 'new'})
        self.git('checkout', '--quiet', 'main')
        self.commit('Documentation', {'docs/guide': 'guide'})
        self.git('merge', '--quiet', '--no-ff', 'feature', '-m',
                 'Merge feature\n\nNote: New: Search is available.\n'
                 'Note: Fixed: Search results stay visible.')
        self.assert_result(self.notes(check=True), output=(
            '## New\n\n- Search is available.\n\n'
            '## Fixed\n\n- Search results stay visible.\n'))

    def test_exact_prior_note_can_be_replaced_without_rewriting_history(self):
        old = self.commit('Old\n\nNote: Removed: Theme dependencies.\n'
                          'Note: Changed: Theme dependencies.')
        self.commit('Correct\n\nNote-Replaces: Removed: Theme dependencies.\n'
                    'Note: Removed: Cursor dependency.')
        self.assert_result(self.notes(), output='## Changed\n\n- Theme dependencies.\n\n'
                           '## Removed\n\n- Cursor dependency.\n')
        self.assert_result(self.notes(old=old), output='## Removed\n\n- Cursor dependency.\n')

    def test_replacement_requires_valid_note_and_new_text(self):
        for trailers in ('Note-Replaces: Removed: Old feature.',
                         'Note-Replaces: Old feature.\nNote: Changed: New feature.'):
            old = self.git('rev-parse', 'HEAD')
            self.commit('Invalid correction\n\n' + trailers)
            self.assert_result(self.notes(old=old), code=2)

    def test_merge_trailer_alone_satisfies_check(self):
        self.git('checkout', '--quiet', '-b', 'feature')
        self.commit('Feature', {'shell/search': 'new'})
        self.git('checkout', '--quiet', 'main')
        self.git('merge', '--quiet', '--no-ff', 'feature', '-m',
                 'Merge feature\n\nNote: New: Search is available.')
        self.assert_result(self.notes(check=True), output='## New\n\n- Search is available.\n')

    def test_merge_resolution_is_a_visible_change(self):
        self.git('checkout', '--quiet', '-b', 'feature')
        self.commit('Feature documentation', {'docs/feature': 'new'})
        self.git('checkout', '--quiet', 'main')
        self.commit('Main documentation', {'docs/main': 'new'})
        self.git('merge', '--quiet', '--no-ff', '--no-commit', 'feature')
        self.commit('Resolve merge', {'shell/resolution': 'new'})
        self.assert_result(self.notes(check=True), code=1)

    def test_empty_and_documentation_only_ranges(self):
        self.assert_result(self.notes(check=True))
        self.commit('Documentation', {'docs/guide': 'new', 'scripts/helper': 'new'})
        self.assert_result(self.notes(check=True))

    def test_each_visible_root_needs_a_note(self):
        old = self.base
        for root in ('shell', 'niri', 'installer', 'upkeep', 'systemd'):
            with self.subTest(root=root):
                new = self.commit('Change defaults', {root + '/fixture': 'new'})
                self.assert_result(self.notes(old=old, new=new, check=True), code=1)
                self.assert_result(self.notes(old=old, new=new))
                old = new

    def test_package_payload_and_recipe_changes(self):
        old = self.commit('Package fixture', {'packaging/example/PKGBUILD': 'pkgname=example\n',
                                              'packaging/example/payload': 'old'})
        new = self.commit('Change payload', {'packaging/example/payload': 'new'})
        self.assert_result(self.notes(old=old, new=new, check=True), code=1)
        old = new
        new = self.commit('Change recipe', {'packaging/example/PKGBUILD': 'pkgname=example\npkgrel=2\n'})
        self.assert_result(self.notes(old=old, new=new, check=True), code=1)

    def test_deleted_package_and_transient_package(self):
        old = self.commit('Package fixture', {'packaging/example/PKGBUILD': 'pkgname=example\n',
                                              'packaging/example/payload': 'old'})
        new = self.commit('Remove package', {'packaging/example/PKGBUILD': None,
                                             'packaging/example/payload': None})
        self.assert_result(self.notes(old=old, new=new, check=True), code=1)
        self.assert_result(self.notes(check=True), code=1)

    def test_packaging_tooling_does_not_need_notes(self):
        self.commit('Mirror tooling', {'packaging/mirror/publish.py': 'new',
                                      'packaging/build.sh': 'new',
                                      'packaging/README.md': 'new'})
        self.assert_result(self.notes(check=True))

    def test_reverted_visible_change_still_needs_note(self):
        self.commit('Add setting', {'niri/fixture': 'new'})
        self.commit('Remove setting', {'niri/fixture': None})
        self.assert_result(self.notes(check=True), code=1)

    def test_rename_out_of_visible_root_still_needs_note(self):
        old = self.commit('Fixture', {'shell/example': 'content'})
        self.commit('Move fixture', {'shell/example': None, 'docs/example': 'content'})
        self.assert_result(self.notes(old=old, check=True), code=1)

    def test_body_and_malformed_notes_do_not_count(self):
        self.commit('Prose\n\nNote: New: This is a body example.\n\n'
                    'The message continues after the example.', {'shell/example': 'new'})
        for trailer in ('Note: new: Wrong case.', 'Note: Other: Unknown kind.',
                        'Note: Fixed:', 'Note: Changed:   '):
            self.commit('Malformed footer\n\n' + trailer)
        self.assert_result(self.notes(check=True), code=1)

    def test_other_trailer_keys_can_share_footer(self):
        self.commit('Fix\n\nReviewed-by: Fixture\nNote: Fixed: Windows open reliably.\n'
                    'Issue: 123')
        self.assert_result(self.notes(), output='## Fixed\n\n- Windows open reliably.\n')

    def test_old_boundary_is_excluded_and_new_is_included(self):
        old = self.commit('Old\n\nNote: New: Old feature is available.')
        new = self.commit('New\n\nNote: New: New feature is available.')
        self.commit('Later\n\nNote: New: Later feature is available.')
        self.assert_result(self.notes(old=old, new=new),
                           output='## New\n\n- New feature is available.\n')

    def test_invalid_and_option_like_refs(self):
        for old, new in (('missing-ref', 'HEAD'), (self.base, 'missing-ref'),
                         ('--all', 'HEAD'), (self.base, '--all')):
            with self.subTest(old=old, new=new):
                self.assert_result(self.notes(old=old, new=new), code=2)

    def test_nonrepository_has_clear_failure(self):
        self.assert_result(self.notes(cwd=Path(self.temp.name)), code=2)

    def test_annotated_tags_resolve_to_commits(self):
        self.git('tag', '-a', 'previous', '-m', 'Previous release')
        self.commit('Feature\n\nNote: New: Search is available.')
        self.git('tag', '-a', 'candidate', '-m', 'Candidate release')
        self.assert_result(self.notes(old='previous', new='candidate'),
                           output='## New\n\n- Search is available.\n')

    def test_shallow_repository_requires_complete_history(self):
        self.commit('Feature\n\nNote: New: Search is available.')
        shallow = Path(self.temp.name) / 'shallow'
        self.git('clone', '--quiet', '--depth', '1', self.repo.as_uri(), str(shallow))
        result = self.notes(old='HEAD', new='HEAD', cwd=shallow)
        self.assert_result(result, code=2)
        self.assertIn('complete local history', result.stderr)


if __name__ == '__main__':
    unittest.main()
