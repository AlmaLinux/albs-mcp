from __future__ import annotations

import ast
import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .client import ALBSClient
from .constants import (
    BUILD_TASK_STATUS,
    KEY_LOG_TYPES,
    LOG_LINES_PER_CHUNK,
    SIGN_TASK_STATUS,
)

mcp = FastMCP(
    "albs-mcp",
    instructions="""\
MCP server for AlmaLinux Build System (build.almalinux.org).

## When to use
- User asks about ALBS builds, build failures, build logs, package building status.
- User wants to create a new build, sign a build, or investigate why a build failed.
- User mentions build IDs, package names in context of AlmaLinux/ALBS.

## Investigating build failures (most common workflow)
1. Call get_build_info(build_id) to see all tasks and their statuses.
2. If there are failed tasks, call get_failed_tasks(build_id) — it shows log files \
for each failed task. Logs marked with ★ are the key ones: mock_root, mock_stderr, mock_build.
3. Download the key log: download_log(build_id, filename). Start with mock_root \
(chroot/dependency issues), then mock_stderr (stderr output), then mock_build (the full build log).
4. Read from the end: read_log_tail(build_id, filename). Errors are almost always \
at the bottom. Default is 3000 lines — this is intentional to save tokens.
5. If the root cause is not visible in the tail, use read_log_range to look at earlier \
sections of the log.
6. IMPORTANT: mock_build logs can be very large (100k+ lines). NEVER try to read the \
whole file at once. Always use read_log_tail first, then read_log_range if needed.

## Creating builds (requires JWT token)
1. ASK the user for: package name(s), platform, and how to build (branch/tag/srpm URL).
2. If the user did NOT specify architectures, use the platform defaults (do NOT ask).
3. Call create_build() with the collected parameters.
4. Platform names and arch_list are validated dynamically against ALBS. \
If you need to show available platforms, call get_platforms().

## Signing builds (requires JWT token)
1. ASK the user which build to sign and which sign key to use.
2. Call get_sign_keys() to show available keys so the user can choose.
3. Call sign_build(build_id, sign_key_id) to create a sign task.

## Important notes
- Read-only tools work without authentication.
- Build creation, signing, and sign key listing require a JWT token.
- Build deletion is intentionally blocked for safety.
""",
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


# ═══════════════════════════════════════════════════════════════════════
#  READ-ONLY TOOLS  (no JWT required)
# ═══════════════════════════════════════════════════════════════════════


@mcp.tool()
async def get_platforms() -> str:
    """Get all available platforms and their supported architectures from ALBS.

    Returns the list of platforms with arch_list fetched dynamically
    from the build system.
    """
    client = _get_client()
    platforms = await client.get_platforms()
    lines = [f"Platforms ({len(platforms)}):", ""]
    for p in platforms:
        arches = ", ".join(p.get("arch_list", []))
        lines.append(f"  {p['name']:30} arches: {arches}")
    return "\n".join(lines)


@mcp.tool()
async def get_build_info(build_id: int) -> str:
    """Get build details: tasks, statuses, packages, architectures.

    Returns a summary of the build including each task's status,
    architecture, package name, and whether it has sign tasks.
    """
    client = _get_client()
    build = await client.get_build(build_id)

    lines = [
        f"Build #{build['id']}",
        f"Created: {build['created_at']}",
        f"Finished: {build.get('finished_at', 'still running')}",
        f"Owner: {build['owner']['username']}",
        f"Released: {build['released']}",
        "",
        "Tasks:",
    ]

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


@mcp.tool()
async def get_failed_tasks(build_id: int) -> str:
    """Get failed tasks for a build with their available log files.

    Shows only tasks that failed, along with log file names.
    Key logs for debugging: mock_build, mock_stderr, mock_root.
    """
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


@mcp.tool()
async def download_log(build_id: int, filename: str) -> str:
    """Download a build log file to local filesystem.

    The file will be saved to $ALBS_LOG_DIR/<build_id>/<filename>
    (default: /tmp/albs-logs/<build_id>/<filename>).
    After downloading, use read_log_tail to read the contents.
    """
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


@mcp.tool()
async def read_log_tail(
    build_id: int,
    filename: str,
    lines: int = LOG_LINES_PER_CHUNK,
) -> str:
    """Read the last N lines of a downloaded log file.

    Reads from the end of the file (where errors usually are).
    Default: last 3000 lines. Use read_log_range for specific sections.
    The log must be downloaded first with download_log.
    """
    client = _get_client()
    content, total, from_line = client.read_log_tail(build_id, filename, lines)
    header = (
        f"=== {filename} | lines {from_line}-{total} of {total} ===\n"
    )
    return header + content


@mcp.tool()
async def read_log_range(
    build_id: int,
    filename: str,
    start_line: int,
    end_line: int,
) -> str:
    """Read a specific range of lines from a downloaded log.

    Use this to look at earlier parts of the log after seeing the tail.
    The log must be downloaded first with download_log.
    """
    client = _get_client()
    content, total = client.read_log_range(build_id, filename, start_line, end_line)
    header = (
        f"=== {filename} | lines {start_line}-{end_line} of {total} ===\n"
    )
    return header + content


@mcp.tool()
async def list_build_logs(build_id: int) -> str:
    """List all available log files for a build from the server.

    Shows all log and config files stored in Pulp for this build.
    """
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


@mcp.tool()
async def search_builds(
    page: int = 1,
    project: str | None = None,
    is_running: bool | None = None,
) -> str:
    """Search builds on ALBS. Returns a page of builds.

    Args:
        page: Page number (default 1).
        project: Filter by project/package name.
        is_running: Filter by running status.
    """
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
#  AUTHENTICATED TOOLS  (JWT required)
# ═══════════════════════════════════════════════════════════════════════


@mcp.tool()
async def get_sign_keys() -> str:
    """Get available sign keys from ALBS. Requires JWT token.

    Returns key ID, name, keyid (GPG fingerprint short), and
    associated platform IDs needed for sign_build.
    """
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


@mcp.tool()
async def create_build(
    packages: list[str],
    platform: str,
    branch: str | None = None,
    from_tag: bool = False,
    from_srpm: bool = False,
    tags: list[str] | None = None,
    arch_list: list[str] | None = None,
    beta: bool = False,
    secureboot: bool = False,
    nosecureboot: bool = False,
    excludes: str | None = None,
    definitions: str | None = None,
    linked_builds: list[int] | None = None,
    flavors: list[str] | None = None,
    with_opts: list[str] | None = None,
    without_opts: list[str] | None = None,
    modules: list[str] | None = None,
) -> str:
    """Create a new build on ALBS. Requires JWT token.

    Platforms and allowed architectures are fetched dynamically from ALBS.
    Use get_platforms to see available options.

    Args:
        packages: List of package names (for git/branch) or SRPM URLs (for from_srpm).
                  For from_tag: use "pkg_name tag_name" format or just "tag_name".
        platform: Target platform. Use get_platforms to see available options.
        branch: Git branch to build from (e.g. "a8", "c9s").
        from_tag: Build from git tags instead of branch.
        from_srpm: Build from source RPM URLs.
        tags: Explicit tags for each package when from_tag=True
              (must match packages length).
        arch_list: Architectures to build. Default: all for the platform.
        beta: Enable beta flavor.
        secureboot: Enable SecureBoot signing.
        nosecureboot: Override secureboot requirement for SB packages.
        excludes: Space-separated packages to exclude from mock.
        definitions: JSON string of mock definitions, e.g. '{"dist": ".el9"}'.
        linked_builds: Build IDs to link.
        flavors: Additional flavor names.
        with_opts: Mock --with options.
        without_opts: Mock --without options.
        modules: Modules to enable, e.g. ["nodejs:18"].
    """
    pkg_dicts: list[dict[str, str]] = []
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

    defs = json.loads(definitions) if definitions else None
    excl = excludes.split() if excludes else None

    client = _get_client()
    try:
        result = await client.create_build(
            packages=pkg_dicts,
            platform=platform,
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
        )
        return (
            f"Build created successfully!\n"
            f"Build ID: {result['id']}\n"
            f"Created at: {result['created_at']}\n"
            f"URL: https://build.almalinux.org/build/{result['id']}"
        )
    except PermissionError as e:
        return f"Auth error: {e}"
    except Exception as e:
        return f"Error creating build: {e}"


@mcp.tool()
async def sign_build(build_id: int, sign_key_id: int = 4) -> str:
    """Sign a build on ALBS. Requires JWT token.

    Use get_sign_keys to see available sign key IDs.

    Args:
        build_id: The build ID to sign.
        sign_key_id: Sign key ID (default: 4). Use get_sign_keys to list.
    """
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


@mcp.tool()
async def delete_build(build_id: int) -> str:
    """Delete a build. CURRENTLY BLOCKED.

    This operation is intentionally disabled for safety.
    """
    return (
        "Build deletion is currently blocked for safety.\n"
        "Can be removed manually in the build system."
    )


def main():
    import argparse

    parser = argparse.ArgumentParser(description="ALBS MCP Server")
    parser.add_argument(
        "--token",
        type=str,
        default=None,
        help="ALBS JWT token for authenticated operations",
    )
    parser.add_argument(
        "--log-dir",
        type=str,
        default=None,
        help="Directory for downloaded logs (default: /tmp/albs-logs)",
    )
    args = parser.parse_args()

    if args.token:
        os.environ["ALBS_JWT_TOKEN"] = args.token
    if args.log_dir:
        os.environ["ALBS_LOG_DIR"] = args.log_dir

    mcp.run()


if __name__ == "__main__":
    main()
