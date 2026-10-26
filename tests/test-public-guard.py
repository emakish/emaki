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


def sample(value):
    return bytes.fromhex(value).decode()


class PublicGuardTests(unittest.TestCase):
    def guard(self, origin='ours'):
        guard = MODULE.Guard(origin)
        guard.policy = copy.deepcopy(guard.policy)
        return guard

    def rules(self, guard):
        return {item['rule'] for item in guard.findings}

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
        line = sample('414920417373697374616e7473')
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
        for line in (sample('414920417373697374616e7473'), sample('436c6175646520436f6465'),
                     sample('436f646578'), sample('4f70656e4149')):
            with self.subTest(line=line):
                guard = self.guard()
                guard.content('README.md', ('Configure ' + line + ' here.').encode())
                self.assertEqual(guard.result()['result'], 'FLAG', guard.findings)

    def test_shipped_baseline_entry_reopens_on_edit(self):
        path = sample('646f63732f6167656e742d72756c65732d73657475702e6d64')
        guard = self.guard()
        lines = (ROOT / path).read_text().splitlines()
        line = next(line for line in lines
                    if (path, MODULE.digest(line.encode())) in guard.baseline_lines
                    and guard.rules['internal'].search(line))
        guard.content(path, line.encode())
        self.assertEqual(guard.result()['result'], 'PASS')
        guard = self.guard()
        guard.content(path, (line + ' changed').encode())
        self.assertEqual(guard.result()['result'], 'BLOCK')

    def test_filename_review_does_not_cover_body_text(self):
        name = sample('636c61756465')
        guard = self.guard()
        guard.baseline_paths.add((name, MODULE.digest(name.encode())))
        guard.text(name, 'path', name)
        self.assertEqual(guard.result()['result'], 'PASS')
        guard.content(name, name.encode())
        self.assertEqual(guard.result()['result'], 'FLAG')

    def test_new_work_record_blocks_even_in_existing_path(self):
        line = sample('4e69676874207265706f72743a20414920617373697374616e747320776f726b656420616c6c206e696768742e')
        for path in ('README.md', 'docs/record.md'):
            with self.subTest(path=path):
                guard = self.guard()
                guard.blob(path, '100644', line.encode())
                self.assertEqual(guard.result()['result'], 'BLOCK')

    def test_named_work_attribution_still_blocks(self):
        name = sample('436c61756465')
        for line in (name + ' wrote this file', 'Written by ' + name,
                     'Reviewed with ' + name, 'Generated using ' + name,
                     sample('41492d67656e657261746564'), sample('6d616368696e652d67656e657261746564')):
            with self.subTest(line=line):
                guard = self.guard()
                guard.content('README.md', line.encode())
                self.assertEqual(guard.result()['result'], 'BLOCK', guard.findings)

    def test_diff_provenance_is_not_a_work_trailer(self):
        line = sample('47656e6572617465642077697468206769742064696666202d2d6e6f2d696e646578')
        guard = self.guard()
        guard.content('README.md', line.encode())
        self.assertEqual(guard.result()['result'], 'PASS')
        for suffix in ('diffuse', sample('436c61756465'), sample('436f646578'),
                       sample('4750542d35'), sample('616e20617373697374616e74')):
            guard = self.guard()
            guard.content('README.md', (sample('47656e657261746564207769746820') + suffix).encode())
            self.assertEqual(guard.result()['result'], 'BLOCK', guard.findings)

    def test_baseline_cannot_allow_secrets_or_private_paths(self):
        line = sample('6768705f') + '01234567890123456789'
        guard = self.guard()
        guard.baseline_lines.add(('README.md', MODULE.digest(line.encode())))
        guard.content('README.md', line.encode())
        self.assertIn('secret', self.rules(guard))
        name = sample('5441534b2d39392e6d64')
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
        guard.blob(sample('5441534b2d39392e6d64'), '100644', b'Hello\n')
        self.assertIn('path-private', self.rules(guard))

    def test_renamed_report(self):
        guard = self.guard()
        guard.content('README.md', sample('2d2030333a3132204c31302066696e6973686564').encode())
        self.assertIn('internal', self.rules(guard))

    def test_renamed_instructions(self):
        guard = self.guard()
        guard.content('README.md', sample('596f752061726520746865206167656e742e20466f6c6c6f7720746865206c65616420616e642072657475726e20612072657669657720766572646963742e').encode())
        self.assertEqual(guard.result()['result'], 'BLOCK')
        self.assertIn('internal', self.rules(guard))

    def test_provenance_phrase(self):
        guard = self.guard()
        guard.content('README.md', sample('546865206d6f64656c2077726f746520746869732066696c65').encode())
        self.assertEqual(guard.result()['result'], 'BLOCK')
        self.assertIn('internal', self.rules(guard))

    def test_product_wording(self):
        guard = self.guard()
        guard.content('README.md', sample('636c61756465').encode())
        self.assertEqual(guard.result()['result'], 'FLAG')

    def test_outside_wording_is_review(self):
        guard = self.guard('outside')
        guard.content('pr-body', sample('636c61756465').encode())
        self.assertEqual(guard.result()['result'], 'FLAG')

    def test_outside_structure_still_blocks(self):
        guard = self.guard('outside')
        guard.blob('surprise/file.txt', '100644', b'Hello\n')
        self.assertEqual(guard.result()['result'], 'BLOCK')

    def test_ambiguous_and_exact_exception(self):
        line = sample('7373682d6167656e74')
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
        guard.content('README.md', sample('636c612d0a2f2f20756465').encode())
        self.assertIn('split', self.rules(guard))

    def test_unicode_obfuscation(self):
        guard = self.guard()
        guard.content('README.md', sample('636ce2808b61756465').encode())
        self.assertEqual(guard.result()['result'], 'BLOCK')

    def test_split_across_exception(self):
        first, second = sample('636c61'), sample('756465')
        guard = self.guard()
        guard.lines.add(('README.md', MODULE.digest(first.encode())))
        guard.content('README.md', (first + '\n' + second).encode())
        self.assertIn('split-boundary', self.rules(guard))

    def test_credentials_outside(self):
        guard = self.guard('outside')
        guard.content('README.md', sample('6768705f').encode() + b'01234567890123456789')
        self.assertIn('secret', self.rules(guard))
        self.assertEqual(guard.result()['result'], 'BLOCK')

    def test_trailer(self):
        guard = self.guard()
        guard.content('commit:example', sample('436f2d617574686f7265642d62793a20506572736f6e').encode())
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
        guard.content('art/example.png', image + sample('636c61756465').encode())
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

    def test_public_checker_sources(self):
        guard = self.guard()
        for name in ('scripts/public/guard.py', 'scripts/public/policy.json',
                     'scripts/public/reviewed-baseline.json',
                     'scripts/public/hooks/pre-push', 'scripts/public/hooks/install.py',
                     'tests/test-public-guard.py'):
            if (ROOT / name).is_file():
                guard.content(name, (ROOT / name).read_bytes())
        self.assertEqual(guard.findings, [])

    def test_untracked_ignored_material(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'README.md').write_text('Welcome')
            (root / 'untracked').mkdir()
            (root / 'untracked/notes.txt').write_text(sample('4e69676874207265706f7274'))
            guard = self.guard()
            guard.tree(root)
            self.assertIn('internal', self.rules(guard))
            self.assertIn('directory', self.rules(guard))

    def test_reviewed_credential_fixtures(self):
        guard = self.guard()
        for name in ('tests/fixtures/report-redaction.json', 'tests/test-r2-client.py'):
            if (ROOT / name).is_file():
                guard.content(name, (ROOT / name).read_bytes())
        self.assertNotIn('secret', self.rules(guard))

    def test_review_cannot_hide_strict_material(self):
        guard = self.guard()
        line = sample('24466f726d61743a')
        guard.lines.add(('README.md', MODULE.digest(line.encode())))
        guard.content('README.md', line.encode())
        self.assertIn('strict', self.rules(guard))

    def test_review_cannot_hide_coarse_report(self):
        guard = self.guard()
        line = sample('4e69676874207265706f7274')
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
        value = sample('636c61756465').encode()
        guard.content('README.md', b'data:text/plain;base64,' + base64.b64encode(value))
        self.assertIn('wording', self.rules(guard))

    def test_utf16_binary(self):
        guard = self.guard()
        guard.content('art/example.dat', sample('636c61756465').encode('utf-16-le'))
        self.assertIn('wording', self.rules(guard))

    def test_pdf_string(self):
        guard = self.guard()
        guard.content('art/example.pdf', b'%PDF-1.4\n(' + sample('636c61756465').encode() + b')\n%%EOF')
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
        extra = sample('436f2d617574686f7265642d62793a20506572736f6e')
        guard.content('commit:example', (line + '\n' + extra).encode(), commit_message=True)
        self.assertIn('internal', self.rules(guard))
        guard.content('commit:example', sample('6768705f').encode(), commit_message=True)
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
            (repo / 'README.md').write_text(sample('636c61756465'))
            run('add', '.')
            run('commit', '-qm', 'Update')
            (repo / 'README.md').write_text('Welcome\n')
            run('add', '.')
            run('commit', '-qm', 'Restore')
            head = run('rev-parse', 'HEAD')
            if (ROOT / 'scripts/public/hooks/install.py').is_file():
                trusted = repo.parent / (repo.name + '-guard')
                try:
                    subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/public/hooks/install.py'),
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
            guard.blob('docs/unreviewed.md', '100644', sample('6768705f').encode())
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
