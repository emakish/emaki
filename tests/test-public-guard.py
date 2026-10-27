#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Adversarial checks for public trees, metadata and history boundaries."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zlib

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('public_guard', ROOT / 'scripts/public/guard.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


SYNTHETIC_RULES = {
    'expressions': {
        'strict': r'fixedmarker',
        'secret': r'fixture-key-',
        'internal': r'private-entry|Private-trailer:|Fixture origin (?!git diff\b)',
        'attribution': r'forbiddenword[a-d]',
        'ambiguous': r'uncertainword',
        'path': r'(?:^|/)PRIVATE-DOC-',
    },
    'guards': [
        {'expression': r'private-location|Signed-off-by:', 'flags': 2, 'skip': []},
        {'expression': r'forbiddenword[a-d]', 'flags': 2, 'skip': []},
        {'expression': r'uncertainword', 'flags': 2, 'skip': ['LICENSE']},
    ],
    'core': r'forbiddenword[a-d]',
}


def fixture_policy():
    policy = json.loads((MODULE.POLICY_HOME / 'policy.json').read_text())
    policy['rules_sha256'] = MODULE.digest(MODULE.canonical_rules(SYNTHETIC_RULES))
    return policy


class PublicGuardTests(unittest.TestCase):
    def guard(self, origin='ours'):
        guard = MODULE.Guard(origin, policy=fixture_policy(), rules=copy.deepcopy(SYNTHETIC_RULES))
        return guard

    def rules(self, guard):
        return {item['rule'] for item in guard.findings}

    def test_explicit_rules_need_no_local_file_or_secret(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / 'policy.json').write_text(json.dumps(fixture_policy()))
            (home / 'reviewed-baseline.json').write_bytes(
                (MODULE.POLICY_HOME / 'reviewed-baseline.json').read_bytes())
            with mock.patch.object(MODULE, 'POLICY_HOME', home), mock.patch.dict(os.environ, {}, clear=True):
                guard = MODULE.Guard(rules=copy.deepcopy(SYNTHETIC_RULES))
                guard.content('README.md', b'Welcome')
                self.assertEqual(guard.result()['result'], 'PASS')

    def test_rules_missing_empty_malformed_or_mismatched(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / 'policy.json').write_text(json.dumps(fixture_policy()))
            (home / 'reviewed-baseline.json').write_bytes(
                (MODULE.POLICY_HOME / 'reviewed-baseline.json').read_bytes())
            with mock.patch.object(MODULE, 'POLICY_HOME', home):
                for value in (None, '', '{}', '{', json.dumps(dict(SYNTHETIC_RULES, core='changed'))):
                    with self.subTest(value=value):
                        if value is not None:
                            (home / 'rules.local.json').write_text(value)
                        with self.assertRaises((ValueError, OSError)):
                            MODULE.Guard()

    def test_rules_schema_and_expressions_fail_closed(self):
        examples = [None, [], {}, dict(SYNTHETIC_RULES, extra='unexpected'),
                    dict(SYNTHETIC_RULES, guards=[]), dict(SYNTHETIC_RULES, core='[')]
        missing = copy.deepcopy(SYNTHETIC_RULES)
        del missing['expressions']['strict']
        examples.append(missing)
        for rules in examples:
            with self.subTest(rules=rules):
                policy = fixture_policy()
                policy['rules_sha256'] = MODULE.digest(MODULE.canonical_rules(rules))
                with self.assertRaises((ValueError, TypeError, OSError)):
                    MODULE.Guard(policy=policy, rules=rules)

    def test_rules_canonical_formatting(self):
        first = MODULE.canonical_rules(SYNTHETIC_RULES)
        reordered = dict(reversed(list(SYNTHETIC_RULES.items())))
        second = MODULE.canonical_rules(json.loads(json.dumps(reordered, indent=4)))
        self.assertEqual(first, second)
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / 'policy.json').write_text(json.dumps(fixture_policy()))
            (home / 'reviewed-baseline.json').write_bytes(
                (MODULE.POLICY_HOME / 'reviewed-baseline.json').read_bytes())
            (home / 'rules.local.json').write_text(json.dumps(reordered, indent=4))
            with mock.patch.object(MODULE, 'POLICY_HOME', home):
                guard = MODULE.Guard()
                guard.content('README.md', b'forbiddenworda')
                self.assertIn('wording', self.rules(guard))

    def test_cli_rules_input_never_falls_back_or_exposes_invalid_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trusted, candidate = root / 'trusted', root / 'candidate'
            trusted.mkdir()
            candidate.mkdir()
            for name in ('guard.py', 'reviewed-baseline.json'):
                (trusted / name).write_bytes((MODULE.POLICY_HOME / name).read_bytes())
            (trusted / 'policy.json').write_text(json.dumps(fixture_policy()))
            # A valid local file must never rescue explicitly supplied bad input.
            (trusted / 'rules.local.json').write_text(json.dumps(SYNTHETIC_RULES))
            command = [sys.executable, '-B', str(trusted / 'guard.py'),
                       '--tree', str(candidate), '--rules-stdin', '--json', str(root / 'result.json')]
            invalid = (b'', b'null', b'[]', b'{}', b'{"private-marker":', b'\xff',
                       json.dumps(dict(SYNTHETIC_RULES, core='private-marker')).encode())
            for value in invalid:
                with self.subTest(value=value):
                    result = subprocess.run(command, input=value, capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 1)
                    self.assertNotIn(b'private-marker', result.stdout + result.stderr)
                    self.assertEqual(result.stderr, b'')
                    output = json.loads((root / 'result.json').read_text())
                    self.assertEqual(output['result'], 'BLOCK')
                    self.assertEqual(output['findings'][0]['rule'], 'rules-unavailable')
            result = subprocess.run(command, input=json.dumps(SYNTHETIC_RULES).encode(),
                                    capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_candidate_rules_cannot_replace_trusted_rules(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'scripts/public').mkdir(parents=True)
            (root / 'scripts/public/rules.local.json').write_text('{}')
            (root / 'README.md').write_text('forbiddenworda')
            guard = self.guard()
            guard.tree(root)
            self.assertIn('wording', self.rules(guard))
            self.assertIn('path-private', self.rules(guard))

    def test_local_rules_path_cannot_be_reviewed_away(self):
        guard = self.guard()
        path = 'scripts/public/rules.local.json'
        guard.policy['paths'].append(path)
        guard.baseline_paths.add((path, MODULE.digest(path.encode())))
        guard.approved_infrastructure = True
        guard.blob(path, '100644', b'{}')
        self.assertIn('path-private', self.rules(guard))

    def test_clean(self):
        guard = self.guard()
        guard.blob('README.md', '100644', b'Welcome to Emaki.\n')
        self.assertEqual(guard.result()['result'], 'PASS')

    def test_reviewed_baseline_is_exact_and_separate(self):
        records = json.loads((MODULE.POLICY_HOME / 'reviewed-baseline.json').read_text())
        for group in records['groups']:
            self.assertTrue(group['reason'])
            self.assertEqual(group['status'], 'owner-decision-pending')
        path = 'README.md'
        line = 'forbiddenworda'
        exact = (path, MODULE.digest(line.encode()))
        guard = self.guard()
        guard.baseline_lines.add(exact)
        guard.content(path, line.encode())
        self.assertEqual(guard.result()['result'], 'PASS')
        for changed_path, changed_line in ((path, line + ' changed'), ('PLANS.md', line)):
            with self.subTest(path=changed_path, line=changed_line):
                guard = self.guard()
                guard.baseline_lines.add(exact)
                guard.content(changed_path, changed_line.encode())
                self.assertEqual(guard.result()['result'], 'FLAG')

    def test_new_product_words_need_review(self):
        for line in ('forbiddenworda', 'forbiddenwordb',
                     'forbiddenwordc', 'forbiddenwordd'):
            with self.subTest(line=line):
                guard = self.guard()
                guard.content('README.md', ('Configure ' + line + ' here.').encode())
                self.assertEqual(guard.result()['result'], 'FLAG', guard.findings)

    def test_baseline_entry_reopens_on_edit(self):
        path, line = 'docs/example.md', 'private-entry instructions'
        guard = self.guard()
        exact = (path, MODULE.digest(line.encode()))
        guard.baseline_lines.add(exact)
        guard.content(path, line.encode())
        self.assertEqual(guard.result()['result'], 'PASS')
        guard = self.guard()
        guard.baseline_lines.add(exact)
        guard.content(path, (line + ' changed').encode())
        self.assertEqual(guard.result()['result'], 'BLOCK')

    def test_filename_review_does_not_cover_body_text(self):
        name = 'forbiddenworda'
        guard = self.guard()
        guard.baseline_paths.add((name, MODULE.digest(name.encode())))
        guard.text(name, 'path', name)
        self.assertEqual(guard.result()['result'], 'PASS')
        guard.content(name, name.encode())
        self.assertEqual(guard.result()['result'], 'FLAG')

    def test_new_work_record_blocks_even_in_existing_path(self):
        line = 'private-entry forbiddenworda'
        for path in ('README.md', 'docs/record.md'):
            with self.subTest(path=path):
                guard = self.guard()
                guard.blob(path, '100644', line.encode())
                self.assertEqual(guard.result()['result'], 'BLOCK')

    def test_named_work_attribution_still_blocks(self):
        name = 'forbiddenworda'
        for line in (name + ' wrote this file', 'Written by ' + name,
                     'Reviewed with ' + name, 'Generated using ' + name,
                     'private-entry generated', 'private-entry generated'):
            with self.subTest(line=line):
                guard = self.guard()
                guard.content('README.md', line.encode())
                self.assertEqual(guard.result()['result'], 'BLOCK', guard.findings)

    def test_diff_provenance_is_not_a_work_trailer(self):
        line = 'Fixture origin git diff --no-index'
        guard = self.guard()
        guard.content('README.md', line.encode())
        self.assertEqual(guard.result()['result'], 'PASS')
        for suffix in ('diffuse', 'forbiddenworda', 'forbiddenwordc',
                       'forbiddenwordb', 'forbiddenwordc'):
            guard = self.guard()
            guard.content('README.md', ('Fixture origin ' + suffix).encode())
            self.assertEqual(guard.result()['result'], 'BLOCK', guard.findings)

    def test_baseline_cannot_allow_secrets_or_private_paths(self):
        line = 'fixture-key-' + '01234567890123456789'
        guard = self.guard()
        guard.baseline_lines.add(('README.md', MODULE.digest(line.encode())))
        guard.content('README.md', line.encode())
        self.assertIn('secret', self.rules(guard))
        name = 'PRIVATE-DOC-99.md'
        guard.baseline_paths.add((name, MODULE.digest(name.encode())))
        guard.path(name, '100644')
        self.assertIn('path-private', self.rules(guard))

    def test_baseline_file_is_protected(self):
        guard = self.guard()
        guard.blob('scripts/public/reviewed-baseline.json', '100644', b'{}\n')
        self.assertIn('infrastructure', self.rules(guard))

    def test_new_top(self):
        guard = self.guard()
        guard.blob('unexpected.md', '100644', b'Hello\n')
        self.assertIn('top-file', self.rules(guard))

    def test_new_directory(self):
        guard = self.guard()
        guard.blob('surprise/file.py', '100644', b'pass\n')
        self.assertIn('directory', self.rules(guard))

    def test_unreviewed_path_in_existing_directory(self):
        guard = self.guard()
        guard.blob('docs/unreviewed.md', '100644', b'Hello\n')
        self.assertIn('path-review', self.rules(guard))

    def test_new_kind(self):
        guard = self.guard()
        guard.blob('tests/example.secretformat', '100644', b'Hello\n')
        self.assertIn('file-kind', self.rules(guard))

    def test_path_shape(self):
        guard = self.guard()
        guard.blob('PRIVATE-DOC-99.md', '100644', b'Hello\n')
        self.assertIn('path-private', self.rules(guard))

    def test_renamed_record(self):
        guard = self.guard()
        guard.content('README.md', 'private-entry completed'.encode())
        self.assertIn('internal', self.rules(guard))

    def test_renamed_instructions(self):
        guard = self.guard()
        guard.content('README.md', 'private-entry instructions'.encode())
        self.assertEqual(guard.result()['result'], 'BLOCK')
        self.assertIn('internal', self.rules(guard))

    def test_provenance_phrase(self):
        guard = self.guard()
        guard.content('README.md', 'private-entry wrote this file'.encode())
        self.assertEqual(guard.result()['result'], 'BLOCK')
        self.assertIn('internal', self.rules(guard))

    def test_product_wording(self):
        guard = self.guard()
        guard.content('README.md', 'forbiddenworda'.encode())
        self.assertEqual(guard.result()['result'], 'FLAG')

    def test_outside_wording_is_review(self):
        guard = self.guard('outside')
        guard.content('pr-body', 'forbiddenworda'.encode())
        self.assertEqual(guard.result()['result'], 'FLAG')

    def test_outside_structure_still_blocks(self):
        guard = self.guard('outside')
        guard.blob('surprise/file.txt', '100644', b'Hello\n')
        self.assertEqual(guard.result()['result'], 'BLOCK')

    def test_ambiguous_and_exact_exception(self):
        line = 'uncertainword'
        guard = self.guard()
        guard.content('README.md', line.encode())
        self.assertEqual(guard.result()['result'], 'FLAG')
        guard = self.guard()
        guard.lines.add(('README.md', MODULE.digest(line.encode())))
        guard.content('README.md', line.encode())
        self.assertEqual(guard.result()['result'], 'PASS')
        guard.content('README.md', (line + ' changed').encode())
        self.assertEqual(guard.result()['result'], 'FLAG')

    def test_split_word(self):
        guard = self.guard()
        guard.content('README.md', 'forbidden-\n// worda'.encode())
        self.assertIn('split', self.rules(guard))

    def test_unicode_obfuscation(self):
        guard = self.guard()
        guard.content('README.md', 'forbidden\u200bworda'.encode())
        self.assertEqual(guard.result()['result'], 'BLOCK')

    def test_split_across_exception(self):
        first, second = 'forbidden', 'worda'
        guard = self.guard()
        guard.lines.add(('README.md', MODULE.digest(first.encode())))
        guard.content('README.md', (first + '\n' + second).encode())
        self.assertIn('split-boundary', self.rules(guard))

    def test_credentials_outside(self):
        guard = self.guard('outside')
        guard.content('README.md', 'fixture-key-'.encode() + b'01234567890123456789')
        self.assertIn('secret', self.rules(guard))
        self.assertEqual(guard.result()['result'], 'BLOCK')

    def test_trailer(self):
        guard = self.guard()
        guard.content('commit:example', 'Private-trailer: Person'.encode())
        self.assertIn('internal', self.rules(guard))

    def test_changed_policy(self):
        guard = self.guard()
        guard.blob('scripts/public/policy.json', '100644', b'{}\n')
        self.assertIn('infrastructure', self.rules(guard))

    def test_submodule(self):
        guard = self.guard()
        guard.path('README.md', '160000')
        self.assertIn('mode', self.rules(guard))

    def test_unreviewed_link(self):
        guard = self.guard()
        guard.blob('README.md', '120000', b'/tmp/private')
        self.assertIn('link-review', self.rules(guard))

    def test_image_digest_and_metadata(self):
        def chunk(kind, body):
            return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))
        image = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
        image += chunk(b'IDAT', zlib.compress(b'\0\0\0\0')) + chunk(b'IEND', b'')
        guard = self.guard()
        guard.policy['binary']['art/example.png'] = MODULE.digest(image)
        guard.content('art/example.png', image)
        self.assertEqual(guard.result()['result'], 'PASS')
        guard.content('art/example.png', image + 'forbiddenworda'.encode())
        self.assertIn('binary-review', self.rules(guard))
        self.assertIn('wording', self.rules(guard))

    def test_broken_image(self):
        guard = self.guard('outside')
        guard.content('art/example.png', b'broken')
        self.assertIn('metadata-invalid', self.rules(guard))
        self.assertEqual(guard.result()['result'], 'BLOCK')

    @staticmethod
    def secret_packet():
        import base64
        def mpi(value):
            return value.bit_length().to_bytes(2, 'big') + value.to_bytes((value.bit_length() + 7) // 8, 'big')
        private = b''.join(mpi(value) for value in (103, 11, 13, 6))
        body = b'\x04\0\0\0\0\x01' + mpi(143) + mpi(7) + b'\0' + private
        body += (sum(private) & 65535).to_bytes(2, 'big')
        packet = b'\xc5' + bytes([len(body)]) + body
        return (b'-----BEGIN PGP PUBLIC KEY BLOCK-----\n\n' + base64.b64encode(packet)
                + b'\n-----END PGP PUBLIC KEY BLOCK-----\n')

    def test_mislabeled_armored_secret(self):
        for data in (self.secret_packet(), self.secret_packet().replace(b'\n', b'  \r\n'),
                     self.secret_packet().replace(b'-----END PGP PUBLIC KEY BLOCK-----', b'-----END PUBLIC KEY BLOCK-----')):
            with self.subTest(size=len(data)):
                guard = self.guard('outside')
                guard.blob('README.md', '100644', data)
                self.assertIn('private-key', self.rules(guard))
                self.assertEqual(guard.result()['result'], 'BLOCK')

    def test_binary_key_cannot_be_reviewed_away(self):
        data = subprocess.check_output(['openssl', 'genpkey', '-algorithm', 'ED25519', '-outform', 'DER'],
                                       stderr=subprocess.DEVNULL)
        guard = self.guard()
        guard.policy['binary']['README.md'] = MODULE.digest(data)
        guard.content('README.md', data)
        self.assertIn('private-key', self.rules(guard))

    def test_missing_key_reader_blocks(self):
        guard = self.guard()
        with mock.patch.object(MODULE.shutil, 'which', return_value=None):
            guard.content('README.md', self.secret_packet())
        self.assertIn('key-reader-failed', self.rules(guard))

    def test_key_reader_timeout_blocks(self):
        guard = self.guard()
        with mock.patch.object(MODULE, 'keys_in', side_effect=subprocess.TimeoutExpired('reader', 60)):
            guard.content('README.md', b'Welcome')
        self.assertIn('key-reader-failed', self.rules(guard))

    def test_compressed_metadata_limit(self):
        with self.assertRaises(ValueError):
            MODULE.inflate(zlib.compress(b'x' * (MODULE.MAX_BYTES + 1)))

    def test_policy_contains_only_rules_fingerprint(self):
        policy = json.loads((MODULE.POLICY_HOME / 'policy.json').read_text())
        self.assertNotIn('expressions', policy)
        self.assertRegex(policy['rules_sha256'], r'^[0-9a-f]{64}$')

    def test_untracked_ignored_material(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'README.md').write_text('Welcome')
            (root / 'untracked').mkdir()
            (root / 'untracked/notes.txt').write_text('private-entry')
            guard = self.guard()
            guard.tree(root)
            self.assertIn('internal', self.rules(guard))
            self.assertIn('directory', self.rules(guard))

    def test_reviewed_credential_fixtures(self):
        guard = self.guard()
        line = 'fixture-key-example'
        guard.credentials.add(('README.md', MODULE.digest(line.encode())))
        guard.content('README.md', line.encode())
        self.assertNotIn('secret', self.rules(guard))
        guard.content('README.md', (line + ' changed').encode())
        self.assertIn('secret', self.rules(guard))

    def test_review_cannot_hide_strict_material(self):
        guard = self.guard()
        line = 'fixedmarker'
        guard.lines.add(('README.md', MODULE.digest(line.encode())))
        guard.content('README.md', line.encode())
        self.assertIn('strict', self.rules(guard))

    def test_review_cannot_hide_restricted_record(self):
        guard = self.guard()
        line = 'private-entry'
        guard.lines.add(('README.md', MODULE.digest(line.encode())))
        guard.content('README.md', line.encode())
        self.assertIn('internal', self.rules(guard))

    def test_author_fields_do_not_choose_origin(self):
        guard = self.guard('outside')
        guard.content('pr-body', b'origin: ours')
        self.assertEqual(guard.origin, 'outside')

    def test_embedded_text(self):
        import base64
        guard = self.guard()
        value = 'forbiddenworda'.encode()
        guard.content('README.md', b'data:text/plain;base64,' + base64.b64encode(value))
        self.assertIn('wording', self.rules(guard))

    def test_utf16_binary(self):
        guard = self.guard()
        guard.content('art/example.dat', 'forbiddenworda'.encode('utf-16-le'))
        self.assertIn('wording', self.rules(guard))

    def test_pdf_string(self):
        guard = self.guard()
        guard.content('art/example.pdf', b'%PDF-1.4\n(' + 'forbiddenworda'.encode() + b')\n%%EOF')
        self.assertIn('wording', self.rules(guard))

    def test_canonical_signoff_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            identity = self.guard().policy['identities'][0]
            name, mail = identity.rsplit(' <', 1)
            env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
            env.update(GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=mail[:-1],
                       GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=mail[:-1],
                       GIT_AUTHOR_DATE='2026-01-01T00:00:00+0000',
                       GIT_COMMITTER_DATE='2026-01-01T00:00:00+0000')
            def run(*args):
                return subprocess.check_output(['git', '-C', str(repo), *args], env=env,
                                               stderr=subprocess.DEVNULL).decode().strip()
            run('init', '-q')
            (repo / 'README.md').write_text('Welcome\n')
            run('add', '.')
            run('commit', '-qm', 'Start')
            base = run('rev-parse', 'HEAD')
            (repo / 'README.md').write_text('Welcome to Emaki.\n')
            run('add', '.')
            run('commit', '-qsm', 'Document the welcome')
            guard = self.guard()
            guard.history(repo, base, run('rev-parse', 'HEAD'))
            self.assertEqual(guard.result()['result'], 'PASS', guard.findings)

    def test_signoff_is_exact_and_metadata_only(self):
        line = 'Signed-off-by: ' + self.guard().policy['identities'][0]
        for path in ('README.md', 'pr-title', 'pr-body', 'commit:untrusted-path'):
            with self.subTest(path=path):
                guard = self.guard()
                guard.content(path, line.encode())
                self.assertEqual(guard.result()['result'], 'BLOCK')
        guard = self.guard()
        guard.content('commit:example', (line + ' changed').encode(), commit_message=True)
        self.assertEqual(guard.result()['result'], 'BLOCK')

    def test_signoff_retains_other_checks(self):
        line = 'Signed-off-by: ' + self.guard().policy['identities'][0]
        guard = self.guard()
        extra = 'Private-trailer: Person'
        guard.content('commit:example', (line + '\n' + extra).encode(), commit_message=True)
        self.assertIn('internal', self.rules(guard))
        guard.content('commit:example', 'fixture-key-'.encode(), commit_message=True)
        self.assertIn('secret', self.rules(guard))

    def test_history_remembers_deleted_file_and_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
            identity = self.guard().policy['identities'][0]
            name, mail = identity.rsplit(' <', 1)
            env.update(GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=mail[:-1],
                       GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=mail[:-1],
                       GIT_AUTHOR_DATE='2026-01-01T00:00:00+0000',
                       GIT_COMMITTER_DATE='2026-01-01T00:00:00+0000')
            def run(*args):
                return subprocess.check_output(['git', '-C', str(repo), *args], env=env, stderr=subprocess.DEVNULL).decode().strip()
            run('init', '-q')
            (repo / 'README.md').write_text('Welcome\n')
            run('add', '.')
            run('commit', '-qm', 'Start')
            base = run('rev-parse', 'HEAD')
            (repo / 'README.md').write_text('forbiddenworda')
            run('add', '.')
            run('commit', '-qm', 'Update')
            (repo / 'README.md').write_text('Welcome\n')
            run('add', '.')
            run('commit', '-qm', 'Restore')
            head = run('rev-parse', 'HEAD')
            if (ROOT / 'scripts/public/hooks/install.py').is_file():
                trusted = repo.parent / (repo.name + '-guard')
                import shutil
                source = repo.parent / (repo.name + '-source')
                shutil.copytree(ROOT / 'scripts/public', source, ignore=shutil.ignore_patterns('rules.local.json', 'test-rules-local.py', '__pycache__'))
                (source / 'rules.local.json').write_text(json.dumps(SYNTHETIC_RULES))
                (source / 'policy.json').write_text(json.dumps(fixture_policy()))
                try:
                    subprocess.run([sys.executable, '-B', str(source / 'hooks/install.py'),
                                    '--repo', str(repo), '--destination', str(trusted), '--base', base],
                                   env=env, check=True, stdout=subprocess.DEVNULL)
                    transport = f'refs/heads/topic {head} refs/heads/topic {base}\n'
                    checked = subprocess.run([sys.executable, '-B', str(trusted / 'hooks/pre-push')],
                                             input=transport, text=True, cwd=repo, env=env,
                                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    self.assertEqual(checked.returncode, 1)
                    self.assertIn('FLAG', checked.stdout)
                finally:
                    import shutil
                    if trusted.exists():
                        shutil.rmtree(trusted)
                    shutil.rmtree(source)
            guard = self.guard()
            guard.history(repo, base, head)
            self.assertIn('wording', self.rules(guard))
            self.assertNotIn('identity', self.rules(guard))
            env.update(GIT_COMMITTER_EMAIL='someone@example.com')
            run('commit', '--allow-empty', '-qm', 'Later')
            guard = self.guard()
            guard.history(repo, head, run('rev-parse', 'HEAD'))
            self.assertIn('identity', self.rules(guard))
            policy_path = repo / 'scripts/public/policy.json'
            policy_path.parent.mkdir(parents=True)
            policy_path.write_text('{}\n')
            run('add', '.')
            env.update(GIT_COMMITTER_EMAIL=mail[:-1])
            run('commit', '-qm', 'Policy change')
            policy_head = run('rev-parse', 'HEAD')
            guard = self.guard()
            guard.history(repo, head, policy_head)
            self.assertIn('infrastructure', self.rules(guard))
            guard = self.guard()
            guard.approved_head = policy_head
            guard.history(repo, head, policy_head)
            self.assertNotIn('infrastructure', self.rules(guard))
            self.assertIn('identity', self.rules(guard))
            guard.blob('docs/unreviewed.md', '100644', 'fixture-key-'.encode())
            self.assertIn('secret', self.rules(guard))
            self.assertIn('path-review', self.rules(guard))
            guard = self.guard()
            guard.approved_head = head
            with self.assertRaises(ValueError):
                guard.history(repo, head, policy_head)
            policy_path.unlink()
            run('add', '.')
            run('commit', '-qm', 'Remove policy')
            deletion_head = run('rev-parse', 'HEAD')
            guard = self.guard()
            guard.approved_head = deletion_head
            guard.history(repo, policy_head, deletion_head)
            self.assertIn('infrastructure-deleted', self.rules(guard))
            graft = repo / '.git/info/grafts'
            graft.write_text(base + '\n')
            with self.assertRaises(ValueError):
                self.guard().history(repo, base, head)
            graft.unlink()
            replacement = repo / '.git/refs/replace' / base
            replacement.parent.mkdir(parents=True)
            replacement.write_text(head + '\n')
            with self.assertRaises(ValueError):
                self.guard().history(repo, base, head)



if __name__ == '__main__':
    unittest.main()
