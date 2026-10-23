"""Core command functions for ALBS.

Shared by both the MCP server (server.py) and CLI (cli.py).
Does NOT import mcp — only client.py and constants.py.
"""
from __future__ import annotations

import ast
import datetime
import json
import os
import re
from collections.abc import Iterable, Iterator
from pathlib import Path

import httpx

from .client import (
    ALBSClient,
    get_completed_task_ids,
    get_whole_package_task_ids,
)
from .constants import (
    ALBS_URL,
    ALMA_ERRATA_ID_PATTERN,
    ALMA_ERRATA_PREFIX,
    BUILD_TASK_COMPLETED,
    BUILD_TASK_STATUS,
    BUILD_TASK_STATUS_BY_NAME,
    CVE_ID_PATTERN,
    ERRATA_DEBRAND_PATTERNS,
    ERRATA_DESCRIPTION_FOOTER,
    ERRATA_INTRO_SEARCH_PAGES,
    ERRATA_SEVERITIES,
    ERRATA_SKIP_RPM_MARKERS,
    KEY_LOG_TYPES,
    LOG_ERROR_PATTERN,
    LOG_LINES_PER_CHUNK,
    LOG_MAX_LINE_CHARS,
    LOG_MAX_RESULT_CHARS,
    LOG_SEARCH_AFTER,
    LOG_SEARCH_BEFORE,
    LOG_SEARCH_MAX_MATCHES,
    RELEASE_STATUS,
    RH_ADVISORY_ID_PATTERN,
    RH_ADVISORY_TITLE_PREFIX,
    RH_BUGZILLA,
    RH_CVE_PAGE,
    SIGN_TASK_STATUS,
)

_client: ALBSClient | None = None


def _load_token_from_credentials() -> str | None:
    """Try reading JWT from ~/.albs/credentials (Python dict with 'token' key)."""
    cred_path = Path.home() / ".albs" / "credentials"
    if not cred_path.is_file():
        return None
    try:
        data = ast.literal_eval(cred_path.read_text())
        return data.get("token")
    except Exception:
        return None


def _get_client() -> ALBSClient:
    global _client
    if _client is None:
        token = os.environ.get("ALBS_JWT_TOKEN") or _load_token_from_credentials()
        _client = ALBSClient(jwt_token=token)
    return _client


def reset_client() -> None:
    """Reset the global client (e.g. after changing env vars)."""
    global _client
    _client = None


def _api_error(action: str, e: Exception) -> str:
    """Format an API/IO error for the MCP client without leaking internals.

    Never returns stack traces or absolute filesystem paths. Strings start
    with "Error" / "Auth error" so the CLI maps them to a non-zero exit code.
    The 404 message reuses `action` (e.g. "reading log mock.log") so a missing
    log file is not misreported as a missing build.
    """
    if isinstance(e, PermissionError):
        return f"Auth error: {e}"
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code == 404:
            return f"Error: {action}: not found (HTTP 404)."
        if code in (401, 403):
            return (
                f"Auth error: not authorized for {action} (HTTP {code}). "
                "A JWT token may be required."
            )
        return f"Error: ALBS API returned HTTP {code} during {action}."
    if isinstance(e, httpx.RequestError):
        return f"Error: could not reach ALBS during {action} (network error)."
    return f"Error during {action}: {e}"


def _count_lines(path: Path) -> int:
    """Count lines in a file without loading it into memory (matches splitlines).

    Counts newline bytes plus a final line lacking a trailing newline.
    """
    total = 0
    last = b"\n"
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            total += chunk.count(b"\n")
            last = chunk[-1:]
    if last != b"\n":
        total += 1
    return total


async def _read_log(client: ALBSClient, build_id: int, filename: str, reader):
    """Run a sync log reader, auto-downloading the log first if it's missing.

    Lets callers read a log without a separate download_log step. A
    path-traversal ValueError from the reader propagates (never triggers a
    download).
    """
    try:
        return reader()
    except FileNotFoundError:
        await client.download_log(build_id, filename)
        return reader()


# ═══════════════════════════════════════════════════════════════════════
#  Read-only commands
# ═══════════════════════════════════════════════════════════════════════


async def get_platforms() -> str:
    client = _get_client()
    try:
        platforms = await client.get_platforms()
    except Exception as e:
        return _api_error("getting platforms", e)
    lines = [f"Platforms ({len(platforms)}):", ""]
    for p in platforms:
        arches = ", ".join(p.get("arch_list", []))
        lines.append(f"  {p['name']:30} arches: {arches}")
    return "\n".join(lines)


def _task_package(task: dict) -> tuple[str, str]:
    """``(package name, NVR)`` for one build task.

    The name comes from the dist-git repo URL; the NVR from the tail of
    ``git_ref`` (``imports/c10s/mingw-glib2-2.89.2-1.el10``). A ref that is a
    plain branch (``c9s``) carries no version, so the bare name is returned as
    the NVR too — never a branch name masquerading as a version.
    """
    name = task["ref"]["url"].split("/")[-1].replace(".git", "")
    tail = (task["ref"].get("git_ref") or "").split("/")[-1]
    return name, tail if tail.startswith(f"{name}-") else name


def _format_mock_options(opts: dict | None) -> str:
    """Render an ALBS mock_options dict as a compact one-line summary.

    Handles the keys ALBS uses (definitions, yum_exclude, with, without,
    module_enable, …) generically so nothing is silently hidden. Returns ""
    when there is nothing meaningful to show, letting callers skip the line.
    """
    if not opts:
        return ""
    parts: list[str] = []
    for key, value in opts.items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, dict):
            rendered = ", ".join(f"{k}={v}" for k, v in value.items())
        elif isinstance(value, (list, tuple)):
            rendered = ", ".join(str(v) for v in value)
        else:
            rendered = str(value)
        parts.append(f"{key}: {rendered}")
    return "; ".join(parts)


async def get_build_info(build_id: int) -> str:
    client = _get_client()
    try:
        build = await client.get_build(build_id)
    except Exception as e:
        return _api_error(f"getting build #{build_id}", e)

    platforms = {
        t["platform"]["name"]
        for t in build["tasks"] if t.get("platform")
    }
    arches = sorted({t["arch"] for t in build["tasks"]})
    flavors = [f["name"] for f in build.get("platform_flavors", [])]
    linked_builds = build.get("linked_builds", [])

    # Secure Boot is a build-level flag, but ALBS reports it per task
    # (is_secure_boot). It is normally uniform across all tasks; surface the
    # mixed case honestly rather than picking one value.
    sb_arches = sorted(
        {t["arch"] for t in build["tasks"] if t.get("is_secure_boot")}
    )
    if not sb_arches:
        secure_boot = "disabled"
    elif len(sb_arches) == len(arches):
        secure_boot = "enabled"
    else:
        secure_boot = f"enabled ({', '.join(sb_arches)})"

    lines = [
        f"Build #{build['id']}",
        f"Created: {build['created_at']}",
        f"Finished: {build.get('finished_at', 'still running')}",
        f"Owner: {build['owner']['username']}",
        f"Platform: {', '.join(sorted(platforms)) or 'N/A'}",
        f"Architectures: {', '.join(arches)}",
        f"Secure Boot: {secure_boot}",
        f"Released: {build['released']}",
    ]
    if flavors:
        lines.append(f"Flavors: {', '.join(flavors)}")
    if linked_builds:
        lines.append(f"Linked builds: {', '.join(str(b) for b in linked_builds)}")

    global_mock = _format_mock_options(build.get("mock_options"))
    if global_mock:
        lines.append(f"Global Mock Options: {global_mock}")

    # Project (per-task) mock options. ALBS stores these per task, but they are
    # normally set once for the project and so are uniform across tasks. Show a
    # single build-level line when uniform; fall back to per-task annotation
    # when they differ (mirrors how Secure Boot is reported honestly).
    task_mocks = {
        t["id"]: _format_mock_options(t.get("mock_options"))
        for t in build["tasks"]
    }
    distinct_task_mocks = {m for m in task_mocks.values() if m}
    uniform_task_mock = ""
    if len(distinct_task_mocks) == 1 and all(task_mocks.values()):
        uniform_task_mock = next(iter(distinct_task_mocks))
        lines.append(f"Project Mock Options: {uniform_task_mock}")

    lines.append("")
    lines.append("Tasks:")

    for t in build["tasks"]:
        status = BUILD_TASK_STATUS.get(t["status"], f"unknown({t['status']})")
        pkg, _ = _task_package(t)
        git_ref = t["ref"].get("git_ref", "N/A")
        log_count = sum(1 for a in t["artifacts"] if a["type"] == "build_log")
        # Only annotate per-task mock options when they are NOT uniform across
        # the build — otherwise the single build-level line above covers them.
        mock = "" if uniform_task_mock else task_mocks[t["id"]]
        mock_str = f"  mock=[{mock}]" if mock else ""
        lines.append(
            f"  [{status:>9}] task_id={t['id']}  arch={t['arch']:>10}  "
            f"pkg={pkg}  ref={git_ref}  logs={log_count}{mock_str}"
        )

    if build["sign_tasks"]:
        lines.append("")
        lines.append("Sign tasks:")
        for st in build["sign_tasks"]:
            s = SIGN_TASK_STATUS.get(st["status"], f"unknown({st['status']})")
            lines.append(f"  [{s}] sign_task_id={st['id']}")

    return "\n".join(lines)


async def get_failed_tasks(build_id: int) -> str:
    client = _get_client()
    try:
        build = await client.get_build(build_id)
    except Exception as e:
        return _api_error(f"getting build #{build_id}", e)

    failed = [t for t in build["tasks"] if t["status"] == 3]
    if not failed:
        return f"Build #{build_id}: no failed tasks."

    lines = [f"Build #{build_id}: {len(failed)} failed task(s)", ""]

    for t in failed:
        pkg, _ = _task_package(t)
        lines.append(f"Task {t['id']} | arch={t['arch']} | pkg={pkg}")

        logs = [a["name"] for a in t["artifacts"] if a["type"] == "build_log"]
        if logs:
            for log_name in sorted(logs):
                marker = " ★" if any(k in log_name for k in KEY_LOG_TYPES) else ""
                lines.append(f"  - {log_name}{marker}")
        else:
            lines.append("  (no logs available)")
        lines.append("")

    lines.append(
        "★ = key logs for debugging. "
        "Use read_log_tail to read them (it downloads automatically)."
    )
    return "\n".join(lines)


async def download_log(build_id: int, filename: str) -> str:
    client = _get_client()
    try:
        path = await client.download_log(build_id, filename)
    except Exception as e:
        return _api_error(f"downloading log {filename}", e)
    size = path.stat().st_size
    total_lines = _count_lines(path)
    return (
        f"Downloaded: {path}\n"
        f"Size: {size:,} bytes\n"
        f"Total lines: {total_lines:,}\n"
        f"Use read_log_tail to read from the end."
    )


async def read_log_tail(
    build_id: int,
    filename: str,
    lines: int = LOG_LINES_PER_CHUNK,
    max_line_chars: int = LOG_MAX_LINE_CHARS,
    before_line: int | None = None,
    max_chars: int = LOG_MAX_RESULT_CHARS,
) -> str:
    client = _get_client()
    try:
        content, total, first, last = await _read_log(
            client, build_id, filename,
            lambda: client.read_log_tail(
                build_id, filename, lines, max_line_chars,
                before_line, max_chars,
            ),
        )
    except Exception as e:
        return _api_error(f"reading log {filename}", e)
    if not content:
        return (
            f"=== {filename} | {total:,} lines | nothing before line "
            f"{before_line} ===\nThis is already the start of the log."
        )
    header = f"=== {filename} | lines {first}-{last} of {total:,} ===\n"
    return header + content + "\n" + _earlier_hint(build_id, filename, first)


def _earlier_hint(build_id: int, filename: str, first_line: int) -> str:
    """Footer telling the caller how to page one screen further up.

    The call is spelled out with the line actually reached, so walking a log
    upward never needs arithmetic (and a page cut short by the size budget
    resumes exactly where it stopped instead of skipping what it dropped).
    """
    if first_line <= 1:
        return "↑ start of log."
    return (
        f'↑ earlier: read_log_tail({build_id}, "{filename}", '
        f"before_line={first_line})"
    )


async def read_log_range(
    build_id: int,
    filename: str,
    start_line: int,
    end_line: int,
    max_line_chars: int = LOG_MAX_LINE_CHARS,
    max_chars: int = LOG_MAX_RESULT_CHARS,
) -> str:
    client = _get_client()
    try:
        content, total, last = await _read_log(
            client, build_id, filename,
            lambda: client.read_log_range(
                build_id, filename, start_line, end_line,
                max_line_chars, max_chars,
            ),
        )
    except Exception as e:
        return _api_error(f"reading log {filename}", e)
    shown_end = min(end_line, total)
    header = f"=== {filename} | lines {start_line}-{last} of {total:,} ===\n"
    out = header + content
    if last and last < shown_end:
        out += (
            f"\n↓ size budget reached at line {last}; continue: "
            f'read_log_range({build_id}, "{filename}", {last + 1}, {shown_end})'
        )
    return out


async def search_log(
    build_id: int,
    filename: str,
    pattern: str | None = None,
    before: int = LOG_SEARCH_BEFORE,
    after: int = LOG_SEARCH_AFTER,
    max_matches: int = LOG_SEARCH_MAX_MATCHES,
    max_line_chars: int = LOG_MAX_LINE_CHARS,
    max_chars: int = LOG_MAX_RESULT_CHARS,
) -> str:
    """Grep a build log for a pattern (default: the build-failure signatures)."""
    client = _get_client()
    effective = pattern or LOG_ERROR_PATTERN
    label = pattern if pattern else "<default build-failure signatures>"
    try:
        hunks, matches, total, omitted = await _read_log(
            client, build_id, filename,
            lambda: client.search_log(
                build_id, filename, effective,
                before=before, after=after,
                max_matches=max_matches, max_line_chars=max_line_chars,
                max_chars=max_chars,
            ),
        )
    except re.error as e:
        return f"Error: invalid regex {effective!r}: {e}"
    except Exception as e:
        return _api_error(f"searching log {filename}", e)

    shown = sum(1 for hunk in hunks for line in hunk if line[2])
    summary = f"{matches} match(es)" if matches else "no matches"
    if matches > shown:
        summary += f", showing the first {shown}"
    if omitted:
        summary += f", {omitted} boilerplate context line(s) omitted"
    out = [f"=== search {filename} | {label} | {summary} in {total:,} lines ==="]
    if not matches:
        out.append(
            "Nothing matched. Widen the pattern, or read_log_tail the end of "
            "the log to see how it terminated."
        )
        return "\n".join(out)
    width = len(str(total))
    for i, hunk in enumerate(hunks):
        if i:
            out.append("--")
        for lineno, text, is_match in hunk:
            marker = ">>>" if is_match else "   "
            out.append(f"{marker} {lineno:>{width}} | {text}")
    return "\n".join(out)


async def list_build_logs(build_id: int) -> str:
    client = _get_client()
    try:
        logs = await client.list_build_logs(build_id)
    except Exception as e:
        return _api_error(f"listing logs for build #{build_id}", e)
    if not logs:
        return f"No logs found for build #{build_id}."
    lines = [f"Build #{build_id}: {len(logs)} log file(s)", ""]
    for name in sorted(logs):
        marker = " ★" if any(k in name for k in KEY_LOG_TYPES) else ""
        lines.append(f"  {name}{marker}")
    lines.append("")
    lines.append("★ = key logs for debugging")
    return "\n".join(lines)


async def search_builds(
    page: int = 1,
    project: str | None = None,
    is_running: bool | None = None,
) -> str:
    client = _get_client()
    try:
        data = await client.search_builds(page, project, is_running)
    except Exception as e:
        return _api_error("searching builds", e)

    builds = data if isinstance(data, list) else data.get("builds", [])
    lines = [f"Builds (page {page}): {len(builds)} result(s)", ""]

    for b in builds[:20]:
        tasks = b.get("tasks", [])
        failed = sum(1 for t in tasks if t["status"] == 3)
        # NVR per package: a dependency question ("which build carries
        # mingw-glib2 >= 2.89.2?") cannot be answered from bare names.
        nvrs: dict[str, str] = {}
        for t in tasks:
            name, nvr = _task_package(t)
            nvrs.setdefault(name, nvr)
        status_str = f"{failed} failed" if failed else "ok"
        released = "released" if b.get("released") else "unreleased"
        lines.append(
            f"  #{b['id']}  {b['created_at'][:10]}  "
            f"tasks={len(tasks)} [{status_str}] [{released}]"
        )
        # A build is in a filtered result set because ONE of its packages
        # matched `project` — that package must never end up hidden behind
        # "(+N more)", or the search silently answers the wrong question.
        needle = (project or "").lower()
        matched = [n for n in sorted(nvrs) if needle and needle in n.lower()]
        if matched:
            lines.append(f"      match: {', '.join(nvrs[n] for n in matched)}")
        rest = [n for n in sorted(nvrs) if n not in set(matched)]
        if rest:
            pkg_str = ", ".join(nvrs[n] for n in rest[:3])
            if len(rest) > 3:
                pkg_str += f" (+{len(rest) - 3} more)"
            lines.append(f"      pkgs: {pkg_str}")

    return "\n".join(lines)


async def get_sign_task_status(build_id: int) -> str:
    client = _get_client()
    try:
        tasks = await client.get_sign_tasks(build_id)
    except Exception as e:
        return _api_error(f"getting sign tasks for build #{build_id}", e)

    if not tasks:
        return f"Build #{build_id}: no sign tasks."

    lines = [f"Build #{build_id}: {len(tasks)} sign task(s)", ""]
    for t in tasks:
        status = SIGN_TASK_STATUS.get(t.get("status"), f"unknown({t.get('status')})")
        line = f"  sign_task_id={t.get('id')}  [{status}]"
        key_id = t.get("sign_key_id")
        if key_id is None:
            key_id = (t.get("sign_key") or {}).get("id")
        if key_id is not None:
            line += f"  sign_key_id={key_id}"
        if t.get("error_message"):
            line += f"  error={t['error_message']}"
        lines.append(line)
    return "\n".join(lines)


async def get_products() -> str:
    client = _get_client()
    try:
        products = await client.get_products()
    except Exception as e:
        return _api_error("getting products", e)
    if not products:
        return "No products available."
    lines = [f"Products ({len(products)}):", ""]
    for p in sorted(products, key=lambda x: x.get("name", "").lower()):
        plats = ", ".join(
            pl.get("name", "") for pl in p.get("platforms", [])
        )
        kind = "community" if p.get("is_community") else "official"
        line = f"  id={p['id']:<5} {p['name']:30} [{kind}]"
        if plats:
            line += f"  platforms: {plats}"
        lines.append(line)
    return "\n".join(lines)


def _format_release_plan(release: dict, *, created: bool) -> str:
    """Format a release (plan + status) for the MCP client / CLI.

    Used both right after creating a plan and when viewing an existing one.
    Lists the source packages the plan would release and the target
    repositories, without dumping every per-arch RPM.
    """
    rid = release.get("id")
    raw_status = release.get("status")
    status = RELEASE_STATUS.get(raw_status, f"unknown({raw_status})")
    plan = release.get("plan") or {}
    packages = plan.get("packages") or []
    repos = plan.get("repositories") or []

    product = release.get("product")
    product_name = product.get("name") if isinstance(product, dict) else product
    platform = release.get("platform")
    platform_name = (
        platform.get("name") if isinstance(platform, dict) else platform
    )

    title = (
        f"Release plan #{rid} created" if created else f"Release plan #{rid}"
    )
    lines = [title, ""]
    lines.append(f"Status: {status}")
    if product_name:
        lines.append(f"Product: {product_name}")
    if platform_name:
        lines.append(f"Platform: {platform_name}")
    if release.get("build_ids") is not None:
        lines.append(f"Builds: {release['build_ids']}")
    lines.append(f"Build tasks: {len(release.get('build_task_ids') or [])}")

    # Distinct source packages (one per src.rpm), preserving plan order.
    sources: list[str] = []
    seen: set[str] = set()
    for entry in packages:
        pkg = entry.get("package") or {}
        nvr = "-".join(
            str(pkg.get(k, "")) for k in ("name", "version", "release")
        ).strip("-")
        if nvr and nvr not in seen:
            seen.add(nvr)
            sources.append(nvr)

    lines.append(
        f"Packages in plan: {len(packages)} "
        f"({len(sources)} source package(s))"
    )
    limit = 50
    for nvr in sources[:limit]:
        lines.append(f"  - {nvr}")
    if len(sources) > limit:
        lines.append(f"  ... (+{len(sources) - limit} more)")

    if repos:
        lines.append("")
        lines.append(f"Target repositories ({len(repos)}):")
        for repo in repos[:limit]:
            arch = repo.get("arch", "")
            name = repo.get("name", "")
            lines.append(f"  - {name} ({arch})")
        if len(repos) > limit:
            lines.append(f"  ... (+{len(repos) - limit} more)")

    if created:
        lines.append("")
        lines.append(f"URL: https://build.almalinux.org/release/{rid}")
        lines.append("")
        lines.append(
            "NOTE: This is only a release PLAN (status: scheduled). No "
            "packages have been published. Committing the plan — the actual "
            "release — is intentionally NOT supported by this MCP."
        )
    return "\n".join(lines)


async def get_release_plan(release_id: int) -> str:
    client = _get_client()
    try:
        release = await client.get_release(release_id)
    except Exception as e:
        return _api_error(f"getting release #{release_id}", e)
    return _format_release_plan(release, created=False)


# ═══════════════════════════════════════════════════════════════════════
#  Authenticated commands
# ═══════════════════════════════════════════════════════════════════════


async def get_sign_keys() -> str:
    client = _get_client()
    try:
        keys = await client.get_sign_keys()
        if not keys:
            return "No sign keys available."
        lines = ["Sign keys:", ""]
        for k in keys:
            platforms = k.get("platform_ids") or []
            plat_str = f"  platforms={platforms}" if platforms else ""
            active = "active" if k.get("active", True) else "inactive"
            lines.append(
                f"  id={k['id']}  name={k['name']}  "
                f"keyid={k['keyid']}  [{active}]{plat_str}"
            )
            if k.get("description"):
                lines.append(f"    {k['description']}")
        return "\n".join(lines)
    except Exception as e:
        return _api_error("getting sign keys", e)


async def get_flavors() -> str:
    client = _get_client()
    try:
        flavors = await client.get_flavors()
        if not flavors:
            return "No flavors available."
        lines = [f"Platform flavors ({len(flavors)}):", ""]
        for name, fid in sorted(flavors.items(), key=lambda x: x[0].lower()):
            lines.append(f"  id={fid:3d}  {name}")
        return "\n".join(lines)
    except Exception as e:
        return _api_error("getting flavors", e)


async def create_build(
    platform: str | None = None,
    platforms: list[str] | None = None,
    packages: list[str] | None = None,
    git_urls: list[str] | None = None,
    branch: str | None = None,
    from_tag: bool = False,
    from_srpm: bool = False,
    tags: list[str] | None = None,
    arch_list: list[str] | None = None,
    skip_tests: bool = False,
    add_epel_dist: bool = False,
    beta: bool = False,
    secureboot: bool = False,
    nosecureboot: bool = False,
    excludes: str | None = None,
    definitions: dict[str, str] | str | None = None,
    linked_builds: list[int] | None = None,
    flavors: list[str] | None = None,
    with_opts: list[str] | None = None,
    without_opts: list[str] | None = None,
    modules: list[str] | None = None,
    independent_tasks: bool = False,
) -> str:
    all_platforms: list[str] = []
    if platform:
        all_platforms.append(platform)
    if platforms:
        for p in platforms:
            if p not in all_platforms:
                all_platforms.append(p)
    if not all_platforms:
        return "Error: at least one of platform or platforms must be provided."

    if not packages and not git_urls:
        return "Error: at least one of packages or git_urls must be provided."
    if git_urls and from_srpm:
        return (
            "Error: git_urls cannot be used with from_srpm. "
            "git_urls are Git repository URLs, not SRPM URLs. "
            "Use packages for SRPM URLs."
        )

    pkg_dicts: list[dict[str, str]] = []

    if packages:
        if from_tag:
            for i, p in enumerate(packages):
                parts = p.strip().split(None, 1)
                if len(parts) == 2:
                    pkg_dicts.append({parts[0]: parts[1]})
                elif tags and i < len(tags):
                    pkg_dicts.append({p: tags[i]})
                else:
                    name = "-".join(p.split("/")[-1].split("-")[:-2])
                    pkg_dicts.append({name: p})
        else:
            for p in packages:
                pkg_dicts.append({p.strip(): "None"})

    if git_urls:
        for url in git_urls:
            if from_tag:
                parts = url.strip().split(None, 1)
                if len(parts) == 2:
                    pkg_dicts.append({parts[0]: parts[1]})
                else:
                    return (
                        "Error: git_urls with from_tag requires 'url tag' format. "
                        f"Got: {url}"
                    )
            else:
                pkg_dicts.append({url.strip(): "None"})

    if definitions is None:
        defs = None
    elif isinstance(definitions, str):
        defs = json.loads(definitions)
    else:
        defs = dict(definitions)
    excl = excludes.split() if excludes else None
    notes: list[str] = []

    if skip_tests:
        if defs is None:
            defs = {}
        defs["__spec_check_template"] = "exit 0;"
        notes.append("Tests disabled (__spec_check_template)")

    if add_epel_dist:
        if not from_tag and not from_srpm:
            return (
                "Error: add_epel_dist requires from_tag or from_srpm. "
                "The dist suffix is extracted from the package name/URL."
            )
        notes.append(
            "add-epel-dist: per-task dist definition "
            "(.elN.alma_altarch) from package name"
        )

    if independent_tasks:
        notes.append(
            "independent_tasks: per-platform task chain disabled "
            "(packages build in parallel, not sequentially)"
        )

    client = _get_client()
    try:
        result = await client.create_build(
            packages=pkg_dicts,
            platforms=all_platforms,
            arch_list=arch_list,
            branch=branch,
            from_tag=from_tag,
            from_srpm=from_srpm,
            beta=beta,
            secureboot=secureboot,
            nosecureboot=nosecureboot,
            excludes=excl,
            definitions=defs,
            linked_builds=linked_builds,
            additional_flavors=flavors,
            with_opts=with_opts,
            without_opts=without_opts,
            modules=modules,
            add_epel_dist=add_epel_dist,
            independent_tasks=independent_tasks,
        )
        lines = [
            "Build created successfully!",
            f"Build ID: {result['id']}",
            f"Created at: {result['created_at']}",
            f"URL: https://build.almalinux.org/build/{result['id']}",
        ]
        if notes:
            lines.append("")
            lines.append("Applied settings:")
            for note in notes:
                lines.append(f"  • {note}")
        return "\n".join(lines)
    except Exception as e:
        return _api_error("creating build", e)


async def sign_build(build_id: int, sign_key_id: int = 4) -> str:
    client = _get_client()
    try:
        result = await client.sign_build(build_id, sign_key_id)
        return (
            f"Sign task created for build #{build_id}\n"
            f"Sign task ID: {result['id']}\n"
            f"Status: {SIGN_TASK_STATUS.get(result['status'], 'unknown')}"
        )
    except Exception as e:
        return _api_error("signing build", e)


async def create_release_plan(
    build_id: int,
    platform: str,
    product: str,
    build_ids: list[int] | None = None,
    whole_packages_only: bool = False,
) -> str:
    """Create a release PLAN (status: scheduled) — never commits/publishes.

    Resolves platform and product names to ids, collects the completed build
    tasks across the given build(s), and asks ALBS to compute the plan.

    Args:
        build_id: Primary build to release.
        platform: Target platform name (e.g. "AlmaLinux-9").
        product: Target product name (e.g. "AlmaLinux", "epel-al").
        build_ids: Optional extra build ids to include in the same plan.
        whole_packages_only: Include only packages whose every arch task
            completed (drop half-built packages). Default False.
    """
    client = _get_client()

    # Creating a release plan is an authenticated write. Fail fast before
    # making any (public) read calls so a missing token returns immediately
    # with a clear auth error instead of doing pointless work.
    if not client.jwt_token:
        return (
            "Auth error: creating a release plan requires a JWT token. "
            "Pass --token or set ALBS_JWT_TOKEN."
        )

    try:
        platform_ids = await client.get_platform_ids()
    except Exception as e:
        return _api_error("getting platforms", e)
    if platform not in platform_ids:
        return (
            f"Error: unknown platform '{platform}'. "
            f"Available: {', '.join(sorted(platform_ids))}"
        )
    platform_id = platform_ids[platform]

    try:
        product_ids = await client.get_product_ids()
    except Exception as e:
        return _api_error("getting products", e)
    if product not in product_ids:
        return (
            f"Error: unknown product '{product}'. "
            f"Available: {', '.join(sorted(product_ids))}"
        )
    product_id = product_ids[product]

    # Primary build first, then any extras (de-duplicated, order preserved).
    all_build_ids: list[int] = [build_id]
    for bid in build_ids or []:
        if bid not in all_build_ids:
            all_build_ids.append(bid)

    task_ids: list[int] = []
    for bid in all_build_ids:
        try:
            info = await client.get_build(bid)
        except Exception as e:
            return _api_error(f"getting build #{bid}", e)
        ids = (
            get_whole_package_task_ids(info)
            if whole_packages_only
            else get_completed_task_ids(info)
        )
        task_ids.extend(ids)

    if not task_ids:
        scope = (
            "fully-completed packages"
            if whole_packages_only
            else "completed tasks"
        )
        return (
            f"Error: no {scope} found in build(s) {all_build_ids}. "
            "A release plan needs completed build tasks."
        )

    try:
        release = await client.create_release(
            build_ids=all_build_ids,
            build_task_ids=task_ids,
            platform_id=platform_id,
            product_id=product_id,
        )
    except Exception as e:
        return _api_error("creating release plan", e)

    if not release.get("id"):
        return f"Error: ALBS release creation returned no id: {release}"

    # The /new/ response usually already carries the computed plan; if a
    # deployment returns only the id, fetch the full release to show it.
    if not (release.get("plan") or {}).get("packages"):
        try:
            release = await client.get_release(release["id"])
        except Exception:
            pass  # fall back to the create response — id/status still useful

    return _format_release_plan(release, created=True)


async def commit_release(release_id: int) -> str:
    return (
        "Committing a release (the actual release that publishes packages) "
        "is intentionally blocked.\n"
        "This MCP only creates release plans. Commit it manually in the "
        "build system if you really intend to publish."
    )


async def delete_build(build_id: int) -> str:
    return (
        "Build deletion is currently blocked for safety.\n"
        "Can be removed manually in the build system."
    )


# ═══════════════════════════════════════════════════════════════════════
#  Errata (advisories)
# ═══════════════════════════════════════════════════════════════════════


class _CommandError(Exception):
    """A finished user-facing error string ("Error: ..." / "Auth error: ...")."""


def _debrand(text: str) -> str:
    for pattern, repl in ERRATA_DEBRAND_PATTERNS:
        text = re.sub(pattern, repl, text)
    return text


def _severity(value: str | None, source: str) -> str:
    """Normalize a Red Hat severity ("important" → "Important")."""
    severity = (value or "").strip().capitalize()
    if severity not in ERRATA_SEVERITIES:
        raise ValueError(
            f"{source} has no usable severity in Red Hat data ({value!r}); "
            f"expected one of {ERRATA_SEVERITIES}."
        )
    return severity


def _utc_z(timestamp: str | None) -> str | None:
    """'2026-03-18T00:00:00+00:00' → '2026-03-18T00:00:00Z', the form ALBS stores."""
    if timestamp and timestamp.endswith("+00:00"):
        return timestamp[:-6] + "Z"
    return timestamp


def _rh_affected(
    package_names: Iterable[str | None], releases: Iterable[str | None]
) -> tuple[set[str], set[str]]:
    """Source package names and module streams a Red Hat CVE record affects.

    `package_names` are package_state entries: "kernel", "nodejs:26/nodejs"
    (module stream / component), or a container repo "rhel9/foo" (ignored).
    `releases` are affected_release packages: an NEVR with epoch
    ("nodejs22-1:22.23.2-2.el10_0") or a module NSVC
    ("nodejs:22-8100020260928121333.6d880403"). Anything else (container
    tags, layered products) is ignored — the result is only ever matched
    against a build's own source packages, so over-collecting is harmless.
    """
    names: set[str] = set()
    modules: set[str] = set()
    for value in package_names:
        if not value:
            continue
        if "/" in value:
            prefix, component = value.split("/", 1)
            if ":" in prefix:
                modules.add(prefix)
                names.add(component)
            continue
        names.add(value)
    for value in releases:
        if not value or "/" in value:
            continue
        nevr = re.fullmatch(r"(.+)-\d+:[^-:]+-[^-:]+", value)
        if nevr:
            names.add(nevr.group(1))
            continue
        nsvc = re.fullmatch(r"([^:]+:[^-:]+)-\d+\.\w+", value)
        if nsvc:
            modules.add(nsvc.group(1))
    return names, modules


def _cve_from_rh(data: dict) -> dict:
    """Normalize a Red Hat per-CVE record to what an advisory needs."""
    cve_id = data["name"]
    if not data.get("public_date"):
        raise ValueError(f"{cve_id} has no public date in Red Hat data.")
    bugzilla = data.get("bugzilla") or {}
    names, modules = _rh_affected(
        (
            p.get("package_name") for p in data.get("package_state") or []
            if p.get("fix_state") != "Not affected"
        ),
        (r.get("package") for r in data.get("affected_release") or []),
    )
    return {
        "id": cve_id,
        "impact": _severity(data.get("threat_severity"), cve_id),
        "public": data["public_date"],
        "cvss3": (data.get("cvss3") or {}).get("cvss3_scoring_vector"),
        "cwe": data.get("cwe"),
        "summary": bugzilla.get("description"),
        "bugzilla_id": bugzilla.get("id"),
        "bugzilla_url": bugzilla.get("url"),
        "packages": names,
        "modules": modules,
    }


def _csaf_source_purls(node: dict) -> Iterator[str]:
    """Package URLs of every source RPM in a CSAF product tree."""
    for branch in node.get("branches") or []:
        yield from _csaf_source_purls(branch)
    product = node.get("product") or {}
    purl = (product.get("product_identification_helper") or {}).get("purl", "")
    if "arch=src" in purl:
        yield purl


def _advisory_from_csaf(data: dict) -> dict:
    """Normalize a Red Hat advisory (CSAF) to the same shape as CVE input.

    The details note is the text AlmaLinux advisories carry as their
    description ("The kernel packages contain ... Security Fix(es): ...").
    Source packages come from the src purls; a modular one carries
    `rpmmod=name:stream:version:context`.
    """
    doc = data["document"]
    advisory_id = doc["tracking"]["id"]
    severity = _severity(
        (doc.get("aggregate_severity") or {}).get("text"), advisory_id
    )
    details = next(
        (n["text"] for n in doc.get("notes") or [] if n.get("category") == "general"),
        None,
    )
    names: set[str] = set()
    modules: set[str] = set()
    for purl in _csaf_source_purls(data.get("product_tree") or {}):
        name = re.match(r"pkg:rpm/[^/]+/([^@?]+)", purl)
        if name:
            names.add(name.group(1))
        module = re.search(r"[?&]rpmmod=([^:&]+):([^:&]+)", purl)
        if module:
            modules.add(f"{module.group(1)}:{module.group(2)}")

    cves = []
    for vuln in data.get("vulnerabilities") or []:
        cve_id = vuln.get("cve")
        if not cve_id:
            continue
        impact = next(
            (t.get("details") for t in vuln.get("threats") or []
             if t.get("category") == "impact"),
            None,
        )
        cvss3 = next(
            (s["cvss_v3"].get("vectorString") for s in vuln.get("scores") or []
             if s.get("cvss_v3")),
            None,
        )
        bz_id = next(
            (i.get("text") for i in vuln.get("ids") or []
             if "Bugzilla" in i.get("system_name", "")),
            None,
        )
        bz_url = next(
            (r.get("url") for r in vuln.get("references") or []
             if r.get("summary", "").startswith("RHBZ#")),
            None,
        )
        public = _utc_z(vuln.get("release_date") or vuln.get("discovery_date"))
        if not public:
            raise ValueError(f"{cve_id} has no public date in {advisory_id}.")
        cves.append({
            "id": cve_id,
            "impact": _severity(impact or severity, cve_id),
            "public": public,
            "cvss3": cvss3,
            "cwe": (vuln.get("cwe") or {}).get("id"),
            "summary": vuln.get("title"),
            "bugzilla_id": bz_id,
            "bugzilla_url": bz_url,
        })
    if not cves:
        raise ValueError(
            f"{advisory_id} lists no CVEs — an AlmaLinux security advisory "
            "must reference at least one CVE."
        )
    title = (doc.get("title") or "").removeprefix(RH_ADVISORY_TITLE_PREFIX)
    title = re.sub(r"^(?:Low|Moderate|Important|Critical):\s*", "", title)
    return {
        "label": advisory_id,
        "title": _debrand(title) if title else None,
        "severity": severity,
        "description": (
            _normalize_fix_lists(_debrand(details)) if details else None
        ),
        "cves": cves,
        "packages": names,
        "modules": modules,
    }


async def _load_security_data(
    client: ALBSClient, cves: list[str] | None, rhsa: str | None
) -> dict:
    """Severity, CVEs, description and affected packages from Red Hat."""
    if bool(cves) == bool(rhsa):
        raise _CommandError(
            "Error: pass either cves (one or more CVE IDs) or rhsa (one Red "
            "Hat advisory ID, e.g. RHSA-2026:13566) — exactly one of them."
        )
    if rhsa:
        advisory_id = rhsa.strip().upper()
        if not re.fullmatch(RH_ADVISORY_ID_PATTERN, advisory_id):
            raise _CommandError(
                f"Error: invalid Red Hat advisory ID {rhsa!r} "
                "(expected e.g. RHSA-2026:13566)."
            )
        try:
            data = await client.get_rh_advisory(advisory_id)
        except Exception as e:
            raise _CommandError(
                _api_error(f"getting Red Hat advisory {advisory_id}", e)
            ) from e
        return _advisory_from_csaf(data)

    cve_ids = list(dict.fromkeys(c.strip().upper() for c in cves or []))
    bad = [c for c in cve_ids if not re.fullmatch(CVE_ID_PATTERN, c)]
    if bad:
        raise _CommandError(
            f"Error: invalid CVE ID(s) {bad} (expected e.g. CVE-2026-31431)."
        )
    infos = []
    for cve_id in cve_ids:
        try:
            data = await client.get_rh_cve(cve_id)
        except Exception as e:
            raise _CommandError(
                _api_error(f"getting Red Hat data for {cve_id}", e)
            ) from e
        infos.append(_cve_from_rh(data))
    return {
        "label": ", ".join(cve_ids),
        "severity": max(
            (c["impact"] for c in infos), key=ERRATA_SEVERITIES.index
        ),
        "title": None,
        "description": None,
        "cves": infos,
        "packages": set().union(*(c["packages"] for c in infos)),
        "modules": set().union(*(c["modules"] for c in infos)),
    }


def _parse_build_ref(ref: int | str) -> int:
    """Build ID from 54743, "54743" or "https://build.almalinux.org/build/54743"."""
    if isinstance(ref, int):
        return ref
    match = re.fullmatch(r"\s*(?:https?://\S*?/build/)?(\d+)/?\s*", str(ref))
    if not match:
        raise ValueError(f"not a build ID or ALBS build URL: {ref!r}")
    return int(match.group(1))


def _rpm_nvra(filename: str) -> tuple[str, str, str, str]:
    """('kernel-doc', '4.18.0', '553.el8_10', 'noarch') from an RPM file name."""
    nvr, arch = filename.removesuffix(".rpm").rsplit(".", 1)
    name, version, release = nvr.rsplit("-", 2)
    return name, version, release, arch


def _build_units(tasks: list[dict]) -> dict[str, dict]:
    """Split one platform's tasks into advisory units.

    A unit is what an advisory includes or leaves out as a whole — the
    source package of a regular build ("kernel"), or the module stream of a
    modular one ("nodejs:24", taken from the tasks' rpm_modules, -devel
    module excluded): a module ships as one piece, so every package built
    for it goes in. Tasks are grouped by their source ref; the unit name of
    a regular package is its src.rpm name, else the dist-git repo name.
    Returns {unit: {"module": "name:stream" | None, "tasks": [...]}}.
    """
    groups: dict[tuple, list[dict]] = {}
    for t in tasks:
        ref = t.get("ref") or {}
        groups.setdefault((ref.get("url"), ref.get("git_ref")), []).append(t)

    units: dict[str, dict] = {}
    for group in groups.values():
        streams = sorted({
            f"{m['name']}:{m['stream']}"
            for t in group for m in t.get("rpm_modules") or []
            if not m["name"].endswith("-devel")
        })
        if len(streams) > 1:
            raise ValueError(
                f"tasks of {_task_package(group[0])[0]} belong to several "
                f"module streams {streams}."
            )
        module = streams[0] if streams else None
        if module:
            name = module
        else:
            srpm = next(
                (a["name"] for t in group for a in t.get("artifacts") or []
                 if a.get("type") == "rpm" and a["name"].endswith(".src.rpm")),
                None,
            )
            name = _rpm_nvra(srpm)[0] if srpm else _task_package(group[0])[0]
        units.setdefault(name, {"module": module, "tasks": []})["tasks"].extend(group)
    return units


def _unit_affected(unit: str, module: str | None, sec: dict) -> bool:
    """Whether Red Hat data says this unit is affected by the advisory."""
    if module is None:
        return unit in sec["packages"]
    module_name = module.split(":")[0]
    return (
        module in sec["modules"]
        or module_name in sec["packages"]
        or module_name in {m.split(":")[0] for m in sec["modules"]}
    )


def _unit_wanted(unit: str, module: str | None, wanted: set[str]) -> bool:
    """Whether the package the user named selects this unit.

    "nodejs" or "nodejs:24" both select the nodejs:24 module unit.
    """
    return unit in wanted or (
        module is not None and module.split(":")[0] in wanted
    )


def _unit_packages(tasks: list[dict], build_id: int) -> list[dict]:
    """Advisory package entries of the completed tasks, one per NEVRA.

    Binary RPMs only — src, debuginfo and debugsource are left out, the same
    rule the ALBS UI applies. A noarch RPM is built on every arch but only
    one copy carries metadata; a file with no metadata copy anywhere has an
    unknown epoch and fails the call rather than going in as epoch 0.
    """
    found: dict[tuple, dict] = {}
    bare: dict[tuple, str] = {}
    for t in tasks:
        if t.get("status") != BUILD_TASK_COMPLETED:
            continue
        for a in t.get("artifacts") or []:
            if a.get("type") != "rpm" or any(
                m in a["name"] for m in ERRATA_SKIP_RPM_MARKERS
            ):
                continue
            meta = a.get("meta")
            if not meta:
                bare[_rpm_nvra(a["name"])] = a["name"]
                continue
            key = (meta["name"], meta["version"], meta["release"], meta["arch"])
            found.setdefault(key, {
                "name": meta["name"],
                "epoch": int(meta.get("epoch") or 0),
                "version": meta["version"],
                "release": meta["release"],
                "arch": meta["arch"],
                "reboot_suggested": False,
                "build_id": build_id,
            })
    missing = sorted(name for key, name in bare.items() if key not in found)
    if missing:
        raise ValueError(
            f"build #{build_id}: ALBS has no package metadata (epoch) for "
            f"{missing[:5]}{' ...' if len(missing) > 5 else ''}."
        )
    return [found[k] for k in sorted(found)]


def _list_names(names: list[str], limit: int = 15) -> str:
    shown = ", ".join(names[:limit])
    return shown + (f" (+{len(names) - limit} more)" if len(names) > limit else "")


def _errata_title(units: list[str]) -> str:
    """'kernel security update', 'a and b security update', 'a, b, and c ...'."""
    if len(units) > 2:
        subject = f"{', '.join(units[:-1])}, and {units[-1]}"
    else:
        subject = " and ".join(units)
    return f"{subject} security update"


def _broken_tasks(build_id: int, units: dict[str, dict]) -> list[dict]:
    """Every failed or cancelled task of the build, whatever its unit.

    These are ALWAYS reported to the user — also when a retry build covers
    the arch, and also for source packages the advisory leaves out.
    """
    broken_states = (
        BUILD_TASK_STATUS_BY_NAME["failed"], BUILD_TASK_STATUS_BY_NAME["cancelled"]
    )
    return [
        {
            "build_id": build_id,
            "unit": unit,
            "arch": t["arch"],
            "task_id": t["id"],
            "status": BUILD_TASK_STATUS[t["status"]],
        }
        for unit, info in units.items() for t in info["tasks"]
        if t.get("status") in broken_states
    ]


def _broken_text(broken: list[dict]) -> str:
    """' Failed/cancelled tasks: build #1 kernel aarch64 (task 7, failed).'"""
    if not broken:
        return ""
    return " Failed/cancelled tasks: " + ", ".join(
        f"build #{b['build_id']} {b['unit']} {b['arch']} "
        f"(task {b['task_id']}, {b['status']})"
        for b in broken
    ) + "."


def _errata_drafts(
    build: dict, sec: dict, wanted: set[str] | None
) -> list[dict]:
    """One advisory draft per platform the build targets.

    Picks the unit `wanted` names (the package the user gave), else the
    units Red Hat lists as affected by the advisory;
    reports the rest as left out, and collects their binary packages. Every
    failed / cancelled task is recorded for the report, chosen unit or not.
    """
    build_id = build["id"]
    by_platform: dict[int, dict] = {}
    for t in build.get("tasks") or []:
        plat = t["platform"]
        by_platform.setdefault(
            plat["id"], {"name": plat["name"], "tasks": []}
        )["tasks"].append(t)
    if not by_platform:
        raise ValueError(f"build #{build_id} has no tasks.")

    running_states = (
        BUILD_TASK_STATUS_BY_NAME["idle"], BUILD_TASK_STATUS_BY_NAME["started"]
    )
    drafts = []
    for platform_id, plat in by_platform.items():
        tasks = plat["tasks"]
        where = f"build #{build_id} ({plat['name']})"
        running = [t for t in tasks if t.get("status") in running_states]
        if running:
            raise ValueError(
                f"{where} is still running ({len(running)} task(s) not "
                "finished) — create the advisory once it completes."
            )
        units = _build_units(tasks)
        modules = {u: info["module"] for u, info in units.items()}
        broken = _broken_tasks(build_id, units)
        if wanted is None:
            chosen = sorted(
                u for u, m in modules.items() if _unit_affected(u, m, sec)
            )
        else:
            chosen = sorted(
                u for u, m in modules.items() if _unit_wanted(u, m, wanted)
            )
        if not chosen:
            if wanted is not None:
                raise ValueError(
                    f"{where}: none of its source packages {sorted(units)} "
                    f"is package {', '.join(sorted(wanted))!r}."
                    + _broken_text(broken)
                )
            affected = sorted(sec["packages"] | sec["modules"])
            raise ValueError(
                f"{where}: none of its source packages {sorted(units)} is "
                f"affected by {sec['label']} according to Red Hat "
                f"(affected: {_list_names(affected) or 'none listed'}). "
                "Name the package (or module) explicitly with "
                "package=..." + _broken_text(broken)
            )
        streams = {modules[u] for u in chosen}
        if len(streams) > 1:
            raise ValueError(
                f"{where}: {chosen} mix modular and non-modular (or several "
                "module) packages, but one advisory carries at most one "
                "module. Name one with package=..."
                + _broken_text(broken)
            )

        packages: list[dict] = []
        completed: dict[tuple[str, str], set[int]] = {}
        for unit in chosen:
            for t in units[unit]["tasks"]:
                if t.get("status") == BUILD_TASK_COMPLETED:
                    completed.setdefault((unit, t["arch"]), set()).add(build_id)
            packages.extend(_unit_packages(units[unit]["tasks"], build_id))
        drafts.append({
            "build_ids": [build_id],
            "platform_id": platform_id,
            "platform_name": plat["name"],
            "module": streams.pop(),
            "units": chosen,
            "skipped": sorted(set(units) - set(chosen)),
            "all_units": modules,
            "packages": packages,
            "broken": broken,
            "completed": completed,
        })
    return drafts


def _broken_warning(b: dict, chosen: list[str], completed: dict) -> str:
    """One `! FAILED` line; says what the failure means for the advisory."""
    head = (
        f"{b['status'].upper()}: build #{b['build_id']} {b['unit']} "
        f"{b['arch']} (task {b['task_id']})"
    )
    if b["unit"] not in chosen:
        return f"{head} — not affected, left out of the advisory anyway"
    rebuilt = sorted(completed.get((b["unit"], b["arch"]), set()) - {b["build_id"]})
    if rebuilt:
        return (
            f"{head} — rebuilt in build "
            f"{', '.join(f'#{r}' for r in rebuilt)}; packages taken from there"
        )
    return (
        f"{head} — its packages are NOT in the advisory "
        "(add the retry build to include them)"
    )


def _merge_drafts(drafts: list[dict]) -> list[dict]:
    """Fold drafts of the same platform and source packages into one.

    That is a build plus its retry / split build — ALSA-2026:A003 carries
    kernel from build 54740 (aarch64 failed) and 54757 (aarch64 rebuilt).
    Different source packages on one platform stay separate advisories
    (kernel ALSA-2026:A004 vs kernel-rt A007). Packages are de-duplicated by
    NEVRA, first build wins. Every failed / cancelled task becomes a warning,
    including the ones a merged retry build covers.
    """
    merged: dict[tuple, dict] = {}
    for d in drafts:
        key = (d["platform_id"], tuple(d["units"]))
        into = merged.get(key)
        if into is None:
            merged[key] = {
                **d,
                "packages": list(d["packages"]),
                "completed": {k: set(v) for k, v in d["completed"].items()},
            }
            continue
        into["build_ids"] = into["build_ids"] + d["build_ids"]
        into["skipped"] = sorted(set(into["skipped"]) | set(d["skipped"]))
        into["all_units"] = {**into["all_units"], **d["all_units"]}
        into["broken"] = into["broken"] + d["broken"]
        for k, builds in d["completed"].items():
            into["completed"].setdefault(k, set()).update(builds)
        nevras = {
            (p["name"], p["epoch"], p["version"], p["release"], p["arch"])
            for p in into["packages"]
        }
        into["packages"].extend(
            p for p in d["packages"]
            if (p["name"], p["epoch"], p["version"], p["release"], p["arch"])
            not in nevras
        )
    for d in merged.values():
        if not d["packages"]:
            raise ValueError(
                f"{_draft_builds(d)} ({d['platform_name']}): {d['units']} "
                "produced no completed binary packages."
                + _broken_text(d["broken"])
            )
        d["warnings"] = [
            _broken_warning(b, d["units"], d["completed"]) for b in d["broken"]
        ]
    return list(merged.values())


def _errata_references(cves: list[dict]) -> list[dict]:
    """CVE references (with the CVE record ALBS stores) + their bugzillas."""
    refs: list[dict] = [
        {
            "href": f"{RH_CVE_PAGE}/{c['id']}",
            "ref_id": c["id"],
            "ref_type": "cve",
            "title": c["id"],
            "cve": {
                "id": c["id"],
                "cvss3": c["cvss3"],
                "cwe": c["cwe"],
                "impact": c["impact"],
                "public": c["public"],
            },
        }
        for c in cves
    ]
    seen: set[str] = set()
    for c in cves:
        bz_id = c["bugzilla_id"]
        if bz_id and bz_id not in seen:
            seen.add(bz_id)
            refs.append({
                "href": c["bugzilla_url"] or f"{RH_BUGZILLA}{bz_id}",
                "ref_id": bz_id,
                "ref_type": "bugzilla",
            })
    return refs


def _title_subjects(title: str | None) -> list[str]:
    """Packages an advisory title names.

    'Important: nodejs:22 security, bug fix, and enhancement update' →
    ['nodejs:22']; 'python3.11 and python3.12 security update' → both.
    """
    rest = re.sub(r"^(?:Low|Moderate|Important|Critical):\s*", "", title or "")
    subject = re.split(
        r"\s+(?:security|bug fix|enhancement|update)\b", rest, maxsplit=1
    )[0]
    return [n for n in re.split(r",\s*(?:and\s+)?|\s+and\s+", subject) if n]


def _description_intro(text: str | None) -> str | None:
    """What a security advisory says about the package before its fix list.

    'The kernel packages contain the Linux kernel, ...' from a description
    '<intro>\\n\\nSecurity Fix(es):\\n...'. None when there is no fix list
    (bug fix advisories say 'see the Release Notes' instead).
    """
    if not text or "Security Fix(es):" not in text:
        return None
    intro = text.split("Security Fix(es):", 1)[0].strip()
    intro = "\n".join(line.rstrip() for line in intro.splitlines())
    return _debrand(intro) or None


async def _package_intro(
    client: ALBSClient, unit: str, platform_id: int
) -> tuple[str, dict] | None:
    """(intro, advisory) — the package description of the newest earlier
    security advisory for `unit`.

    The same platform is searched first, then all of them. An advisory titled
    with the unit itself ("nodejs:24") beats one for another stream of the
    same module ("nodejs:22"); a different package whose name merely
    contains the unit ("kernel-rt" for "kernel") never matches.
    """
    base = unit.split(":")[0]
    for pid in (platform_id, None):
        fallback = None
        for page in range(1, ERRATA_INTRO_SEARCH_PAGES + 1):
            data = await client.query_errata(
                title=base, platform_id=pid, page=page
            )
            records = data.get("records") or []
            for r in records:
                intro = _description_intro(
                    r.get("description") or r.get("original_description")
                )
                if not intro:
                    continue
                names = _title_subjects(r.get("title") or r.get("original_title"))
                if unit in names:
                    return intro, r
                if fallback is None and ":" in unit and any(
                    n.split(":")[0] == base and ":" in n for n in names
                ):
                    fallback = intro, r
            if len(records) < 10:
                break
        if fallback:
            return fallback
    return None


def _record_cves(record: dict) -> set[str]:
    """CVE IDs an existing advisory references, upper-cased (some store
    'cve-2026-43284')."""
    return {
        str(ref.get("ref_id", "")).upper()
        for ref in record.get("references") or []
        if str(ref.get("ref_type", "")).split(".")[-1] == "cve"
    }


async def _advisories_with_cves(
    client: ALBSClient, cve_ids: list[str], platform_id: int
) -> dict[str, dict]:
    """Existing advisories on `platform_id` that reference any of `cve_ids`.

    ALBS's cveId filter is a substring match, so each record's references
    are checked for the exact CVE. Returns {advisory id: {"record": ...,
    "cves": {matched CVE ids}}}.
    """
    found: dict[str, dict] = {}
    for cve_id in cve_ids:
        page = 1
        while True:
            data = await client.query_errata(
                cve_id=cve_id, platform_id=platform_id, page=page
            )
            records = data.get("records") or []
            for r in records:
                if cve_id in _record_cves(r):
                    found.setdefault(
                        r["id"], {"record": r, "cves": set()}
                    )["cves"].add(cve_id)
            if len(records) < 10 or page * 10 >= (data.get("total_records") or 0):
                break
            page += 1
    return found


def _same_package(record: dict, unit: str, module: str | None) -> bool:
    """Whether an existing advisory is for the same package / module stream.

    By its module field for a module stream, else by its packages' source
    RPM, with the title as a fallback — so kernel-rt is not kernel and
    nodejs:22 is not nodejs:24.
    """
    if module:
        if record.get("module"):
            return record["module"] == module
    elif any(
        p.get("source_srpm") == unit for p in record.get("packages") or []
    ):
        return True
    return unit in _title_subjects(
        record.get("title") or record.get("original_title")
    )


def _fix_line(text: str) -> str:
    """One list item the AlmaLinux way: '  * text'."""
    return f"  * {text}"


def _fixes_text(cves: list[dict]) -> str:
    """'Security Fix(es):' list built from the CVEs, with the closing note.

    One fix per line, two-space indent, no blank lines between them — the
    layout of AlmaLinux advisories (ALSA-2026:A010, ALSA-2026:87073).
    """
    fixes = "\n".join(
        _fix_line(f"{c['summary'] or c['id']} ({c['id']})") for c in cves
    )
    return f"Security Fix(es):\n\n{fixes}\n\n{ERRATA_DESCRIPTION_FOOTER}"


def _normalize_fix_lists(text: str) -> str:
    """Re-lay Red Hat's '* item\\n\\n* item' lists as AlmaLinux's '  * item' lines.

    CSAF details put a blank line between list items; AlmaLinux advisories
    list them one per line with a two-space indent. A blank line is dropped
    only when it sits between two list items, so paragraphs stay apart.
    """
    lines = [
        _fix_line(m.group(1)) if (m := re.match(r"\s*\*\s+(.*)", line)) else line
        for line in text.splitlines()
    ]
    is_item = [line.startswith("  * ") for line in lines]
    return "\n".join(
        line for i, line in enumerate(lines)
        if line.strip()
        or not (0 < i < len(lines) - 1 and is_item[i - 1] and is_item[i + 1])
    )


def _next_errata_number(existing: Iterable[str], year: int) -> int:
    """Highest A-number used in `year` on any platform, plus one."""
    numbers = [
        int(m.group(2))
        for errata_id in existing
        if (m := re.fullmatch(ALMA_ERRATA_ID_PATTERN, errata_id))
        and int(m.group(1)) == year
    ]
    return max(numbers, default=0) + 1


def _errata_url(errata_id: str, platform_id: int) -> str:
    return f"{ALBS_URL}/errata?id={errata_id}&platform_id={platform_id}"


async def _prepare_errata(
    client: ALBSClient,
    builds: list[int | str],
    cves: list[str] | None,
    rhsa: str | None,
    package: str | None,
    issued_date: str | None,
    updated_date: str | None,
    title: str | None,
    description: str | None,
) -> tuple[dict, list[dict]]:
    """Validate input, read Red Hat + build data, number and build payloads.

    Shared by preview_errata and create_errata so the preview is exactly
    what gets created. Drafts are ordered by platform id (AlmaLinux-8, -9,
    -10 …) and numbered from the next free AlmaLinux ID of the issue year,
    the way ALSA-2026:A001..A003 were. Dates default to today.

    Title: `title`, else the RHSA's, else "<package> security update".
    Description: `description`, else the RHSA's details, else the package
    description of the newest earlier advisory for the package followed by
    the CVE fix list. Raises _CommandError / ValueError.
    """
    try:
        issued = (
            datetime.date.fromisoformat(issued_date)
            if issued_date else datetime.date.today()
        )
        updated = (
            datetime.date.fromisoformat(updated_date) if updated_date else issued
        )
    except (TypeError, ValueError):
        raise _CommandError(
            "Error: dates must be YYYY-MM-DD (got "
            f"issued_date={issued_date!r}, updated_date={updated_date!r})."
        ) from None
    if updated < issued:
        raise _CommandError(
            f"Error: updated_date {updated} is before issued_date {issued}."
        )
    build_ids = list(dict.fromkeys(_parse_build_ref(b) for b in builds or []))
    if not build_ids:
        raise _CommandError("Error: at least one build is required.")

    sec = await _load_security_data(client, cves, rhsa)
    wanted = {package.strip()} if package and package.strip() else None

    drafts: list[dict] = []
    for build_id in build_ids:
        try:
            build = await client.get_build(build_id)
        except Exception as e:
            raise _CommandError(_api_error(f"getting build #{build_id}", e)) from e
        drafts.extend(_errata_drafts(build, sec, wanted))

    drafts = _merge_drafts(drafts)
    drafts.sort(key=lambda d: d["platform_id"])
    try:
        existing = await client.list_alma_errata_ids(issued.year)
    except Exception as e:
        raise _CommandError(
            _api_error("listing AlmaLinux advisory IDs", e)
        ) from e
    number = _next_errata_number(existing, issued.year)

    references = _errata_references(sec["cves"])
    for offset, d in enumerate(drafts):
        if description:
            text = description
            d["description_note"] = "as given"
        elif sec["description"]:
            text = sec["description"]
            d["description_note"] = f"details text of {sec['label']}"
        else:
            intros, sources, missing = [], [], []
            for unit in d["units"]:
                try:
                    found = await _package_intro(client, unit, d["platform_id"])
                except Exception as e:
                    raise _CommandError(_api_error(
                        f"searching earlier advisories for {unit}", e
                    )) from e
                if found:
                    intros.append(found[0])
                    sources.append(found[1]["id"])
                else:
                    missing.append(unit)
            text = "\n\n".join([*intros, _fixes_text(sec["cves"])])
            notes = []
            if sources:
                notes.append(
                    f"package text from {', '.join(sources)} + CVE fix list"
                )
            if missing:
                notes.append(
                    f"NOTE: no earlier security advisory found for "
                    f"{', '.join(missing)}, so there is no package text — "
                    "pass description=... to add it"
                )
            d["description_note"] = "; ".join(notes)
        # The user named the package: say so when Red Hat does not tie one
        # of the given CVEs to it — usually a wrong CVE ID.
        d["notes"] = [
            f"NOTE: Red Hat does not list {', '.join(d['units'])} as affected "
            f"by {c['id']} — check the CVE list"
            for c in sec["cves"]
            if wanted is not None and "packages" in c
            and not any(_unit_affected(u, d["module"], c) for u in d["units"])
        ]
        d["payload"] = {
            "id": f"{ALMA_ERRATA_PREFIX}-{issued.year}:A{number + offset:03d}",
            "platform_id": d["platform_id"],
            "issued_date": issued.isoformat(),
            "updated_date": updated.isoformat(),
            "title": title or sec["title"] or _errata_title(d["units"]),
            "description": text,
            "module": d["module"] or "",
            "devel_module": False,
            "severity": sec["severity"],
            "references": references,
            "packages": d["packages"],
        }

    # Advisories that already carry these CVEs on the same platform: for the
    # same package that is a duplicate (create_errata refuses it unless
    # forced); for another package it is only worth a NOTE (kernel and
    # kernel-rt legitimately share CVEs).
    cve_ids = [c["id"] for c in sec["cves"]]
    existing: dict[int, dict[str, dict]] = {}
    for d in drafts:
        pid = d["platform_id"]
        if pid not in existing:
            try:
                existing[pid] = await _advisories_with_cves(client, cve_ids, pid)
            except Exception as e:
                raise _CommandError(_api_error(
                    "searching existing advisories for the CVEs", e
                )) from e
        d["duplicates"], d["related"] = [], []
        for errata_id, hit in sorted(existing[pid].items()):
            record = hit["record"]
            entry = {
                "id": errata_id,
                "title": record.get("title") or record.get("original_title"),
                "cves": sorted(hit["cves"]),
            }
            same = any(
                _same_package(record, u, d["module"]) for u in d["units"]
            )
            (d["duplicates"] if same else d["related"]).append(entry)
    return sec, drafts


def _draft_builds(d: dict) -> str:
    noun = "build" if len(d["build_ids"]) == 1 else "builds"
    return f"{noun} {', '.join(f'#{b}' for b in d['build_ids'])}"


def _format_errata_draft(d: dict) -> list[str]:
    p = d["payload"]
    names = sorted({pkg["name"] for pkg in p["packages"]})
    arches = sorted({pkg["arch"] for pkg in p["packages"]})
    lines = [
        f"{p['id']}  {d['platform_name']}  {_draft_builds(d)}",
        f"  Title: {p['title']}  (ALBS shows \"{p['severity']}: {p['title']}\")",
        f"  Module: {p['module'] or 'none'}",
        f"  Source packages: {', '.join(d['units'])}",
    ]
    if d["skipped"]:
        lines.append(f"  Left out (not affected): {', '.join(d['skipped'])}")
    lines.append(
        f"  Packages: {len(p['packages'])} RPM(s) ({', '.join(arches)})"
    )
    lines.append(f"    {_list_names(names, 40)}")
    lines.append(f"  Description: {d['description_note']}")
    lines.extend(f"  {n}" for n in d["notes"])
    lines.extend(
        f"  NOTE: {', '.join(r['cves'])} also in {r['id']} \"{r['title']}\" "
        f"(another package on {d['platform_name']})"
        for r in d["related"]
    )
    lines.extend(f"  !! {line}" for line in _duplicate_lines(d))
    lines.extend(f"  ! {w}" for w in d["warnings"])
    return lines


def _duplicate_lines(d: dict) -> list[str]:
    return [
        f"DUPLICATE: {r['id']} \"{r['title']}\" already covers "
        f"{', '.join(r['cves'])} for {', '.join(d['units'])} on "
        f"{d['platform_name']}"
        for r in d["duplicates"]
    ]


def _duplicate_summary(drafts: list[dict]) -> list[str]:
    """Headline for advisories that would repeat existing ones."""
    count = sum(1 for d in drafts if d["duplicates"])
    if not count:
        return []
    return [
        f"!! DUPLICATE: {count} advisory(ies) here would repeat existing ones "
        "(same package, platform and CVEs) — listed below as `!! DUPLICATE`. "
        "create_errata refuses them unless force=True. Tell the user."
    ]


def _broken_summary(drafts: list[dict]) -> list[str]:
    """Headline for failed / cancelled build tasks, so none goes unnoticed."""
    count = sum(len(d["warnings"]) for d in drafts)
    if not count:
        return []
    return [
        f"!!! {count} build task(s) FAILED or were CANCELLED — each is listed "
        "below as `! ...`. Tell the user about every one."
    ]


def _format_errata_common(drafts: list[dict]) -> list[str]:
    """References (shared) and descriptions — once if every advisory has the
    same text, else per advisory (kernel and kernel-rt differ)."""
    lines = ["", "References:"]
    for ref in drafts[0]["payload"]["references"]:
        cve = ref.get("cve")
        if cve:
            extra = "  ".join(
                v for v in (cve["cvss3"], cve["cwe"], f"public {cve['public']}") if v
            )
            lines.append(f"  {ref['ref_id']}  {cve['impact']}  {extra}")
        else:
            lines.append(f"  {ref['ref_type']} {ref['ref_id']}  {ref['href']}")
    texts: dict[str, list[str]] = {}
    for d in drafts:
        texts.setdefault(d["payload"]["description"], []).append(d["payload"]["id"])
    for text, ids in texts.items():
        lines.append("")
        lines.append(
            "Description:" if len(texts) == 1
            else f"Description ({', '.join(ids)}):"
        )
        lines.extend(f"  {line}".rstrip() for line in text.splitlines())
    return lines


async def preview_errata(
    builds: list[int | str],
    cves: list[str] | None = None,
    rhsa: str | None = None,
    package: str | None = None,
    issued_date: str | None = None,
    updated_date: str | None = None,
    title: str | None = None,
    description: str | None = None,
) -> str:
    """Show the advisories create_errata would create. Creates nothing."""
    client = _get_client()
    try:
        sec, drafts = await _prepare_errata(
            client, builds, cves, rhsa, package, issued_date, updated_date,
            title, description,
        )
    except _CommandError as e:
        return str(e)
    except ValueError as e:
        return f"Error: {e}"

    first = drafts[0]["payload"]
    lines = [
        f"Errata preview: {len(drafts)} advisory(ies) — NOTHING has been "
        "created yet.",
        "IDs are provisional: create_errata re-reads the latest AlmaLinux ID "
        "and numbers the advisories in this order.",
        "",
        f"Source: {sec['label']}",
        f"Severity: {first['severity']}",
        f"Issued: {first['issued_date']}  Updated: {first['updated_date']}",
        *_duplicate_summary(drafts),
        *_broken_summary(drafts),
    ]
    for d in drafts:
        lines.append("")
        lines.extend(_format_errata_draft(d))
    lines.extend(_format_errata_common(drafts))
    return "\n".join(lines)


async def create_errata(
    builds: list[int | str],
    cves: list[str] | None = None,
    rhsa: str | None = None,
    package: str | None = None,
    issued_date: str | None = None,
    updated_date: str | None = None,
    title: str | None = None,
    description: str | None = None,
    force: bool = False,
) -> str:
    """Create one AlmaLinux advisory per build and platform on ALBS.

    Refuses — creating nothing — when an advisory would duplicate an
    existing one (same platform, package and CVEs), unless `force`.
    """
    client = _get_client()
    if not client.jwt_token:
        return (
            "Auth error: creating errata requires a JWT token. "
            "Pass --token or set ALBS_JWT_TOKEN."
        )
    try:
        sec, drafts = await _prepare_errata(
            client, builds, cves, rhsa, package, issued_date, updated_date,
            title, description,
        )
    except _CommandError as e:
        return str(e)
    except ValueError as e:
        return f"Error: {e}"

    duplicated = [d for d in drafts if d["duplicates"]]
    if duplicated and not force:
        lines = [
            "Error: nothing was created — these advisories would duplicate "
            "existing ones:"
        ]
        for d in duplicated:
            lines.append(
                f"  {d['payload']['id']} ({d['platform_name']}, "
                f"{_draft_builds(d)}):"
            )
            lines.extend(f"    {line}" for line in _duplicate_lines(d))
        lines.append(
            "Pass force=True only if the user explicitly wants a second "
            "advisory anyway."
        )
        lines.extend(_broken_summary(drafts))
        lines.extend(
            f"  ! {d['payload']['id']}: {w}"
            for d in drafts for w in d["warnings"]
        )
        return "\n".join(lines)

    ids = [d["payload"]["id"] for d in drafts]
    try:
        taken = await client.query_errata(errata_ids=ids)
    except Exception as e:
        return _api_error("checking advisory IDs", e)
    if taken.get("total_records"):
        clash = sorted({r["id"] for r in taken.get("records") or []})
        return (
            f"Error: advisory ID(s) {clash or ids} already exist on ALBS — "
            "another advisory was created meanwhile. Nothing was created; "
            "run create_errata again to renumber."
        )

    def summary(d: dict) -> str:
        return f"{d['payload']['id']} ({d['platform_name']}, {_draft_builds(d)})"

    created: list[dict] = []
    for i, d in enumerate(drafts):
        errata_id = d["payload"]["id"]
        try:
            result = await client.create_errata(d["payload"])
            error = None if result.get("ok") else (result.get("error") or str(result))
        except Exception as e:
            error = _api_error(f"creating {errata_id}", e).removeprefix("Error: ")
        if error:
            lines = [f"Error: creating {errata_id} failed: {error}"]
            if created:
                lines.append(
                    "Already created: " + ", ".join(summary(c) for c in created)
                )
            rest = drafts[i + 1:]
            if rest:
                lines.append(
                    "Not attempted: " + ", ".join(summary(r) for r in rest)
                )
            lines.extend(_broken_summary(drafts))
            lines.extend(
                f"  ! {r['payload']['id']}: {w}"
                for r in drafts for w in r["warnings"]
            )
            return "\n".join(lines)
        created.append(d)

    first = drafts[0]["payload"]
    lines = [
        f"Created {len(created)} advisory(ies) on ALBS — queued, ALBS stores "
        "them in the background:",
        *_broken_summary(created),
    ]
    for d in created:
        p = d["payload"]
        lines.append(
            f"  {p['id']}  {d['platform_name']}  {_draft_builds(d)}  "
            f"{p['title']}  {len(p['packages'])} package(s)"
            + (f"  module {p['module']}" if p["module"] else "")
        )
        lines.append(f"    {_errata_url(p['id'], d['platform_id'])}")
        lines.extend(
            f"    !! created despite (force=True): {line}"
            for line in _duplicate_lines(d)
        )
        lines.extend(f"    ! {w}" for w in d["warnings"])
    lines.append("")
    lines.append(
        f"Source: {sec['label']} | Severity: {first['severity']} | "
        f"Issued: {first['issued_date']} | Updated: {first['updated_date']}"
    )
    lines.append(
        f'Confirm with get_errata("{first["id"]}") once ALBS has processed '
        "it. Nothing is released — releasing an advisory is a separate step "
        "in ALBS."
    )
    return "\n".join(lines)


def _format_errata_record(record: dict, platform_names: dict[int, str]) -> str:
    platform_id = record.get("platform_id")
    refs = []
    for ref in record.get("references") or []:
        kind = str(ref.get("ref_type", "")).split(".")[-1]
        refs.append(ref["ref_id"] if kind == "cve" else f"{kind} {ref['ref_id']}")
    packages = record.get("packages") or []
    sources = sorted({p["source_srpm"] for p in packages if p.get("source_srpm")})
    builds = sorted({
        a["build_id"]
        for p in packages for a in p.get("albs_packages") or []
        if a.get("build_id")
    })
    package_line = f"Packages: {len(packages)}"
    if sources:
        package_line += f" (source: {', '.join(sources)})"
    if builds:
        package_line += f", builds: {', '.join(f'#{b}' for b in builds)}"
    lines = [
        f"Advisory {record['id']} — "
        f"{platform_names.get(platform_id, f'platform {platform_id}')}",
        f"Title: {record.get('title') or record.get('original_title')}",
        f"Severity: {record.get('severity')}",
        f"Release status: {record.get('release_status') or 'unknown'}",
        f"Issued: {str(record.get('issued_date'))[:10]}  "
        f"Updated: {str(record.get('updated_date'))[:10]}",
        f"Module: {record.get('module') or 'none'}",
        f"References: {', '.join(refs) or 'none'}",
        package_line,
        f"URL: {_errata_url(record['id'], platform_id)}",
    ]
    text = record.get("description") or record.get("original_description")
    if text:
        lines.append("Description:")
        lines.extend(f"  {line}".rstrip() for line in text.splitlines())
    return "\n".join(lines)


async def get_errata(errata_id: str, platform: str | None = None) -> str:
    """Show an advisory (every platform it exists on, or just `platform`)."""
    client = _get_client()
    errata_id = errata_id.strip().upper()
    try:
        platform_ids = await client.get_platform_ids()
    except Exception as e:
        return _api_error("getting platforms", e)
    platform_id = None
    if platform:
        if platform not in platform_ids:
            return (
                f"Error: unknown platform '{platform}'. "
                f"Available: {', '.join(sorted(platform_ids))}"
            )
        platform_id = platform_ids[platform]
    try:
        data = await client.query_errata(
            errata_ids=[errata_id], platform_id=platform_id
        )
    except Exception as e:
        return _api_error(f"getting advisory {errata_id}", e)
    records = data.get("records") or []
    if not records:
        where = f" on {platform}" if platform else ""
        return (
            f"Error: advisory {errata_id} not found{where}. ALBS stores new "
            "advisories in the background — if it was just created, retry "
            "in a few seconds."
        )
    names = {pid: name for name, pid in platform_ids.items()}
    return "\n\n".join(_format_errata_record(r, names) for r in records)
