#!/usr/bin/env python3
"""tests/vm/iso-install.sh against a stub ssh: no QEMU, no guest. The alongside fixture on an
image whose installer refuses the mode ends NOT APPLICABLE (exit 77), never a failure or a pass."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
VM = ROOT / 'tests/vm'
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)

# Answers by the remote command (the last argument). STUB_OFFER=refuse makes the worker refuse
# the alongside mode as the current installer core does; accept makes it take the plan.
STUB_SSH = r'''#!/usr/bin/env bash
command=${!#}
printf '%s\n' "$command" >>"$STUB_LOG"
case $command in
    *'cat > '*) cat >/dev/null ;;
    *'emaki-install-cli --plan'*)
        if [[ $STUB_OFFER == refuse ]]; then
            echo '{"type": "plan_ack", "plan_id": "p1", "errors": [{"code": "unsupported_mode", "message": "Installing alongside Windows is not available in this version."}]}'
            exit 3
        fi
        echo '{"type": "plan_ack", "plan_id": "p1", "errors": []}'
        [[ $command != *--yes* ]] || echo '{"type": "done", "ok": true}' ;;
esac
exit 0
'''


class IsoInstallAlongsideTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='iso-install-', dir=CACHE)
        self.base = Path(self.work.name)
        self.vm = self.base / 'repo/tests/vm'
        self.vm.mkdir(parents=True)
        for name in ('iso-install.sh', 'iso-common.sh', 'iso-wait-ssh.sh'):
            shutil.copy(VM / name, self.vm / name)
        shutil.copytree(VM / 'fixtures', self.vm / 'fixtures')
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        (self.bin / 'ssh').write_text(STUB_SSH)
        (self.bin / 'ssh').chmod(0o755)
        self.key = self.base / 'key'
        self.key.write_text('not a key\n')
        self.log = self.base / 'calls.log'
        self.log.touch()
        self.dir = self.base / 'vm'

    def tearDown(self):
        self.work.cleanup()

    def install(self, fixture, offer):
        env = dict(os.environ, PATH=f'{self.bin}{os.pathsep}{os.environ["PATH"]}', STUB_LOG=str(self.log),
                   STUB_OFFER=offer, EMAKI_ISO_SSH_KEY=str(self.key), HOME=str(self.base / 'home'))
        result = subprocess.run(['bash', str(self.vm / 'iso-install.sh'), fixture, '--dir', str(self.dir),
                                 '--ssh-port', '2299'], env=env, text=True, capture_output=True, timeout=60)
        return result, self.log.read_text().splitlines()

    def run_dir(self):
        return Path((self.dir / 'last-install-run').read_text().strip())

    def test_refused_alongside_is_not_applicable(self):
        # A prepared Windows baseline: the old script went on to the install and exited 3.
        self.dir.mkdir()
        (self.dir / 'windows-before.json').write_text('{}\n')
        result, calls = self.install('alongside', 'refuse')
        self.assertEqual(result.returncode, 77, result.stdout + result.stderr)
        self.assertIn('NOT APPLICABLE', result.stdout)
        run = self.run_dir()
        self.assertTrue((run / 'NOT-APPLICABLE').is_file())
        self.assertEqual((run / 'exit-code').read_text().strip(), '77')
        self.assertIn('unsupported_mode', (run / 'offer.ndjson').read_text())
        self.assertFalse(any('--yes' in call for call in calls), 'nothing may be installed')
        self.assertFalse(any('windows-fixture' in call for call in calls))

    def test_offered_alongside_goes_on_to_the_real_install_path(self):
        result, calls = self.install('alongside', 'accept')
        self.assertNotIn(result.returncode, (0, 77))
        self.assertIn('prepare the Windows fixture and baseline first', result.stderr)
        self.assertFalse((self.run_dir() / 'NOT-APPLICABLE').exists())

    def test_erase_fixture_is_not_probed(self):
        result, calls = self.install('erase-btrfs', 'accept')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Command-line installation evidence [CLI]:', result.stdout)
        cli = [call for call in calls if 'emaki-install-cli' in call]
        self.assertEqual(len(cli), 1)
        self.assertIn('--yes', cli[0])
        lines = (self.run_dir() / 'install.ndjson').read_text().splitlines()
        self.assertEqual(json.loads(lines[-1])['type'], 'done')


if __name__ == '__main__':
    unittest.main()
