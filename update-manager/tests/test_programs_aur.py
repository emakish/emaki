#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""AUR section: consent, trust facts, recipe pins, build plans and program matching."""
import gzip
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import programs_aur as aur

COMMIT = 'a' * 40


def entry(name, version='1-1', base=None, depends=(), make=(), **fields):
    value = dict(Name=name, PackageBase=base or name, Version=version, Description=name + ' tool',
                 NumVotes=12, Popularity=0.4567, Maintainer='someone', OutOfDate=None,
                 FirstSubmitted=1600000000, LastModified=1700000000, URL='https://example.org/' + name,
                 License=['MIT'], Depends=list(depends), MakeDepends=list(make))
    value.update(fields)
    return value


class FakeSystem:
    def __init__(self, aur_entries, installed=None, repository=None, commits=None):
        self.entries = {row['Name']: row for row in aur_entries}
        self.installed_packages = dict(installed or {})
        self.repository_rows = dict(repository or {})
        self.commits = dict(commits or {})
        self.info_calls = []

    def installed(self):
        return dict(self.installed_packages)

    def info(self, names):
        self.info_calls.append(list(names))
        found = [self.entries[name] for name in names if name in self.entries]
        return {row['package']: row for row in aur.records(dict(version=5, type='multiinfo', results=found), {})}

    def unsatisfied(self, deps):
        return [dep for dep in deps if aur.dependency_name(dep) not in self.installed_packages]

    def repository(self, dep):
        rows = self.repository_rows.get(aur.dependency_name(dep))
        return None if rows is None else [dict(repository='extra', package=name, version='1', downloadSize=10)
                                          for name in rows]

    def commit(self, base):
        return self.commits.get(base, COMMIT)


class ConsentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='programs-aur-')
        self.addCleanup(self.tmp.cleanup)
        self.path = aur.consent_path(self.tmp.name)

    def test_section_is_closed_until_the_warning_is_accepted(self):
        self.assertEqual(aur.consent(self.path), dict(enabled=False, showWarning=True, accepted=''))
        state = aur.set_consent(self.path, True)
        self.assertTrue(state['enabled'])
        self.assertTrue(state['showWarning'])
        self.assertTrue(state['accepted'])

    def test_dont_show_again_keeps_the_first_acceptance_and_revoking_closes(self):
        first = aur.set_consent(self.path, True)['accepted']
        state = aur.set_consent(self.path, True, show_warning=False)
        self.assertEqual(state['accepted'], first)
        self.assertFalse(state['showWarning'])
        self.assertFalse(aur.set_consent(self.path, False)['enabled'])
        self.assertFalse(self.path.exists())

    def test_consent_to_an_older_or_malformed_warning_does_not_count(self):
        self.path.parent.mkdir(parents=True)
        for value in (dict(version=1, warning='2020-01-01', accepted='x'), [1], dict(version=2),
                      dict(version=1, warning=aur.WARNING_VERSION, accepted=1)):
            self.path.write_text(json.dumps(value))
            self.assertFalse(aur.consent(self.path)['enabled'], value)
        self.path.write_text('{broken')
        self.assertFalse(aur.consent(self.path)['enabled'])

    def test_warning_choice_must_be_boolean(self):
        with self.assertRaises(ValueError):
            aur.set_consent(self.path, True, show_warning='no')


class RecordTests(unittest.TestCase):
    def test_trust_facts_replace_screenshots(self):
        row = aur.records(dict(version=5, type='multiinfo', results=[entry(
            'paint-bin', base='paint', Maintainer=None, OutOfDate=1700000500)]), {'paint-bin': '0.9-1'})[0]
        self.assertEqual(row['key'], 'aur:paint-bin')
        self.assertEqual(row['packageBase'], 'paint')
        self.assertTrue(row['orphan'])
        self.assertEqual(row['votes'], 12)
        self.assertEqual(row['popularity'], 0.46)
        self.assertEqual(row['outOfDate'], '2023-11-14T22:21:40+00:00')
        self.assertEqual(row['firstSubmitted'], '2020-09-13T12:26:40+00:00')
        self.assertEqual(row['installedVersion'], '0.9-1')
        self.assertEqual(row['actions'], ['remove', 'install'])
        current = aur.records(dict(version=5, type='multiinfo', results=[entry('paint-bin')]), {'paint-bin': '1-1'})[0]
        self.assertEqual(current['actions'], ['remove'])
        self.assertIn('h=paint', row['recipe'])
        self.assertEqual(row['page'], 'https://aur.archlinux.org/packages/paint-bin')

    def test_update_is_offered_only_for_a_newer_arch_version(self):
        for available, installed, update in (
                ('1-1', '2-1', False), ('1-1', '1-1', False), ('2-1', '1-1', True),
                ('1:1-1', '2:1-1', False), ('2:1-1', '1:9-1', True),
                ('1.9-1', '1.10-1', False), ('1-2', '1-1', True)):
            with self.subTest(available=available, installed=installed):
                row = aur.record(entry('paint', available), {'paint': installed})
                self.assertEqual('install' in row['actions'], update)

    def test_unavailable_or_invalid_version_comparison_offers_no_update(self):
        for result in (OSError('unavailable'), RuntimeError('failed'), '', 'unknown'):
            with self.subTest(result=result):
                options = {'side_effect': result} if isinstance(result, Exception) else {'return_value': result}
                with patch.object(aur.arch, 'run', **options) as compare:
                    self.assertEqual(aur.actions('1-1', '2-1'), ['remove'])
                compare.assert_called_once_with(['vercmp', '2-1', '1-1'])

    def test_sparse_rpc_facts_stay_unknown(self):
        row = aur.record(dict(Name='paint-bin', Version='1'), {})
        for field in ('votes', 'popularity', 'maintainer', 'orphan', 'outOfDate',
                      'firstSubmitted', 'lastModified'):
            self.assertIsNone(row.get(field), field)
        known = aur.record(dict(Name='paint-bin', Version='1', NumVotes=0,
                                Popularity=0, Maintainer=None, OutOfDate=None), {})
        self.assertEqual(known['votes'], 0)
        self.assertEqual(known['popularity'], 0)
        self.assertTrue(known['orphan'])
        self.assertEqual(known['outOfDate'], '')

    def test_names_and_dependencies_are_never_options(self):
        for bad in (entry('--root'), entry('ok', base='-x'), entry('ok', depends=['--overwrite=*']),
                    entry('ok', NumVotes=-1), entry('ok', OutOfDate='yesterday'), entry('ok', Version='')):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                aur.records(dict(version=5, type='multiinfo', results=[bad]), {})
        with self.assertRaises(ValueError):
            aur.records(dict(version=5, type='error', results=[]), {})

    def test_only_web_upstream_links_are_kept(self):
        row = aur.records(dict(version=5, type='search', results=[entry('ok', URL='file:///etc/shadow')]), {})[0]
        self.assertEqual(row['upstream'], '')


class RecipeTests(unittest.TestCase):
    @staticmethod
    def packet(text):
        data = text.encode()
        return b'%04x' % (len(data) + 4) + data

    def test_reference_advertisement_names_the_exact_commit(self):
        raw = (self.packet('# service=git-upload-pack\n') + b'0000'
               + self.packet(COMMIT + ' HEAD\0multi_ack symref=HEAD:refs/heads/master\n')
               + self.packet('b' * 40 + ' refs/heads/master\n') + b'0000')
        self.assertEqual(aur.parse_refs(raw), COMMIT)

    def test_truncated_or_empty_advertisement_is_refused(self):
        for raw in (b'00ff' + b'x' * 10, b'0000', self.packet('nothing here\n')):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                aur.parse_refs(raw)

    def test_recipe_link_is_pinned_to_the_commit(self):
        self.assertEqual(aur.recipe_link('paint', COMMIT),
                         'https://aur.archlinux.org/cgit/aur.git/tree/PKGBUILD?h=paint&id=' + COMMIT)


class PlanTests(unittest.TestCase):
    def test_plan_lists_builds_in_dependency_order_and_marks_build_only_packages(self):
        system = FakeSystem(
            [entry('paint', depends=['libbrush>=2', 'gtk3'], make=['cmake']), entry('libbrush', make=['meson'])],
            installed={'glibc': '1'}, repository={'gtk3': ['gtk3', 'atk'], 'cmake': ['cmake'], 'meson': ['meson'],
                                                  'base-devel': ['base-devel', 'gcc'], 'git': ['git']})
        plan = aur.build_plan('paint', system)
        self.assertEqual([build['packageBase'] for build in plan['builds']], ['libbrush', 'paint'])
        self.assertEqual(plan['builds'][1]['packages'], [dict(package='paint', version='1-1')])
        self.assertEqual(plan['builds'][0]['commit'], COMMIT)
        self.assertIn('id=' + COMMIT, plan['builds'][1]['recipe'])
        purposes = {row['package']: row['purpose'] for row in plan['packages']}
        self.assertEqual(purposes, {'gtk3': 'run', 'atk': 'run', 'cmake': 'build', 'meson': 'build',
                                    'base-devel': 'build', 'gcc': 'build', 'git': 'build'})
        self.assertRegex(plan['token'], '^[0-9a-f]{64}$')

    def test_token_changes_with_the_recipe_commit_and_installed_state(self):
        def plan(**changes):
            system = FakeSystem([entry('paint')], installed={'base-devel': '1', 'git': '1'}, **changes)
            return aur.build_plan('paint', system)['token']
        self.assertEqual(plan(), plan())
        self.assertNotEqual(plan(), plan(commits={'paint': 'c' * 40}))
        system = FakeSystem([entry('paint')], installed={'base-devel': '1', 'git': '1', 'extra': '2'})
        self.assertNotEqual(plan(), aur.build_plan('paint', system)['token'])

    def test_installed_build_tools_are_not_pulled_again(self):
        system = FakeSystem([entry('paint')], installed={'base-devel': '1', 'git': '1'})
        self.assertEqual(aur.build_plan('paint', system)['packages'], [])

    def test_a_dependency_found_nowhere_refuses_with_its_name(self):
        system = FakeSystem([entry('paint', depends=['ghost'])], installed={'base-devel': '1', 'git': '1'})
        with self.assertRaises(aur.Refused) as caught:
            aur.build_plan('paint', system)
        self.assertEqual(caught.exception.code, 'missing-dependency')
        self.assertEqual(caught.exception.fields, dict(dependency='ghost'))

    def test_a_library_dependency_is_resolved_by_its_provider_or_refused_by_name(self):
        # electron37 on the AUR depends on libFLAC.so; one such record once failed the whole list.
        record = aur.records(dict(version=5, type='multiinfo', results=[
            entry('paint', depends=['libFLAC.so', 'libGL.so=1-64'])]), {})[0]
        self.assertEqual(record['depends'], ['libFLAC.so', 'libGL.so=1-64'])
        system = FakeSystem([entry('paint', depends=['libFLAC.so'])], installed={'base-devel': '1', 'git': '1'},
                            repository={'libFLAC.so': ['flac']})
        self.assertEqual([row['package'] for row in aur.build_plan('paint', system)['packages']], ['flac'])
        system = FakeSystem([entry('paint', depends=['libGhost.so'])], installed={'base-devel': '1', 'git': '1'})
        with self.assertRaises(aur.Refused) as caught:
            aur.build_plan('paint', system)
        self.assertEqual((caught.exception.code, caught.exception.fields), ('missing-dependency', dict(dependency='libGhost.so')))
        self.assertEqual(system.info_calls, [['paint']])

    def test_cycles_and_unknown_targets_refuse(self):
        system = FakeSystem([entry('a', depends=['b']), entry('b', depends=['a'])],
                            installed={'base-devel': '1', 'git': '1'})
        with self.assertRaises(aur.Refused) as caught:
            aur.build_plan('a', system)
        self.assertEqual(caught.exception.code, 'dependency-cycle')
        with self.assertRaises(aur.Refused) as caught:
            aur.build_plan('missing', system)
        self.assertEqual(caught.exception.code, 'not-found')

    def test_split_packages_build_once(self):
        system = FakeSystem([entry('suite-app', base='suite', depends=['suite-lib']),
                             entry('suite-lib', base='suite')], installed={'base-devel': '1', 'git': '1'})
        commits = []
        system.commit = lambda base: commits.append(base) or COMMIT
        plan = aur.build_plan('suite-app', system)
        self.assertEqual(commits, ['suite'])
        self.assertEqual(len(plan['builds']), 1)
        self.assertEqual(plan['builds'][0]['packages'], [dict(package='suite-app', version='1-1'),
                                                       dict(package='suite-lib', version='1-1')])

    def test_split_runtime_outputs_keep_all_their_dependencies(self):
        system = FakeSystem([entry('suite-app', base='suite', depends=['suite-lib']),
                             entry('suite-lib', base='suite', depends=['suite-app', 'helper', 'gtk3'],
                                   make=['cmake']), entry('helper')],
                            installed={'base-devel': '1', 'git': '1'},
                            repository={'gtk3': ['gtk3'], 'cmake': ['cmake']})
        plan = aur.build_plan('suite-app', system)
        self.assertEqual([step['packageBase'] for step in plan['builds']], ['helper', 'suite'])
        self.assertEqual({row['package']: row['purpose'] for row in plan['packages']},
                         {'gtk3': 'run', 'cmake': 'build'})

    def test_later_split_output_is_traversed_and_scheduled_before_its_base(self):
        system = FakeSystem([entry('app', depends=['suite-a', 'suite-b']),
                             entry('suite-a', base='suite'),
                             entry('suite-b', base='suite', depends=['helper']), entry('helper')],
                            installed={'base-devel': '1', 'git': '1'})
        plan = aur.build_plan('app', system)
        self.assertEqual([step['packageBase'] for step in plan['builds']], ['helper', 'suite', 'app'])
        self.assertEqual([row['package'] for row in plan['builds'][1]['packages']], ['suite-a', 'suite-b'])

    def test_split_outputs_do_not_hide_inter_base_or_build_time_cycles(self):
        cases = [
            [entry('suite-app', base='suite', depends=['helper']),
             entry('helper', depends=['suite-lib']), entry('suite-lib', base='suite')],
            [entry('suite-app', base='suite', make=['suite-lib']), entry('suite-lib', base='suite')],
        ]
        for entries in cases:
            with self.subTest(entries=entries), self.assertRaises(aur.Refused) as caught:
                aur.build_plan('suite-app', FakeSystem(entries, installed={'base-devel': '1', 'git': '1'}))
            self.assertEqual(caught.exception.code, 'dependency-cycle')

    def test_runtime_promotion_reaches_dependencies_of_an_existing_build(self):
        system = FakeSystem([entry('app', depends=['first', 'shared']), entry('first', make=['shared']),
                             entry('shared', depends=['helper', 'gtk3']), entry('helper')],
                            installed={'base-devel': '1', 'git': '1'}, repository={'gtk3': ['gtk3']})
        plan = aur.build_plan('app', system)
        self.assertEqual({step['packageBase']: step['purpose'] for step in plan['builds']},
                         {'app': 'run', 'first': 'run', 'shared': 'run', 'helper': 'run'})
        self.assertEqual(plan['packages'][0]['purpose'], 'run')

    def test_a_shared_dependency_is_built_once(self):
        system = FakeSystem([entry('paint', depends=['liba', 'libb']), entry('liba', depends=['libc']),
                             entry('libb', depends=['libc']), entry('libc')],
                            installed={'base-devel': '1', 'git': '1'})
        system.unsatisfied = lambda deps: list(deps)
        system.repository = lambda dep: None if dep not in ('base-devel', 'git') else []
        plan = aur.build_plan('paint', system)
        self.assertEqual([build['packageBase'] for build in plan['builds']], ['libc', 'liba', 'libb', 'paint'])

    def test_bad_recipe_commit_refuses(self):
        system = FakeSystem([entry('paint')], installed={'base-devel': '1', 'git': '1'}, commits={'paint': 'HEAD'})
        with self.assertRaises(aur.Refused) as caught:
            aur.build_plan('paint', system)
        self.assertEqual(caught.exception.code, 'recipe-unavailable')


class MatchTests(unittest.TestCase):
    ARCH = dict(key='arch:extra/obsidian:md.obsidian.Obsidian', kind='arch', package='obsidian',
                id='md.obsidian.Obsidian', name='Obsidian')
    FLATPAK = dict(key='flatpak:system:flathub:app/com.visualstudio.code/aarch64/stable', kind='flatpak',
                   id='com.visualstudio.code', name='Visual Studio Code')

    def test_exact_and_binary_repackaging_join_one_program(self):
        self.assertIs(aur.same_program('visual-studio-code-bin', [self.ARCH, self.FLATPAK]), self.FLATPAK)
        self.assertIs(aur.same_program('obsidian-bin', [self.ARCH, self.FLATPAK]), self.ARCH)
        self.assertIn('visual-studio-code-bin', aur.candidates(self.FLATPAK))

    def test_development_snapshots_and_ambiguous_names_stay_separate(self):
        self.assertIsNone(aur.same_program('obsidian-git', [self.ARCH]))
        other = dict(self.FLATPAK, id='org.other.Obsidian', name='Other')
        self.assertIsNone(aur.same_program('obsidian', [self.ARCH, other]))


def archive(records, raw=None):
    """The AUR's metadata archive: a gzip JSON array of package records."""
    return io.BytesIO(gzip.compress((json.dumps(records) if raw is None else raw).encode()))


def meta(name, popularity, votes, base=None):
    return dict(ID=1, Name=name, PackageBase=base or name, Version='1-1', NumVotes=votes, Popularity=popularity)


class PopularTests(unittest.TestCase):
    def test_the_list_is_the_aurs_own_popularity_order(self):
        records = [meta('quiet', 0.01, 900), meta('yay', 20.7, 2649), meta('paru', 19.8, 1260),
                   meta('tie-b', 3.0, 5), meta('tie-a', 3.0, 5), meta('tie-votes', 3.0, 9)]
        rows = aur.popular(archive(records), count=5)
        self.assertEqual([row['name'] for row in rows], ['yay', 'paru', 'tie-votes', 'tie-a', 'tie-b'])
        self.assertEqual(rows[0], dict(name='yay', popularity=20.7, votes=2649))

    def test_one_entry_per_recipe_and_bad_records_are_skipped(self):
        records = [meta('libsoup-docs', 6.8, 36, base='libsoup'), meta('libsoup', 6.8, 36),
                   meta('--root', 99.0, 1), dict(Name='novotes', PackageBase='novotes', Popularity=50.0),
                   meta('boolean', True, 1), 'not a record', meta('kept', 1.0, 1)]
        self.assertEqual([row['name'] for row in aur.popular(archive(records))], ['libsoup', 'kept'])

    def test_records_split_across_reads_are_joined(self):
        records = [meta('pkg-%05d' % index, index / 1000, index) for index in range(30000)]
        rows = aur.popular(archive(records), count=3)
        self.assertEqual([row['name'] for row in rows], ['pkg-29999', 'pkg-29998', 'pkg-29997'])

    def test_a_broken_archive_is_refused(self):
        for raw in ('{"Name": "yay"}', '[' + json.dumps(meta('yay', 1.0, 1)), '[{"Name": ', '[1, 2'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                aur.popular(archive(None, raw))
        with self.assertRaises(OSError):
            aur.popular(io.BytesIO(b'not gzip'))

    def test_names_are_kept_a_day_and_an_old_list_serves_when_the_aur_is_down(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'aur-popular.json'
            calls = []

            def fetch():
                calls.append(1)
                return archive([meta('yay', 2.0, 2), meta('paru', 1.0, 1)])
            self.assertEqual(aur.popular_names(path, fetch, now=1000), (['yay', 'paru'], False))
            self.assertEqual(aur.popular_names(path, fetch, now=1000 + 3600), (['yay', 'paru'], False))
            self.assertEqual(len(calls), 1)

            def down():
                raise OSError('offline')
            self.assertEqual(aur.popular_names(path, down, now=1000 + 2 * 86400), (['yay', 'paru'], True))
            path.write_text(json.dumps(dict(time=0, names=['--root'])))
            with self.assertRaises(OSError):
                aur.popular_names(path, down, now=1000)

    def test_only_the_aur_serves_the_archive(self):
        class Response(io.BytesIO):
            url = 'https://example.org/packages-meta-v1.json.gz'
        with self.assertRaises(ValueError):
            aur.fetch_meta(lambda request, timeout: Response(b''))
        self.assertEqual(aur.META, 'https://aur.archlinux.org/packages-meta-v1.json.gz')


if __name__ == '__main__':
    unittest.main()
