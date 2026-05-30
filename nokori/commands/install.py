from __future__ import annotations

import argparse
import copy
import difflib
import json
import os
import shutil
import sys
from pathlib import Path

from ..config import Config
from ..constants import DEFAULT_GATE_MATCHER

NOKORI_MARKER = "nokori"

_HOOK_SPECS = (
    ("SessionStart",   "*",                                              "session-start",     5),
    ("UserPromptSubmit", "*",                                            "user-prompt-submit", 10),
    ("PreToolUse",     DEFAULT_GATE_MATCHER,                             "pre-tool-use",      5),
    ("SessionEnd",     "*",                                              "session-end",       5),
)


def _settings_path() -> Path:
    base = os.environ.get("NOKORI_CLAUDE_HOME")
    if base:
        return Path(base).expanduser() / "settings.json"
    return Path("~/.claude/settings.json").expanduser()


def _build_command() -> str:
    nokori = shutil.which("nokori")
    if nokori:
        return f"{nokori} hook"
    return f"{sys.executable} -m nokori hook"


def _build_hook_entry(matcher: str, command: str, event_arg: str, timeout: int) -> dict:
    return {
        "matcher": matcher,
        "hooks": [
            {
                "type": "command",
                "command": f"{command} {event_arg}",
                "timeout": timeout,
            }
        ],
    }


def _is_nokori(entry: dict) -> bool:
    for h in entry.get("hooks", []):
        cmd = h.get("command", "")
        if NOKORI_MARKER in cmd or "nokori" in cmd:
            return True
    return False


def _strip_nokori(spec: list) -> list:
    return [entry for entry in spec if not _is_nokori(entry)]


def _merge_settings(current: dict, command: str) -> dict:
    merged = copy.deepcopy(current) if current else {}
    hooks = merged.setdefault("hooks", {})
    for event, matcher, event_arg, timeout in _HOOK_SPECS:
        spec = list(hooks.get(event) or [])
        spec = _strip_nokori(spec)
        spec.append(_build_hook_entry(matcher, command, event_arg, timeout))
        hooks[event] = spec
    return merged


def _remove_nokori_settings(current: dict) -> dict:
    if not current:
        return {}
    merged = copy.deepcopy(current)
    hooks = merged.get("hooks", {})
    for event in [e for e, *_ in _HOOK_SPECS]:
        spec = hooks.get(event)
        if not spec:
            continue
        cleaned = _strip_nokori(spec)
        if cleaned:
            hooks[event] = cleaned
        else:
            hooks.pop(event, None)
    if hooks == {}:
        merged.pop("hooks", None)
    return merged


def _read_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"cannot read {path}: {e}") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"{path} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object at the top level")
    return data


def _write_settings(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _diff(before: dict, after: dict, path: Path) -> str:
    a = json.dumps(before, indent=2, ensure_ascii=False, sort_keys=True).splitlines(keepends=True)
    b = json.dumps(after, indent=2, ensure_ascii=False, sort_keys=True).splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(a, b, fromfile=str(path), tofile=f"{path} (proposed)")
    )


def _set_env_flag(data: dict, key: str, value: str | None) -> None:
    env = data.setdefault("env", {})
    if value is None:
        env.pop(key, None)
        if not env:
            data.pop("env", None)
    else:
        env[key] = value


def run(args: argparse.Namespace, cfg: Config) -> int:
    path = _settings_path()
    try:
        before = _read_settings(path)
    except ValueError as e:
        print(f"nokori: {e}", file=sys.stderr)
        return 1
    command = _build_command()

    if args.uninstall:
        after = _remove_nokori_settings(before)
        verb = "uninstall"
    elif args.disable:
        after = copy.deepcopy(before) or {}
        _set_env_flag(after, "NOKORI_DISABLED", "1")
        verb = "disable"
    elif args.enable:
        after = copy.deepcopy(before) or {}
        _set_env_flag(after, "NOKORI_DISABLED", None)
        verb = "enable"
    else:
        after = _merge_settings(before, command)
        verb = "install"

    if args.dry_run:
        diff = _diff(before, after, path)
        if not diff:
            print(f"({verb}) no changes needed")
            return 0
        sys.stdout.write(diff)
        if not diff.endswith("\n"):
            sys.stdout.write("\n")
        return 0

    if before == after:
        print(f"({verb}) no changes needed")
        return 0

    _write_settings(path, after)
    print(f"({verb}) wrote {path}")
    if verb == "install":
        print(
            "Gate has two layers: settings.json PreToolUse matcher (when hook runs) "
            "vs config.toml NOKORI_GATE_MATCHER (block inside hook). See README."
        )
    return 0
