# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""A stopped real worker leaves scratch data for its renderer to remove."""
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import render


class RenderCleanup(unittest.TestCase):
    def test_stopped_worker_cleanup_on_success_and_timeout(self):
        evidence = render.ROOT / '.cache/evidence'
        evidence.mkdir(parents=True, exist_ok=True)
        for timeout in (False, True):
            with self.subTest(timeout=timeout), tempfile.TemporaryDirectory(dir=evidence) as temporary:
                output = Path(temporary)
                unrelated = output / 'keep'
                unrelated.write_text('unrelated render data')
                worker_paths = []
                renderer_paths = []

                def run_window(command, *, env, **kwargs):
                    renderer_paths.append(Path(env['TMPDIR']))
                    with subprocess.Popen(
                        [sys.executable, str(render.UI / 'tests/real-worker.py')],
                        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    ) as worker:
                        try:
                            worker.stdin.write(b'{"type":"hello","id":1}\n')
                            worker.stdin.flush()
                            with selectors.DefaultSelector() as ready:
                                ready.register(worker.stdout, selectors.EVENT_READ)
                                self.assertTrue(ready.select(timeout=10), 'Worker did not reply')
                            self.assertTrue(worker.stdout.readline(), 'Worker exited before replying')
                            worker_paths.extend(renderer_paths[-1].glob('emaki-real-worker-*'))
                            self.assertTrue(worker_paths, 'Worker scratch escaped renderer ownership')
                        finally:
                            worker.terminate()
                            worker.communicate(timeout=10)
                    self.assertTrue(all(path.exists() for path in worker_paths))
                    if timeout:
                        raise subprocess.TimeoutExpired(command, 15)
                    return subprocess.CompletedProcess(command, 0, 'SCREENSHOT_OK error-real\nLAYOUT_OK\n', '')

                with patch.object(sys, 'argv', ['render.py', '--screen', 'error-real',
                                               '--repo-shell', '--output', str(output)]), \
                        patch.object(render.subprocess, 'run', side_effect=run_window), \
                        patch.object(render, 'validate_frame'):
                    if timeout:
                        with self.assertRaises(subprocess.TimeoutExpired):
                            render.main()
                    else:
                        render.main()
                self.assertTrue(worker_paths)
                self.assertTrue(all(not path.exists() for path in worker_paths + renderer_paths))
                self.assertEqual(unrelated.read_text(), 'unrelated render data')


if __name__ == '__main__':
    unittest.main()
