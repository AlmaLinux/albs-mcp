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
from .constants import (
    LOG_MAX_LINE_CHARS,
    LOG_MAX_RESULT_CHARS,
    LOG_SEARCH_AFTER,
    LOG_SEARCH_BEFORE,
    LOG_SEARCH_MAX_MATCHES,
)

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
    _exec(cmd.read_log_tail(
        args.build_id, args.filename, args.lines, args.max_line_chars,
        args.before_line, args.max_chars,
    ))


def _cmd_log_range(args: argparse.Namespace) -> None:
    _exec(cmd.read_log_range(
        args.build_id, args.filename, args.start_line, args.end_line,
        args.max_line_chars, args.max_chars,
    ))


def _cmd_log_search(args: argparse.Namespace) -> None:
    _exec(cmd.search_log(
        args.build_id, args.filename, args.pattern,
        args.before, args.after, args.max_matches, args.max_line_chars,
        args.max_chars,
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


def _cmd_products(args: argparse.Namespace) -> None:
    _exec(cmd.get_products())


def _cmd_release_plan(args: argparse.Namespace) -> None:
    _exec(cmd.get_release_plan(args.release_id))


def _cmd_create_release_plan(args: argparse.Namespace) -> None:
    _exec(cmd.create_release_plan(
        build_id=args.build_id,
        platform=args.platform,
        product=args.product,
        build_ids=args.add_build or None,
        whole_packages_only=args.whole_packages_only,
    ))


def _cmd_commit_release(args: argparse.Namespace) -> None:
    _exec(cmd.commit_release(args.release_id))


# ═══════════════════════════════════════════════════════════════════════
#  Parser construction
# ═══════════════════════════════════════════════════════════════════════


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="albs",
        description=(
            "CLI for AlmaLinux Build System (build.almalinux.org). "
            "Investigate build failures, create builds, sign packages, "
            "create release plans."
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
    p.add_argument(
        "--before-line", type=int, default=None,
        help="Read the page ending just before this line (page upward).",
    )
    p.add_argument(
        "--max-line-chars", type=int, default=LOG_MAX_LINE_CHARS,
        help=f"Clip each line (default: {LOG_MAX_LINE_CHARS}, 0 = verbatim).",
    )
    p.add_argument(
        "--max-chars", type=int, default=LOG_MAX_RESULT_CHARS,
        help=f"Result size budget in chars (default: {LOG_MAX_RESULT_CHARS}, 0 = unlimited).",
    )
    p.set_defaults(func=_cmd_log_tail)

    # ── log-range ─────────────────────────────────────────────────────
    p = sub.add_parser("log-range", help="Read a line range from a downloaded log.")
    p.add_argument("build_id", type=int)
    p.add_argument("filename")
    p.add_argument("start_line", type=int)
    p.add_argument("end_line", type=int)
    p.add_argument(
        "--max-line-chars", type=int, default=LOG_MAX_LINE_CHARS,
        help=f"Clip each line (default: {LOG_MAX_LINE_CHARS}, 0 = verbatim).",
    )
    p.add_argument(
        "--max-chars", type=int, default=LOG_MAX_RESULT_CHARS,
        help=f"Result size budget in chars (default: {LOG_MAX_RESULT_CHARS}, 0 = unlimited).",
    )
    p.set_defaults(func=_cmd_log_range)

    # ── log-search ────────────────────────────────────────────────────
    p = sub.add_parser(
        "log-search",
        help="Grep a build log for the failure and show it with context.",
    )
    p.add_argument("build_id", type=int)
    p.add_argument("filename")
    p.add_argument(
        "-e", "--pattern", default=None,
        help="Regex to search (default: the built-in build-failure signatures).",
    )
    p.add_argument(
        "-B", "--before", type=int, default=LOG_SEARCH_BEFORE,
        help=f"Context lines before a match (default: {LOG_SEARCH_BEFORE}).",
    )
    p.add_argument(
        "-A", "--after", type=int, default=LOG_SEARCH_AFTER,
        help=f"Context lines after a match (default: {LOG_SEARCH_AFTER}).",
    )
    p.add_argument(
        "-m", "--max-matches", type=int, default=LOG_SEARCH_MAX_MATCHES,
        help=f"Matches to report (default: {LOG_SEARCH_MAX_MATCHES}).",
    )
    p.add_argument(
        "--max-line-chars", type=int, default=LOG_MAX_LINE_CHARS,
        help=f"Clip each line (default: {LOG_MAX_LINE_CHARS}, 0 = verbatim).",
    )
    p.add_argument(
        "--max-chars", type=int, default=LOG_MAX_RESULT_CHARS,
        help=f"Result size budget in chars (default: {LOG_MAX_RESULT_CHARS}, 0 = unlimited).",
    )
    p.set_defaults(func=_cmd_log_search)

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

    # ── products ──────────────────────────────────────────────────────
    p = sub.add_parser("products", help="List all products (release targets).")
    p.set_defaults(func=_cmd_products)

    # ── release-plan ──────────────────────────────────────────────────
    p = sub.add_parser("release-plan", help="View an existing release plan.")
    p.add_argument("release_id", type=int)
    p.set_defaults(func=_cmd_release_plan)

    # ── create-release-plan ───────────────────────────────────────────
    p = sub.add_parser(
        "create-release-plan",
        help="Create a release plan (requires JWT). Never performs the release.",
    )
    p.add_argument("build_id", type=int)
    p.add_argument("--platform", required=True, help="Target platform name.")
    p.add_argument(
        "--product", required=True,
        help="Target product name (use 'products' to list).",
    )
    p.add_argument(
        "--add-build", action="append", type=int, default=[],
        help="Additional build ID to include in the plan (repeat for multiple).",
    )
    p.add_argument(
        "--whole-packages-only", dest="whole_packages_only",
        action="store_true",
        help=(
            "Include only packages whose every arch task completed "
            "(drop half-built packages). For PARTIAL builds."
        ),
    )
    p.set_defaults(func=_cmd_create_release_plan)

    # ── commit-release ────────────────────────────────────────────────
    p = sub.add_parser(
        "commit-release",
        help="Commit (perform) a release. Blocked — only plans are supported.",
    )
    p.add_argument("release_id", type=int)
    p.set_defaults(func=_cmd_commit_release)

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
