from __future__ import annotations

import argparse
import shutil
import sys

from ..config import Config


def run(_args: argparse.Namespace, cfg: Config) -> int:
    target = cfg.data_dir
    if not target.exists():
        print(f"(nothing to reset; {target} does not exist)")
        return 0
    print(f"This will permanently delete {target} (all rules, logs, jobs).")
    print("Type 'yes' to confirm: ", end="", flush=True)
    answer = sys.stdin.readline().strip()
    if answer != "yes":
        print("aborted")
        return 1
    shutil.rmtree(target)
    cfg.ensure_dirs()
    print(f"reset {target}")
    return 0
