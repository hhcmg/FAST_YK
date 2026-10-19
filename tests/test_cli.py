"""Keep the user's existing exit-status behavior for failed downloads."""

import subprocess
import sys
import unittest
from pathlib import Path


class ExitStatusTests(unittest.TestCase):
    def test_failed_batch_still_exits_zero_after_printing_summary(self):
        script = """
import runpy
import subprocess
import sys
import tempfile
from types import SimpleNamespace

# The chosen WHU product does not load CDDIS credentials. Every external
# command is replaced before the original script entry point is executed.
subprocess.run = lambda *args, **kwargs: SimpleNamespace(returncode=1)
with tempfile.TemporaryDirectory() as output:
    sys.argv = ['download.py', 'whu', '2025', '98', '2025', '98',
                '--workers', '1', '--path-mode', 'exact', '--output-dir', output]
    runpy.run_path('download.py', run_name='__main__')
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("应下文件个数: 1", result.stdout)
        self.assertIn("下载失败个数: 1", result.stdout)
        self.assertIn("Failed Targets", result.stdout)


if __name__ == "__main__":
    unittest.main()
