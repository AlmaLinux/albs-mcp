"""Core command functions for ALBS.

Shared by both the MCP server (server.py) and CLI (cli.py).
Does NOT import mcp — only client.py and constants.py.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path

from .client import ALBSClient
from .constants import (
    BUILD_TASK_STATUS,
    KEY_LOG_TYPES,
    LOG_LINES_PER_CHUNK,
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


# ═══════════════════════════════════════════════════════════════════════
#  Read-only commands
# ═══════════════════════════════════════════════════════════════════════


async def get_platforms() -> str:
    client = _get_client()
    platforms = await client.get_platforms()
    lines = [f"Platforms ({len(platforms)}):", ""]
    for p in platforms:
        arches = ", ".join(p.get("arch_list", []))
        lines.append(f"  {p['name']:30} arches: {arches}")
    return "\n".join(lines)


async def get_build_info(build_id: int) -> str:
    client = _get_client()
    build = await client.get_build(build_id)

    platforms = {
        t["platform"]["name"]
        for t in build["tasks"] if t.get("platform")
    }
    arches = sorted({t["arch"] for t in build["tasks"]})
    flavors = [f["name"] for f in build.get("platform_flavors", [])]

    lines = [
        f"Build #{build['id']}",
        f"Created: {build['created_at']}",
        f"Finished: {build.get('finished_at', 'still running')}",
        f"Owner: {build['owner']['username']}",
        f"Platform: {', '.join(sorted(platforms)) or 'N/A'}",
        f"Architectures: {', '.join(arches)}",
        f"Released: {build['released']}",
    ]
    if flavors:
        lines.append(f"Flavors: {', '.join(flavors)}")

    lines.append("")
    lines.append("Tasks:")

    for t in build["tasks"]:
        status = BUILD_TASK_STATUS.get(t["status"], f"unknown({t['status']})")
        pkg = t["ref"]["url"].split("/")[-1].replace(".git", "")
        git_ref = t["ref"].get("git_ref", "N/A")
        log_count = sum(1 for a in t["artifacts"] if a["type"] == "build_log")
        lines.append(
            f"  [{status:>9}] task_id={t['id']}  arch={t['arch']:>10}  "
            f"pkg={pkg}  ref={git_ref}  logs={log_count}"
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
    build = await client.get_build(build_id)

    failed = [t for t in build["tasks"] if t["status"] == 3]
    if not failed:
        return f"Build #{build_id}: no failed tasks."

    lines = [f"Build #{build_id}: {len(failed)} failed task(s)", ""]

    for t in failed:
        pkg = t["ref"]["url"].split("/")[-1].replace(".git", "")
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
        "Use download_log + read_log_tail to investigate."
    )
    return "\n".join(lines)


async def download_log(build_id: int, filename: str) -> str:
    client = _get_client()
    path = await client.download_log(build_id, filename)
    size = path.stat().st_size
    total_lines = len(path.read_text(errors="replace").splitlines())
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
) -> str:
    client = _get_client()
    content, total, from_line = client.read_log_tail(build_id, filename, lines)
    header = (
        f"=== {filename} | lines {from_line}-{total} of {total} ===\n"
    )
    return header + content


async def read_log_range(
    build_id: int,
    filename: str,
    start_line: int,
    end_line: int,
) -> str:
    client = _get_client()
    content, total = client.read_log_range(build_id, filename, start_line, end_line)
    header = (
        f"=== {filename} | lines {start_line}-{end_line} of {total} ===\n"
    )
    return header + content


async def list_build_logs(build_id: int) -> str:
    client = _get_client()
    logs = await client.list_build_logs(build_id)
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
    data = await client.search_builds(page, project, is_running)

    builds = data if isinstance(data, list) else data.get("builds", [])
    lines = [f"Builds (page {page}): {len(builds)} result(s)", ""]

    for b in builds[:20]:
        task_count = len(b.get("tasks", []))
        failed = sum(1 for t in b.get("tasks", []) if t["status"] == 3)
        pkgs = set()
        for t in b.get("tasks", []):
            name = t["ref"]["url"].split("/")[-1].replace(".git", "")
            pkgs.add(name)
        pkg_str = ", ".join(sorted(pkgs)[:3])
        if len(pkgs) > 3:
            pkg_str += f" (+{len(pkgs) - 3} more)"
        status_str = f"{failed} failed" if failed else "ok"
        lines.append(
            f"  #{b['id']}  {b['created_at'][:10]}  "
            f"tasks={task_count} [{status_str}]  {pkg_str}"
        )

    return "\n".join(lines)


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
    except PermissionError as e:
        return f"Auth error: {e}"
    except Exception as e:
        return f"Error getting sign keys: {e}"


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
    except PermissionError as e:
        return f"Auth error: {e}"
    except Exception as e:
        return f"Error getting flavors: {e}"


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
    except PermissionError as e:
        return f"Auth error: {e}"
    except Exception as e:
        return f"Error creating build: {e}"


async def sign_build(build_id: int, sign_key_id: int = 4) -> str:
    client = _get_client()
    try:
        result = await client.sign_build(build_id, sign_key_id)
        return (
            f"Sign task created for build #{build_id}\n"
            f"Sign task ID: {result['id']}\n"
            f"Status: {SIGN_TASK_STATUS.get(result['status'], 'unknown')}"
        )
    except PermissionError as e:
        return f"Auth error: {e}"
    except Exception as e:
        return f"Error signing build: {e}"


async def delete_build(build_id: int) -> str:
    return (
        "Build deletion is currently blocked for safety.\n"
        "Can be removed manually in the build system."
    )
