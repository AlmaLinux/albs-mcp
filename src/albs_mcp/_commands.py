"""Core command functions for ALBS.

Shared by both the MCP server (server.py) and CLI (cli.py).
Does NOT import mcp — only client.py and constants.py.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import httpx

from .client import (
    ALBSClient,
    get_completed_task_ids,
    get_whole_package_task_ids,
)
from .constants import (
    BUILD_TASK_STATUS,
    KEY_LOG_TYPES,
    LOG_LINES_PER_CHUNK,
    RELEASE_STATUS,
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
    try:
        build = await client.get_build(build_id)
    except Exception as e:
        return _api_error(f"getting build #{build_id}", e)

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
) -> str:
    client = _get_client()
    try:
        content, total, from_line = await _read_log(
            client, build_id, filename,
            lambda: client.read_log_tail(build_id, filename, lines),
        )
    except Exception as e:
        return _api_error(f"reading log {filename}", e)
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
    try:
        content, total = await _read_log(
            client, build_id, filename,
            lambda: client.read_log_range(build_id, filename, start_line, end_line),
        )
    except Exception as e:
        return _api_error(f"reading log {filename}", e)
    header = (
        f"=== {filename} | lines {start_line}-{end_line} of {total} ===\n"
    )
    return header + content


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
