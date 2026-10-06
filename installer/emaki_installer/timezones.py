"""Validated live-session time zone changes; never used for target configuration."""
from datetime import datetime
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from .errors import Code, require


def validate_timezone(name, root=Path('/usr/share/zoneinfo')):
    require(isinstance(name, str) and len(name) <= 128
            and re.fullmatch(r'[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*', name),
            Code.BAD_CONFIG, 'Invalid time zone.')
    names = {'UTC'}
    for table in ('zone1970.tab', 'zone.tab'):
        path = root / table
        if path.is_file():
            names.update(line.split()[2] for line in path.read_text().splitlines()
                         if line and not line.startswith('#') and len(line.split()) >= 3)
    require(name in names, Code.BAD_CONFIG, 'Choose a time zone from the city list.')
    path = (root / name).resolve()
    require(path.is_relative_to(root.resolve()) and path.is_file(),
            Code.BAD_CONFIG, 'Time zone data is unavailable.')
    with path.open('rb') as stream:
        require(stream.read(4) == b'TZif', Code.BAD_CONFIG, 'Invalid time zone data.')
        stream.seek(0)
        return ZoneInfo.from_file(stream, key=name)


def set_live_timezone(name, runner, *, root=Path('/usr/share/zoneinfo'),
                      boot=Path('/run/archiso/bootmnt'),
                      release=Path('/etc/os-release')):
    # Check at each request, not just daemon startup. timedated changes the live
    # /etc/localtime through the system bus, outside the worker's mount namespace.
    require(os.geteuid() == 0 and boot.is_dir() and release.is_file()
            and 'IMAGE_ID=emaki' in release.read_text().splitlines(),
            Code.BAD_REQUEST, 'Live clock changes are available only on the Emaki ISO.')
    zone = validate_timezone(name, root)
    runner.run(['timedatectl', 'set-timezone', name])
    now = datetime.now(zone)
    return dict(timezone=name, unix_ms=int(now.timestamp() * 1000),
                offset_seconds=int(now.utcoffset().total_seconds()),
                abbreviation=now.tzname())
