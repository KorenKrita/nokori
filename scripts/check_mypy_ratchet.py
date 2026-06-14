"""Prevent the set of mypy-loose modules from growing.

Reads pyproject.toml, extracts modules with disallow_untyped_defs = false,
and exits 1 if any module is present that is not in the known baseline.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

KNOWN_LOOSE = {
    "nokori.runtime.*",
    "nokori.gate.*",
    "nokori.cold.*",
    "nokori.web.*",
    "nokori.search.*",
    "nokori.hooks.*",
    "nokori.llm.*",
    "nokori.events.*",
    "nokori.lifecycle.*",
    "nokori.posthoc.*",
    "nokori.extract.*",
    "nokori.commands.*",
    "nokori.archive.*",
    "nokori.merge.*",
    "nokori.config",
    "nokori.config_editor",
    "nokori.utils.*",
}


def main() -> int:
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if not pyproject_path.exists():
        print(f"Mypy ratchet check FAILED: {pyproject_path} not found.")
        return 1
    with pyproject_path.open("rb") as f:
        config = tomllib.load(f)

    mypy_config = config.get("tool", {}).get("mypy", {})
    if not mypy_config:
        print("Mypy ratchet check FAILED: no [tool.mypy] section found in pyproject.toml.")
        print("Is the mypy configuration intact?")
        return 1
    overrides = mypy_config.get("overrides", [])

    # ponytail: only tracks disallow_untyped_defs — other relaxations (warn_return_any,
    # disable_error_code) are not ratcheted yet; expand when those are worth locking.
    current_loose: set[str] = set()
    for override in overrides:
        if override.get("disallow_untyped_defs") == False:  # noqa: E712
            modules = override.get("module", [])
            if isinstance(modules, str):
                modules = [modules]
            current_loose.update(modules)

    new_modules = current_loose - KNOWN_LOOSE
    if new_modules:
        print("Mypy ratchet check FAILED: new loose modules detected:")
        for mod in sorted(new_modules):
            print(f"  - {mod}")
        print("\nTo fix: either add type annotations to the module or add it to")
        print("KNOWN_LOOSE in scripts/check_mypy_ratchet.py (requires justification).")
        return 1

    removed = KNOWN_LOOSE - current_loose
    if removed:
        mods = ", ".join(sorted(removed))
        print(f"::warning file=scripts/check_mypy_ratchet.py::"
              f"Modules tightened but KNOWN_LOOSE not updated: {mods}")
        print(f"\n{len(removed)} module(s) tightened — update KNOWN_LOOSE to lock progress:")
        for mod in sorted(removed):
            print(f"  - {mod}")
        print("Remove these from KNOWN_LOOSE in scripts/check_mypy_ratchet.py.")
        if len(removed) > 3:
            print("\nDrift exceeds threshold (>3). Failing CI to force KNOWN_LOOSE update.")
            return 1

    if removed:
        print("Mypy ratchet check passed (with warnings — update KNOWN_LOOSE).")
    else:
        print("Mypy ratchet check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
