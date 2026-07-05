from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import replace

from . import __version__
from .config import Config
from .errors import NokoriError
from .utils.logging import configure as configure_logging


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nokori",
        description="Claude Code learning layer — rules from corrections, inject + one-shot Gate",
    )
    p.add_argument("--version", action="version", version=f"nokori {__version__}")
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="debug logging (same as NOKORI_LOG_LEVEL=debug)",
    )
    sub = p.add_subparsers(dest="command", required=False, metavar="<command>")

    sp_add = sub.add_parser(
        "add",
        help="add a rule manually",
        description="Create a new rule from trigger and action text.",
        epilog="Examples:\n  nokori add --trigger 'When writing tests' --action 'Use pytest fixtures'",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sp_add.add_argument("--trigger", required=True, help="trigger scenario (English canonical)")
    sp_add.add_argument("--action", required=True, help="correct behavior")
    sp_add.add_argument("--severity", default="reminder", choices=("reminder", "high_risk"))
    sp_add.add_argument("--variants", default=None, help="comma-separated variants")
    sp_add.add_argument("--terms-en", default=None, help="comma-separated English search terms")
    sp_add.add_argument("--terms-zh", default=None, help="comma-separated Chinese search terms")
    sp_add.add_argument("--project-id", default=None, help="restrict to a specific project_id")

    sp_list = sub.add_parser(
        "list",
        help="list rules",
        description="Display all active rules in a table format.",
        epilog="Examples:\n  nokori list\n  nokori list --all --project myproj",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sp_list.add_argument(
        "--all",
        action="store_true",
        help="include candidate, suppressed, and archived rules",
    )
    sp_list.add_argument("--project", default=None)
    sp_list.add_argument(
        "--global-eligible",
        action="store_true",
        help="show only trusted project-scoped rules approaching cross-project promotion",
    )
    sp_list.add_argument("--json", action="store_true", help="output JSON array instead of table")

    sp_show = sub.add_parser(
        "show",
        help="show a rule by short id",
        description="Display full details of a rule including trigger, action, and metadata.",
        epilog="Examples:\n  nokori show abc123",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sp_show.add_argument("short_id")

    sp_dismiss = sub.add_parser(
        "dismiss",
        help="archive a rule by short id",
        description="Archive (dismiss) a rule so it is no longer injected.",
        epilog="Examples:\n  nokori dismiss abc123",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sp_dismiss.add_argument("short_id")

    sp_edit = sub.add_parser(
        "edit",
        help="edit a rule by short id",
        description="Modify fields of an existing rule.",
        epilog="Examples:\n  nokori edit abc123 --trigger 'New trigger text'\n  nokori edit abc123 --severity high_risk",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sp_edit.add_argument("short_id")
    sp_edit.add_argument("--trigger", default=None, help="replace trigger_text")
    sp_edit.add_argument("--action", default=None)
    sp_edit.add_argument("--rationale", default=None)
    sp_edit.add_argument("--variants", default=None, help="comma-separated variants")
    sp_edit.add_argument("--terms-en", default=None, help="comma-separated English terms")
    sp_edit.add_argument("--terms-zh", default=None, help="comma-separated Chinese terms")
    sp_edit.add_argument("--severity", default=None, choices=("reminder", "high_risk"))
    sp_edit.add_argument("--status", default=None, choices=("archived",))

    sp_test = sub.add_parser(
        "test",
        help="simulate retrieval for a prompt",
        description="Simulate rule retrieval for a given prompt without injecting.",
        epilog="Examples:\n  nokori test 'Write a React component'\n  nokori test 'deploy to prod' --project myproj",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sp_test.add_argument("prompt", help="user prompt to simulate")
    sp_test.add_argument(
        "--project",
        default=None,
        help="project_id filter (default: cwd via git root hash, same as hooks)",
    )

    sp_search = sub.add_parser(
        "search",
        help="search rules by prompt (compact table output)",
        description="Search rules by prompt text with relevance scoring.",
        epilog="Examples:\n  nokori search 'how to handle errors'\n  nokori search 'testing' --project myproj",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sp_search.add_argument("prompt", help="text to search against")
    sp_search.add_argument(
        "--project",
        default=None,
        help="project_id filter (default: cwd via git root hash)",
    )

    sp_extract = sub.add_parser(
        "extract",
        help="run the extraction pipeline",
        description="Extract rules from conversation transcripts.",
        epilog="Examples:\n  nokori extract\n  nokori extract --session ~/transcripts/chat.jsonl --dry-run",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sp_extract.add_argument("--session", default=None, help="explicit transcript path")
    sp_extract.add_argument(
        "--project",
        default=None,
        help="project_id for merge pool (--session only; default: job file then cwd)",
    )
    sp_extract.add_argument("--dry-run", action="store_true")

    sub.add_parser(
        "status",
        help="rules counts, promotion progress, open sessions (operational snapshot)",
    )
    sub.add_parser("logs", help="tail nokori log files")
    sub.add_parser(
        "health",
        help="connectivity checks: db, hooks, LLM, embedding readiness",
        description="Run connectivity checks for database, hooks, LLM, and embeddings.",
        epilog="Examples:\n  nokori health",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub.add_parser(
        "maintain",
        help="run maintenance jobs now",
        description="Run scheduled maintenance tasks immediately (pruning, promotion, etc.).",
        epilog="Examples:\n  nokori maintain",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    sp_report = sub.add_parser(
        "report",
        help="AI-friendly system status report",
        description="Generate a status report summarizing rules, sessions, and events.",
        epilog="Examples:\n  nokori report\n  nokori report --since 2024-01-01 --json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sp_report.add_argument(
        "--since", default=None, help="ISO timestamp start (default: 7 days ago)"
    )
    sp_report.add_argument("--session", default=None, help="filter to a specific session_id")
    sp_report.add_argument("--json", action="store_true", help="output JSON instead of markdown")
    sp_report.add_argument(
        "--metrics", action="store_true", help="show cold-path quality metrics"
    )

    sp_stream = sub.add_parser("stream", help="AI-friendly event stream")
    sp_stream.add_argument(
        "--since", default=None, help="ISO timestamp start (default: 1 hour ago)"
    )
    sp_stream.add_argument("--session", default=None, help="filter to a specific session_id")
    sp_stream.add_argument("--type", default=None, help="filter by event source type")
    sp_stream.add_argument("--verbose", action="store_true", help="full JSON per event")
    sp_stream.add_argument("--limit", type=int, default=100, help="max events (dump mode)")
    sp_stream.add_argument("--follow", action="store_true", help="continuous mode (like tail -f)")

    sp_export = sub.add_parser("export", help="export rules to JSON")
    sp_export.add_argument("path")
    sp_import = sub.add_parser("import", help="import rules from JSON")
    sp_import.add_argument("path")

    sp_install = sub.add_parser(
        "install",
        help="install hooks for Claude Code, Cursor, and/or OMP",
        description=(
            "Register Nokori hooks. Default: Claude only (~/.claude/settings.json). "
            "Use --cursor for native ~/.cursor/hooks.json; --omp for "
            "~/.omp/agent/extensions/nokori.ts; --all for Claude Code and Cursor; "
            "uninstall without platform flags removes all Nokori hooks."
        ),
    )
    sp_install.add_argument(
        "--claude",
        action="store_true",
        help="install into ~/.claude/settings.json (default when no platform flag)",
    )
    sp_install.add_argument(
        "--cursor",
        action="store_true",
        help="install into ~/.cursor/hooks.json (native Cursor agent hooks)",
    )
    sp_install.add_argument(
        "--omp",
        action="store_true",
        help="install into ~/.omp/agent/extensions/nokori.ts (OMP hook extension)",
    )
    sp_install.add_argument(
        "--all",
        dest="all_platforms",
        action="store_true",
        help="install into Claude Code and Cursor; use --omp separately",
    )
    sp_install.add_argument("--dry-run", action="store_true")
    sp_install.add_argument(
        "--no-prefetch-embed",
        action="store_true",
        help="skip local model prefetch after hook install (when local-embed is installed)",
    )
    install_mode = sp_install.add_mutually_exclusive_group()
    install_mode.add_argument("--uninstall", action="store_true")
    install_mode.add_argument(
        "--disable",
        action="store_true",
        help=(
            "set NOKORI_DISABLED=1 in Claude ~/.claude/settings.json env only; "
            "does not stop Cursor hooks (use --uninstall --cursor)"
        ),
    )
    install_mode.add_argument(
        "--enable",
        action="store_true",
        help="clear NOKORI_DISABLED in Claude settings.json env (Cursor unchanged)",
    )

    sp_hook = sub.add_parser("hook", help="hook entry (called by Claude Code)")
    sp_hook.add_argument(
        "event",
        choices=("session-start", "user-prompt-submit", "pre-tool-use", "session-end"),
    )

    sp_embed = sub.add_parser(
        "embed",
        help="shared local embedding server (one model for all sessions)",
    )
    sp_embed.add_argument(
        "embed_action",
        choices=("serve", "start", "stop", "status", "prefetch"),
        help="serve=foreground; start=detach; stop=shutdown; status=probe; prefetch=download local weights",
    )

    sp_web = sub.add_parser("web", help="launch the web UI dashboard")
    sp_web.add_argument("--port", type=int, default=8765, help="server port (default: 8765)")
    sp_web.add_argument("--no-browser", action="store_true", help="don't auto-open browser")

    return p


def _dispatch(args: argparse.Namespace, cfg: Config) -> int:
    cmd = args.command
    if cmd is None:
        _build_parser().print_help()
        return 0

    if cmd == "add":
        from .commands import add as cmd_add

        return cmd_add.run(args, cfg)
    if cmd == "list":
        from .commands import list_rules

        return list_rules.run(args, cfg)
    if cmd == "show":
        from .commands import show

        return show.run(args, cfg)
    if cmd == "dismiss":
        from .commands import dismiss

        return dismiss.run(args, cfg)
    if cmd == "edit":
        from .commands import edit

        return edit.run(args, cfg)
    if cmd == "test":
        from .commands import test as cmd_test

        return cmd_test.run(args, cfg)
    if cmd == "search":
        from .commands import search_debug

        return search_debug.run(args, cfg)
    if cmd == "extract":
        from .commands import extract

        return extract.run(args, cfg)
    if cmd == "status":
        from .commands import status

        return status.run(args, cfg)
    if cmd == "logs":
        from .commands import logs

        return logs.run(args, cfg)
    if cmd == "health":
        from .commands import health

        return health.run(args, cfg)
    if cmd == "maintain":
        from .commands import maintain

        return maintain.run(args, cfg)
    if cmd == "report":
        from .commands import report

        return report.run(args, cfg)
    if cmd == "stream":
        from .commands import stream

        return stream.run(args, cfg)
    if cmd == "export":
        from .commands import export_import

        return export_import.run_export(args, cfg)
    if cmd == "import":
        from .commands import export_import

        return export_import.run_import(args, cfg)
    if cmd == "install":
        from .commands import install

        return install.run(args, cfg)
    if cmd == "hook":
        from . import hooks as hooks_pkg

        return hooks_pkg.dispatch(args.event, cfg)
    if cmd == "embed":
        from .commands import embed_cmd

        return embed_cmd.run(args, cfg)
    if cmd == "web":
        from .web import run as web_run

        return web_run(args, cfg)

    print(f"nokori: unknown command {cmd!r}", file=sys.stderr)
    return 2


def main(argv: Sequence[str]) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv))

    try:
        cfg = Config.from_env()
    except NokoriError as e:
        print(f"nokori: {e}", file=sys.stderr)
        if getattr(e, "remediation", None):
            print(f"  hint: {e.remediation}", file=sys.stderr)
        return 1
    except Exception as e:
        import tomllib

        if isinstance(e, tomllib.TOMLDecodeError):
            if args.command == "hook":
                print('{"continue": true}')
                return 0
            print(f"nokori: invalid config.toml: {e}", file=sys.stderr)
            return 1
        raise

    if getattr(args, "verbose", False):
        cfg = replace(cfg, log_level="debug")

    if cfg.disabled and args.command == "hook":
        print('{"continue": true}')
        return 0

    cfg.ensure_dirs()
    configure_logging(cfg.logs_dir, cfg.log_level)

    try:
        return _dispatch(args, cfg)
    except NokoriError as e:
        print(f"nokori: {e}", file=sys.stderr)
        if getattr(e, "remediation", None):
            print(f"  hint: {e.remediation}", file=sys.stderr)
        return 1
