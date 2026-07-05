from __future__ import annotations

import argparse
import copy
import difflib
import json
import os
import sys
import textwrap
from pathlib import Path

from ..config import Config
from ..constants import CURSOR_GATE_MATCHER, DEFAULT_GATE_MATCHER
from ..utils.fs import atomic_write_json

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
) -> tuple[bool, bool, bool]:
    """Default: Claude only. --all: Claude+Cursor. Use --omp for OMP. Uninstall with no flags: all."""
    if getattr(args, "all_platforms", False):
        return True, True, False
    claude = bool(getattr(args, "claude", False))
    cursor = bool(getattr(args, "cursor", False))
    omp = bool(getattr(args, "omp", False))
    if not claude and not cursor and not omp:
        if uninstall:
            return True, True, True
        return True, False, False
    return claude, cursor, omp


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

def _omp_extension_path() -> Path:
    base = os.environ.get("NOKORI_OMP_HOME")
    if base:
        return Path(base).expanduser() / "extensions" / "nokori.ts"
    return Path("~/.omp/agent/extensions/nokori.ts").expanduser()


def _python_executable() -> str:
    return sys.executable


def _build_command() -> str:
    # -I: ignore PYTHONPATH / cwd so hooks always use the installed package
    # (avoids shadowing by a repo-local ``nokori/`` when cwd is the project).
    import shlex

    return f"{shlex.quote(_python_executable())} -I -m nokori hook"


def _build_omp_hook_source() -> str:
    python_executable = json.dumps(_python_executable())
    return textwrap.dedent(
        f"""\
        import {{ spawnSync }} from \"node:child_process\";
        import type {{ ExtensionAPI }} from \"@oh-my-pi/pi-coding-agent/extensibility/extensions\";

        type JsonObject = Record<string, unknown>;

        function asRecord(value: unknown): JsonObject | null {{
          return value !== null && typeof value === \"object\" ? (value as JsonObject) : null;
        }}

        function getSessionFile(ctx: {{
          sessionManager?: {{ getSessionFile?: () => string | null | undefined }};
        }}): string | undefined {{
          const sessionFile = ctx.sessionManager?.getSessionFile?.();
          return typeof sessionFile === \"string\" && sessionFile.length > 0 ? sessionFile : undefined;
        }}

        function buildCommonPayload(ctx: {{
          cwd?: string;
          sessionManager?: {{ getSessionFile?: () => string | null | undefined }};
        }}): JsonObject {{
          const sessionFile = getSessionFile(ctx);
          const payload: JsonObject = {{
            host: \"omp\",
            cwd: typeof ctx.cwd === \"string\" && ctx.cwd.length > 0 ? ctx.cwd : process.cwd(),
          }};
          if (sessionFile) {{
            payload.session_id = sessionFile;
            payload.transcript_path = sessionFile;
          }}
          return payload;
        }}

        function runNokori(event: string, payload: JsonObject, timeoutMs: number): JsonObject | null {{
          const result = spawnSync({python_executable}, ["-I", "-m", "nokori", "hook", event], {{
            input: JSON.stringify(payload),
            encoding: "utf8",
            env: {{ ...process.env, NOKORI_HOST: "omp" }},
            timeout: timeoutMs,
          }});
          if (result.error || result.status !== 0) {{
            return null;
          }}
          const stdout = typeof result.stdout === \"string\" ? result.stdout.trim() : \"\";
          if (!stdout) {{
            return null;
          }}
          try {{
            return asRecord(JSON.parse(stdout));
          }} catch {{
            return null;
          }}
        }}

        function extractInjection(result: JsonObject | null): string | undefined {{
          if (!result) return undefined;
          const hookSpecificOutput = asRecord(result.hookSpecificOutput);
          const additionalContext = hookSpecificOutput?.additionalContext;
          if (typeof additionalContext === \"string\" && additionalContext.length > 0) {{
            return additionalContext;
          }}
          const legacy = result.additional_context;
          return typeof legacy === \"string\" && legacy.length > 0 ? legacy : undefined;
        }}

        function extractBlock(result: JsonObject | null): {{ block: true; reason: string }} | undefined {{
          if (!result) return undefined;
          const hookSpecificOutput = asRecord(result.hookSpecificOutput);
          const hookDecision = hookSpecificOutput?.permissionDecision;
          const topLevelDecision = result.permission;
          if (hookDecision !== \"deny\" && topLevelDecision !== \"deny\") {{
            return undefined;
          }}
          const reason = hookSpecificOutput?.permissionDecisionReason;
          if (typeof reason === \"string\" && reason.length > 0) {{
            return {{ block: true, reason }};
          }}
          const userMessage = result.user_message;
          if (typeof userMessage === \"string\" && userMessage.length > 0) {{
            return {{ block: true, reason: userMessage }};
          }}
          const agentMessage = result.agent_message;
          if (typeof agentMessage === \"string\" && agentMessage.length > 0) {{
            return {{ block: true, reason: agentMessage }};
          }}
          return {{ block: true, reason: \"Nokori blocked this tool call.\" }};
        }}

        export default function nokori(pi: ExtensionAPI): void {{
          pi.on(\"session_start\", (_event, ctx) => {{
            const result = runNokori("session-start", buildCommonPayload(ctx), 5_000);
            const text = extractInjection(result);
            if (!text) return;
            pi.sendMessage({{
              customType: \"nokori\",
              content: text,
              display: true,
              details: {{ source: \"nokori\" }},
              attribution: \"agent\",
            }});
          }});

          pi.on(\"before_agent_start\", (event, ctx) => {{
            const result = runNokori("user-prompt-submit", {{
              ...buildCommonPayload(ctx),
              prompt: event.prompt,
            }}, 10_000);
            const text = extractInjection(result);
            if (!text) return;
            return {{
              message: {{
                customType: \"nokori\",
                content: text,
                display: true,
                details: {{ source: \"nokori\" }},
              }},
            }};
          }});

          pi.on(\"tool_call\", (event, ctx) => {{
            const result = runNokori("pre-tool-use", {{
              ...buildCommonPayload(ctx),
              tool_name: event.toolName,
              tool: event.toolName,
              tool_input: event.input,
            }}, 5_000);
            return extractBlock(result);
          }});

          pi.on(\"session_shutdown\", (_event, ctx) => {{
            runNokori("session-end", buildCommonPayload(ctx), 2_000);
          }});
        }}
        """
    ).strip() + "\n"


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
        atomic_write_json(path, data, mkdir=True, indent=2)
    except OSError as e:
        raise ValueError(f"cannot write {path}: {e}") from e


def _print_both_targets_note() -> None:
    print(_BOTH_TARGETS_NOTE.rstrip())


def _diff(before: dict, after: dict, path: Path) -> str:
    a = json.dumps(before, indent=2, ensure_ascii=False, sort_keys=True).splitlines(keepends=True)
    b = json.dumps(after, indent=2, ensure_ascii=False, sort_keys=True).splitlines(keepends=True)
    return "".join(difflib.unified_diff(a, b, fromfile=str(path), tofile=f"{path} (proposed)"))


def _diff_text(before: str | None, after: str | None, path: Path) -> str:
    a = (before or "").splitlines(keepends=True)
    b = (after or "").splitlines(keepends=True)
    return "".join(difflib.unified_diff(a, b, fromfile=str(path), tofile=f"{path} (proposed)"))


def _set_env_flag(data: dict, key: str, value: str | None) -> None:
    env = data.setdefault("env", {})
    if value is None:
        env.pop(key, None)
        if not env:
            data.pop("env", None)
    else:
        env[key] = value


def _read_text_file(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"cannot read {path}: {e}") from e


def _write_text_file(path: Path, data: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(data, encoding="utf-8")
    except OSError as e:
        raise ValueError(f"cannot write {path}: {e}") from e


def _remove_text_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        raise ValueError(f"cannot remove {path}: {e}") from e


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


def _install_one_text_target(
    *,
    label: str,
    path: Path,
    before: str | None,
    after: str | None,
    dry_run: bool,
    verb: str,
) -> bool:
    """Return True if file content changed."""
    if dry_run:
        diff = _diff_text(before, after, path)
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
    if after is None:
        _remove_text_file(path)
        print(f"({verb}) {label}: removed {path}")
        return True
    _write_text_file(path, after)
    print(f"({verb}) {label}: wrote {path}")
    return True


def run(args: argparse.Namespace, cfg: Config) -> int:
    claude_target, cursor_target, omp_target = resolve_install_targets(
        args,
        uninstall=bool(args.uninstall),
    )
    command = _build_command()
    omp_source = _build_omp_hook_source()

    claude_path = _settings_path()
    cursor_path = _cursor_hooks_path()
    omp_path = _omp_extension_path()

    try:
        claude_before = _read_json_file(claude_path) if claude_target else {}
        cursor_before = _read_json_file(cursor_path) if cursor_target else {}
        omp_before = _read_text_file(omp_path) if omp_target else None
    except ValueError as e:
        print(f"nokori: {e}", file=sys.stderr)
        return 1

    if args.uninstall:
        claude_after = _remove_nokori_claude(claude_before) if claude_target else claude_before
        cursor_after = _remove_nokori_cursor(cursor_before) if cursor_target else cursor_before
        omp_after = None if omp_target else omp_before
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
        omp_after = omp_before
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
        omp_after = omp_before
        verb = "enable"
    else:
        claude_after = (
            _merge_claude_settings(claude_before, command) if claude_target else claude_before
        )
        cursor_after = (
            _merge_cursor_hooks(cursor_before, command) if cursor_target else cursor_before
        )
        omp_after = omp_source if omp_target else omp_before
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
    if omp_target:
        any_change |= _install_one_text_target(
            label="OMP",
            path=omp_path,
            before=omp_before,
            after=omp_after,
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
        PLATFORM_OMP,
        format_platforms_label,
        merge_platforms,
        remove_platforms,
    )

    if verb == "install":
        selected = [
            p
            for p, on in (
                (PLATFORM_CLAUDE, claude_target),
                (PLATFORM_CURSOR, cursor_target),
                (PLATFORM_OMP, omp_target),
            )
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
            for p, on in (
                (PLATFORM_CLAUDE, claude_target),
                (PLATFORM_CURSOR, cursor_target),
                (PLATFORM_OMP, omp_target),
            )
            if on
        ]
        recorded = remove_platforms(cfg, removed)
        print(f"Recorded install platforms: {format_platforms_label(recorded)}")

    if verb == "install" and not dry_run and not getattr(args, "no_prefetch_embed", False):
        from ..prefetch import maybe_prefetch_local_embed

        maybe_prefetch_local_embed(cfg)

    return 0
