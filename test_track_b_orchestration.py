#!/usr/bin/env python3
import csv
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class TrackBOrchestrationTests(unittest.TestCase):
    def test_schedule_is_complete_and_uses_unix_line_endings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            schedule = Path(temp_dir) / "schedule.csv"
            subprocess.run(
                [
                    "python3",
                    str(ROOT / "generate_track_b_schedule.py"),
                    "--seed",
                    "20260904",
                    "--repeats",
                    "3",
                    "--output",
                    str(schedule),
                ],
                check=True,
            )
            raw = schedule.read_bytes()
            self.assertNotIn(b"\r", raw)
            rows = list(csv.DictReader(raw.decode().splitlines()))
            self.assertEqual(len(rows), 84)

    def test_runner_does_not_let_ssh_consume_schedule_stdin(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            bin_dir = temp / "bin"
            bin_dir.mkdir()
            ssh_log = temp / "ssh.log"
            fake_ssh = bin_dir / "ssh"
            fake_ssh.write_text(
                """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$SSH_LOG"
if [[ " $* " != *" -n "* ]]; then
  cat >/dev/null
fi
"""
            )
            fake_ssh.chmod(0o755)
            for command in ("rsync", "sleep", "timeout"):
                path = bin_dir / command
                path.write_text("#!/usr/bin/env bash\nexit 0\n")
                path.chmod(0o755)

            schedule = temp / "schedule.csv"
            subprocess.run(
                [
                    "python3",
                    str(ROOT / "generate_track_b_schedule.py"),
                    "--seed",
                    "20260904",
                    "--repeats",
                    "3",
                    "--output",
                    str(schedule),
                ],
                check=True,
            )
            config = temp / "intervention.env"
            config.write_text(
                "\n".join(
                    [
                        "INTERVENTION_SSH_KEY=/dev/null",
                        "INTERVENTION_USER=root",
                        "INTERVENTION_HOST=example",
                        "INTERVENTION_URL=http://example/v1",
                        "INTERVENTION_METRICS_URL=http://example/metrics",
                        "INTERVENTION_MODEL=model",
                    ]
                )
                + "\n"
            )
            results = temp / "results"
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{bin_dir}:{env['PATH']}",
                    "SSH_LOG": str(ssh_log),
                    "ISOLATION_RESULTS_DIR": str(results),
                    "ISOLATION_INSTALL_DIR": str(ROOT),
                    "ISOLATION_PYTHON": "/bin/true",
                }
            )
            subprocess.run(
                ["bash", str(ROOT / "run_track_b.sh"), str(config), str(schedule)],
                check=True,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(len(list(results.glob("*/COMPLETE"))), 84)
            self.assertTrue(
                all(" -n " in f" {line} " for line in ssh_log.read_text().splitlines())
            )


if __name__ == "__main__":
    unittest.main()
