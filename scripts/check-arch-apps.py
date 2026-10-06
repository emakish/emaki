#!/usr/bin/env python3
"""Check Rich package names and estimate added offline bytes using Arch's API.

This is a size estimate, not the ISO builder's authoritative pacman transaction.
Only core/extra x86_64/any packages and required dependencies are counted.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import urllib.parse
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parent.parent
API = 'https://archlinux.org/packages/search/json/'


def fetch(query):
    url = API + '?' + urllib.parse.urlencode(query, doseq=True)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise


def dependencies(recipe):
    result = subprocess.run(['bash', '-eu', '-c',
                             'startdir=$1; source "$1/PKGBUILD"; printf "%s\\n" "${depends[@]}"',
                             '_', str(recipe.parent)], check=True, capture_output=True, text=True)
    return [line for line in result.stdout.splitlines() if line]


def main():
    query = {'repo': ['Core', 'Extra'], 'arch': ['x86_64', 'any']}
    first = fetch(query)
    records = first['results']
    with ThreadPoolExecutor(max_workers=4) as pool:
        for page in pool.map(lambda n: fetch(dict(query, page=n)), range(2, first['num_pages'] + 1)):
            records.extend(page['results'])
    packages = {p['pkgname']: p for p in records}
    local = {p.parent.name: dependencies(p) for p in ROOT.glob('packaging/*/PKGBUILD')}
    providers = {}
    for p in records:
        for dep in p['provides']:
            providers.setdefault(re.split(r'[<>=]', dep)[0], []).append(p['pkgname'])
    choices = {}
    replacements = {'xdg-desktop-portal-gnome': 'xdg-desktop-portal-gnome-emaki'}

    def closure(seeds):
        seen, pending = set(), sorted(seeds, reverse=True)
        while pending:
            dep = re.split(r'[<>=]', pending.pop())[0]
            dep = replacements.get(dep, dep)
            if dep in local:
                if dep not in seen:
                    seen.add(dep)
                    pending.extend(local[dep])
                continue
            if dep not in packages:
                options = sorted(providers.get(dep, []))
                if not options:
                    raise ValueError(f'No current repository provider for {dep}')
                dep = next((p for p in options if p in seen or p in pending), options[0])
                choices[dep] = options
            if dep in seen:
                continue
            seen.add(dep)
            pending.extend(packages[dep]['depends'])
        return seen

    direct = sorted(dep for dep in local['emaki-apps'] if dep not in local)
    missing = set(direct) - packages.keys()
    if missing:
        raise ValueError(f'Unknown Rich package names: {sorted(missing)}')
    seeds = set((ROOT / 'iso/profile/packages.x86_64').read_text().split())
    seeds.update((ROOT / 'iso/target-packages.txt').read_text().split())
    seeds.discard('emaki-apps')
    baseline = closure(seeds)
    rich = closure(seeds | {'emaki-apps'})
    if 'nautilus' in rich:
        raise ValueError('Nautilus is forbidden in the target closure')
    added = rich - baseline - local.keys()
    fields = ('pkgname', 'repo', 'arch', 'pkgver', 'pkgrel', 'compressed_size', 'installed_size')
    report = {
        'checked_at': datetime.now(timezone.utc).isoformat(), 'source': API,
        'method': 'Current core/extra API required-dependency closure; virtual providers chosen deterministically. Pacman build resolution remains authoritative.',
        'direct': [{k: packages[name][k] for k in fields} for name in direct],
        'minimal': [{k: packages[name][k] for k in fields} for name in ('dolphin', 'firefox', 'kitty')],
        'infrastructure': [{k: packages[name][k] for k in (*fields, 'depends')}
                           for name in ('gnome-keyring', 'polkit-gnome', 'xdg-desktop-portal-gnome',
                                        'xdg-desktop-portal-gtk')],
        'local_portal': {'name': 'xdg-desktop-portal-gnome-emaki',
                         'depends': local['xdg-desktop-portal-gnome-emaki']},
        'live_and_target_closure_names': sorted(rich),
        'retained_gnome_apps': sorted(rich & {'nautilus', 'loupe', 'file-roller', 'papers'}),
        'direct_compressed_bytes': sum(packages[n]['compressed_size'] for n in direct),
        'added_offline_compressed_bytes': sum(packages[n]['compressed_size'] for n in added),
        'added': [{k: packages[name][k] for k in fields} for name in sorted(added)],
        'virtual_provider_choices': choices,
    }
    target = ROOT / 'packaging/emaki-apps/arch-packages.json'
    target.write_text(json.dumps(report, indent=2) + '\n')
    print(f'PASS: {len(direct)} Rich package names in current core/extra')
    print(f'Direct downloads: {report["direct_compressed_bytes"]} bytes')
    print(f'Added offline closure: {len(added)} packages, {report["added_offline_compressed_bytes"]} bytes')
    print(f'Retained transitive GNOME apps: {report["retained_gnome_apps"]}')


if __name__ == '__main__':
    main()
