# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Render the shell launcher with an explicit fixture program search path."""
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parent.parent
RENDER = runpy.run_path(str(ROOT / 'scripts/render-paths'))


def shell_wrapper(directory, search_path):
    directory.mkdir(parents=True, exist_ok=True)
    values = RENDER['paths'](system={'TRUSTED_PATH': search_path})
    wrapper = directory / 'emaki-shell'
    wrapper.write_text(RENDER['substitute']((ROOT / 'scripts/emaki-shell').read_text(), values))
    wrapper.chmod(0o755)
    (directory / 'emaki_paths.py').write_text(RENDER['modules'](values)[0])
    for name in ('emaki-shell-health', 'emaki-session-files', 'emaki-shell-trusted'):
        (directory / name).symlink_to(ROOT / 'scripts' / name)
    return wrapper
