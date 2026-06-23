import subprocess
import sys


def test_help_lists_backtest_interval():
    out = subprocess.run(
        [sys.executable, "main.py", "--help"],
        capture_output=True,
        text=True,
        cwd="/root/dsa-intraday",
    )
    assert "--backtest-interval" in (out.stdout + out.stderr)
