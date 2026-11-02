import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNNER = PROJECT_ROOT / "scripts" / "run_with_timestamped_logs.py"


class TimestampedLogRunnerTest(unittest.TestCase):
    def test_stdout_and_stderr_receive_timestamp_prefixes(self) -> None:
        with TemporaryDirectory() as directory:
            stdout_path = Path(directory) / "stdout.log"
            stderr_path = Path(directory) / "stderr.log"
            result = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "--stdout-log",
                    str(stdout_path),
                    "--stderr-log",
                    str(stderr_path),
                    "--",
                    sys.executable,
                    "-c",
                    "import sys; print('normal'); print('warning', file=sys.stderr)",
                ],
                check=False,
                timeout=10,
            )
            stdout = stdout_path.read_text(encoding="utf-8")
            stderr = stderr_path.read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0)
        self.assertRegex(stdout, r"\[\d{4}-\d{2}-\d{2}T.*\] normal")
        self.assertRegex(stderr, r"\[\d{4}-\d{2}-\d{2}T.*\] warning")
