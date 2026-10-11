# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Resolve live ISO devices to physical media without mounting anything."""
import json
from pathlib import Path


from .constants import BOOT_MOUNT
COLUMNS = 'PATH,KNAME,TYPE,PKNAME,MAJ:MIN,RM,TRAN'


def device_path(name):
    return name if name.startswith('/dev/') else '/dev/' + name


class BootMedia:
    def __init__(self, run, nodes, *, root=Path('/'), sysfs_root=None):
        self.run = run
        self.root = Path(root)
        self.sysfs = Path(sysfs_root) if sysfs_root is not None else self.root / 'sys'
        self.nodes, self.parents, self.aliases = {}, {}, {}

        def walk(rows, parent=None):
            for node in rows:
                path = device_path(node.get('path') or node.get('kname') or node.get('name') or '')
                self.nodes[path] = node
                if parent:
                    self.parents.setdefault(path, set()).add(parent)
                if node.get('pkname'):
                    self.parents.setdefault(path, set()).add(device_path(node['pkname']))
                self.aliases[path] = path
                if node.get('kname'):
                    self.aliases[device_path(node['kname'])] = path
                walk(node.get('children', []), path)

        walk(nodes)
        self.numbers = {node['maj:min']: path for path, node in self.nodes.items()
                        if node.get('maj:min')}

    def mounted_source(self, *, target=None):
        option, path = ('--target', target) if target is not None else ('--mountpoint', str(BOOT_MOUNT))
        try:
            data = json.loads(self.run(['findmnt', '--json', option, path,
                                        '--output', 'SOURCE,MAJ:MIN']))
            mounts = data.get('filesystems', [])
            if len(mounts) != 1:
                return None
            source = mounts[0].get('source', '').split('[', 1)[0]
            return self.numbers.get(mounts[0].get('maj:min')) or self.aliases.get(source, '')
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError):
            return None

    def physical(self, source, visited=frozenset()):
        """Every branch must reach a disk or optical drive; partial knowledge is unsafe."""
        source = self.aliases.get(source, source)
        if source not in self.nodes or source in visited or len(visited) >= 16:
            return set()
        node = self.nodes[source]
        visited = visited | {source}
        if node.get('type') in {'disk', 'rom'}:
            return {source}
        number = node.get('maj:min')
        sysnode = (self.sysfs / 'dev/block' / number if number else
                   self.sysfs / 'class/block' / Path(node.get('kname') or source).name)
        if node.get('type') == 'loop':
            try:
                backing = (sysnode / 'loop/backing_file').read_text().strip()
            except OSError:
                return set()
            if not backing.startswith('/'):
                return set()
            return self.physical(self.mounted_source(target=backing), visited)
        parents = set(self.parents.get(source, ()))
        # A mapper can be listed separately from its disk. Kernel dependencies
        # also cover multiple slaves; resolving only one would permit data loss.
        try:
            parents.update(device_path(entry.name) for entry in (sysnode / 'slaves').iterdir())
        except OSError:
            pass
        if not parents:
            return set()
        disks = set()
        for parent in parents:
            resolved = self.physical(parent, visited)
            if not resolved:
                return set()
            disks.update(resolved)
        return disks

    def removable(self, sources):
        return any(self.nodes[path].get('rm') in (True, 1, '1')
                   or self.nodes[path].get('tran') == 'usb' for path in sources)
