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
        "-v", "--verbose",
        action="store_true",
        help="debug logging (same as NOKORI_LOG_LEVEL=debug)",
    )
    sub = p.add_subparsers(dest="command", required=False, metavar="<command>")

    sp_add = sub.add_parser("add", help="add a rule manually")
    sp_add.add_argument("--trigger", required=True, help="trigger scenario (English canonical)")
    sp_add.add_argument("--action", required=True, help="correct behavior")
    sp_add.add_argument("--behavior", default=None, help="incorrect behavior")
    sp_add.add_argument("--rationale", default=None, help="one-line evidence")
    sp_add.add_argument(
        "--source-type",
        default="correction",
        choices=("correction", "preference", "solution", "anti_pattern"),
    )
    sp_add.add_argument("--confidence", default="high", choices=("high", "medium", "low"))
    sp_add.add_argument("--severity", default="reminder", choices=("reminder", "high_risk", "gate_eligible"))
    sp_add.add_argument("--variants", default=None, help="comma-separated variants")
    sp_add.add_argument("--terms-en", default=None, help="comma-separated English search terms")
    sp_add.add_argument("--terms-zh", default=None, help="comma-separated Chinese search terms")
    sp_add.add_argument("--project-id", default=None, help="restrict to a specific project_id")

    sp_list = sub.add_parser("list", help="list rules")
    sp_list.add_argument(
        "--all",
        action="store_true",
        help="include candidate, archived, and merged rules",
    )
    sp_list.add_argument("--project", default=None)

    sp_show = sub.add_parser("show", help="show a rule by short id")
    sp_show.add_argument("short_id")

    sp_dismiss = sub.add_parser("dismiss", help="archive a rule by short id")
    sp_dismiss.add_argument("short_id")

    sp_edit = sub.add_parser("edit", help="edit a rule by short id")
    sp_edit.add_argument("short_id")
    sp_edit.add_argument("--trigger", default=None, help="replace trigger_text")
    sp_edit.add_argument("--action", default=None)
    sp_edit.add_argument("--rationale", default=None)
    sp_edit.add_argument("--variants", default=None, help="comma-separated variants")
    sp_edit.add_argument("--terms-en", default=None, help="comma-separated English terms")
    sp_edit.add_argument("--terms-zh", default=None, help="comma-separated Chinese terms")
    sp_edit.add_argument("--severity", default=None, choices=("reminder", "high_risk", "gate_eligible"))
    sp_edit.add_argument("--status", default=None, choices=("active", "trusted", "suppressed", "archived"))

    sp_test = sub.add_parser("test", help="simulate retrieval for a prompt")
    sp_test.add_argument("prompt", help="user prompt to simulate")
    sp_test.add_argument(
        "--project",
        default=None,
        help="project_id filter (default: cwd via git root hash, same as hooks)",
    )

    sp_extract = sub.add_parser("extract", help="run the extraction pipeline")
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
    )
    sub.add_parser("maintain", help="run maintenance jobs now")
    sp_reset = sub.add_parser("reset", help="reset the database (destructive)")
    sp_reset.add_argument(
        "--force",
        action="store_true",
        help="skip confirmation prompt",
    )

    sp_export = sub.add_parser("export", help="export rules to JSON")
    sp_export.add_argument("path")
    sp_import = sub.add_parser("import", help="import rules from JSON")
    sp_import.add_argument("path")

    sp_install = sub.add_parser(
        "install",
        help="install hooks for Claude Code and/or Cursor",
        description=(
            "Register Nokori hooks. Default: Claude only (~/.claude/settings.json). "
            "Use --cursor for native ~/.cursor/hooks.json; --all for both "
            "(prints duplicate-hook warning)."
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
        "--all",
        dest="all_platforms",
        action="store_true",
        help="install into Claude Code and Cursor (prints duplicate-hook warning)",
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
    if cmd == "reset":
        from .commands import reset

        return reset.run(args, cfg)
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
        return 1
