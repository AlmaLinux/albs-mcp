"""CLI interface for AlmaLinux Build System.

Alternative to the MCP server — same functionality, invoked via shell commands.
Delegates to _commands.py to avoid duplicating formatting logic.
Does NOT import the MCP stack.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

from . import _commands as cmd

_ERROR_PREFIXES = ("Error", "Auth error")


def _run(coro):
    return asyncio.run(coro)


def _init(args: argparse.Namespace) -> None:
    """Apply global options and reset the client."""
    if getattr(args, "token", None):
        os.environ["ALBS_JWT_TOKEN"] = args.token
    if getattr(args, "log_dir", None):
        os.environ["ALBS_LOG_DIR"] = args.log_dir
    cmd.reset_client()


def _exec(coro) -> None:
    """Run an async command, print the result, and exit with proper code."""
    try:
        result = _run(coro)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(result)
    if any(result.startswith(p) for p in _ERROR_PREFIXES):
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════
#  Subcommand handlers
# ═══════════════════════════════════════════════════════════════════════


def _cmd_platforms(args: argparse.Namespace) -> None:
    _exec(cmd.get_platforms())


def _cmd_build_info(args: argparse.Namespace) -> None:
    _exec(cmd.get_build_info(args.build_id))


def _cmd_failed_tasks(args: argparse.Namespace) -> None:
    _exec(cmd.get_failed_tasks(args.build_id))


def _cmd_build_logs(args: argparse.Namespace) -> None:
    _exec(cmd.list_build_logs(args.build_id))


def _cmd_download_log(args: argparse.Namespace) -> None:
    _exec(cmd.download_log(args.build_id, args.filename))


def _cmd_log_tail(args: argparse.Namespace) -> None:
    _exec(cmd.read_log_tail(args.build_id, args.filename, args.lines))


def _cmd_log_range(args: argparse.Namespace) -> None:
    _exec(cmd.read_log_range(
        args.build_id, args.filename, args.start_line, args.end_line,
    ))


def _cmd_search(args: argparse.Namespace) -> None:
    _exec(cmd.search_builds(args.page, args.project, args.running))


def _cmd_sign_keys(args: argparse.Namespace) -> None:
    _exec(cmd.get_sign_keys())


def _cmd_sign_status(args: argparse.Namespace) -> None:
    _exec(cmd.get_sign_task_status(args.build_id))


def _cmd_flavors(args: argparse.Namespace) -> None:
    _exec(cmd.get_flavors())


def _cmd_create_build(args: argparse.Namespace) -> None:
    _exec(cmd.create_build(
        platform=args.platform,
        platforms=args.add_platform or None,
        packages=args.packages or None,
        git_urls=args.git_url or None,
        branch=args.branch,
        from_tag=args.from_tag,
        from_srpm=args.from_srpm,
        tags=args.tag or None,
        arch_list=args.arch or None,
        skip_tests=args.skip_tests,
        add_epel_dist=args.add_epel_dist,
        beta=args.beta,
        secureboot=args.secureboot,
        nosecureboot=args.nosecureboot,
        excludes=args.excludes,
        definitions=args.definitions,
        linked_builds=args.linked_build or None,
        flavors=args.flavor or None,
        with_opts=getattr(args, "with") or None,
        without_opts=args.without or None,
        modules=args.module or None,
        independent_tasks=args.independent_tasks,
    ))


def _cmd_sign_build(args: argparse.Namespace) -> None:
    _exec(cmd.sign_build(args.build_id, args.key_id))


# ═══════════════════════════════════════════════════════════════════════
#  Parser construction
# ═══════════════════════════════════════════════════════════════════════


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="albs",
        description=(
            "CLI for AlmaLinux Build System (build.almalinux.org). "
            "Investigate build failures, create builds, sign packages."
        ),
    )
    parser.add_argument(
        "--token", default=None,
        help="ALBS JWT token (or set ALBS_JWT_TOKEN env var).",
    )
    parser.add_argument(
        "--log-dir", dest="log_dir", default=None,
        help="Directory for downloaded logs (default: /tmp/albs-logs).",
    )

    sub = parser.add_subparsers(dest="command")

    # ── platforms ──────────────────────────────────────────────────────
    p = sub.add_parser("platforms", help="List all platforms and architectures.")
    p.set_defaults(func=_cmd_platforms)

    # ── build-info ────────────────────────────────────────────────────
    p = sub.add_parser("build-info", help="Get build details.")
    p.add_argument("build_id", type=int)
    p.set_defaults(func=_cmd_build_info)

    # ── failed-tasks ──────────────────────────────────────────────────
    p = sub.add_parser("failed-tasks", help="Get failed tasks with log files.")
    p.add_argument("build_id", type=int)
    p.set_defaults(func=_cmd_failed_tasks)

    # ── build-logs ────────────────────────────────────────────────────
    p = sub.add_parser("build-logs", help="List log files for a build.")
    p.add_argument("build_id", type=int)
    p.set_defaults(func=_cmd_build_logs)

    # ── download-log ──────────────────────────────────────────────────
    p = sub.add_parser("download-log", help="Download a build log file.")
    p.add_argument("build_id", type=int)
    p.add_argument("filename")
    p.set_defaults(func=_cmd_download_log)

    # ── log-tail ──────────────────────────────────────────────────────
    p = sub.add_parser("log-tail", help="Read last N lines of a downloaded log.")
    p.add_argument("build_id", type=int)
    p.add_argument("filename")
    p.add_argument(
        "-n", "--lines", type=int, default=3000,
        help="Number of lines from the end (default: 3000).",
    )
    p.set_defaults(func=_cmd_log_tail)

    # ── log-range ─────────────────────────────────────────────────────
    p = sub.add_parser("log-range", help="Read a line range from a downloaded log.")
    p.add_argument("build_id", type=int)
    p.add_argument("filename")
    p.add_argument("start_line", type=int)
    p.add_argument("end_line", type=int)
    p.set_defaults(func=_cmd_log_range)

    # ── search ────────────────────────────────────────────────────────
    p = sub.add_parser("search", help="Search builds on ALBS.")
    p.add_argument("--page", type=int, default=1, help="Page number (default: 1).")
    p.add_argument("--project", default=None, help="Filter by package name.")
    p.add_argument(
        "--running", default=None, action="store_true",
        help="Show only running builds.",
    )
    p.add_argument(
        "--no-running", dest="running", action="store_false",
        help="Show only finished builds.",
    )
    p.set_defaults(func=_cmd_search)

    # ── sign-keys ─────────────────────────────────────────────────────
    p = sub.add_parser("sign-keys", help="List available sign keys (requires JWT).")
    p.set_defaults(func=_cmd_sign_keys)

    # ── sign-status ───────────────────────────────────────────────────
    p = sub.add_parser("sign-status", help="Show sign task status for a build.")
    p.add_argument("build_id", type=int)
    p.set_defaults(func=_cmd_sign_status)

    # ── flavors ───────────────────────────────────────────────────────
    p = sub.add_parser("flavors", help="List all platform flavors.")
    p.set_defaults(func=_cmd_flavors)

    # ── create-build ──────────────────────────────────────────────────
    p = sub.add_parser("create-build", help="Create a new build (requires JWT).")
    p.add_argument("platform", help="Target platform.")
    p.add_argument("packages", nargs="*", default=[], help="Package names or SRPM URLs.")
    p.add_argument(
        "--add-platform", action="append", default=[],
        help="Additional platform to build on (repeat for multiple).",
    )
    p.add_argument(
        "--git-url", action="append", default=[],
        help="Custom Git repo URL (repeat for multiple). Use for repos outside git.almalinux.org.",
    )
    p.add_argument("--branch", default=None, help="Git branch (e.g. c9s, c10s).")
    p.add_argument("--from-tag", action="store_true", help="Build from git tags.")
    p.add_argument("--from-srpm", action="store_true", help="Build from SRPM URLs.")
    p.add_argument(
        "--tag", action="append", default=[],
        help="Explicit tag per package (repeat for each).",
    )
    p.add_argument(
        "--arch", action="append", default=[],
        help="Architecture to build (repeat for multiple).",
    )
    p.add_argument("--skip-tests", action="store_true", help="Disable %%check phase.")
    p.add_argument(
        "--add-epel-dist", action="store_true",
        help="Extract .elN dist suffix and set per-task mock definition.",
    )
    p.add_argument("--beta", action="store_true", help="Enable beta flavor.")
    p.add_argument("--secureboot", action="store_true", help="Enable SecureBoot.")
    p.add_argument(
        "--nosecureboot", action="store_true",
        help="Override secureboot requirement.",
    )
    p.add_argument("--excludes", default=None, help="Space-separated packages to exclude.")
    p.add_argument(
        "--definitions", default=None,
        help='JSON mock definitions, e.g. \'{"dist": ".el9"}\'.',
    )
    p.add_argument(
        "--linked-build", action="append", type=int, default=[],
        help="Build ID to link (repeat for multiple).",
    )
    p.add_argument(
        "--flavor", action="append", default=[],
        help="Additional flavor name (repeat for multiple).",
    )
    p.add_argument(
        "--with", action="append", default=[], dest="with",
        help="Mock --with option (repeat for multiple).",
    )
    p.add_argument(
        "--without", action="append", default=[],
        help="Mock --without option (repeat for multiple).",
    )
    p.add_argument(
        "--module", action="append", default=[],
        help='Module to enable, e.g. "nodejs:18" (repeat).',
    )
    p.add_argument(
        "--independent-tasks", dest="independent_tasks", action="store_true",
        help=(
            "Disable the per-platform sequential task chain so packages build "
            "independently / in parallel within each platform (default: chained)."
        ),
    )
    p.set_defaults(func=_cmd_create_build)

    # ── sign-build ────────────────────────────────────────────────────
    p = sub.add_parser("sign-build", help="Sign a build (requires JWT).")
    p.add_argument("build_id", type=int)
    p.add_argument(
        "--key-id", type=int, default=4,
        help="Sign key ID (default: 4; use sign-keys to list).",
    )
    p.set_defaults(func=_cmd_sign_build)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    _init(args)
    args.func(args)


if __name__ == "__main__":
    main()
