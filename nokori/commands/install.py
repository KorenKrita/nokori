from __future__ import annotations

import argparse
import contextlib
import copy
import difflib
import json
import os
import sys
from pathlib import Path

from ..config import Config
from ..constants import CURSOR_GATE_MATCHER, DEFAULT_GATE_MATCHER

NOKORI_MARKER = "nokori"

_CURSOR_DISABLE_HINT = (
    "Cursor hooks unchanged. To stop Nokori in Cursor: nokori install --uninstall --cursor"
)

_BOTH_TARGETS_NOTE = """\
Installed for BOTH Claude Code and native Cursor hooks.
  Do not also enable Cursor Settings → Import hooks from Claude Code for the same
  events, and turn OFF project-level hooks imported from .claude in this repo.
  Otherwise nokori may run twice (double inject / gate / extract).
  See README: "Using Nokori in Cursor".
"""

_CLAUDE_HOOK_SPECS = (
    ("SessionStart", "*", "session-start", 5),
    ("UserPromptSubmit", "*", "user-prompt-submit", 10),
    ("PreToolUse", DEFAULT_GATE_MATCHER, "pre-tool-use", 5),
    ("SessionEnd", "*", "session-end", 5),
)

# Cursor native hooks.json (https://cursor.com/docs — agent hook events)
_CURSOR_HOOK_SPECS = (
    ("sessionStart", None, "session-start", 5),
    # No matcher: Cursor runs beforeSubmitPrompt for every send (matcher is optional).
    ("beforeSubmitPrompt", None, "user-prompt-submit", 10),
    ("preToolUse", CURSOR_GATE_MATCHER, "pre-tool-use", 5),
    ("sessionEnd", None, "session-end", 5),
)


def resolve_install_targets(
    args: argparse.Namespace,
    *,
    uninstall: bool = False,
) -> tuple[bool, bool]:
    """Default: Claude only. --all: both. Uninstall with no flags: both."""
    if getattr(args, "all_platforms", False):
        return True, True
    claude = bool(getattr(args, "claude", False))
    cursor = bool(getattr(args, "cursor", False))
    if not claude and not cursor:
        if uninstall:
            return True, True
        return True, False
    return claude, cursor


def _settings_path() -> Path:
    base = os.environ.get("NOKORI_CLAUDE_HOME")
    if base:
        return Path(base).expanduser() / "settings.json"
    return Path("~/.claude/settings.json").expanduser()


def _cursor_hooks_path() -> Path:
    base = os.environ.get("NOKORI_CURSOR_HOME")
    if base:
        return Path(base).expanduser() / "hooks.json"
    return Path("~/.cursor/hooks.json").expanduser()


def _build_command() -> str:
    # -I: ignore PYTHONPATH / cwd so hooks always use the installed package
    # (avoids shadowing by a repo-local ``nokori/`` when cwd is the project).
    import shlex

    return f"{shlex.quote(sys.executable)} -I -m nokori hook"


def _build_claude_hook_entry(matcher: str, command: str, event_arg: str, timeout: int) -> dict:
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


def _build_cursor_hook_entry(
    matcher: str | None,
    command: str,
    event_arg: str,
    timeout: int,
) -> dict:
    entry: dict = {
        "command": f"{command} {event_arg}",
        "timeout": timeout,
    }
    if matcher:
        entry["matcher"] = matcher
    return entry


def _is_nokori_claude(entry: dict) -> bool:
    for h in entry.get("hooks", []):
        cmd = h.get("command", "")
        if NOKORI_MARKER in cmd:
            return True
    return False


def _is_nokori_cursor(entry: dict) -> bool:
    cmd = entry.get("command", "")
    return NOKORI_MARKER in cmd or "nokori" in cmd


def _strip_nokori_claude(spec: list) -> list:
    return [entry for entry in spec if not _is_nokori_claude(entry)]


def _strip_nokori_cursor(spec: list) -> list:
    return [entry for entry in spec if not _is_nokori_cursor(entry)]


def _merge_claude_settings(current: dict, command: str) -> dict:
    merged = copy.deepcopy(current) if current else {}
    hooks = merged.setdefault("hooks", {})
    for event, matcher, event_arg, timeout in _CLAUDE_HOOK_SPECS:
        raw = hooks.get(event)
        spec = list(raw) if isinstance(raw, list) else []
        spec = _strip_nokori_claude(spec)
        spec.append(_build_claude_hook_entry(matcher, command, event_arg, timeout))
        hooks[event] = spec
    return merged


def _merge_cursor_hooks(current: dict, command: str) -> dict:
    merged = copy.deepcopy(current) if current else {}
    merged.setdefault("version", 1)
    hooks = merged.setdefault("hooks", {})
    for event, matcher, event_arg, timeout in _CURSOR_HOOK_SPECS:
        raw = hooks.get(event)
        spec = list(raw) if isinstance(raw, list) else []
        spec = _strip_nokori_cursor(spec)
        spec.append(_build_cursor_hook_entry(matcher, command, event_arg, timeout))
        hooks[event] = spec
    return merged


def _remove_nokori_claude(current: dict) -> dict:
    if not current:
        return {}
    merged = copy.deepcopy(current)
    hooks = merged.get("hooks", {})
    if not isinstance(hooks, dict):
        merged.pop("hooks", None)
        return merged
    for event in [e for e, *_ in _CLAUDE_HOOK_SPECS]:
        spec = hooks.get(event)
        if not spec:
            continue
        cleaned = _strip_nokori_claude(spec)
        if cleaned:
            hooks[event] = cleaned
        else:
            hooks.pop(event, None)
    if hooks == {}:
        merged.pop("hooks", None)
    return merged


def _remove_nokori_cursor(current: dict) -> dict:
    if not current:
        return {}
    merged = copy.deepcopy(current)
    hooks = merged.get("hooks", {})
    if not isinstance(hooks, dict):
        merged.pop("hooks", None)
        return merged
    for event in [e for e, *_ in _CURSOR_HOOK_SPECS]:
        spec = hooks.get(event)
        if not spec:
            continue
        cleaned = _strip_nokori_cursor(spec)
        if cleaned:
            hooks[event] = cleaned
        else:
            hooks.pop(event, None)
    if hooks == {}:
        merged.pop("hooks", None)
    if merged == {"version": 1}:
        return {}
    return merged


def _env_flag_is_truthy(env: dict, key: str) -> bool:
    val = env.get(key)
    if val is None:
        return False
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _claude_has_nokori_hooks(hooks: dict) -> bool:
    needed = ("SessionStart", "UserPromptSubmit", "PreToolUse", "SessionEnd")
    for evt in needed:
        spec = hooks.get(evt) or []
        if not any(
            isinstance(entry, dict)
            and any("nokori" in (h.get("command", "")) for h in entry.get("hooks", []))
            for entry in spec
        ):
            return False
    return True


def _cursor_has_nokori_hooks(hooks: dict) -> bool:
    needed = ("sessionStart", "beforeSubmitPrompt", "preToolUse", "sessionEnd")
    for evt in needed:
        spec = hooks.get(evt) or []
        if not any(
            isinstance(entry, dict) and "nokori" in entry.get("command", "") for entry in spec
        ):
            return False
    return True


def describe_claude_hooks() -> dict[str, object]:
    """Installed/disabled state for ~/.claude/settings.json (Nokori hooks + env)."""
    path = _settings_path()
    if not path.exists():
        return {
            "path": str(path),
            "installed": False,
            "disabled": False,
            "note": "settings.json missing",
        }
    try:
        data = _read_json_file(path)
    except ValueError as e:
        return {
            "path": str(path),
            "installed": False,
            "disabled": False,
            "note": str(e),
        }
    hooks = data.get("hooks") or {}
    env = data.get("env") or {}
    return {
        "path": str(path),
        "installed": _claude_has_nokori_hooks(hooks),
        "disabled": _env_flag_is_truthy(env, "NOKORI_DISABLED"),
        "note": "",
    }


def describe_dual_hook_registration() -> dict[str, object]:
    """True when both Claude settings and native Cursor hooks register nokori."""
    claude = describe_claude_hooks()
    cursor = describe_cursor_hooks()
    both = bool(claude.get("installed")) and bool(cursor.get("installed"))
    return {
        "both_installed": both,
        "note": (
            "Both ~/.claude/settings.json and ~/.cursor/hooks.json register "
            "nokori; hook coalesce suppresses duplicate work at runtime. "
            "Prefer one path: Claude import OR nokori install --cursor."
            if both
            else ""
        ),
    }


def describe_cursor_hooks() -> dict[str, object]:
    """Installed state for ~/.cursor/hooks.json (no --disable; use uninstall)."""
    path = _cursor_hooks_path()
    if not path.exists():
        return {
            "path": str(path),
            "installed": False,
            "note": "hooks.json missing",
        }
    try:
        data = _read_json_file(path)
    except ValueError as e:
        return {
            "path": str(path),
            "installed": False,
            "note": str(e),
        }
    hooks = data.get("hooks") or {}
    return {
        "path": str(path),
        "installed": _cursor_has_nokori_hooks(hooks),
        "note": "",
    }


def _read_json_file(path: Path) -> dict:
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


def _write_json_file(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise ValueError(f"cannot write {path}: {e}") from e


def _print_both_targets_note() -> None:
    print(_BOTH_TARGETS_NOTE.rstrip())


def _diff(before: dict, after: dict, path: Path) -> str:
    a = json.dumps(before, indent=2, ensure_ascii=False, sort_keys=True).splitlines(keepends=True)
    b = json.dumps(after, indent=2, ensure_ascii=False, sort_keys=True).splitlines(keepends=True)
    return "".join(difflib.unified_diff(a, b, fromfile=str(path), tofile=f"{path} (proposed)"))


def _set_env_flag(data: dict, key: str, value: str | None) -> None:
    env = data.setdefault("env", {})
    if value is None:
        env.pop(key, None)
        if not env:
            data.pop("env", None)
    else:
        env[key] = value


def _install_one_target(
    *,
    label: str,
    path: Path,
    before: dict,
    after: dict,
    dry_run: bool,
    verb: str,
) -> bool:
    """Return True if file content changed."""
    if dry_run:
        diff = _diff(before, after, path)
        if diff:
            print(f"--- {label} ({path}) ---")
            sys.stdout.write(diff)
            if not diff.endswith("\n"):
                sys.stdout.write("\n")
        else:
            print(f"({verb}) {label}: no changes needed")
        return before != after

    if before == after:
        print(f"({verb}) {label}: no changes needed")
        return False
    _write_json_file(path, after)
    print(f"({verb}) {label}: wrote {path}")
    return True


def run(args: argparse.Namespace, cfg: Config) -> int:
    claude_target, cursor_target = resolve_install_targets(
        args,
        uninstall=bool(args.uninstall),
    )
    command = _build_command()

    claude_path = _settings_path()
    cursor_path = _cursor_hooks_path()

    try:
        claude_before = _read_json_file(claude_path) if claude_target else {}
        cursor_before = _read_json_file(cursor_path) if cursor_target else {}
    except ValueError as e:
        print(f"nokori: {e}", file=sys.stderr)
        return 1

    if args.uninstall:
        claude_after = _remove_nokori_claude(claude_before) if claude_target else claude_before
        cursor_after = _remove_nokori_cursor(cursor_before) if cursor_target else cursor_before
        verb = "uninstall"
    elif args.disable:
        if not claude_target:
            print(
                "nokori: --disable only sets NOKORI_DISABLED in "
                "~/.claude/settings.json (Claude Code).\n"
                "  Cursor is not affected. To remove Cursor hooks:\n"
                "    nokori install --uninstall --cursor",
                file=sys.stderr,
            )
            return 1
        claude_after = copy.deepcopy(claude_before) or {}
        _set_env_flag(claude_after, "NOKORI_DISABLED", "1")
        cursor_after = cursor_before
        verb = "disable"
    elif args.enable:
        if not claude_target:
            print(
                "nokori: --enable only clears NOKORI_DISABLED in "
                "~/.claude/settings.json (Claude Code).\n"
                "  Cursor hooks are unchanged.",
                file=sys.stderr,
            )
            return 1
        claude_after = copy.deepcopy(claude_before) or {}
        _set_env_flag(claude_after, "NOKORI_DISABLED", None)
        cursor_after = cursor_before
        verb = "enable"
    else:
        claude_after = (
            _merge_claude_settings(claude_before, command) if claude_target else claude_before
        )
        cursor_after = (
            _merge_cursor_hooks(cursor_before, command) if cursor_target else cursor_before
        )
        verb = "install"

    dry_run = bool(args.dry_run)
    any_change = False

    if claude_target:
        any_change |= _install_one_target(
            label="Claude Code",
            path=claude_path,
            before=claude_before,
            after=claude_after,
            dry_run=dry_run,
            verb=verb,
        )
    if cursor_target:
        any_change |= _install_one_target(
            label="Cursor",
            path=cursor_path,
            before=cursor_before,
            after=cursor_after,
            dry_run=dry_run,
            verb=verb,
        )

    if verb == "disable" and claude_target and not dry_run:
        print(
            "(disable) Claude: set NOKORI_DISABLED=1 in settings.json env "
            "(hooks remain registered)."
        )
        if describe_cursor_hooks().get("installed"):
            print(f"(disable) {_CURSOR_DISABLE_HINT}")
    elif verb == "enable" and claude_target and not dry_run:
        print("(enable) Claude: cleared NOKORI_DISABLED in settings.json env.")

    if verb == "install" and claude_target and cursor_target:
        _print_both_targets_note()
    elif verb == "install" and claude_target and not dry_run:
        print(
            "Gate has two layers: settings.json PreToolUse matcher (when hook runs) "
            "vs config.toml NOKORI_GATE_MATCHER (block inside hook). See README."
        )

    from ..install_targets import (
        PLATFORM_CLAUDE,
        PLATFORM_CURSOR,
        format_platforms_label,
        merge_platforms,
        remove_platforms,
    )

    if verb == "install":
        selected = [
            p
            for p, on in ((PLATFORM_CLAUDE, claude_target), (PLATFORM_CURSOR, cursor_target))
            if on
        ]
        if dry_run:
            print(f"(dry-run) would record platforms: {format_platforms_label(selected)}")
        else:
            recorded = merge_platforms(cfg, selected)
            print(f"Recorded install platforms: {format_platforms_label(recorded)}")
            print(
                "Hook diagnostics: set log_level=info in config.toml (or NOKORI_LOG_LEVEL=info); see ~/.nokori/logs/hook.log"
            )
    elif verb == "uninstall" and not dry_run:
        removed = [
            p
            for p, on in ((PLATFORM_CLAUDE, claude_target), (PLATFORM_CURSOR, cursor_target))
            if on
        ]
        recorded = remove_platforms(cfg, removed)
        print(f"Recorded install platforms: {format_platforms_label(recorded)}")

    if verb == "install" and not dry_run and not getattr(args, "no_prefetch_embed", False):
        from ..prefetch import maybe_prefetch_local_embed

        maybe_prefetch_local_embed(cfg)

    return 0
