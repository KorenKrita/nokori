from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import __version__
from .config import Config
from .errors import NokoriError
from .utils.logging import configure as configure_logging


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nokori", description="Claude Code 反复犯错纠正层")
    p.add_argument("--version", action="version", version=f"nokori {__version__}")
    sub = p.add_subparsers(dest="command", required=False, metavar="<command>")

    sp_add = sub.add_parser("add", help="add a rule manually")
    sp_add.add_argument("--trigger", help="trigger scenario (English canonical)")
    sp_add.add_argument("--action", help="correct behavior")
    sp_add.add_argument("--behavior", default=None, help="incorrect behavior")
    sp_add.add_argument("--rationale", default=None, help="one-line evidence")
    sp_add.add_argument(
        "--source-type",
        default="correction",
        choices=("correction", "preference", "solution", "anti_pattern"),
    )
    sp_add.add_argument("--confidence", default="high", choices=("high", "medium"))
    sp_add.add_argument("--variants", default=None, help="comma-separated variants")
    sp_add.add_argument("--terms-en", default=None, help="comma-separated English search terms")
    sp_add.add_argument("--terms-zh", default=None, help="comma-separated Chinese search terms")
    sp_add.add_argument("--project-id", default=None, help="restrict to a specific project_id")

    sp_list = sub.add_parser("list", help="list rules")
    sp_list.add_argument("--all", action="store_true", help="include candidate / archived")
    sp_list.add_argument("--project", default=None)

    sp_show = sub.add_parser("show", help="show a rule by short id")
    sp_show.add_argument("short_id")

    sp_dismiss = sub.add_parser("dismiss", help="archive a rule by short id")
    sp_dismiss.add_argument("short_id")

    sp_edit = sub.add_parser("edit", help="edit a rule by short id")
    sp_edit.add_argument("short_id")
    sp_edit.add_argument("--action", default=None)
    sp_edit.add_argument("--rationale", default=None)
    sp_edit.add_argument("--confidence", default=None, choices=("high", "medium"))
    sp_edit.add_argument("--status", default=None, choices=("active", "dormant", "archived"))

    sp_test = sub.add_parser("test", help="simulate retrieval for a prompt")
    sp_test.add_argument("prompt", help="user prompt to simulate")
    sp_test.add_argument(
        "--project",
        default=None,
        help="project_id filter (default: cwd via git root hash, same as hooks)",
    )

    sp_extract = sub.add_parser("extract", help="run the extraction pipeline")
    sp_extract.add_argument("--session", default=None, help="explicit transcript path")
    sp_extract.add_argument("--dry-run", action="store_true")

    sub.add_parser("status", help="print current rule + extract status")
    sub.add_parser("logs", help="tail nokori log files")
    sub.add_parser("health", help="check db / hooks / llm / embedding")
    sub.add_parser("maintain", help="run maintenance jobs now")
    sub.add_parser("reset", help="reset the database (destructive)")

    sp_export = sub.add_parser("export", help="export rules to JSON")
    sp_export.add_argument("path")
    sp_import = sub.add_parser("import", help="import rules from JSON")
    sp_import.add_argument("path")

    sp_install = sub.add_parser("install", help="install/uninstall hooks")
    sp_install.add_argument("--dry-run", action="store_true")
    sp_install.add_argument("--uninstall", action="store_true")
    sp_install.add_argument("--disable", action="store_true")
    sp_install.add_argument("--enable", action="store_true")

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
        choices=("serve", "start", "stop", "status"),
        help="serve=run foreground; start=detach; stop=shutdown; status=probe",
    )

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
