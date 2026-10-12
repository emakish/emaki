#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Owner-signed gate exceptions (packaging/mirror/release_exceptions.py) through publish.sh.

The bucket, pacman and keys are the throwaway ones of tests/test-publish.py; the release key is
never touched. A record passes one exceptable gate for exactly its artifacts, while signed by a
trusted, unrevoked key, unexpired and unrevoked itself; nothing passes a gate in NEVER.
"""
import ast
import datetime
import importlib.util
import json
from pathlib import Path
import secrets
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load('test_publish', HERE / 'test-publish.py')
publish = base.publish
exceptions = sys.modules['release_exceptions']
setUpModule, tearDownModule = base.setUpModule, base.tearDownModule


class GateIds(unittest.TestCase):
    """Every refusal point names a stable gate, and every gate is either exceptable or never."""

    def test_every_refusal_has_a_listed_gate(self):
        source = (ROOT / 'packaging/mirror/publish.py').read_text()
        used, exceptable_calls = set(), set()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, 'attr', None)
            if name == 'Refused':
                gate = [k.value for k in node.keywords if k.arg == 'gate']
                self.assertEqual(len(gate), 1, f'line {node.lineno}: Refused without gate=')
                if isinstance(gate[0], ast.Constant):
                    used.add(gate[0].value)
                else:  # gate() refuses under its own id; abandon() keeps the gate of the refusal it wraps
                    self.assertIn(ast.unparse(gate[0]), ('gate', "getattr(error, 'gate', None)"), node.lineno)
            elif name == 'gate' and node.args and isinstance(node.args[0], ast.Constant):
                exceptable_calls.add(node.args[0].value)
        self.assertEqual(set(exceptions.EXCEPTABLE) & set(exceptions.NEVER), set())
        self.assertEqual(used - set(exceptions.NEVER), set(), 'gate ids missing from NEVER')
        self.assertIn('build.alongside-acceptance', exceptions.EXCEPTABLE)
        self.assertEqual(exceptable_calls | {'build.alongside-acceptance'},
                         set(exceptions.EXCEPTABLE))
        for gate in ('signature.package', 'signature.database', 'signature.image'):
            self.assertIn(gate, exceptions.NEVER)


class Exceptions(unittest.TestCase):
    setUpClass = classmethod(base.PublishTests.setUpClass.__func__)
    ok, refused, candidate = base.PublishTests.ok, base.PublishTests.refused, base.PublishTests.candidate
    stamp_results, manifest_of_testing = base.PublishTests.stamp_results, base.PublishTests.manifest_of_testing
    fake_iso = base.PublishTests.fake_iso

    def setUp(self):
        self.addCleanup(base.no_gnupg_daemons_left, self)
        self.world = base.World()

    def tearDown(self):
        self.world.close()

    def dirty(self):
        (self.world.tree / 'uncommitted').write_text('x\n')

    def scope(self, directory):
        return {p.name: publish.sha256_file(p) for p in sorted(Path(directory).glob('*.pkg.tar.zst'))}

    def new(self, gate, *files, reason='owner accepts this risk', extra=()):
        result = self.ok(self.world.publish('exception', 'new', gate, *files, '--reason', reason, *extra))
        return json.loads(result.stdout[:result.stdout.index('\n}') + 2])['id']

    def forge(self, gate, scope, created=None, days=7, keys=None, sign=True, who=...):
        """A record written by hand and signed with a test key, for cases the CLI refuses to make."""
        keys = keys or self.world.keys
        created = created or publish.utcnow()
        record = {'format': 1, 'id': f'{created:%Y%m%dT%H%M%SZ}-{gate}-{secrets.token_hex(2)}', 'gate': gate,
                  'scope': scope, 'reason': 'forged for a test', 'created': exceptions.stamp(created),
                  'expires': exceptions.stamp(created + datetime.timedelta(days=days)),
                  'who': self.world.keys.fpr if who is ... else who}
        path, _, _ = exceptions.paths(self.world.exceptions, record['id'])
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(exceptions.encode(record))
        if sign:
            keys.sign(path)
        return record['id'], path

    def test_alongside_exception_creation_uses_the_exact_source_path(self):
        constants = self.world.tree / 'installer/emaki_installer/constants.py'
        constants.parent.mkdir(parents=True, exist_ok=True)
        constants.write_text('ALONGSIDE = True\n')
        exception_id = self.new('build.alongside-acceptance', constants)
        path, _, _ = exceptions.paths(self.world.exceptions, exception_id)
        record = json.loads(path.read_text())
        self.assertEqual(record['scope'], {
            'installer/emaki_installer/constants.py': publish.sha256_file(constants)})

    def test_valid_exception_passes_its_gate_and_is_logged(self):
        world, packages = self.world, self.candidate('1-1')
        self.dirty()
        refusal = world.publish('publish', 'testing', packages)
        self.refused(refusal, 'REFUSED [publish.tree-clean]: the working tree')
        for name, digest in self.scope(packages).items():
            self.assertIn(f'{name}  {digest}', refusal.stderr)  # the exact scope to sign
        exception_id = self.new('publish.tree-clean', *sorted(packages.glob('*.pkg.tar.zst')),
                                reason='hotfix from a tree with a local note')
        result = self.ok(world.publish('publish', 'testing', packages))
        self.assertIn(f'PASSED BY EXCEPTION {exception_id}: hotfix from a tree with a local note', result.stdout)
        self.assertIn(f'[publish.tree-clean] PASSED BY EXCEPTION {exception_id}', (world.state / 'release.log').read_text())
        served = json.loads((world.bucket / 'served/testing' / world.pointer('testing')).read_text())
        self.assertEqual(served['exceptions'], [exception_id])
        listing = self.ok(world.publish('exception', 'list')).stdout
        self.assertIn(f'{exception_id}  valid', listing)
        shown = self.ok(world.publish('exception', 'show', exception_id)).stdout
        self.assertIn('"gate": "publish.tree-clean"', shown)
        self.assertIn('status: valid', shown)

    def test_expired_unsigned_tampered_and_revoked_records_pass_nothing(self):
        world, packages = self.world, self.candidate('1-1')
        self.dirty()
        scope = self.scope(packages)
        expired, _ = self.forge('publish.tree-clean', scope,
                                created=publish.utcnow() - datetime.timedelta(days=8), days=7)
        unsigned, _ = self.forge('publish.tree-clean', scope, sign=False)
        tampered, path = self.forge('publish.tree-clean', scope)
        path.write_bytes(path.read_bytes().replace(b'forged for a test', b'forged for a tesT'))
        too_long, _ = self.forge('publish.tree-clean', scope, days=30)
        stranger = base.Keys(world.base / 'stranger', 'Stranger <s@example.invalid>')
        stranger.close()
        foreign, _ = self.forge('publish.tree-clean', scope, keys=stranger)
        result = world.publish('publish', 'testing', packages)
        self.refused(result, '[publish.tree-clean]')
        self.assertIn(f'{expired}: expired', result.stderr)
        self.assertIn(f'{unsigned}: unsigned', result.stderr)
        self.assertIn(f'{tampered}: its signature does not verify', result.stderr)
        self.assertIn(f'{foreign}: its signature does not verify', result.stderr)
        self.assertIn(f'{too_long}: its lifetime is not 1 to 7 days', result.stderr)
        self.assertIsNone(world.pointer('testing'))
        valid = self.new('publish.tree-clean', *sorted(packages.glob('*.pkg.tar.zst')))
        self.ok(world.publish('exception', 'revoke', valid, '--reason', 'changed my mind'))
        self.refused(world.publish('exception', 'revoke', valid, '--reason', 'again'), 'already revoked')
        self.assertIn(f'{valid}: revoked', world.publish('publish', 'testing', packages).stderr)
        self.assertIn(f'{valid}  revoked', self.ok(world.publish('exception', 'list')).stdout)
        # A record signed by a key listed in emaki-revoked passes nothing either.
        fresh = self.new('publish.tree-clean', *sorted(packages.glob('*.pkg.tar.zst')))
        revoked_keys = world.base / 'emaki-revoked'
        revoked_keys.write_text(world.keys.fpr + '\n')
        world.extra_args = ['--revoked', revoked_keys]
        self.assertIn(f'{fresh}: its signature does not verify', world.publish('publish', 'testing', packages).stderr)
        world.extra_args = []
        self.ok(world.publish('publish', 'testing', packages))

    def test_a_record_naming_no_fingerprint_passes_nothing(self):
        """A failed verification names no signer, so it never equals `who`: a record whose `who`
        is null, not a string or not a full fingerprint passes nothing, whatever lies in its .sig."""
        world, packages = self.world, self.candidate('1-1')
        self.dirty()
        scope = self.scope(packages)
        forged = []
        for who in (None, 123, '', world.keys.fpr.lower()):
            exception_id, path = self.forge('publish.tree-clean', scope, sign=False, who=who)
            Path(f'{path}.sig').write_text('not a signature\n')
            forged.append(exception_id)
        result = world.publish('publish', 'testing', packages)
        self.refused(result, 'REFUSED [publish.tree-clean]')
        self.assertNotIn('PASSED BY EXCEPTION', result.stdout)
        for exception_id in forged:
            self.assertIn(f'exception {exception_id}: ', result.stderr)
        self.assertIsNone(world.pointer('testing'))
        self.assertNotIn('  valid', self.ok(world.publish('exception', 'list')).stdout)

    def test_revoking_stops_an_interrupted_publish(self):
        """A step saved in the journal does not keep an exception alive: a resumed publish checks
        the tree again, and a revoked exception refuses it before the pointer moves."""
        world, packages = self.world, self.candidate('1-1')
        self.dirty()
        files = sorted(packages.glob('*.pkg.tar.zst'))
        for step in (0, 5):
            exception_id = self.new('publish.tree-clean', *files)
            killed = world.publish('publish', 'testing', packages, kill=step)
            self.assertEqual(killed.returncode, -9, killed.stdout + killed.stderr)
            self.assertIsNone(world.pointer('testing'))
            self.ok(world.publish('exception', 'revoke', exception_id, '--reason', 'no longer approved'))
            resumed = world.publish('publish', 'testing', packages)
            self.refused(resumed, 'REFUSED [publish.tree-clean]')
            self.assertIn(f'{exception_id}: revoked', resumed.stderr)
            self.assertIsNone(world.pointer('testing'), f'killed after step {step}')
        # A valid exception still passes, and the served record names only it.
        exception_id = self.new('publish.tree-clean', *files)
        self.ok(world.publish('publish', 'testing', packages))
        served = json.loads((world.bucket / 'served/testing' / world.pointer('testing')).read_text())
        self.assertEqual(served['exceptions'], [exception_id])

    def test_wrong_gate_and_wrong_hash_are_refusals(self):
        world, packages = self.world, self.candidate('1-1')
        self.dirty()
        files = sorted(packages.glob('*.pkg.tar.zst'))
        # The same artifacts under another gate: not this gate's exception.
        self.new('release.transactions', *files)
        self.refused(world.publish('publish', 'testing', packages), '[publish.tree-clean]')
        # The candidate rebuilt with other bytes under the same names: the scope no longer matches.
        covered = self.new('publish.tree-clean', *files)
        config = next(p for p in files if p.name.startswith('emaki-config-'))
        other = world.base / 'other'
        base.make_package(other, 'emaki-config', '1-1', payload='other bytes\n')
        world.keys.sign(other / config.name)
        for name in (config.name, config.name + '.sig'):
            (other / name).replace(packages / name)
        base.patched_sources(packages)
        result = world.publish('publish', 'testing', packages)
        self.refused(result, '[publish.tree-clean]')
        self.assertIn(f'{covered}: {config.name}: sha256', result.stderr)
        # Fewer artifacts than the gate looks at is a mismatch as well.
        self.new('publish.tree-clean', config.parent / config.name)
        self.assertIn('not covered', world.publish('publish', 'testing', packages).stderr)

    def test_gates_that_can_never_be_excepted(self):
        world, packages = self.world, self.candidate('2-1')
        files = sorted(packages.glob('*.pkg.tar.zst'))
        for gate in ('signature.package', 'signature.database', 'immutable.version'):
            self.refused(world.publish('exception', 'new', gate, *files, '--reason', 'no'),
                         f'gate {gate} can never be passed by an exception')
        self.refused(world.publish('exception', 'new', 'no.such-gate', *files, '--reason', 'no'), 'unknown gate')
        self.refused(world.publish('exception', 'new', 'publish.tree-clean', *files, '--reason', 'x',
                                   '--days', '8'), 'lasts 1 to 7 days')
        gates = self.ok(world.publish('exception', 'gates')).stdout
        self.assertLess(gates.index('iso.stable-packages'), gates.index('Gates no exception passes'))
        self.assertGreater(gates.index('signature.package'), gates.index('Gates no exception passes'))
        # A correctly signed record for a never-gate changes nothing: an unsigned package stays refused.
        self.forge('signature.package', self.scope(packages))
        (packages / (files[0].name + '.sig')).unlink()
        self.refused(world.publish('publish', 'testing', packages), 'REFUSED [signature.package]')
        listing = self.ok(world.publish('exception', 'list')).stdout
        self.assertIn('gate signature.package can never be passed by an exception', listing)

    def test_the_060_image_with_packages_stable_does_not_serve(self):
        """2026-10-12: the fixed 0.6.0 image carried emaki and emaki-config rebuilt with a new commit
        stamp and the live-only installer, none of them in stable. Each refusal is passed by its
        own owner-signed exception for exactly this image, and the release record names them."""
        world = self.world
        dl = world.base / 'dl-bucket'
        dl.mkdir()
        server = base.pointer_server.serve(dl)
        try:
            world.extra_args = ['--dl-backend', f'local:{dl}',
                                '--dl-public-url', f'http://127.0.0.1:{server.server_address[1]}']
            stable = self.candidate('1-15')
            self.ok(world.publish('publish', 'testing', stable))
            self.ok(world.publish('promote', '--first'))
            self.ok(world.publish('stamp', *self.stamp_results(self.manifest_of_testing())))
            fixed = world.base / 'fixed-image-packages'
            for path in stable.glob('*.pkg.tar.zst'):
                if not path.name.startswith(('emaki-config-', 'emaki-1-')):
                    (fixed / path.name).parent.mkdir(exist_ok=True)
                    (fixed / path.name).write_bytes(path.read_bytes())
            base.make_package(fixed, 'emaki-config', '1-16', payload='commit stamp b\n')
            base.make_package(fixed, 'emaki', '1-16', depends=('emaki-config=1-16', 'emaki-mirrorlist'))
            base.make_package(fixed, 'emaki-installer', '1-6')
            image = self.fake_iso('0.6.0', fixed)
            refusal = world.publish('iso', image)
            self.refused(refusal, 'REFUSED [iso.stable-packages]: stable does not serve the packages of this image')
            for name in ('emaki-1-16-any', 'emaki-config-1-16-any', 'emaki-installer-1-6-any'):
                self.assertIn(name, refusal.stderr)
            self.assertIn(f'{image.name}  {publish.sha256_file(image)}', refusal.stderr)
            self.assertFalse((dl / 'iso').exists())
            first = self.new('iso.stable-packages', image,
                             reason='config and emaki differ only by the commit stamp; the installer is live-only')
            refusal = world.publish('iso', image)
            self.refused(refusal, 'REFUSED [iso.emaki-source-record]')
            self.assertIn(f'PASSED BY EXCEPTION {first}', refusal.stdout)
            self.assertFalse((dl / 'iso').exists())
            second = self.new('iso.emaki-source-record', image, reason='same rebuilds; sources ship with 0.6.1')
            result = self.ok(world.publish('iso', image))
            self.assertIn(f'PASSED BY EXCEPTION {first}: config and emaki differ only', result.stdout)
            self.assertIn(f'PASSED BY EXCEPTION {second}: same rebuilds', result.stdout)
            self.assertTrue((dl / 'iso/0.6.0' / image.name).is_file())
            record = json.loads((world.bucket / 'released/iso/0.6.0').read_text())
            self.assertEqual(record['exceptions'], sorted([first, second]))
            log = (world.state / 'release.log').read_text()
            self.assertIn(f'iso [iso.stable-packages] PASSED BY EXCEPTION {first}', log)
            # Another image under the same exceptions is refused: the scope is this image's hash.
            other = self.fake_iso('0.6.1', fixed, label='b')
            self.refused(world.publish('iso', other), '[iso.stable-packages]')
        finally:
            server.shutdown()
            server.server_close()

    def iso_stopped_before_upload(self, image):
        """`iso` run until the record is written and stopped right before the image upload."""
        world = self.world
        stop = '\n'.join([
            'import importlib.util, sys',
            "spec = importlib.util.spec_from_file_location('publish', sys.argv[1])",
            'publish = importlib.util.module_from_spec(spec)',
            'spec.loader.exec_module(publish)',
            'upload = publish.LocalBackend.put_large',
            'def stop(self, key, path):',
            "    if key.endswith('.iso'):",
            '        raise SystemExit(91)',
            '    return upload(self, key, path)',
            'publish.LocalBackend.put_large = stop',
            'raise SystemExit(publish.main(sys.argv[2:]))'])
        env = dict(base.os.environ, HOME=str(world.home), GNUPGHOME=str(world.keys.home))
        return base.run([sys.executable, '-c', stop, base.PUBLISH, *world.args(), 'iso', image,
                         '--arch-sources', world.arch_sources(image)], env=env)

    def test_an_interrupted_image_names_the_exception_that_published_it(self):
        """The release record is written once, before the image upload. A run stopped there and
        retried under a replacement exception still names the replacement publicly, in a record
        of its own for the same image."""
        world = self.world
        dl = world.base / 'dl-bucket'
        dl.mkdir()
        server = base.pointer_server.serve(dl)
        try:
            world.extra_args = ['--dl-backend', f'local:{dl}',
                                '--dl-public-url', f'http://127.0.0.1:{server.server_address[1]}']
            packages = self.candidate('1-1')
            self.ok(world.publish('publish', 'testing', packages))
            self.ok(world.publish('promote', '--first'))
            image = self.fake_iso('9.9.9', packages)
            first = self.new('stamp.missing', image, reason='initial approval')
            stopped = self.iso_stopped_before_upload(image)
            self.assertEqual(stopped.returncode, 91, stopped.stdout + stopped.stderr)
            record_path = world.bucket / 'released/iso/9.9.9'
            record = record_path.read_bytes()
            self.assertEqual(json.loads(record)['exceptions'], [first])
            self.assertFalse((dl / 'iso/9.9.9' / image.name).exists())
            self.ok(world.publish('exception', 'revoke', first, '--reason', 'approval replaced'))
            second = self.new('stamp.missing', image, reason='replacement approval')
            result = self.ok(world.publish('iso', image))
            self.assertIn(f'PASSED BY EXCEPTION {second}', result.stdout)
            self.assertEqual((dl / 'iso/9.9.9' / image.name).read_bytes(), image.read_bytes())
            self.assertEqual(record_path.read_bytes(), record)  # written once
            extra = sorted((world.bucket / 'released/iso-exceptions/9.9.9').iterdir())
            self.assertEqual(len(extra), 1)
            self.assertEqual(json.loads(extra[0].read_text()),
                             {'exceptions': [second], 'image_sha256': publish.sha256_file(image)})
            # The same exception again adds nothing: the records already name it.
            self.ok(world.publish('iso', image))
            self.assertEqual(len(list((world.bucket / 'released/iso-exceptions/9.9.9').iterdir())), 1)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
