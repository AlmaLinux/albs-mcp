from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from . import _commands as cmd
from .constants import (
    LOG_LINES_PER_CHUNK,
    LOG_MAX_LINE_CHARS,
    LOG_MAX_RESULT_CHARS,
    LOG_SEARCH_AFTER,
    LOG_SEARCH_BEFORE,
    LOG_SEARCH_MAX_MATCHES,
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
3. Find the error with search_log(build_id, filename) — it greps the log for the standard \
failure signatures (compiler/link errors, failed patch hunks, unresolved BuildRequires, \
RPM packaging errors, %check failures, OOM/network trouble) and returns every hit with \
its line number and surrounding context. Do this BEFORE reading any tail, and run it on \
mock_build even when mock_root and mock_stderr look clean. It downloads the log \
automatically — there is no need to call download_log first.
4. Do NOT conclude from a tail. `make -j` keeps compiling other objects after the first \
failure, so the real error sits hundreds of lines above the end and the tail shows only \
`make: *** [Makefile:NNNN: all] Error 2` — the symptom. Reporting that line as the root \
cause is a wrong answer: name the file, line, and diagnostic that search_log found.
5. Use read_log_tail (how the build terminated) and read_log_range around a line number \
search_log reported to widen the context. Read mock_root for chroot/dependency issues and \
mock_stderr for segfaults, OOM kills, and toolchain crashes.
6. When the search finds nothing and you have to READ the log, page it bottom-up instead \
of guessing line ranges: call read_log_tail, then copy the `↑ earlier:` call printed at \
the bottom of the result to get the page above it, and repeat. Each page is sized to fit \
the result budget, and the pages join up exactly — a guessed read_log_range window is how \
you miss the error.
7. IMPORTANT: mock_build logs can be very large (100k+ lines) with individual lines \
several KB long. NEVER read the whole file. Reads clip each line to 500 chars by default \
(max_line_chars=0 for a verbatim line) and cap the whole result at 40000 chars; leave both \
on — an unclipped read can exceed the transport's size limit and return nothing at all.

## Creating builds (requires JWT token)
1. ASK the user for: package name(s), platform(s), and how to build (branch/tag/srpm URL).
2. If the user did NOT specify architectures, use the platform defaults (do NOT ask).
3. Call create_build() with the collected parameters. Use platform for a single platform, \
or platforms for multiple (e.g. platforms=["AlmaLinux-8", "AlmaLinux-9"]). \
Both can be combined; duplicates are removed automatically.
4. Platform names and arch_list are validated dynamically against ALBS. \
If you need to show available platforms, call get_platforms(). \
When arch_list is specified with multiple platforms, it is validated against each platform.
5. Use skip_tests=True to disable the %check phase in any build. \
This adds --define "__spec_check_template exit 0;" to the mock definitions.
6. For external Git repositories (outside git.almalinux.org/rpms), use the git_urls \
parameter instead of packages. Pass the full .git URL \
(e.g. "https://github.com/user/repo.git"). The branch parameter sets the git ref. \
git_urls can be combined with packages in the same build. \
git_urls cannot be used with from_srpm.
7. Use independent_tasks=True when the user wants packages in the build to start \
in parallel within each platform instead of the default sequential chain. \
The flag is applied to every platform entry in the payload.

## Building EPEL packages (SRPMs from dl.fedoraproject.org/pub/epel/)
When a user wants to build packages from EPEL SRPMs, you MUST handle the following \
BEFORE calling create_build:
1. ASK the user if they want to enable add-epel-dist, \
UNLESS they already mentioned it. If yes, pass add_epel_dist=True. \
This extracts the .elN dist suffix from each package name/URL and sets a per-task \
mock definition: dist=".elN.alma_altarch". Only works with from_tag or from_srpm.
2. Add the correct EPEL flavors via the flavors parameter:
   - For almalinux-10: flavors=["EPEL-10", "EPEL-10_altarch"]
   - For almalinux-kitten-10: flavors=["EPEL-10", "EPEL-Kitten_altarch"]
3. Use arch_list=["x86_64_v2"] unless the user explicitly specified different architectures.

## Signing builds (requires JWT token)
1. First call get_build_info(build_id) and present a short summary to the user: \
platform, architectures, package list, and flavors. The user needs this to decide \
which sign key to use.
2. Call get_sign_keys() to show available keys so the user can choose.
3. If the build has EPEL*_altarch flavors and was built only for x86_64_v2, \
this is an EPEL-altarch build. Tell the user that EPEL flavors are present \
and the build targets only x86_64_v2, which indicates it should likely be \
signed with an EPEL key.
4. ASK the user to confirm the sign key before signing.
5. Call sign_build(build_id, sign_key_id) to create a sign task.
6. To check whether signing finished, call get_sign_task_status(build_id) — \
it shows each sign task's status (idle/in_progress/completed/failed).

## Creating release plans (requires JWT token)
This server can CREATE a release plan but NEVER performs the actual release \
(it does not commit/publish). Creating a plan is safe: ALBS records a \
"scheduled" release and computes which packages go to which repositories, \
but nothing is published until the plan is committed — which this server \
intentionally does not do.
1. ASK the user for the build id, the target platform, and the target \
product. Use get_products() to list products (id, name, official/community, \
platforms) so the user can choose. Use get_platforms() for platform names.
2. The build must have completed tasks — only completed build tasks go into \
a plan. If the build failed entirely, there is nothing to release.
3. Call create_release_plan(build_id, platform, product). It collects the \
completed build tasks automatically, resolves the platform/product names to \
ids, and creates the scheduled plan. Platform and product names are \
validated against ALBS — unknown names return an error with the valid list.
4. For a PARTIAL build that was superseded by a 'retry failed' build, pass \
whole_packages_only=True so only packages whose every arch task completed \
are included (half-built packages are dropped).
5. Report the plan: status (scheduled), the source packages and target \
repositories. Make clear to the user that NOTHING has been published — it \
is only a plan.
6. To view an existing plan later, call get_release_plan(release_id).
7. If the user asks to actually release / commit / publish, call \
commit_release — it is intentionally blocked and explains that only plans \
are supported here.

## Important notes
- Read-only tools work without authentication.
- Build/release creation, signing, and sign key listing require a JWT token.
- Product and release viewing (get_products, get_release_plan) are read-only.
- Build deletion is intentionally blocked for safety.
- Committing/performing a release is intentionally blocked — this server \
only creates release plans, never the actual release.
""",
)


# ═══════════════════════════════════════════════════════════════════════
#  READ-ONLY TOOLS  (no JWT required)
# ═══════════════════════════════════════════════════════════════════════


@mcp.tool()
async def get_platforms() -> str:
    """Get all available platforms and their supported architectures from ALBS.

    Returns the list of platforms with arch_list fetched dynamically
    from the build system.
    """
    return await cmd.get_platforms()


@mcp.tool()
async def get_build_info(build_id: int) -> str:
    """Get build details: tasks, statuses, packages, architectures.

    Returns a summary of the build including each task's status,
    architecture, package name, and whether it has sign tasks. Also lists
    any linked builds (their -br repos are mixed into the buildroot), which
    is needed to accurately reproduce a similar rebuild.
    """
    return await cmd.get_build_info(build_id)


@mcp.tool()
async def get_failed_tasks(build_id: int) -> str:
    """Get failed tasks for a build with their available log files.

    Shows only tasks that failed, along with log file names.
    Key logs for debugging: mock_build, mock_stderr, mock_root.
    """
    return await cmd.get_failed_tasks(build_id)


@mcp.tool()
async def download_log(build_id: int, filename: str) -> str:
    """Download a build log file to local filesystem.

    The file will be saved to $ALBS_LOG_DIR/<build_id>/<filename>
    (default: /tmp/albs-logs/<build_id>/<filename>).
    After downloading, use read_log_tail to read the contents.
    """
    return await cmd.download_log(build_id, filename)


@mcp.tool()
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
    """Find the failure in a build log: grep it and return each hit with context.

    START HERE when investigating a failed build — do not read tails first.
    In a parallel `make -j` log the real compile error sits hundreds of lines
    above the end, so the tail shows only `make: *** Error 2`: the symptom, not
    the cause. This finds the cause in one call.

    `pattern` is a Python regex; omit it to use the built-in build-failure
    signatures (compiler/link errors, failed patch hunks, unresolved
    BuildRequires, RPM packaging errors, %check failures, OOM/network trouble).
    Matched lines are prefixed `>>>` and every line is numbered, so a hit's
    number can be fed straight to read_log_range to see more.

    Only the first `max_matches` hits are reported — the first error is the root
    cause and the rest are cascades — but the header counts all of them. Lines
    are clipped to `max_line_chars` around the match (0 = verbatim, needed when
    quoting an exact multi-KB command line). The log is downloaded
    automatically if not already on disk.
    """
    return await cmd.search_log(
        build_id, filename, pattern, before, after, max_matches,
        max_line_chars, max_chars,
    )


@mcp.tool()
async def read_log_tail(
    build_id: int,
    filename: str,
    lines: int = LOG_LINES_PER_CHUNK,
    max_line_chars: int = LOG_MAX_LINE_CHARS,
    before_line: int | None = None,
    max_chars: int = LOG_MAX_RESULT_CHARS,
) -> str:
    """Read a page of a build log from the end, and page upward from there.

    Good for how the build terminated (`RPM build errors:`, the mock
    exception). To FIND a compile error, use search_log — the end of a
    `make -j` log holds only the wrapper error.

    One call returns as many lines as fit in `max_chars` (~10k tokens), up to
    `lines` lines. That budget is what sizes the page: the same budget is about
    165 lines of a mock_build log or 350 of a mock_root, so a fixed line count
    would be wrong for one of them. The result ends with the exact call for the
    page above it — `before_line=<first line shown>` — so walking a log bottom
    to top needs no arithmetic and skips nothing.

    `before_line=N` reads the page ending at line N-1. Each line is clipped to
    `max_line_chars` (0 = verbatim); keep the clip on, mock_build lines can be
    several KB of gcc flags. The log is downloaded automatically if not already
    on disk — no need to call download_log first.
    """
    return await cmd.read_log_tail(
        build_id, filename, lines, max_line_chars, before_line, max_chars
    )


@mcp.tool()
async def read_log_range(
    build_id: int,
    filename: str,
    start_line: int,
    end_line: int,
    max_line_chars: int = LOG_MAX_LINE_CHARS,
    max_chars: int = LOG_MAX_RESULT_CHARS,
) -> str:
    """Read a specific range of lines from a build log.

    Use this to widen the window around a line number that search_log
    reported, or to inspect an earlier section. Reads forward from
    `start_line`; a range too large for `max_chars` stops early and tells you
    the call to continue with, so nothing is silently lost.

    Each line is clipped to `max_line_chars` (0 = verbatim). The log is
    downloaded automatically if not already on disk — no need to call
    download_log first.
    """
    return await cmd.read_log_range(
        build_id, filename, start_line, end_line, max_line_chars, max_chars
    )


@mcp.tool()
async def list_build_logs(build_id: int) -> str:
    """List all available log files for a build from the server.

    Shows all log and config files stored in Pulp for this build.
    """
    return await cmd.list_build_logs(build_id)


@mcp.tool()
async def search_builds(
    page: int = 1,
    project: str | None = None,
    is_running: bool | None = None,
) -> str:
    """Search builds on ALBS. Returns a page of builds.

    Each build line carries its task count, failed count, and release state,
    then the packages as full NVRs (``mingw-glib2-2.89.2-1.el10``) so a version
    question can be answered without opening the build. When `project` is set,
    the packages that matched it are listed on their own ``match:`` line — the
    remaining ones are summarised and may be truncated.

    Args:
        page: Page number (default 1).
        project: Filter by project/package name.
        is_running: Filter by running status.
    """
    return await cmd.search_builds(page, project, is_running)


@mcp.tool()
async def get_sign_task_status(build_id: int) -> str:
    """Get the status of sign tasks for a build.

    Use this after sign_build to check whether signing completed or failed.
    Returns each sign task's ID, status (idle/in_progress/completed/failed),
    and sign key ID. No authentication required.
    """
    return await cmd.get_sign_task_status(build_id)


@mcp.tool()
async def get_products() -> str:
    """List all products on ALBS. No authentication required.

    Returns each product's id, name, official/community flag, and the
    platforms it covers. Use this to pick the target product when creating
    a release plan with create_release_plan.
    """
    return await cmd.get_products()


@mcp.tool()
async def get_release_plan(release_id: int) -> str:
    """View an existing release plan. No authentication required.

    Returns the release status (scheduled/in_progress/completed/failed/
    reverted), product, platform, the source packages it covers, and the
    target repositories.
    """
    return await cmd.get_release_plan(release_id)


# ═══════════════════════════════════════════════════════════════════════
#  AUTHENTICATED TOOLS  (JWT required)
# ═══════════════════════════════════════════════════════════════════════


@mcp.tool()
async def get_sign_keys() -> str:
    """Get available sign keys from ALBS. Requires JWT token.

    Returns key ID, name, keyid (GPG fingerprint short), and
    associated platform IDs needed for sign_build.
    """
    return await cmd.get_sign_keys()


@mcp.tool()
async def get_flavors() -> str:
    """List all available platform flavors on ALBS.

    Returns flavor names and IDs, useful for verifying correct flavor names
    before creating builds with the flavors parameter.
    """
    return await cmd.get_flavors()


@mcp.tool()
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
    definitions: dict[str, str] | None = None,
    linked_builds: list[int] | None = None,
    flavors: list[str] | None = None,
    with_opts: list[str] | None = None,
    without_opts: list[str] | None = None,
    modules: list[str] | None = None,
    independent_tasks: bool = False,
) -> str:
    """Create a new build on ALBS. Requires JWT token.

    Platforms and allowed architectures are fetched dynamically from ALBS.
    Use get_platforms to see available options.

    For EPEL builds (SRPMs from dl.fedoraproject.org/pub/epel/), the tool
    automatically applies EPEL-specific flavors and defaults arch to x86_64_v2.

    Args:
        platform: Target platform (single). Use get_platforms to see available options.
        platforms: List of target platforms to build on (e.g. ["AlmaLinux-8", "AlmaLinux-9"]).
                   Can be used alone or combined with platform. At least one must be provided.
        packages: List of package names (for git/branch) or SRPM URLs (for from_srpm).
                  For from_tag: use "pkg_name tag_name" format or just "tag_name".
                  At least one of packages or git_urls must be provided.
        git_urls: List of custom Git repository URLs to build from
                  (e.g. ["https://github.com/user/repo.git"]). Use for repos
                  outside git.almalinux.org/rpms. The branch parameter sets the
                  git ref. For from_tag, use "url tag_name" format.
                  Cannot be used with from_srpm.
        branch: Git branch to build from (e.g. "a8", "c9s").
        from_tag: Build from git tags instead of branch.
        from_srpm: Build from source RPM URLs.
        tags: Explicit tags for each package when from_tag=True
              (must match packages length).
        arch_list: Architectures to build. Default: all for the platform
                   (x86_64_v2 for EPEL builds).
        skip_tests: Disable %check phase by adding
                    --define "__spec_check_template exit 0;" to mock definitions.
        add_epel_dist: Extract .elN dist suffix from each package name/URL
                       and set it as a per-task mock definition:
                       dist=".elN.alma_altarch". Only works with from_tag
                       or from_srpm. Recommended for EPEL-altarch builds.
        beta: Enable beta flavor.
        secureboot: Enable SecureBoot signing.
        nosecureboot: Override secureboot requirement for SB packages.
        excludes: Space-separated packages to exclude from mock.
        definitions: Dict of mock definitions, e.g. {"dist": ".el9"}.
        linked_builds: Build IDs to link.
        flavors: Additional flavor names.
        with_opts: Mock --with options.
        without_opts: Mock --without options.
        modules: Modules to enable, e.g. ["nodejs:18"].
        independent_tasks: When True, disables the per-platform sequential
                           task chain so packages build independently / in
                           parallel within each platform (the default ALBS
                           behavior chains task N's start on task N-1's
                           completion). Applied to every platform entry in
                           the payload. Default: False.
    """
    return await cmd.create_build(
        platform=platform,
        platforms=platforms,
        packages=packages,
        git_urls=git_urls,
        branch=branch,
        from_tag=from_tag,
        from_srpm=from_srpm,
        tags=tags,
        arch_list=arch_list,
        skip_tests=skip_tests,
        add_epel_dist=add_epel_dist,
        beta=beta,
        secureboot=secureboot,
        nosecureboot=nosecureboot,
        excludes=excludes,
        definitions=definitions,
        linked_builds=linked_builds,
        flavors=flavors,
        with_opts=with_opts,
        without_opts=without_opts,
        modules=modules,
        independent_tasks=independent_tasks,
    )


@mcp.tool()
async def sign_build(build_id: int, sign_key_id: int = 4) -> str:
    """Sign a build on ALBS. Requires JWT token.

    Use get_sign_keys to see available sign key IDs.

    Args:
        build_id: The build ID to sign.
        sign_key_id: Sign key ID (default: 4). Use get_sign_keys to list.
    """
    return await cmd.sign_build(build_id, sign_key_id)


@mcp.tool()
async def create_release_plan(
    build_id: int,
    platform: str,
    product: str,
    build_ids: list[int] | None = None,
    whole_packages_only: bool = False,
) -> str:
    """Create a release PLAN on ALBS. Requires JWT token.

    Creates a "scheduled" release and computes which packages go to which
    repositories. It NEVER performs the actual release — nothing is
    published. Committing the plan (the real release) is intentionally not
    supported here.

    The completed build tasks are collected automatically; the build must
    have completed tasks. Platform and product names are validated against
    ALBS — use get_platforms() and get_products() to see valid names.

    Args:
        build_id: The build to release.
        platform: Target platform name (e.g. "AlmaLinux-9").
        product: Target product name (e.g. "AlmaLinux", "epel-al").
                 Use get_products() to list available products.
        build_ids: Optional additional build ids to include in the same plan.
        whole_packages_only: When True, include only packages whose every
                             architecture task completed (drop half-built
                             packages). Use for a PARTIAL build superseded by
                             a 'retry failed' build. Default: False.
    """
    return await cmd.create_release_plan(
        build_id=build_id,
        platform=platform,
        product=product,
        build_ids=build_ids,
        whole_packages_only=whole_packages_only,
    )


@mcp.tool()
async def commit_release(release_id: int) -> str:
    """Commit (perform) a release. CURRENTLY BLOCKED.

    This server only creates release plans. Performing the actual release
    (publishing packages) is intentionally disabled.
    """
    return await cmd.commit_release(release_id)


@mcp.tool()
async def delete_build(build_id: int) -> str:
    """Delete a build. CURRENTLY BLOCKED.

    This operation is intentionally disabled for safety.
    """
    return await cmd.delete_build(build_id)


# ═══════════════════════════════════════════════════════════════════════
#  PROMPTS  (user-invoked slash commands)
# ═══════════════════════════════════════════════════════════════════════


@mcp.prompt(
    title="Investigate a failed ALBS build",
    description="Seed the build-failure investigation workflow for a build ID.",
)
def investigate_build(build_id: str) -> str:
    """User-invoked entry point for the build-failure investigation workflow.

    Thin wrapper: it parameterizes the investigation workflow that already
    lives in the server instructions by build_id. The detailed rationale
    (why search before reading, why a tail is not the cause) stays in the
    instructions and is not duplicated here.
    """
    return (
        f"Investigate why ALBS build {build_id} failed.\n\n"
        "Follow the build-failure investigation workflow from the albs-mcp "
        "server instructions:\n"
        f"1. Call get_build_info({build_id}) to see all tasks and statuses.\n"
        f"2. Call get_failed_tasks({build_id}) to list failed tasks and their "
        "log files (★ marks the key logs).\n"
        "3. For each failed task, run search_log on the key logs (it "
        "auto-downloads) to locate the failure — mock_root for "
        "chroot/dependency issues, mock_stderr for crashes, mock_build for the "
        "compile. Then use read_log_tail and read_log_range around the line "
        "numbers it reports. Never read a large mock_build log from line 1.\n"
        "3a. If the search comes up empty, page the log bottom-up: call "
        "read_log_tail, then copy the `↑ earlier:` call it prints to get the "
        "page above, and repeat. Do not guess read_log_range windows.\n"
        "4. Report the root cause of the failure, citing the log evidence: the "
        "file, line, and diagnostic. `make: *** [Makefile:NNNN: all] Error 2` "
        "is a symptom, not a root cause — keep digging until you have the "
        "error that caused it."
    )


@mcp.prompt(
    title="Create an ALBS release plan",
    description="Seed the release-plan workflow for a build ID (never releases).",
)
def release_plan(build_id: str) -> str:
    """User-invoked entry point for the release-plan workflow.

    Thin wrapper: it parameterizes the release-plan workflow that already
    lives in the server instructions by build_id. The detailed rationale
    (only completed tasks, never commit/publish) stays in the instructions
    and is not duplicated here.
    """
    return (
        f"Create a release plan for ALBS build {build_id}. Do NOT perform "
        "the actual release — only create the plan.\n\n"
        "Follow the release-plan workflow from the albs-mcp server "
        "instructions:\n"
        f"1. Call get_build_info({build_id}) to confirm the platform and "
        "that the build has completed tasks.\n"
        "2. Call get_products() and ask me which product to release to "
        "(and confirm the platform).\n"
        f"3. Call create_release_plan({build_id}, platform, product) to "
        "create the scheduled plan.\n"
        "4. Report the plan (status, source packages, target repositories) "
        "and make clear that nothing has been published — it is only a plan. "
        "Never call commit_release."
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
