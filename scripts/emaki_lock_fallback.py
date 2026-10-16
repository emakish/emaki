# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Observe fallback main-loop progress without changing the person's config."""
import os
from pathlib import Path
import stat
import tempfile


class FallbackHeartbeat:
    """Keep the generated config and its inherited source descriptor alive per child.

    The caller supplies the config already selected by the ordinary precedence rules.
    A changed marker proves progress only; compositor lock confirmation is separate.
    """

    def __init__(self, config_path):
        self._original_fd = -1
        self.pass_fds = ()
        self._directory = tempfile.TemporaryDirectory(prefix='emaki-lock-heartbeat-', dir='/tmp')
        try:
            # Reject special files without blocking on an accidentally selected FIFO.
            self._original_fd = os.open(config_path, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK)
            if not stat.S_ISREG(os.fstat(self._original_fd).st_mode):
                raise OSError('fallback config is not a regular file')
            self.pass_fds = (self._original_fd,)
            directory = Path(self._directory.name)
            self._heartbeat_path = directory / 'heartbeat'
            self._heartbeat_path.touch(mode=0o600, exist_ok=False)
            self._stamp = self._marker_stamp()
            self.config_path = directory / 'hyprlock.conf'
            # Defer descriptor canonicalization until after source's glob expansion.
            # Its second canonicalization then restores the original parent directory
            # for relative includes, without interpreting its filename as a pattern.
            descriptor = str(self._original_fd)
            source = '/proc/self/fd/' + descriptor[:-1] + '[' + descriptor[-1] + ']'
            # Every command-path character comes from the fixed ASCII temporary prefix
            # and tempfile's generated suffix, never the source config or environment.
            command = f'touch -- {self._heartbeat_path}; printf .'
            with self.config_path.open('x', encoding='utf-8') as stream:
                os.chmod(self.config_path, 0o600)
                stream.write(
                    f'source = {source}\n'
                    'label {\n'
                    '    monitor =\n'
                    '    color = rgba(00000000)\n'
                    '    font_size = 1\n'
                    '    shadow_passes = 0\n'
                    f'    text = cmd[update:500] {command}\n'
                    '}\n'
                )
        except BaseException:
            self.close()
            raise

    def _marker_stamp(self):
        marker = self._heartbeat_path.stat()
        return marker.st_mtime_ns, marker.st_ctime_ns

    def progressed(self):
        """Return true once per newly observed timestamp, never for the initial seed."""
        if self._original_fd < 0:
            return False
        try:
            stamp = self._marker_stamp()
        except OSError:
            return False
        if stamp == self._stamp:
            return False
        self._stamp = stamp
        return True

    def close(self):
        """Release the inherited source and generation-specific runtime files."""
        if self._original_fd >= 0:
            os.close(self._original_fd)
            self._original_fd = -1
        self.pass_fds = ()
        self._directory.cleanup()
