#!/usr/bin/env python3
"""Read-only Linux shell sampler; never attaches, signals or restarts a process.

Run on an otherwise comparable desktop before and after a change:
    python3 tests/measure-shell.py --seconds 60 --json before.json

Memory is read from smaps_rollup (status provides RSS/Swap fallbacks). DRM
allocated and resident bytes remain separate: drm-memory-* is the legacy alias
of drm-total-*, not additional memory. Shared allocations produce bounds, not an
exact unique VRAM count. Missing counters are null, never zero. GPU engine times
are per-client accumulated busy time. GPU power is amdgpu device power (including
on AC); battery power is whole-machine discharge. Neither is shell-only power.
Only the requested JSON output is written; /proc and /sys are read-only inputs.
"""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time
import reaper
reaper.guard()  # nothing this test starts outlives it


MEMORY_FIELDS = ('pss_bytes', 'rss_bytes', 'pss_anon_bytes', 'anon_huge_pages_bytes', 'swap_bytes')
DRM_MEMORY_FIELDS = ('vram_total_bytes', 'vram_resident_bytes', 'gtt_total_bytes', 'gtt_resident_bytes')
DRM_FIELDS = (*DRM_MEMORY_FIELDS, 'vram_shared_bytes', 'gtt_shared_bytes')


class MeasurementError(Exception):
    """The selected shell can no longer be measured consistently."""


def read_fields(path):
    return {key.strip(): value.strip() for line in path.read_text().splitlines()
            if ':' in line for key, value in [line.split(':', 1)]}


def byte_count(value):
    """Kernel proc counters use binary kB/KiB; accept other explicit units too."""
    if value is None:
        return None
    match = re.fullmatch(r'(\d+)\s*(B|kB|KiB|MB|MiB|GB|GiB)?', value)
    if not match:
        return None
    factor = {'B': 1, None: 1, 'kB': 1024, 'KiB': 1024,
              'MB': 1024 ** 2, 'MiB': 1024 ** 2, 'GB': 1024 ** 3, 'GiB': 1024 ** 3}
    return int(match[1]) * factor[match[2]]


def process_stat(proc, pid, role='shell'):
    try:
        content = (proc / str(pid) / 'stat').read_text()
        # comm is parenthesized and may itself contain spaces and parentheses.
        fields = content.rsplit(')', 1)[1].split()
        if fields[0] in ('Z', 'X', 'x'):
            raise MeasurementError(role + ' exited during measurement')
        return dict(cpu_ticks=int(fields[11]) + int(fields[12]),
                    start_ticks=int(fields[19]), threads=int(fields[17]))
    except (OSError, ValueError, IndexError) as error:
        raise MeasurementError('cannot read ' + role + ' stat (process exited or access denied)') from error


def find_shell_pid():
    try:
        result = subprocess.run(['systemctl', '--user', 'show', '-p', 'MainPID', '--value', 'emaki-shell'],
                                check=True, capture_output=True, text=True, timeout=10)
        pid = int(result.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise MeasurementError('cannot obtain emaki-shell MainPID from the user service manager') from error
    if pid <= 1:
        raise MeasurementError('emaki-shell is not running (MainPID is zero or invalid)')
    return pid


def memory_sample(directory, warnings):
    sources = {}
    try:
        rollup = read_fields(directory / 'smaps_rollup')
    except OSError:
        rollup = {}
        warnings.add('smaps_rollup unavailable; Pss/Pss_Anon/AnonHugePages may be missing')
    try:
        status = read_fields(directory / 'status')
    except OSError:
        status = {}
        warnings.add('status unavailable')
    values = {}
    for name, rollup_key, status_key in (
            ('pss_bytes', 'Pss', None), ('rss_bytes', 'Rss', 'VmRSS'),
            ('pss_anon_bytes', 'Pss_Anon', None), ('anon_huge_pages_bytes', 'AnonHugePages', None),
            ('swap_bytes', 'Swap', 'VmSwap')):
        value = byte_count(rollup.get(rollup_key))
        source = 'smaps_rollup:' + rollup_key
        if value is None and status_key:
            value = byte_count(status.get(status_key))
            source = 'status:' + status_key
        values[name] = value
        sources[name] = source if value is not None else None
    threads = status.get('Threads', '')
    return values, sources, int(threads) if threads.isdigit() else None


def nanoseconds(value):
    match = re.fullmatch(r'(\d+)\s+ns', value)
    return int(match[1]) if match else None


def client_identity(client):
    return (client['device'], client['driver'], client['client_id'])


def drm_sample(directory, warnings):
    """Deduplicate fds by device/client, preserving counters and their source.

    Shared bytes are not additive unique allocations. Within a device the union
    lies between sum(private) + max(shared) and sum(total). Shared buffers can
    belong to clients outside this process, so the upper bound cannot be reduced.
    For residency, shared-resident is unknown: min(shared, resident) is its upper
    bound. Missing shared counters assume all that client's bytes may be shared.
    These bounds cover reporting clients, not inaccessible/unreported clients.
    """
    clients = {}
    try:
        descriptors = sorted((directory / 'fdinfo').iterdir())
    except OSError:
        warnings.add('fdinfo unavailable; DRM counters unavailable')
        descriptors = []
    for path in descriptors:
        try:
            fields = read_fields(path)
        except OSError:
            warnings.add('some fdinfo entries became unreadable; DRM scan may be incomplete')
            continue
        counters = {}
        engines = {}
        for key, value in fields.items():
            if re.fullmatch(r'drm-(memory|total|resident|shared)-(vram|gtt)', key):
                parsed = byte_count(value)
                if parsed is not None:
                    counters[key] = parsed
            elif key.startswith('drm-engine-') and not key.startswith('drm-engine-capacity-'):
                parsed = nanoseconds(value)
                if parsed is not None:
                    engines[key.removeprefix('drm-engine-')] = parsed
        if not counters and not engines:
            continue
        client_id = fields.get('drm-client-id')
        if not client_id:
            warnings.add('DRM counters without drm-client-id excluded to avoid double counting')
            continue
        device = fields.get('drm-pdev', '')
        driver = fields.get('drm-driver', '')
        if not device:
            warnings.add('DRM device identity unavailable; client ids deduplicated within driver only')
        identity = (device, driver, client_id)
        client = clients.setdefault(identity, dict(device=device or None, driver=driver or None,
                                                   client_id=client_id, fd_count=0, counters={}, engines_ns={}))
        client['fd_count'] += 1
        for destination, source in ((client['counters'], counters), (client['engines_ns'], engines)):
            for key, value in source.items():
                destination[key] = max(value, destination.get(key, 0))
    totals, coverage, ranges = {}, {}, {}
    for region in ('vram', 'gtt'):
        for kind in ('total', 'resident', 'shared'):
            key = region + '_' + kind + '_bytes'
            values = []
            for client in clients.values():
                raw = client['counters']
                source = 'drm-' + kind + '-' + region
                if source not in raw and kind == 'total':
                    source = 'drm-memory-' + region
                client[key] = raw.get(source)
                client[key + '_source'] = source if source in raw else None
                if client[key] is not None:
                    values.append(client[key])
            totals[key] = sum(values) if values else None
            coverage[key] = len(values)
            if values and len(values) != len(clients):
                warnings.add(key + ' missing for some DRM clients; sum covers reporting clients only')
        for kind in ('total', 'resident'):
            key = region + '_' + kind + '_bytes'
            groups = {}
            shared_known = 0
            for client in clients.values():
                amount = client[key]
                if amount is None:
                    continue
                shared = client[region + '_shared_bytes']
                if shared is not None:
                    shared_known += 1
                possible_shared = min(amount, shared) if shared is not None else amount
                # Unknown device ids could refer to any of the known devices:
                # combine all groups below in that case for a conservative bound.
                groups.setdefault(client['device'], []).append((amount, possible_shared))
            unknown_device = None in groups
            if unknown_device:
                groups = {None: [value for group in groups.values() for value in group]}
            lower = sum(sum(amount - shared for amount, shared in group)
                        + max(shared for _, shared in group) for group in groups.values()) if groups else None
            ranges[key] = dict(lower_bytes=lower, upper_bytes=totals[key],
                               reporting_clients=coverage[key], shared_reporting_clients=shared_known,
                               all_clients_report=coverage[key] == len(clients) and bool(clients),
                               all_shared_report=shared_known == coverage[key] and bool(groups),
                               device_identity_complete=not unknown_device)
    engines = {}
    for client in clients.values():
        for engine, value in client['engines_ns'].items():
            engines[engine] = engines.get(engine, 0) + value
    return dict(**totals, client_count=len(clients), coverage=coverage, clients=list(clients.values()),
                ranges=ranges, engines_ns=engines)


def engine_delta(before, after, elapsed, warnings, role):
    """Compare matching clients only. Client turnover/reset makes an interval partial."""
    old = {client_identity(client): client for client in before['clients']}
    new = {client_identity(client): client for client in after['clients']}
    engines = set(before['engines_ns']) | set(after['engines_ns'])
    result = {}
    for engine in sorted(engines):
        identities = {identity for identity, client in (*old.items(), *new.items()) if engine in client['engines_ns']}
        delta, matched, reset = 0, 0, 0
        for identity in identities:
            a = old.get(identity, {}).get('engines_ns', {}).get(engine)
            b = new.get(identity, {}).get('engines_ns', {}).get(engine)
            if a is not None and b is not None and b >= a:
                delta += b - a
                matched += 1
            elif a is not None and b is not None:
                reset += 1
        complete = matched == len(identities)
        if not complete:
            warnings.add(role + ' GPU ' + engine + ': client turnover, missing counter or reset; deltas are partial')
        result[engine] = dict(delta_ns=delta if matched else None, matched_clients=matched,
                              interval_clients=len(identities), reset_clients=reset, complete=complete,
                              elapsed_seconds=elapsed)
    return result


def process_executable(proc_root, pid):
    try:
        argv = (proc_root / str(pid) / 'cmdline').read_bytes().split(b'\0')
        return os.path.basename(os.fsdecode(argv[0])) if argv else ''
    except OSError as error:
        raise MeasurementError('cannot verify process command line') from error


def process_uid(directory):
    try:
        values = read_fields(directory / 'status').get('Uid', '').split()
        return int(values[0]) if values else directory.stat().st_uid
    except (OSError, ValueError):
        return None


def find_niri(proc_root, shell_pid, warnings):
    """Find one same-user niri-emaki; comm alone truncates and is not an identity."""
    owner = process_uid(proc_root / str(shell_pid))
    if owner is None:
        warnings.add('cannot establish shell UID; niri lookup skipped')
        return None
    candidates = []
    try:
        entries = sorted(proc_root.iterdir())
    except OSError:
        warnings.add('cannot scan proc for niri-emaki')
        return None
    for entry in entries:
        if not entry.name.isdigit() or process_uid(entry) != owner:
            continue
        try:
            pid = int(entry.name)
            identity = process_stat(proc_root, pid, 'niri')
            executable = process_executable(proc_root, pid)
            if executable != 'niri-emaki':
                continue
            if process_stat(proc_root, pid, 'niri')['start_ticks'] != identity['start_ticks']:
                continue
            candidates.append(dict(pid=pid, start_ticks=identity['start_ticks'], executable=executable, uid=owner))
        except MeasurementError:
            continue
    if len(candidates) != 1:
        warnings.add('niri-emaki not found for shell UID' if not candidates else
                     'multiple niri-emaki processes for shell UID; niri GPU sample omitted')
        return None
    return candidates[0]


def niri_sample(identity, proc_root, warnings):
    if identity is None:
        return dict(state='unavailable', drm=None)
    pid, expected = identity['pid'], identity['start_ticks']
    try:
        before = process_stat(proc_root, pid, 'niri')
        if before['start_ticks'] != expected or process_executable(proc_root, pid) != identity['executable']:
            raise MeasurementError('niri PID was reused or executable changed')
        drm = drm_sample(proc_root / str(pid), warnings)
        after = process_stat(proc_root, pid, 'niri')
        if after['start_ticks'] != expected or process_executable(proc_root, pid) != identity['executable']:
            raise MeasurementError('niri PID was reused or executable changed during sample')
        return dict(state='available', pid=pid, start_ticks=expected, drm=drm)
    except MeasurementError as error:
        warnings.add(str(error) + '; original niri identity retained, no replacement selected')
        return dict(state='unavailable', pid=pid, start_ticks=expected, error=str(error), drm=None)


def gpu_power_sample(sys_root):
    """amdgpu device power in microwatts; available independently of battery state."""
    devices = {}
    for path in sorted((sys_root / 'class/hwmon').glob('hwmon*')):
        try:
            if (path / 'name').read_text().strip() != 'amdgpu':
                continue
        except OSError:
            continue
        device_link = path / 'device'
        device = str(device_link.resolve()) if device_link.exists() else str(path)
        row = dict(device=device, hwmon=str(path), watts=None, source=None)
        try:
            row['label'] = (path / 'power1_label').read_text().strip()
        except OSError:
            row['label'] = None
        for name in ('power1_input', 'power1_average'):
            try:
                raw = int((path / name).read_text())
                if raw < 0:
                    continue
                row.update(watts=raw / 1_000_000, source=str(path / name))
                break
            except (OSError, ValueError):
                continue
        if device not in devices or devices[device]['watts'] is None:
            devices[device] = row
    rows = list(devices.values())
    complete = bool(rows) and all(row['watts'] is not None for row in rows)
    return dict(watts=sum(row['watts'] for row in rows) if complete else None, devices=rows,
                reason='amdgpu device power, not shell-only or whole-machine power', complete=complete)


def battery_sample(sys_root):
    root = sys_root / 'class/power_supply'
    try:
        supplies = sorted(root.iterdir())
    except OSError:
        return dict(state='unavailable', watts=None, reason='power_supply unavailable', batteries=[])
    batteries = []
    online = False
    for supply in supplies:
        try:
            kind = (supply / 'type').read_text().strip()
        except OSError:
            kind = 'Battery' if supply.name.startswith('BAT') else ''
        if kind != 'Battery':
            try:
                online = online or int((supply / 'online').read_text()) == 1
            except (OSError, ValueError):
                pass
            continue
        # Exclude peripheral batteries (mouse, headphones); this tool measures
        # system power, and the documented Linux source is BAT*.
        if not supply.name.startswith('BAT'):
            continue
        try:
            if (supply / 'present').read_text().strip() == '0':
                continue
        except OSError:
            pass
        try:
            status = (supply / 'status').read_text().strip()
        except OSError:
            status = 'Unknown'
        batteries.append(dict(name=supply.name, status=status, watts=None, source=None))
    if online or any(row['status'] == 'Charging' for row in batteries):
        return dict(state='ac', watts=None, reason='on AC, no power number', batteries=batteries)
    if not batteries or not all(row['status'] == 'Discharging' for row in batteries):
        return dict(state='unavailable', watts=None, reason='battery discharge not confirmed', batteries=batteries)
    for row in batteries:
        path = root / row['name']
        try:
            power = int((path / 'power_now').read_text())
            if power < 0:
                raise ValueError('negative power counter')
            row.update(watts=power / 1_000_000, source='power_now')
        except (OSError, ValueError):
            try:
                current = abs(int((path / 'current_now').read_text()))
                voltage = int((path / 'voltage_now').read_text())
                if voltage <= 0:
                    raise ValueError('invalid voltage')
                row.update(watts=current * voltage / 1_000_000_000_000,
                           source='current_now*voltage_now')
            except (OSError, ValueError):
                pass
    if any(row['watts'] is None for row in batteries):
        return dict(state='battery', watts=None, reason='some discharging batteries lack power counters', batteries=batteries)
    return dict(state='battery', watts=sum(row['watts'] for row in batteries),
                reason='whole-machine battery discharge', batteries=batteries)


def sample(pid, proc_root, sys_root, ticks_per_second, expected_start, warnings, niri=None):
    before = process_stat(proc_root, pid)
    if before['start_ticks'] != expected_start:
        raise MeasurementError('shell PID was reused; measurement stopped')
    memory, sources, threads = memory_sample(proc_root / str(pid), warnings)
    drm = drm_sample(proc_root / str(pid), warnings)
    power = battery_sample(sys_root)
    gpu_power = gpu_power_sample(sys_root)
    compositor = niri_sample(niri, proc_root, warnings)
    try:
        machine_uptime = float((proc_root / 'uptime').read_text().split()[0])
        if not math.isfinite(machine_uptime) or machine_uptime < 0:
            raise ValueError('invalid uptime')
    except (OSError, ValueError, IndexError) as error:
        raise MeasurementError('cannot read machine uptime') from error
    after = process_stat(proc_root, pid)
    if after['start_ticks'] != expected_start:
        raise MeasurementError('shell PID was reused during a sample; measurement stopped')
    return dict(**memory, memory_sources=sources, threads=threads if threads is not None else after['threads'],
                cpu_seconds=after['cpu_ticks'] / ticks_per_second,
                shell_uptime_seconds=max(0.0, machine_uptime - expected_start / ticks_per_second),
                machine_uptime_seconds=machine_uptime, drm=drm, power=power, gpu_power=gpu_power, niri=compositor)


def distribution(values):
    available = [value for value in values if value is not None]
    return dict(samples=len(available), min=min(available), mean=statistics.fmean(available),
                max=max(available), last=values[-1]) if available else dict(samples=0, min=None, mean=None, max=None, last=None)


def summarize(samples):
    summary = {key: distribution([row[key] for row in samples]) for key in (*MEMORY_FIELDS, 'threads')}
    summary.update({key: distribution([row['drm'][key] for row in samples]) for key in DRM_FIELDS})
    for key in DRM_MEMORY_FIELDS:
        for bound in ('lower', 'upper'):
            summary[key.removesuffix('_bytes') + '_' + bound + '_bytes'] = distribution(
                [row['drm']['ranges'][key][bound + '_bytes'] for row in samples])
    summary['power_watts'] = distribution([row['power']['watts'] for row in samples])
    summary['gpu_power_watts'] = distribution([row['gpu_power']['watts'] for row in samples])
    summary['gpu_engines'] = {}
    for role in ('shell', 'niri'):
        intervals = [row['gpu_engine_deltas'][role] for row in samples[1:]]
        names = set().union(*(interval.keys() for interval in intervals))
        engines = {}
        for name in sorted(names):
            rows = [interval[name] for interval in intervals if name in interval]
            deltas = [row['delta_ns'] for row in rows if row['delta_ns'] is not None]
            engines[name] = dict(delta_ns=sum(deltas) if deltas else None,
                                 complete=bool(rows) and len(rows) == len(intervals) and all(row['complete'] for row in rows),
                                 measured_intervals=len(deltas), total_intervals=len(intervals),
                                 covered_seconds=sum(row['elapsed_seconds'] for row in rows if row['delta_ns'] is not None))
        summary['gpu_engines'][role] = engines
    elapsed = samples[-1]['elapsed_seconds'] - samples[0]['elapsed_seconds']
    cpu_delta = samples[-1]['cpu_seconds'] - samples[0]['cpu_seconds']
    summary.update(cpu_time_delta_seconds=cpu_delta, cpu_elapsed_seconds=elapsed,
                   cpu_percent_one_core=100 * cpu_delta / elapsed if elapsed > 0 else None)
    return summary


def measure(pid, seconds=60.0, interval=1.0, proc_root=Path('/proc'), sys_root=Path('/sys'),
            ticks_per_second=None, monotonic=time.monotonic, sleep=time.sleep):
    ticks = ticks_per_second if ticks_per_second is not None else os.sysconf('SC_CLK_TCK')
    identity = process_stat(proc_root, pid)
    executable = process_executable(proc_root, pid)
    if executable not in ('qs', 'quickshell'):
        raise MeasurementError('service MainPID is not qs/quickshell (possibly still starting)')
    try:
        boot_id = (proc_root / 'sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        boot_id = None
    started_utc = datetime.now(timezone.utc).isoformat()
    started = monotonic()
    warnings = set()
    niri = find_niri(proc_root, pid, warnings)
    samples = []
    error = None
    while True:
        try:
            row = sample(pid, proc_root, sys_root, ticks, identity['start_ticks'], warnings, niri)
        except MeasurementError as failure:
            if not samples:
                raise
            error = str(failure)
            break
        row['elapsed_seconds'] = monotonic() - started
        row['cpu_delta_seconds'] = row['cpu_seconds'] - samples[-1]['cpu_seconds'] if samples else None
        if row['cpu_delta_seconds'] is not None and row['cpu_delta_seconds'] < 0:
            error = 'shell CPU counter decreased; measurement stopped'
            break
        delta = row['elapsed_seconds'] - samples[-1]['elapsed_seconds'] if samples else 0
        row['cpu_percent_one_core'] = 100 * row['cpu_delta_seconds'] / delta if delta > 0 else None
        row['gpu_engine_deltas'] = dict(shell={}, niri={})
        if samples:
            row['gpu_engine_deltas']['shell'] = engine_delta(samples[-1]['drm'], row['drm'], delta, warnings, 'shell')
            previous_niri = samples[-1]['niri']['drm']
            if previous_niri is not None and row['niri']['drm'] is not None:
                row['gpu_engine_deltas']['niri'] = engine_delta(previous_niri, row['niri']['drm'], delta, warnings, 'niri')
        samples.append(row)
        now = monotonic()
        if now - started >= seconds:
            break
        # Keep wall-clock cadence without accumulating the time spent reading
        # rollup/fdinfo. Skip missed deadlines instead of sampling in a burst.
        deadline = min(started + (math.floor((now - started) / interval) + 1) * interval, started + seconds)
        sleep(max(0.0, deadline - now))
    return dict(schema_version=2, pid=pid, executable=executable, niri=niri, start_ticks=identity['start_ticks'],
                boot_id=boot_id, clock_ticks_per_second=ticks, started_utc=started_utc,
                requested_seconds=seconds, interval_seconds=interval, completed=error is None, error=error,
                elapsed_seconds=samples[-1]['elapsed_seconds'], sample_count=len(samples),
                provenance=dict(memory='smaps_rollup/status; per-field sources in samples',
                                cpu='proc stat utime + stime divided by clock_ticks_per_second',
                                drm='proc fdinfo, deduplicated by device/driver/client; aliases not added',
                                drm_ranges='per device: sum(amount - possible_shared) + max(possible_shared) '
                                           'through sum(amount); missing shared assumes all may be shared; '
                                           'resident possible_shared = min(shared, resident); reporting clients only',
                                gpu_engines='drm-engine-* ns; consecutive matched clients only; partial/reset intervals marked',
                                gpu_power='amdgpu hwmon power1_input, else power1_average, microwatts / 1e6; device-wide',
                                battery_power='BAT* power_now or abs(current_now) * voltage_now, confirmed discharge only'),
                warnings=sorted(warnings), summary=summarize(samples), samples=samples)


def print_report(report):
    first, last = report['samples'][0], report['samples'][-1]
    print(f"Shell PID {report['pid']} ({report['executable']}), {report['sample_count']} samples, "
          f"{report['elapsed_seconds']:.2f} s; started {report['started_utc']}")
    print(f"Shell uptime {first['shell_uptime_seconds']:.1f} → {last['shell_uptime_seconds']:.1f} s; "
          f"machine uptime {first['machine_uptime_seconds']:.1f} → {last['machine_uptime_seconds']:.1f} s")
    print(f"{'Metric':<24} {'Min':>12} {'Mean':>12} {'Max':>12} {'Last':>12} {'Samples':>8}")
    for key, title, scale in (
            ('pss_bytes', 'Pss MiB', 1024 ** 2), ('rss_bytes', 'Rss MiB', 1024 ** 2),
            ('pss_anon_bytes', 'Pss_Anon MiB', 1024 ** 2), ('anon_huge_pages_bytes', 'AnonHugePages MiB', 1024 ** 2),
            ('swap_bytes', 'Swap MiB', 1024 ** 2), ('threads', 'Threads', 1),
            ('vram_total_bytes', 'VRAM allocated MiB', 1024 ** 2), ('vram_resident_bytes', 'VRAM resident MiB', 1024 ** 2),
            ('gtt_total_bytes', 'GTT allocated MiB', 1024 ** 2), ('gtt_resident_bytes', 'GTT resident MiB', 1024 ** 2),
            ('power_watts', 'System battery W', 1), ('gpu_power_watts', 'amdgpu device W', 1)):
        row = report['summary'][key]
        values = []
        for name in ('min', 'mean', 'max', 'last'):
            if key in DRM_MEMORY_FIELDS:
                prefix = key.removesuffix('_bytes')
                lower = report['summary'][prefix + '_lower_bytes'][name]
                upper = report['summary'][prefix + '_upper_bytes'][name]
                values.append('n/a' if lower is None or upper is None else f'{lower / scale:.2f}–{upper / scale:.2f}')
            else:
                values.append('n/a' if row[name] is None else f'{row[name] / scale:.2f}')
        print(f'{title:<24} ' + ' '.join(f'{value:>12}' for value in values) + f" {row['samples']:>8}")
    summary = report['summary']
    cpu = summary['cpu_percent_one_core']
    print(f"CPU time delta: {summary['cpu_time_delta_seconds']:.3f} s over {summary['cpu_elapsed_seconds']:.3f} s; "
          + (f'{cpu:.2f}% of one core' if cpu is not None else 'percentage unavailable'))
    for role, engines in summary['gpu_engines'].items():
        for name, value in engines.items():
            busy = 'n/a' if value['delta_ns'] is None else f"{value['delta_ns'] / 1_000_000_000:.6f} s"
            print(f"{role} GPU {name}: {busy} accumulated busy time over {value['covered_seconds']:.3f} s"
                  + (' (partial)' if not value['complete'] else ''))
    for role in ('shell', 'niri'):
        if not summary['gpu_engines'][role]:
            print(role + ' GPU engine times unavailable')
    print('DRM ranges bound unique bytes within reporting clients; missing shared counters widen the range.')
    print('Allocated/resident ranges differ; shared-resident counters are unavailable. JSON retains raw client totals.')
    states = {row['power']['state'] for row in report['samples']}
    if states == {'ac'}:
        print('on AC, no power number (battery); amdgpu device power is independent')
    elif 'ac' in states:
        print('AC/battery state changed; power statistics cover battery samples only')
    elif not summary['power_watts']['samples']:
        print('No battery power number: ' + last['power']['reason'])
    print('Means are arithmetic sample means; n/a = unsupported/unreadable, not zero.')
    for warning in report['warnings']:
        print('Warning: ' + warning)
    if report['error']:
        print('Incomplete: ' + report['error'])


def compare_reports(before, after):
    """Compare sample means and time-normalized engine rates, never raw run totals."""
    metrics = {}
    for key in (*MEMORY_FIELDS, 'threads', 'power_watts', 'gpu_power_watts',
                *(key.removesuffix('_bytes') + '_' + bound + '_bytes'
                  for key in DRM_MEMORY_FIELDS for bound in ('lower', 'upper'))):
        a = before.get('summary', {}).get(key, {}).get('mean')
        b = after.get('summary', {}).get(key, {}).get('mean')
        metrics[key] = dict(before=a, after=b, delta=b - a if a is not None and b is not None else None)
    for key in ('cpu_percent_one_core',):
        a, b = before['summary'].get(key), after['summary'].get(key)
        metrics[key] = dict(before=a, after=b, delta=b - a if a is not None and b is not None else None)
    for role in ('shell', 'niri'):
        a_engines = before.get('summary', {}).get('gpu_engines', {}).get(role, {})
        b_engines = after.get('summary', {}).get('gpu_engines', {}).get(role, {})
        for name in sorted(set(a_engines) | set(b_engines)):
            def rate(engines):
                row = engines.get(name, {})
                if row.get('complete') and row.get('covered_seconds', 0) > 0 and row.get('delta_ns') is not None:
                    return row['delta_ns'] / 1_000_000_000 / row['covered_seconds']
                return None
            a, b = rate(a_engines), rate(b_engines)
            metrics[role + '_gpu_' + name + '_busy_seconds_per_second'] = dict(
                before=a, after=b, delta=b - a if a is not None and b is not None else None)
    return dict(kind='comparison', before_started_utc=before.get('started_utc'),
                after_started_utc=after.get('started_utc'), metrics=metrics,
                note='Deltas compare sample means, CPU percentages and complete GPU busy-time rates; '
                     'DRM endpoint deltas are not bounds on the true unique-memory change.')


def print_comparison(report):
    print(report['note'])
    print(f"{'Metric (bytes unless named)':<48} {'Before':>15} {'After':>15} {'Delta':>15}")
    for name, row in report['metrics'].items():
        values = ['n/a' if row[key] is None else f'{row[key]:.3f}' for key in ('before', 'after', 'delta')]
        print(f'{name:<48} ' + ' '.join(f'{value:>15}' for value in values))


def positive_float(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError('must be finite and greater than zero')
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--seconds', type=positive_float, default=60.0, help='measurement duration (default: 60)')
    parser.add_argument('--interval', type=positive_float, default=1.0, help='sample interval (default: 1 second)')
    parser.add_argument('--json', type=Path, help='write complete samples, provenance and summary as JSON')
    parser.add_argument('--compare', nargs=2, type=Path, metavar=('BEFORE_JSON', 'AFTER_JSON'),
                        help='compare saved reports without reading live processes; --json writes the comparison')
    args = parser.parse_args(argv)
    try:
        if args.compare:
            report = compare_reports(*(json.loads(path.read_text()) for path in args.compare))
            print_comparison(report)
            if args.json:
                args.json.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
            return 0
        report = measure(find_shell_pid(), seconds=args.seconds, interval=args.interval)
        print_report(report)
        if args.json:
            args.json.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
        return 0 if report['completed'] else 1
    except (MeasurementError, OSError, ValueError, KeyError, TypeError) as error:
        print('measure-shell: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
