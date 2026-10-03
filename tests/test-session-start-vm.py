#!/usr/bin/env python3
"""Offline startup-capture tests: no VM, SSH, root, sockets or live services."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFilter

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tests/vm' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guest = load('startup_guest_fixture', 'guest-session-start.py')
host = load('startup_host_fixture', 'capture-session-start.py')


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='sv-', dir=CACHE)
        self.base = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_root_and_vm_guards_precede_any_side_effect(self):
        with patch.object(guest.os, 'getuid', return_value=1000), patch.object(guest.subprocess, 'run') as command:
            with self.assertRaisesRegex(RuntimeError, 'root'):
                guest.guard()
            command.assert_not_called()
        for kind, accepted in (('qemu', True), ('kvm', True), ('none', False), ('docker', False)):
            with self.subTest(kind=kind), patch.object(guest.os, 'getuid', return_value=0), \
                    patch.object(guest.os, 'geteuid', return_value=0), \
                    patch.object(guest.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=kind)):
                if accepted:
                    guest.guard()
                else:
                    with self.assertRaisesRegex(RuntimeError, 'QEMU/KVM'):
                        guest.guard()
        with patch.object(sys, 'argv', ['capture', '--token', 'a' * 32]), \
                patch.object(guest, 'guard', side_effect=RuntimeError('fixture refused')), \
                patch.object(guest, 'private_directory') as directory, patch.object(guest, 'Capture') as capture:
            with self.assertRaisesRegex(RuntimeError, 'fixture refused'):
                guest.main()
            directory.assert_not_called()
            capture.assert_not_called()

    def test_metadata_removes_titles_and_arbitrary_journal_text(self):
        windows = guest.sanitized_reply('windows', [dict(id=17, pid=42, app_id='org.example.App',
                                                        workspace_id=2, title='SECRET DOCUMENT', secret='PASSWORD')])
        self.assertEqual(windows, [dict(id=17, pid=42, app_id='org.example.App', workspace_id=2)])
        layers = guest.sanitized_reply('layers', [dict(namespace='emaki-session-cover', output='Virtual-1',
                                                     layer='Overlay', keyboard_interactivity='Exclusive', title='SECRET')])
        self.assertNotIn('SECRET', json.dumps(layers))
        self.assertNotIn('id', layers[0], 'niri does not expose a stable layer id')
        token = 'a' * 32
        marker = dict(phase='drain-ready', token=token, atMs=1000,
                      observation=dict(ready=True, windowCount=2, title='SECRET'))
        record = json.dumps(dict(MESSAGE='QS prefix EMAKI_SESSION_COVER ' + json.dumps(marker), SECRET='PASSWORD'))
        result = guest.cover_event(record)
        self.assertEqual(result['observation'], dict(ready=True, windowCount=2))
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertIsNone(guest.cover_event(json.dumps(dict(MESSAGE='arbitrary SECRET journal message'))))

    def test_colored_journal_byte_fields_keep_bounded_cover_stages(self):
        for phase in ('greeter-frozen', 'loaded', 'drain-ready', 'finished', 'skipped-deadline'):
            with self.subTest(phase=phase):
                marker = dict(phase=phase, token='a' * 32, atMs=1000,
                              frames={'Virtual-1': 2, 'DP-2': 0, 'bool': True, 'huge': 10000, '../SECRET': 2},
                              observation=dict(coverMapped=True, windowCount=-1, title='SECRET'))
                message = '\x1b[34m DEBUG\x1b[97m qml\x1b[0m: EMAKI_SESSION_COVER ' + json.dumps(marker)
                result = guest.cover_event(json.dumps(dict(MESSAGE=list(message.encode()))))
                self.assertEqual(result['phase'], phase)
                self.assertEqual(result['frames'], {'Virtual-1': 2, 'DP-2': 0})
                self.assertEqual(result['observation'], dict(coverMapped=True))
                self.assertNotIn('SECRET', json.dumps(result))
        self.assertEqual(guest.output_map({str(index): 2 for index in range(33)}), {})
        for payload in ([True], [256], [-1], [255], [65] * 16385, ['not a byte'], {'MESSAGE': 'SECRET'}):
            self.assertIsNone(guest.cover_event(json.dumps(dict(MESSAGE=payload))))
        marker.update(frames={'Virtual-1': 2.0})
        result = guest.cover_event(json.dumps(dict(MESSAGE='EMAKI_SESSION_COVER ' + json.dumps(marker))))
        self.assertEqual(result['frames'], {})

    def test_cover_journal_matches_creation_time_without_disclosing_token(self):
        def record(created):
            event = dict(phase='greeter-frozen', handoffAtMs=created, atMs=1000)
            return json.dumps(dict(MESSAGE='EMAKI_SESSION_COVER ' + json.dumps(event), _UID='0', _PID='999'))
        response = SimpleNamespace(returncode=0, stdout='\n'.join((record(900), record(901),
                                                                  json.dumps(dict(MESSAGE='SECRET')))))
        with patch.object(guest.subprocess, 'run', return_value=response) as command:
            events, status = guest.journal_cover_events(1_000_000_000, 2_000_000_000, {900})
        self.assertEqual(status, 'filtered')
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['handoffAtMs'], 900)
        self.assertNotIn('token', events[0])
        args = command.call_args.args[0]
        self.assertIn('--grep=EMAKI_SESSION_COVER ', args)
        self.assertIn('--until=@2.0', args)
        self.assertIn('--all', args)
        self.assertFalse(any(arg.startswith(('_UID=', '_PID=')) for arg in args))
        self.assertNotIn('SECRET', json.dumps(events))
        with patch.object(guest.subprocess, 'run', return_value=response):
            self.assertEqual(guest.journal_cover_events(0, 2_000_000_000, set()), ([], 'filtered-empty'))
        for invalid in (SimpleNamespace(returncode=1, stdout=''),
                        SimpleNamespace(returncode=0, stdout='x' * (4 * 1024 * 1024 + 1))):
            with patch.object(guest.subprocess, 'run', return_value=invalid):
                self.assertEqual(guest.journal_cover_events(0, 2_000_000_000, {900}), ([], 'unavailable'))

    def test_runtime_state_is_bounded_no_follow_and_allowlisted(self):
        runtime = self.base / 'startup'
        runtime.mkdir(mode=0o700)
        token = 'a' * 32
        payload = dict(version=1, token=token, createdMs=10000, shellReadyMs=10100,
                       compositorClaimed=True, shellClaimed=True, shellReady=True, coverFinished=False, title='SECRET', environment={'PASSWORD': 'SECRET'})
        (runtime / (token + '.json')).write_text(json.dumps(payload))
        linked = self.base / 'linked.json'
        linked.write_text(json.dumps(dict(payload, token='b' * 32)))
        (runtime / ('b' * 32 + '.json')).symlink_to(linked)
        (runtime / ('c' * 32 + '.json')).write_text('x' * 65537)
        (runtime / ('d' * 32 + '.json')).write_text(json.dumps(dict(payload, token='d' * 32, createdMs=1)))
        with patch.object(guest, 'Path', return_value=runtime):
            result = guest.startup_states(SimpleNamespace(pw_uid=os.getuid()), 10000)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['token'], token)
        self.assertEqual(result[0]['shellReadyMs'], 10100)
        self.assertTrue(result[0]['compositorClaimed'])
        self.assertFalse(result[0]['coverFinished'])
        self.assertNotIn('SECRET', json.dumps(result))
        payload.update(coverFinished=True, coverDrainReason='ready')
        payload['coverDrainStartedMs'] = 10500
        (runtime / (token + '.json')).write_text(json.dumps(payload))
        with patch.object(guest, 'Path', return_value=runtime):
            result = guest.startup_states(SimpleNamespace(pw_uid=os.getuid()), 10000)
        self.assertEqual(result[0]['coverDrainReason'], 'ready')
        self.assertTrue(result[0]['coverFinished'])
        self.assertEqual(result[0]['coverDrainStartedMs'], 10500)
        self.assertNotIn('SECRET', json.dumps(result))

    def test_runtime_scan_finds_current_state_after_many_old_states_and_non_json_entries(self):
        runtime = self.base / 'startup-history'
        runtime.mkdir(mode=0o700)
        for index in range(40):
            token = f'{index:032x}'
            filename = runtime / (token + '.json')
            filename.write_text(json.dumps(dict(version=1, token=token, createdMs=1)))
            os.utime(filename, ns=(1, 1))
            (runtime / (token + '.kdl')).write_text('old wrapper')
            (runtime / ('directory-' + token)).mkdir()
        current = 'f' * 32
        filename = runtime / (current + '.json')
        filename.write_text(json.dumps(dict(version=1, token=current, createdMs=10000, coverClaimed=True)))
        original_names = sorted(item.name for item in runtime.iterdir())
        scan = {}
        with patch.object(guest, 'Path', return_value=runtime):
            states = guest.startup_states(SimpleNamespace(pw_uid=os.getuid()), 10000, diagnostics=scan)
        self.assertEqual([state['token'] for state in states], [current])
        self.assertEqual(scan['walked'], 121)
        self.assertEqual(scan['candidates'], 1)
        self.assertFalse(scan['walkTruncated'])
        self.assertFalse(scan['candidatesTruncated'])
        self.assertEqual(original_names, sorted(item.name for item in runtime.iterdir()), 'capture must not purge guest files')

    def test_runtime_scan_limits_are_reported_as_incomplete(self):
        runtime = self.base / 'startup-bounded'
        runtime.mkdir(mode=0o700)
        for index in range(35):
            token = f'{index:032x}'
            filename = runtime / (token + '.json')
            filename.write_text(json.dumps(dict(version=1, token=token, createdMs=10000)))
            os.utime(filename, ns=(10_000_000_000 + index, 10_000_000_000 + index))
        scan = {}
        with patch.object(guest, 'Path', return_value=runtime):
            states = guest.startup_states(SimpleNamespace(pw_uid=os.getuid()), 10000, diagnostics=scan)
        self.assertEqual(len(states), 32)
        self.assertEqual(states[0]['token'], f'{34:032x}', 'most recently written candidate must be inspected first')
        self.assertTrue(scan['candidatesTruncated'])
        directory, rows, greeter, metadata, events, _ = self.continuity_fixture(['glass'])
        metadata[0]['startupStateScan'] = scan
        report = host.continuity_analysis(directory, rows, greeter, metadata, events, True)
        self.assertEqual(host.continuity_status(report), 'INCOMPLETE')
        self.assertIn('startup-state-scan-truncated', report['incomplete'])
        with patch.object(guest, 'Path', return_value=runtime), patch.object(guest, 'STATE_WALK_LIMIT', 4):
            guest.startup_states(SimpleNamespace(pw_uid=os.getuid()), 10000, diagnostics=scan)
        self.assertEqual(scan['walked'], 4)
        self.assertTrue(scan['walkTruncated'])
        self.assertEqual(len(list(runtime.iterdir())), 35)

    def continuity_fixture(self, kinds, *, exact=True):
        directory = self.base / 'continuity'
        directory.mkdir(exist_ok=True)
        (directory / 'guest').mkdir(exist_ok=True)
        glass = Image.new('RGB', (640,400), '#708090')
        draw = ImageDraw.Draw(glass)
        for x in range(0,640,30):
            draw.line((x,0,639-x,399), fill='#cc7788', width=3)
        glass.save(directory / 'guest/handoff.png')
        glass.save(directory / 'greeter.png')
        variants = dict(glass=glass, plain=Image.new('RGB',glass.size,'#fff8f3'),
                        wallpaper=Image.new('RGB',glass.size,'#345678'))
        defect = glass.copy(); defect.putpixel((0,0),(0,0,0)); variants['local-defect']=defect
        tolerant = glass.copy(); tolerant.putpixel((0,0),tuple(min(255,c+2) for c in glass.getpixel((0,0))))
        variants['tolerated']=tolerant
        rows=[]
        for index,kind in enumerate(kinds):
            filename=f'{index:03d}.png'; variants[kind].save(directory/filename)
            rows.append(dict(file=filename,startNs=(1000+index*80)*1_000_000,endNs=(1020+index*80)*1_000_000))
        events=[dict(phase='greeter-frozen',atMs=990)]
        if exact:
            events.append(dict(phase='drain-ready',atMs=rows[-1]['endNs']/1e6+40 if rows else 1100,
                               observation=dict(ready=True)))
        metadata=[dict(startup=[dict(compositorClaimed=True,shellReady=True)])]
        return directory,rows,[dict(file='greeter.png')],metadata,events,variants

    def test_identical_whole_frames_and_two_byte_tolerance(self):
        fixture=self.continuity_fixture(['glass','tolerated','glass'])
        result=host.continuity_analysis(*fixture[:5],True)
        self.assertEqual(result['failures'],[])
        self.assertEqual(host.continuity_status(result),'REVIEW_REQUIRED')
        self.assertEqual(result['thresholds']['maxChannelError'],2)

    def test_any_plain_or_local_pixel_regression_fails(self):
        for kind in ('plain','wallpaper','local-defect'):
            fixture=self.continuity_fixture(['glass',kind,'glass'])
            result=host.continuity_analysis(*fixture[:5],True)
            self.assertEqual(host.continuity_status(result),'FAIL')
            self.assertEqual(result['failures'][0]['kind'],'different-frame-before-drain')

    def test_deadline_drain_or_skip_fails_even_with_identical_frames(self):
        for phase in ('drain-cap','drain-deadline','skipped-deadline'):
            fixture=self.continuity_fixture(['glass','glass'])
            fixture[4].append(dict(phase=phase,atMs=1100))
            result=host.continuity_analysis(*fixture[:5],True)
            self.assertEqual(host.continuity_status(result),'FAIL')
            self.assertEqual(result['failures'][0]['kind'],'deadline-instead-of-readiness')

    def test_missing_physical_frames_and_gaps_are_incomplete(self):
        fixture=self.continuity_fixture(['glass','glass'])
        result=host.continuity_analysis(fixture[0],[],*fixture[2:5],True)
        self.assertEqual(host.continuity_status(result),'INCOMPLETE')
        fixture[1][1]['endNs']+=400_000_000
        result=host.continuity_analysis(*fixture[:5],True,period_ms=40)
        self.assertEqual(host.continuity_status(result),'INCOMPLETE')

    def test_size_change_and_unproven_ready_drain_fail(self):
        fixture=self.continuity_fixture(['glass'])
        Image.new('RGB',(639,400)).save(fixture[0]/fixture[1][0]['file'])
        result=host.continuity_analysis(*fixture[:5],True)
        self.assertEqual(host.continuity_status(result),'FAIL')
        fixture=self.continuity_fixture(['glass'])
        fixture[4][-1]['observation']={}
        self.assertEqual(host.continuity_status(host.continuity_analysis(*fixture[:5],True)),'FAIL')

    def write_capture_fixture(self, sequence):
        directory, rows, greeter, metadata, events, variants = self.continuity_fixture(sequence)
        evidence = self.base / 'evidence'
        frames = evidence / 'guest/frames'
        frames.mkdir(parents=True)
        fixture_rows = []
        for index, row in enumerate(rows):
            filename = f'frames/user-{index:05d}.png'
            (evidence / 'guest' / filename).write_bytes((directory / row['file']).read_bytes())
            fixture_rows.append(dict(row, file=filename, side='user', state='frame'))
        (frames / 'greeter-00000.png').write_bytes((directory / greeter[0]['file']).read_bytes())
        fixture_rows.insert(0, dict(side='greeter', state='frame', file='frames/greeter-00000.png',
                                    startNs=800_000_000, endNs=820_000_000))
        snapshots = [dict(side='user', epochNs=row['endNs'], endNs=row['endNs'] + 1,
                          layers=[dict(namespace=name, output='Virtual-1') for name in ('emaki-test-bar', 'emaki-test-dock')],
                          startup=metadata[0]['startup']) for row in rows]
        (evidence / 'guest/frames.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in fixture_rows))
        (evidence / 'guest/metadata.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in snapshots))
        (evidence / 'guest/cover-events.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in events))
        (evidence / 'guest/manifest.json').write_text(json.dumps(dict(startNs=800_000_000, endNs=1_500_000_000,
                                                                    requestedIntervalMs=80, firstSockets={'user': {'epochNs': 990_000_000}})))
        (evidence / 'guest/handoff.png').write_bytes((directory / 'guest/handoff.png').read_bytes())
        (evidence / 'host-frames.jsonl').write_text(''.join(json.dumps(dict(row, state='frame',
            file='guest/frames/user-' + f'{index:05d}.png')) + '\n' for index,row in enumerate(rows)))
        return evidence

    def test_offline_analysis_fails_old_sequence_and_never_modifies_source_or_contacts_vm(self):
        evidence = self.write_capture_fixture(['plain', 'wallpaper', 'glass', 'plain'])
        original = {str(path.relative_to(evidence)): path.read_bytes() for path in evidence.rglob('*') if path.is_file()}
        destination = self.base / 'analysis'
        with patch.object(sys, 'argv', ['capture', '--analyze-only', str(evidence), '--output', str(destination)]), \
                patch.object(host.check, 'checked_remote', side_effect=AssertionError('no SSH allowed')), \
                patch.object(host.subprocess, 'Popen', side_effect=AssertionError('no process allowed')), patch('builtins.print'):
            self.assertEqual(host.main(), 1)
        result = json.loads((destination / 'analysis.json').read_text())
        self.assertEqual(result['status'], 'FAIL')
        self.assertTrue(result['plateContinuity']['failures'])
        self.assertFalse(result['visualPass'])
        self.assertEqual(original, {str(path.relative_to(evidence)): path.read_bytes() for path in evidence.rglob('*') if path.is_file()})

    def test_analyze_only_rc1_for_incomplete_and_rc2_only_for_remaining_review(self):
        evidence = self.write_capture_fixture(['glass', 'glass'])
        for name, expected_status, expected_code in (('complete', 'REVIEW_REQUIRED', 2),
                                                      ('no-metadata', 'INCOMPLETE', 1)):
            if name == 'no-metadata':
                (evidence / 'guest/metadata.jsonl').unlink()
            destination = self.base / name
            with patch.object(sys, 'argv', ['capture', '--analyze-only', str(evidence), '--output', str(destination)]), \
                    patch.object(host.check, 'checked_remote', side_effect=AssertionError('no SSH allowed')), \
                    patch.object(host.subprocess, 'Popen', side_effect=AssertionError('no process allowed')), patch('builtins.print') as output:
                self.assertEqual(host.main(), expected_code)
            report = json.loads((destination / 'analysis.json').read_text())
            self.assertEqual(report['status'], expected_status)
            self.assertIn('whole-frame continuity', output.call_args.args[0])
            self.assertEqual(report['summary'], host.analysis_summary(report))
            self.assertFalse(report['visualPass'])

    def test_capture_commands_drop_uid_and_ignore_host_environment(self):
        account = SimpleNamespace(pw_uid=1234, pw_gid=1235)
        with patch.dict(os.environ, NIRI_SOCKET='/host/DO_NOT_USE', WAYLAND_DISPLAY='host-wayland'):
            environment = guest.environment(account, Path('/run/user/1234/wayland-1'), Path('/run/user/1234/niri.test.sock'))
        self.assertEqual(environment['NIRI_SOCKET'], '/run/user/1234/niri.test.sock')
        self.assertEqual(environment['WAYLAND_DISPLAY'], 'wayland-1')
        self.assertNotIn('HOME', environment)
        with patch.object(guest.subprocess, 'run') as run:
            guest.as_user(account, ['/usr/bin/grim', '-'], environment, stdout=subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs['user'], 1234)
        self.assertEqual(run.call_args.kwargs['group'], 1235)
        self.assertEqual(run.call_args.kwargs['extra_groups'], [])

    def test_wallpaper_fixture_dispatch_drops_uid_before_home_access(self):
        account = SimpleNamespace(pw_uid=1234, pw_gid=1235, pw_dir='/home/arch')
        with patch.object(guest.pwd, 'getpwnam', return_value=account), \
                patch.object(guest.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='{"state":"published"}')) as run:
            self.assertEqual(guest.fixture_as_user('prepare', 'a' * 32)['state'], 'published')
        self.assertEqual(run.call_args.kwargs['user'], 1234)
        self.assertEqual(run.call_args.kwargs['group'], 1235)
        self.assertEqual(run.call_args.kwargs['extra_groups'], [])
        self.assertEqual(run.call_args.kwargs['env'], dict(PATH='/usr/bin:/bin', HOME='/home/arch'))
        self.assertIn('wallpaper-user-prepare', run.call_args.args[0])
        with patch.object(guest.os, 'getuid', return_value=0), patch.object(guest, 'user_directory') as directory:
            with self.assertRaisesRegex(RuntimeError, 'refuses root'):
                guest.wallpaper_fixture('prepare', 'a' * 32, self.base)
            directory.assert_not_called()

    def test_wallpaper_fixture_publishes_identical_pixels_and_restores_original_config(self):
        original = b'[default]\npath = "/original/image.png"\n'
        config = self.base / '.config/wpaperd/config.toml'
        config.parent.mkdir(parents=True)
        config.write_bytes(original)
        config.chmod(0o640)
        published = self.base / 'published'
        (published / 'wpaperd').mkdir(parents=True)
        def publish(*args, **kwargs):
            self.assertNotIn('user', kwargs, 'already running unprivileged')
            self.assertEqual(kwargs['env']['XDG_CONFIG_HOME'], str(self.base / '.config'))
            if config.read_bytes() == original:
                return SimpleNamespace(returncode=0, stdout='{"state":"published"}')
            image = Path(tomllib.loads(config.read_text())['default']['path']).read_bytes()
            name = 'wallpaper-' + guest.hashlib.sha256(image).hexdigest() + '.image'
            (published / name).write_bytes(image)
            (published / 'wpaperd/config.toml').write_text('[default]\npath = ' + json.dumps(str(published / name)))
            return SimpleNamespace(returncode=0, stdout='{"state":"published"}')
        with patch.object(guest.subprocess, 'run', side_effect=publish):
            result = guest.wallpaper_fixture('prepare', 'a' * 32, self.base, publication=published)
            self.assertEqual(result['state'], 'published')
            picture = Path(tomllib.loads(config.read_text())['default']['path'])
            with Image.open(picture) as image:
                self.assertEqual(image.size, (1280, 720))
                self.assertNotEqual(image.getpixel((0, 0)), image.getpixel((900, 300)))
            self.assertEqual(guest.wallpaper_fixture('restore', 'a' * 32, self.base)['state'], 'restored')
        self.assertEqual(config.read_bytes(), original)
        self.assertEqual(config.stat().st_mode & 0o777, 0o640)
        self.assertFalse(list(config.parent.glob('.c9-*')))

    def test_wallpaper_fixture_refuses_links_and_retains_backup_after_publish_failure(self):
        config_dir = self.base / '.config/wpaperd'
        config_dir.mkdir(parents=True)
        secret = self.base / 'do-not-touch'
        secret.write_bytes(b'original')
        config = config_dir / 'config.toml'
        config.symlink_to(secret)
        with patch.object(guest.subprocess, 'run') as run:
            with self.assertRaises(OSError):
                guest.wallpaper_fixture('prepare', 'a' * 32, self.base)
            run.assert_not_called()
        self.assertEqual(secret.read_bytes(), b'original')
        config.unlink()
        with patch.object(guest.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='{"state":"refused"}')):
            with self.assertRaisesRegex(RuntimeError, 'backup retained'):
                guest.wallpaper_fixture('prepare', 'b' * 32, self.base)
        self.assertTrue((config_dir / ('.c9-backup-' + 'b' * 32 + '.json')).is_file())
        with patch.object(guest.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='{"state":"unpublished"}')):
            result = guest.wallpaper_fixture('restore', 'b' * 32, self.base)
        self.assertEqual(result['publication'], 'unpublished')
        self.assertFalse(config.exists())
        self.assertFalse(list(config_dir.glob('.c9-*')))

    def test_material_trace_detects_flat_strip_and_keeps_drain_phase(self):
        reference = Image.new('RGB', (640, 400), '#365d77')
        draw = ImageDraw.Draw(reference)
        for x in (10, 255, 465):
            draw.rounded_rectangle((x + 2, 8, x + 154, 47), radius=14, fill='#223849')
            draw.rounded_rectangle((x, 4, x + 150, 39), radius=14, fill='#d8e1df')
        flat = reference.copy()
        ImageDraw.Draw(flat).rectangle((0, 0, 639, 47), fill='#d8e1df')
        self.assertGreater(host.material_score(flat, reference, host.MATERIAL_ROIS['bar-material']), .012)
        self.assertEqual(host.material_score(reference, reference, host.MATERIAL_ROIS['bar-shadow']), 0)
        events = [dict(phase='greeter-frozen', atMs=10), dict(phase='loaded', atMs=100),
                  dict(phase='drain-ready', atMs=200), dict(phase='finished', atMs=1200)]
        self.assertEqual(host.cover_phase(events, 5_000_000), 'unknown')
        self.assertEqual(host.cover_phase(events, 110_000_000), 'loaded')
        self.assertEqual(host.cover_phase(events, 210_000_000), 'drain-ready')
        self.assertEqual(host.cover_phase(events, 1_210_000_000), 'finished')
        self.assertEqual(host.cover_phase(events + [dict(phase='loaded', atMs=220)], 230_000_000), 'drain-ready')

    def test_missing_pre_auth_frame_refuses_readiness(self):
        process = SimpleNamespace(stdout=io.StringIO(json.dumps(dict(event='ready', greeterFrameReady=False))))
        selector = SimpleNamespace(register=lambda *args: None, select=lambda *args: [object()])
        with patch.object(host.selectors, 'DefaultSelector') as constructor:
            constructor.return_value.__enter__.return_value = selector
            with self.assertRaisesRegex(RuntimeError, 'refusing to type'):
                host.ready_line(process)
        process.stdout = io.StringIO(json.dumps(dict(event='ready', greeterFrameReady=True, userSocketBeforeAuth=True)))
        with patch.object(host.selectors, 'DefaultSelector') as constructor:
            constructor.return_value.__enter__.return_value = selector
            with self.assertRaisesRegex(RuntimeError, 'existed before authentication'):
                host.ready_line(process)

    def test_frame_gaps_include_first_socket_latency_and_failed_attempts(self):
        rows = [dict(state='frame', endNs=100_000_000), dict(state='unavailable', endNs=200_000_000),
                dict(state='frame', endNs=500_000_000)]
        result = host.frame_gaps(rows, 0, 550_000_000)
        self.assertEqual(result['frames'], 2)
        self.assertEqual(result['maximumMs'], 400)
        self.assertEqual(len(result['over180Ms']), 1)

    def test_archive_rejects_traversal_and_links(self):
        for name, kind in (('../escape', tarfile.REGTYPE), ('manifest.json', tarfile.SYMTYPE)):
            with self.subTest(name=name, kind=kind):
                archive = self.base / 'malicious.tar'
                with tarfile.open(archive, 'w') as output:
                    entry = tarfile.TarInfo(name)
                    entry.type = kind
                    entry.linkname = '/etc/passwd'
                    output.addfile(entry)
                with self.assertRaisesRegex(ValueError, 'unsafe'):
                    host.extract_evidence(archive, self.base / ('extract-' + str(len(list(self.base.iterdir())))))
        self.assertFalse((self.base / 'escape').exists())

    def test_roi_trace_flags_bar_without_dock_and_never_claims_visual_pass(self):
        output = self.base / 'evidence'
        frames = output / 'guest/frames'
        frames.mkdir(parents=True)
        desktop = Image.new('RGB', (640, 400), '#102030')
        draw = ImageDraw.Draw(desktop)
        for x in (12, 238, 470):
            draw.rectangle((x, 8, x + 150, 40), fill='#ddddee', outline='white', width=3)
            draw.text((x + 12, 18), 'READY ISLAND', fill='black')
        draw.rectangle((175, 350, 460, 390), fill='#eeeeee', outline='white', width=3)
        for x in range(185, 440, 30):
            draw.rectangle((x, 359, x + 17, 380), fill='#306090')
        partial = desktop.copy()
        ImageDraw.Draw(partial).rectangle((0, 330, 639, 399), fill='#102030')
        events, metadata = [], []
        token = 'a' * 32
        for index in range(7):
            image = partial if index == 0 else desktop
            filename = f'frames/user-{index:05d}.png'
            image.save(output / 'guest' / filename)
            timestamp = 1_000_000_000 + index * 80_000_000
            events.append(dict(side='user', state='frame', file=filename, startNs=timestamp - 10_000_000, endNs=timestamp))
            metadata.append(dict(side='user', epochNs=timestamp, endNs=timestamp + 1,
                                 layers=[dict(namespace=name, output='Virtual-1') for name in ('emaki-test-bar', 'emaki-test-dock')],
                                 windows=[dict(id=1, app_id='fixture')],
                                 startup=[dict(token=token, coverClaimed=True, shellClaimed=True, shellReady=True, coverFinished=True)]))
        (output / 'guest/frames.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in events))
        (output / 'guest/metadata.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in metadata))
        (output / 'guest/manifest.json').write_text(json.dumps(dict(startNs=900_000_000, endNs=1_500_000_000,
                                                                  firstSockets={'user': {'epochNs': 950_000_000}})))
        result = host.analyze(output)
        self.assertEqual(result['status'], 'INCOMPLETE', 'missing greeter/drain proof cannot be review-only')
        self.assertFalse(result['visualPass'])
        self.assertTrue(result['referenceStable'])
        self.assertEqual(result['candidates'][0]['kind'], 'uncovered-unfinished')
        trace = host.lines(output / 'visual-trace.jsonl')
        self.assertTrue(trace[0]['matches']['bar-left'])
        self.assertFalse(trace[0]['matches']['dock'])
        self.assertTrue(all(trace[-1]['matches'].values()))
        self.assertEqual(result['topMessageSource'].split()[0], 'UNKNOWN')
        self.assertTrue(any('Host display' in reason for reason in result['reasons']))
        self.assertTrue((output / 'top-message-sheet-000.png').exists())


if __name__ == '__main__':
    unittest.main()
