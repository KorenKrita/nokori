"""Run all local verification checks sequentially, stopping on first failure."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

CHECKS = [
    ("ruff check", [sys.executable, "-m", "ruff", "check", "nokori/"]),
    ("mypy", [sys.executable, "-m", "mypy", "nokori/"]),
    ("mypy ratchet", [sys.executable, "scripts/check_mypy_ratchet.py"]),
    ("pytest", [sys.executable, "-m", "pytest", "tests/", "-q"]),
]


def main() -> int:
    for name, cmd in CHECKS:
        print(f"\n{'=' * 60}")
        print(f"  {name}")
        print(f"{'=' * 60}\n")
        try:
            result = subprocess.run(cmd, cwd=PROJECT_ROOT)
        except FileNotFoundError:
            print(f"\nFAILED: {name} — command not found: {cmd[0]}")
            return 1
        if result.returncode != 0:
            print(f"\nFAILED: {name} (exit {result.returncode})")
            return 1
    print(f"\n{'=' * 60}")
    print("  All checks passed.")
    print(f"{'=' * 60}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
