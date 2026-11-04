#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Decode a bounded first frame before accepting local overview media."""
import json
import subprocess
from pathlib import Path


def inspect(path):
    try:
        if not Path(path).is_file():
            raise ValueError
        probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                                '-show_entries', 'stream=codec_name:format=format_name',
                                '-of', 'json', '-protocol_whitelist', 'file,pipe', path], capture_output=True, timeout=8, check=True)
        metadata = json.loads(probe.stdout)
        if not metadata.get('streams'):
            raise ValueError
        subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-threads', '1', '-filter_threads', '1', '-protocol_whitelist', 'file,pipe', '-i', path,
                        '-map', '0:v:0', '-frames:v', '1', '-f', 'null', '-'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10, check=True)
        formats = metadata.get('format', {}).get('format_name', '').split(',')
        picture = any(name.endswith('_pipe') or name in ('image2', 'gif', 'ico') for name in formats)
        return dict(state='ready', path=path, kind='picture' if picture else 'video')
    except (OSError, ValueError, subprocess.SubprocessError):
        return dict(state='invalid_overview_background')
