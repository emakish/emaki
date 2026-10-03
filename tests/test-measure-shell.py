#!/usr/bin/env python3
"""Measure-shell fixtures: fake proc/sys, no service manager or live process reads."""
import contextlib
import argparse
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('measure_shell', ROOT / 'tests/measure-shell.py')
measure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(measure)


class Fixture:
    pid = 123

    def __init__(self, directory):
        self.proc = directory / 'proc'
        self.sys = directory / 'sys'
        self.process = self.proc / str(self.pid)
        self.power = self.sys / 'class/power_supply'
        self.power.mkdir(parents=True)
        self.write(self.proc / 'uptime', '1200.0 1000.0\n')
        self.write(self.proc / 'sys/kernel/random/boot_id', 'fixture-boot\n')
        self.write(self.process / 'cmdline', b'/usr/bin/qs\0-n\0-p\0/fixture/shell\0')
        self.stat()
        self.write(self.process / 'status', 'Name:\tqs\nUid:\t1000 1000 1000 1000\nVmRSS:\t9000 kB\nVmSwap:\t45 kB\nThreads:\t17\n')
        self.write(self.process / 'smaps_rollup', '''00400000-00500000 ---p 00000000 00:00 0 [rollup]
Rss:                8192 kB
Pss:               4096 kB
Pss_Anon:          3072 kB
AnonHugePages:     2048 kB
Swap:               32 kB
''')
        (self.process / 'fdinfo').mkdir()
        self.supply('AC', 'Mains', online='0')
        self.supply('BAT0', 'Battery', present='1', status='Discharging', power_now='12500000')

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, bytes):
            path.write_bytes(value)
        else:
            path.write_text(value)

    def stat(self, user=100, system=50, start=10000, state='S', pid=None):
        fields = ['0'] * 50
        fields[0] = state
        fields[11], fields[12], fields[17], fields[19] = map(str, (user, system, 16, start))
        pid = self.pid if pid is None else pid
        self.write(self.proc / str(pid) / 'stat', str(pid) + ' (fixture (nested) worker) ' + ' '.join(fields) + '\n')

    def supply(self, name, kind, **values):
        for key, value in dict(type=kind, **values).items():
            self.write(self.power / name / key, value + '\n')

    def drm(self, fd, client='7', device='0000:c1:00.0', pid=None, **counters):
        values = dict(**{'drm-driver': 'amdgpu'}, **counters)
        if client is not None:
            values['drm-client-id'] = client
        if device is not None:
            values['drm-pdev'] = device
        process = self.process if pid is None else self.proc / str(pid)
        self.write(process / 'fdinfo' / str(fd), ''.join(key + ': ' + value + '\n' for key, value in values.items()))


    def niri(self, pid=456, uid=1000, executable='niri-emaki', start=5000):
        process = self.proc / str(pid)
        self.write(process / 'cmdline', ('/usr/local/bin/' + executable).encode() + b'\0--session\0')
        self.write(process / 'status', f'Name:\tniri-emaki\nUid:\t{uid} {uid} {uid} {uid}\n')
        self.stat(pid=pid, start=start)
        (process / 'fdinfo').mkdir(exist_ok=True)
        return pid

    def hwmon(self, index, name='amdgpu', device=None, **values):
        path = self.sys / 'class/hwmon' / f'hwmon{index}'
        for key, value in dict(name=name, **values).items():
            self.write(path / key, str(value) + '\n')
        if device is not None:
            target = self.sys / 'devices' / device
            target.mkdir(parents=True, exist_ok=True)
            (path / 'device').symlink_to(target)
        return path


class Clock:
    def __init__(self, fixture, update=None):
        self.now = 0.0
        self.fixture = fixture
        self.update = update
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay
        self.fixture.stat(user=100 + round(self.now * 30))
        self.fixture.write(self.fixture.proc / 'uptime', f'{1200 + self.now} 1000\n')
        if self.update:
            self.update(self.now)


class MeasureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='measure-shell-')
        self.addCleanup(self.temporary.cleanup)
        self.fixture = Fixture(Path(self.temporary.name))

    def collect(self, seconds=2.5, update=None):
        clock = Clock(self.fixture, update)
        report = measure.measure(self.fixture.pid, seconds=seconds, proc_root=self.fixture.proc,
                                 sys_root=self.fixture.sys, ticks_per_second=100,
                                 monotonic=clock.monotonic, sleep=clock.sleep)
        return report, clock

    def test_byte_counts_and_parenthesized_comm(self):
        self.assertEqual(measure.byte_count('1024 kB'), 1048576)
        self.assertEqual(measure.byte_count('1 MiB'), 1048576)
        self.assertEqual(measure.byte_count('3'), 3)
        self.assertIsNone(measure.byte_count('12 nonsense'))
        self.assertIsNone(measure.byte_count('-1 KiB'))
        self.assertEqual(measure.process_stat(self.fixture.proc, self.fixture.pid),
                         dict(cpu_ticks=150, start_ticks=10000, threads=16))

    def test_duration_and_interval_reject_nonfinite_or_nonpositive(self):
        for value in ('0', '-1', 'nan', 'inf', '-inf'):
            with self.assertRaises(argparse.ArgumentTypeError):
                measure.positive_float(value)
        self.assertEqual(measure.positive_float('.25'), .25)

    def test_proc_memory_cadence_cpu_and_uptime(self):
        snapshot = {path: path.read_bytes() for path in self.fixture.sys.rglob('*') if path.is_file()}
        report, clock = self.collect()
        self.assertTrue(report['completed'])
        self.assertEqual([row['elapsed_seconds'] for row in report['samples']], [0, 1, 2, 2.5])
        self.assertEqual(clock.sleeps, [1, 1, .5])
        self.assertEqual(report['boot_id'], 'fixture-boot')
        self.assertEqual(report['sample_count'], 4)
        self.assertEqual(report['samples'][0]['pss_bytes'], 4 * 1024 ** 2)
        self.assertEqual(report['samples'][0]['rss_bytes'], 8 * 1024 ** 2)
        self.assertEqual(report['samples'][0]['pss_anon_bytes'], 3 * 1024 ** 2)
        self.assertEqual(report['samples'][0]['anon_huge_pages_bytes'], 2 * 1024 ** 2)
        self.assertEqual(report['samples'][0]['swap_bytes'], 32 * 1024)
        self.assertEqual(report['samples'][0]['threads'], 17)
        self.assertEqual(report['samples'][0]['shell_uptime_seconds'], 1100)
        self.assertEqual(report['samples'][-1]['machine_uptime_seconds'], 1202.5)
        self.assertAlmostEqual(report['summary']['cpu_time_delta_seconds'], .75)
        self.assertAlmostEqual(report['summary']['cpu_percent_one_core'], 30)
        self.assertEqual(report['summary']['power_watts']['mean'], 12.5)
        self.assertEqual(snapshot, {path: path.read_bytes() for path in self.fixture.sys.rglob('*') if path.is_file()})

    def test_missing_rollup_uses_status_without_inventing_pss(self):
        (self.fixture.process / 'smaps_rollup').unlink()
        report, _ = self.collect(seconds=1)
        first = report['samples'][0]
        self.assertIsNone(first['pss_bytes'])
        self.assertIsNone(first['anon_huge_pages_bytes'])
        self.assertEqual(first['rss_bytes'], 9000 * 1024)
        self.assertEqual(first['swap_bytes'], 45 * 1024)
        self.assertEqual(first['memory_sources']['rss_bytes'], 'status:VmRSS')
        self.assertEqual(report['summary']['pss_bytes']['samples'], 0)
        self.assertTrue(any('smaps_rollup' in warning for warning in report['warnings']))
        (self.fixture.process / 'status').unlink()
        report, _ = self.collect(seconds=1)
        self.assertEqual(report['samples'][0]['threads'], 16)

    def test_drm_deduplicates_clients_aliases_and_devices(self):
        f = self.fixture
        f.drm(3, **{'drm-memory-vram': '4000 KiB', 'drm-total-vram': '4096 KiB',
                   'drm-resident-vram': '2048 KiB', 'drm-memory-gtt': '256 KiB'})
        f.drm(4, **{'drm-total-vram': '5120 KiB', 'drm-resident-vram': '3072 KiB',
                   'drm-total-gtt': '512 KiB', 'drm-resident-gtt': '128 KiB'})
        f.drm(5, client='8', **{'drm-memory-vram': '1 MiB', 'drm-resident-vram': '512 KiB',
                              'drm-memory-gtt': '256 KiB', 'drm-resident-gtt': '64 KiB'})
        f.drm(6, device='0000:c2:00.0', **{'drm-total-vram': '2 MiB', 'drm-resident-vram': '1 MiB',
                                         'drm-memory-gtt': '256 KiB', 'drm-resident-gtt': '64 KiB'})
        f.drm(7, client=None, **{'drm-total-vram': '999 MiB'})
        raw = {path: path.read_bytes() for path in (f.process / 'fdinfo').iterdir()}
        warnings = set()
        drm = measure.drm_sample(f.process, warnings)
        self.assertEqual(drm['client_count'], 3)
        self.assertEqual(drm['vram_total_bytes'], 8 * 1024 ** 2)
        self.assertEqual(drm['vram_resident_bytes'], int(4.5 * 1024 ** 2))
        self.assertEqual(drm['gtt_total_bytes'], 1024 ** 2)
        self.assertEqual(drm['gtt_resident_bytes'], 256 * 1024)
        self.assertEqual(drm['clients'][0]['fd_count'], 2)
        self.assertEqual(drm['clients'][0]['vram_total_bytes_source'], 'drm-total-vram')
        self.assertTrue(any('without drm-client-id' in warning for warning in warnings))
        self.assertEqual(raw, {path: path.read_bytes() for path in (f.process / 'fdinfo').iterdir()})

    def test_drm_unsupported_and_partial_are_explicit(self):
        warnings = set()
        drm = measure.drm_sample(self.fixture.process, warnings)
        self.assertIsNone(drm['vram_total_bytes'])
        self.assertIsNone(drm['gtt_resident_bytes'])
        self.fixture.drm(3, **{'drm-resident-vram': '0 KiB'})
        self.fixture.drm(4, client='8', device=None, **{'drm-total-gtt': '100 KiB'})
        drm = measure.drm_sample(self.fixture.process, warnings)
        self.assertEqual(drm['vram_resident_bytes'], 0)
        self.assertIsNone(drm['vram_total_bytes'])
        self.assertEqual(drm['coverage']['vram_resident_bytes'], 1)
        self.assertTrue(any('some DRM clients' in warning for warning in warnings))
        self.assertTrue(any('device identity' in warning for warning in warnings))

    def test_shared_vram_example_bounds_and_duplicate_fds(self):
        f = self.fixture
        # Reviewer-provided counters are fixture inputs, not a live measurement.
        f.drm(3, client='10', **{'drm-total-vram': '582660 KiB', 'drm-shared-vram': '88128 KiB',
                               'drm-resident-vram': '400000 KiB'})
        f.drm(4, client='12', **{'drm-total-vram': '38604 KiB', 'drm-shared-vram': '38592 KiB',
                               'drm-resident-vram': '30000 KiB'})
        f.drm(5, client='12', **{'drm-total-vram': '38604 KiB', 'drm-shared-vram': '38592 KiB'})
        drm = measure.drm_sample(f.process, set())
        self.assertEqual(drm['vram_shared_bytes'], 126720 * 1024)
        bounds = drm['ranges']['vram_total_bytes']
        self.assertEqual(bounds['lower_bytes'], (582660 + 38604 - 38592) * 1024)
        self.assertEqual(bounds['upper_bytes'], (582660 + 38604) * 1024)
        self.assertTrue(bounds['all_shared_report'])
        self.assertTrue(bounds['all_clients_report'])
        self.assertEqual(drm['ranges']['vram_resident_bytes']['lower_bytes'], 400000 * 1024)
        self.assertEqual(drm['ranges']['vram_resident_bytes']['upper_bytes'], 430000 * 1024)
        self.assertEqual(drm['clients'][1]['fd_count'], 2)

    def test_shared_bounds_missing_counters_devices_and_zero(self):
        f = self.fixture
        f.drm(3, client='1', **{'drm-total-vram': '8 MiB', 'drm-shared-vram': '2 MiB'})
        f.drm(4, client='2', **{'drm-total-vram': '4 MiB'})
        f.drm(5, client='1', device='0000:c2:00.0', **{'drm-total-vram': '1 MiB', 'drm-shared-vram': '0'})
        drm = measure.drm_sample(f.process, set())
        bounds = drm['ranges']['vram_total_bytes']
        self.assertEqual((bounds['lower_bytes'], bounds['upper_bytes']), (11 * 1024 ** 2, 13 * 1024 ** 2))
        self.assertFalse(bounds['all_shared_report'])
        self.assertTrue(bounds['device_identity_complete'])
        # A missing pdev must not assume a distinct GPU and strengthen the bound.
        f.drm(6, client='3', device=None, **{'drm-total-vram': '10 MiB'})
        bounds = measure.drm_sample(f.process, set())['ranges']['vram_total_bytes']
        self.assertEqual(bounds['lower_bytes'], 17 * 1024 ** 2)
        self.assertFalse(bounds['device_identity_complete'])

    def test_engine_deltas_deduplicate_and_keep_engine_only_clients(self):
        f = self.fixture
        f.drm(3, **{'drm-engine-gfx': '100000000 ns', 'drm-engine-compute': '40000000 ns',
                   'drm-engine-capacity-gfx': '1'})
        f.drm(4, **{'drm-engine-gfx': '110000000 ns'})
        f.drm(5, client='8', **{'drm-engine-gfx': '20000000 ns', 'drm-engine-dma': '10 ns'})
        before = measure.drm_sample(f.process, set())
        self.assertEqual(before['engines_ns']['gfx'], 130000000)
        self.assertEqual(before['client_count'], 2)
        self.assertNotIn('capacity-gfx', before['engines_ns'])
        f.drm(3, **{'drm-engine-gfx': '130000000 ns', 'drm-engine-compute': '60000000 ns'})
        f.drm(5, client='8', **{'drm-engine-gfx': '35000000 ns', 'drm-engine-dma': '15 ns'})
        after = measure.drm_sample(f.process, set())
        delta = measure.engine_delta(before, after, 1, set(), 'shell')
        self.assertEqual(delta['gfx']['delta_ns'], 35000000)
        self.assertEqual(delta['compute']['delta_ns'], 20000000)
        self.assertEqual(delta['dma']['delta_ns'], 5)
        self.assertTrue(all(row['complete'] for row in delta.values()))
        self.assertEqual(delta['gfx']['matched_clients'], 2)

    def test_engine_reset_and_turnover_never_invent_deltas(self):
        f = self.fixture
        f.drm(3, **{'drm-engine-gfx': '100 ns'})
        f.drm(4, client='8', **{'drm-engine-gfx': '200 ns'})
        before = measure.drm_sample(f.process, set())
        f.drm(3, **{'drm-engine-gfx': '150 ns'})
        f.drm(4, client='8', **{'drm-engine-gfx': '5 ns'})
        f.drm(5, client='9', **{'drm-engine-gfx': '900 ns'})
        warnings = set()
        delta = measure.engine_delta(before, measure.drm_sample(f.process, set()), 1, warnings, 'shell')['gfx']
        self.assertEqual(delta['delta_ns'], 50)
        self.assertEqual(delta['reset_clients'], 1)
        self.assertFalse(delta['complete'])
        self.assertEqual(delta['interval_clients'], 3)
        self.assertTrue(any('partial' in warning for warning in warnings))
        (f.process / 'fdinfo/3').unlink()
        delta = measure.engine_delta(before, measure.drm_sample(f.process, set()), 1, warnings, 'shell')['gfx']
        self.assertIsNone(delta['delta_ns'])

    def test_niri_discovery_identity_and_gpu_delta(self):
        f = self.fixture
        pid = f.niri()
        f.niri(pid=457, uid=1001)  # Other user's compositor is excluded.
        f.niri(pid=458, executable='niri-emaki-helper')
        f.drm(3, pid=pid, **{'drm-engine-gfx': '1000000000 ns'})
        f.drm(3, **{'drm-engine-gfx': '500000000 ns'})
        def advance(now):
            f.drm(3, pid=pid, **{'drm-engine-gfx': f'{1000000000 + round(now * 20000000)} ns'})
            f.drm(3, **{'drm-engine-gfx': f'{500000000 + round(now * 10000000)} ns'})
        report, _ = self.collect(seconds=2, update=advance)
        self.assertEqual(report['niri']['pid'], pid)
        self.assertEqual(report['niri']['start_ticks'], 5000)
        self.assertEqual(report['summary']['gpu_engines']['niri']['gfx']['delta_ns'], 40000000)
        self.assertEqual(report['summary']['gpu_engines']['shell']['gfx']['delta_ns'], 20000000)
        self.assertTrue(report['summary']['gpu_engines']['niri']['gfx']['complete'])
        f.niri(pid=459)
        warnings = set()
        self.assertIsNone(measure.find_niri(f.proc, f.pid, warnings))
        self.assertTrue(any('multiple' in warning for warning in warnings))

    def test_niri_restart_and_exec_do_not_reselect_or_corrupt_shell_sample(self):
        f = self.fixture
        pid = f.niri()
        f.drm(3, pid=pid, **{'drm-engine-gfx': '100 ns'})
        def restart(now):
            if now == 1:
                f.stat(pid=pid, start=5001)
                f.niri(pid=459)
        report, _ = self.collect(seconds=2, update=restart)
        self.assertTrue(report['completed'])
        self.assertEqual(report['niri']['pid'], pid)
        self.assertEqual([row['niri']['state'] for row in report['samples']], ['available', 'unavailable', 'unavailable'])
        self.assertEqual(report['summary']['gpu_engines']['niri'], {})
        self.assertTrue(any('reused' in warning for warning in report['warnings']))
        f.stat(pid=pid, start=5000)
        f.write(f.proc / str(pid) / 'cmdline', b'/usr/bin/unrelated\0')
        sample = measure.niri_sample(dict(pid=pid, start_ticks=5000, executable='niri-emaki'), f.proc, set())
        self.assertEqual(sample['state'], 'unavailable')

    def test_niri_identity_change_during_drm_read_is_rejected(self):
        f = self.fixture
        pid = f.niri()
        identity = dict(pid=pid, start_ticks=5000, executable='niri-emaki')
        read = measure.drm_sample
        def change_executable(directory, warnings):
            result = read(directory, warnings)
            f.write(f.proc / str(pid) / 'cmdline', b'/usr/bin/unrelated\0')
            return result
        with patch.object(measure, 'drm_sample', change_executable):
            row = measure.niri_sample(identity, f.proc, set())
        self.assertIsNone(row['drm'])
        self.assertIn('during sample', row['error'])

    def test_amdgpu_power_on_ac_sources_dedup_and_missing(self):
        f = self.fixture
        f.supply('AC', 'Mains', online='1')
        f.hwmon(0, name='cpu', power1_input=99000000)
        a = f.hwmon(1, device='0000:c1:00.0', power1_input=12000000, power1_average=10000000)
        f.hwmon(2, device='0000:c1:00.0', power1_input=12000000)
        f.hwmon(3, device='0000:c2:00.0', power1_average=3500000)
        report, _ = self.collect(seconds=1)
        self.assertIsNone(report['summary']['power_watts']['mean'])
        self.assertEqual(report['summary']['gpu_power_watts']['mean'], 15.5)
        self.assertEqual(len(report['samples'][0]['gpu_power']['devices']), 2)
        self.assertEqual(report['samples'][0]['gpu_power']['devices'][0]['source'], str(a / 'power1_input'))
        # Negative/missing sensor data remains missing, never a fake zero.
        f.hwmon(4, device='0000:c3:00.0', power1_input=-1)
        result = measure.gpu_power_sample(f.sys)
        self.assertFalse(result['complete'])
        self.assertIsNone(result['watts'])
        self.assertIsNone(result['devices'][-1]['source'])

    def test_compare_saved_reports_never_looks_up_processes(self):
        before, _ = self.collect(seconds=1)
        after, _ = self.collect(seconds=1)
        after['summary']['pss_bytes']['mean'] += 1024
        after['summary']['gpu_engines']['shell']['gfx'] = dict(delta_ns=1000000, covered_seconds=1, complete=False)
        temporary = Path(self.temporary.name)
        for name, report in (('before', before), ('after', after)):
            (temporary / (name + '.json')).write_text(json.dumps(report))
        output = io.StringIO()
        with patch.object(measure, 'find_shell_pid', side_effect=AssertionError('live lookup')), \
                patch.object(measure, 'measure', side_effect=AssertionError('live measurement')), \
                contextlib.redirect_stdout(output):
            self.assertEqual(measure.main(['--compare', str(temporary / 'before.json'), str(temporary / 'after.json'),
                                          '--json', str(temporary / 'comparison.json')]), 0)
        result = json.loads((temporary / 'comparison.json').read_text())
        self.assertEqual(result['metrics']['pss_bytes']['delta'], 1024)
        self.assertIsNone(result['metrics']['shell_gpu_gfx_busy_seconds_per_second']['delta'])
        self.assertIn('not bounds on the true', output.getvalue())

    def test_ac_never_reports_power_and_battery_fallback_is_watts(self):
        f = self.fixture
        f.supply('AC', 'Mains', online='1')
        power = measure.battery_sample(f.sys)
        self.assertEqual(power['state'], 'ac')
        self.assertIsNone(power['watts'])
        self.assertEqual(power['reason'], 'on AC, no power number')
        f.supply('AC', 'Mains', online='0')
        f.supply('BAT0', 'Battery', status='Charging')
        self.assertEqual(measure.battery_sample(f.sys)['state'], 'ac')
        f.supply('BAT0', 'Battery', status='Discharging', current_now='-2000000', voltage_now='12000000')
        (f.power / 'BAT0/power_now').unlink()
        power = measure.battery_sample(f.sys)
        self.assertEqual(power['watts'], 24)
        self.assertEqual(power['batteries'][0]['source'], 'current_now*voltage_now')
        f.supply('BAT1', 'Battery', status='Discharging', power_now='3000000')
        f.supply('mouse', 'Battery', status='Discharging', power_now='99000000')
        self.assertEqual(measure.battery_sample(f.sys)['watts'], 27)
        f.supply('BAT1', 'Battery', present='0')
        self.assertEqual(measure.battery_sample(f.sys)['watts'], 24)

    def test_unknown_or_incomplete_battery_has_no_number(self):
        f = self.fixture
        f.supply('BAT0', 'Battery', status='Unknown')
        self.assertIsNone(measure.battery_sample(f.sys)['watts'])
        f.supply('BAT0', 'Battery', status='Discharging')
        f.supply('BAT1', 'Battery', status='Discharging')
        power = measure.battery_sample(f.sys)
        self.assertEqual(power['state'], 'battery')
        self.assertIsNone(power['watts'])

    def test_pid_reuse_and_exit_keep_partial_samples(self):
        report, _ = self.collect(update=lambda _: self.fixture.stat(start=11000))
        self.assertFalse(report['completed'])
        self.assertEqual(report['sample_count'], 1)
        self.assertIn('reused', report['error'])
        self.fixture.stat()
        report, _ = self.collect(update=lambda _: (self.fixture.process / 'stat').unlink())
        self.assertFalse(report['completed'])
        self.assertEqual(report['sample_count'], 1)
        self.assertIn('exited', report['error'])

    def test_pid_reuse_inside_a_sample_is_rejected(self):
        real = measure.memory_sample
        def change_identity(directory, warnings):
            result = real(directory, warnings)
            self.fixture.stat(start=10001)
            return result
        with patch.object(measure, 'memory_sample', change_identity):
            with self.assertRaisesRegex(measure.MeasurementError, 'reused during a sample'):
                self.collect()

    def test_service_lookup_is_read_only_and_rejects_inactive(self):
        with patch.object(measure.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '123\n', '')) as run:
            self.assertEqual(measure.find_shell_pid(), 123)
            self.assertEqual(run.call_args.args[0],
                             ['systemctl', '--user', 'show', '-p', 'MainPID', '--value', 'emaki-shell'])
        for value in ('0\n', '1\n', 'garbage'):
            with patch.object(measure.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, value, '')):
                with self.assertRaises(measure.MeasurementError):
                    measure.find_shell_pid()
        self.fixture.write(self.fixture.process / 'cmdline', b'/bin/sh\0emaki-shell\0')
        with self.assertRaisesRegex(measure.MeasurementError, 'not qs/quickshell'):
            self.collect()

    def test_cli_prints_table_and_writes_raw_json(self):
        self.fixture.supply('AC', 'Mains', online='1')
        real_measure = measure.measure
        clock = Clock(self.fixture)
        def fake_roots(pid, **kwargs):
            return real_measure(pid, proc_root=self.fixture.proc, sys_root=self.fixture.sys, ticks_per_second=100,
                                monotonic=clock.monotonic, sleep=clock.sleep, **kwargs)
        output_path = Path(self.temporary.name) / 'report.json'
        output = io.StringIO()
        with patch.object(measure, 'measure', fake_roots), \
                patch.object(measure.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '123\n', '')), \
                contextlib.redirect_stdout(output):
            self.assertEqual(measure.main(['--seconds', '2', '--json', str(output_path)]), 0)
        report = json.loads(output_path.read_text())
        self.assertEqual(report['sample_count'], 3)
        self.assertEqual(report['requested_seconds'], 2)
        self.assertIsNone(report['summary']['power_watts']['mean'])
        self.assertIn('on AC, no power number', output.getvalue())
        self.assertIn('Pss_Anon MiB', output.getvalue())
        self.assertIn('VRAM resident MiB', output.getvalue())
        self.assertIn('CPU time delta:', output.getvalue())


if __name__ == '__main__':
    unittest.main()
