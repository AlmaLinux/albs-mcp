# albs-mcp

MCP server and CLI for [AlmaLinux Build System](https://build.almalinux.org) (ALBS).

Gives AI coding assistants direct access to ALBS — investigate build failures, create builds, sign packages, all through natural language.

Two ways to use:

| | MCP server | CLI + Skill |
|---|---|---|
| How it works | AI calls tools via MCP protocol | AI runs `albs` commands via shell |
| Setup | Add to MCP config | Install `albs` + add skill to your AI tool |
| Best for | Dedicated ALBS workflow | Lightweight setup, avoiding MCP context pollution |
| Works without AI | No | Yes (`albs` works as a standalone CLI) |

## What it can do

### Without a token (read-only)

- **Investigate build failures** — the main use case. Give the agent a build ID and it greps the logs for the failure signatures (`search_log`), pinning the exact file, line, and diagnostic, then widens the context around it. No guessing at line offsets and no burning tokens on 100k+ line log files.
- **Get build details** — statuses of all tasks, packages, architectures, sign tasks.
- **List and search builds** — browse recent builds, filter by package name or status.
- **Get platforms** — dynamically fetched list of all platforms and their supported architectures.
- **Download and read logs** — any log file from any build: grep it (`search_log`), page it bottom-up (`read_log_tail`), or read a line range. No read can blow up: each line is clipped to 500 chars and the whole result to 40k chars (`max_line_chars=0` / `max_chars=0` to lift), so a log whose single lines run to several KB of compiler flags comes back in pages that join up exactly instead of one oversized blob. Reading auto-downloads the log if it isn't on disk yet.
- **Check sign status** — see whether sign tasks for a build completed or failed.
- **List products** — all release targets (products) with their platforms, official/community flag, and IDs.
- **View release plans** — status, source packages, and target repositories of any existing release.
- **View and preview errata** — look up any advisory (`get_errata`), and preview the AlmaLinux advisories a CVE/RHSA + build(s) would produce (`preview_errata`) before creating them.

### With a JWT token (authenticated)

- **Create builds** — specify packages, platform(s), branch/tag/SRPM. Supports multiple platforms in a single build (e.g. AlmaLinux-8 + AlmaLinux-9). Architectures default to each platform's full list unless you override. Supports custom Git URLs for repos outside `git.almalinux.org` (e.g. GitHub, GitLab). Supports all mkbuild.py options: linked builds, mock definitions, excludes, flavors, secureboot, modules, with/without.
- **Sign builds** — create sign tasks with a chosen key.
- **List sign keys** — see available keys with IDs and platform mappings.
- **Create release plans** — build a *scheduled* release plan for a build (which packages go to which repositories) targeting a chosen platform + product. **The actual release is never performed** — this only creates the plan; committing/publishing is intentionally blocked.
- **Create errata** — AlmaLinux-issued advisories (`ALSA-YYYY:A001`, …). You give only the package (or module), its CVEs — or one RHSA — and the build(s); the build may contain other packages. Everything else is automatic: the ID (next free AlmaLinux number of the year, one counter for all platforms), dates (today), title, description (the package text from the newest earlier advisory for that package + the CVE fix list), CVE information and references (CVE + bugzilla) from Red Hat, the module of a modular build (`nodejs:24`), and the package's RPMs. With an RHSA, title, description, CVEs and severity all come from the RHSA. One advisory per platform and package — a build plus its retry build merge into one. An advisory that would repeat an existing one (same platform, package and CVEs — e.g. the ALSA imported from the RHSA) is flagged and not created unless forced. Creating an advisory does not release it.
- **Delete builds** — intentionally blocked for safety.

### Log types

ALBS produces several log files per build task. The key ones for debugging:

| Log | What's inside |
|---|---|
| `mock_root` | Chroot setup, dependency resolution. Check first — if deps failed, nothing else matters. |
| `mock_stderr` | Stderr output from the build process. Often has the clearest error message. |
| `mock_build` | Full build log (can be 100k+ lines). The complete rpmbuild output — where compile errors live. Grep it with `search_log`; its tail shows only the `make` wrapper error, not the cause. |
| `mock_state` | Mock state transitions. |
| `mock_hw_info` | Hardware info of the build node. |
| `mock_installed_pkgs` | List of packages installed in the chroot. |
| `albs` | ALBS-level task log (task assignment, upload). |
| `mock.*.cfg` | Mock configuration used for the build. |

## Install

```bash
pip install git+https://github.com/AlmaLinux/albs-mcp.git
```

This installs both the MCP server (`albs-mcp`) and the CLI (`albs`).

## Authentication

The JWT token is read from (checked in order):

1. `ALBS_JWT_TOKEN` environment variable
2. `~/.albs/credentials` file (Python dict with a `token` key):

```python
{"token": "eyJ..."}
```

Without a token both MCP and CLI work in read-only mode.

> **Never commit real tokens.** Use env vars or `~/.albs/credentials`, not CLI arguments.

## Setup option 1: MCP server

Add to your MCP client config (e.g. `mcp.json` or equivalent):

```json
{
  "mcpServers": {
    "albs": {
      "command": "albs-mcp"
    }
  }
}
```

## Setup option 2: CLI + Skill

For setups where MCP context pollution is a concern, or when using tools that don't support MCP.

**Step 1.** Install the package (same as above — gives you the `albs` command):

```bash
pip install git+https://github.com/AlmaLinux/albs-mcp.git
```

**Step 2.** Add the workflow instructions to your AI tool:

```bash
# Copy the skill directory to your tool's skills location, e.g.:
cp -r skills/albs-cli <YOUR_SKILLS_DIR>/albs-cli
```

Or copy the contents of `skills/albs-cli/SKILL.md` into your project's `AGENTS.md` or equivalent instructions file.

The skill teaches the AI agent the same workflows (investigation order, EPEL handling, signing) but via `albs` shell commands instead of MCP tool calls.

**Step 3.** Verify:

```bash
albs --help
```

The CLI also works standalone — no AI needed. Useful for scripts and manual terminal use.

## CLI usage

```bash
# List platforms
albs platforms

# Investigate a build
albs build-info 52679
albs failed-tasks 52679
# log-search greps for the failure and shows it with context — start here.
# It auto-downloads the log if needed (download-log is optional)
albs log-search 52679 "mock_build.395391.1772974729.log"
# ...or grep for something specific
albs log-search 52679 "mock_build.395391.1772974729.log" -e "Hunk #\d+ FAILED" -A 3
# When the search finds nothing, page the log bottom-up: each page prints the
# exact command for the page above it, so the pages join up with no gaps
albs log-tail 52679 "mock_build.395391.1772974729.log"
albs log-tail 52679 "mock_build.395391.1772974729.log" --before-line 772

# Search builds
albs search --project bash --page 2

# Create a build (requires JWT)
albs create-build AlmaLinux-9 bash --branch c9s
albs create-build AlmaLinux-10 https://example.com/pkg.src.rpm \
    --from-srpm --add-epel-dist --arch x86_64_v2 \
    --flavor EPEL-10 --flavor EPEL-10_altarch

# Build on multiple platforms at once
albs create-build AlmaLinux-8 bash --branch c9s \
    --add-platform AlmaLinux-9

# Build from an external Git repo (e.g. GitHub)
albs create-build AlmaLinux-10 \
    --git-url https://github.com/ykohut/leapp-data.git \
    --branch devel-ng-0.23.0

# Independent tasks (disable the default sequential per-platform task chain,
# so packages build in parallel within each platform)
albs create-build AlmaLinux-9 bash glibc openssl --branch c9s --independent-tasks

# Sign a build (requires JWT)
albs sign-keys
albs sign-build 52679 --key-id 4

# Check whether signing finished
albs sign-status 52679

# List products (release targets) and view an existing release plan
albs products
albs release-plan 39229

# Create a release plan (requires JWT) — never performs the actual release
albs create-release-plan 62316 --platform AlmaLinux-8 --product AlmaLinux
# Release a PARTIAL build (only fully-completed packages):
albs create-release-plan 62316 --platform AlmaLinux-8 --product AlmaLinux \
    --whole-packages-only

# View an advisory
albs errata ALSA-2026:A001

# Preview, then create, AlmaLinux advisories: package + CVE(s) + build(s)
# (IDs or URLs). Dates default to today (--issued-date / --updated-date).
albs preview-errata 54743 https://build.almalinux.org/build/54740 54757 \
    --package kernel --cve CVE-2026-31431 --cve CVE-2026-43284
albs create-errata 54743 54740 54757 \
    --package kernel --cve CVE-2026-31431 --cve CVE-2026-43284
# From an RHSA everything (title, description, CVEs, severity) comes from it:
albs preview-errata 54740 54757 --rhsa RHSA-2026:13566

# Pass token via flag or env var
albs --token "eyJ..." sign-keys
ALBS_JWT_TOKEN="eyJ..." albs sign-keys
```

Run `albs --help` or `albs <command> --help` for full usage.

## Tools reference

### Read-only (no auth)

| Tool | Description |
|---|---|
| `get_platforms` | All platforms and their architectures, fetched dynamically from ALBS |
| `get_build_info` | Build summary: every task with status, arch, package, git ref, log count, plus Secure Boot state, flavors and any linked builds |
| `get_failed_tasks` | Only failed tasks with their log files listed; key logs marked with ★ |
| `list_build_logs` | All log/config files available for a build on the server |
| `download_log` | Download a log file to local disk (`/tmp/albs-logs/<build_id>/`) |
| `search_log` | **Start here on a failure**: grep a log for the build-failure signatures (or your own regex) and get every hit with line numbers and context; auto-downloads if needed |
| `read_log_tail` | Read a page of a log from the end, and page upward from there (`before_line`); each result prints the exact call for the page above it. Shows how the build terminated, not where the compile error is |
| `read_log_range` | Read a specific line range from a log (e.g. around a `search_log` hit); stops at the size budget and tells you how to continue |
| `search_builds` | Browse builds by page, filter by package name or running status — shows each package as an NVR plus the build's release state; a `project` filter lists the matched package on its own `match:` line |
| `get_sign_task_status` | Status of a build's sign tasks (idle/in_progress/completed/failed) — use after `sign_build` |
| `get_products` | List all products (release targets): ID, name, official/community, platforms |
| `get_release_plan` | View an existing release: status, source packages, target repositories |
| `get_errata` | View an advisory: title, severity, release status, dates, module, references, packages, builds, description |
| `preview_errata` | Show the advisories `create_errata` would create from a package + CVEs (or an RHSA) + builds — IDs, dates, platforms, titles, severity, module, included/left-out source packages, packages, description source, references, every failed build task, and existing advisories that already cover the CVEs (`!! DUPLICATE` for the same package). Creates nothing |

### Authenticated (JWT required)

| Tool | Description |
|---|---|
| `get_sign_keys` | List sign keys: ID, name, GPG keyid, active status, platform mappings |
| `create_build` | Create a build: packages or custom Git URLs + platform(s) + branch/tag/srpm, with all mock options |
| `sign_build` | Create a sign task for a build with a chosen key |
| `create_release_plan` | Create a *scheduled* release plan for a build + platform + product. **Never performs the actual release** — only the plan |
| `create_errata` | Create AlmaLinux advisories (`ALSA-YYYY:Annn`) from a package + CVEs (or an RHSA) + build(s); everything else is filled automatically, IDs assigned right before creating. Refuses duplicates of existing advisories unless `force=True`. Does not release them |
| `commit_release` | **Blocked** — performing the actual release is disabled; only plans are supported |
| `delete_build` | **Blocked** — disabled for safety |

## Prompts

MCP prompts are user-invoked workflow entry points. In clients like Claude Code they appear as slash commands (`/mcp__albs__<name>`); the user triggers them, not the agent.

| Prompt | Arguments | Description |
|---|---|---|
| `investigate_build` | `build_id` | Seeds the build-failure investigation workflow for a build ID. Equivalent to asking "why did build N fail?", but as a one-step parameterized command. |
| `release_plan` | `build_id` | Seeds the release-plan workflow for a build ID (confirm platform, pick product, create the plan). **Never performs the actual release.** |
| `new_errata` | `advisory`, `builds`, `package` (optional) | Seeds the errata workflow for CVE(s)/an RHSA, build(s) and the package: preview, confirm, create, verify. |

Example (Claude Code):

```
/mcp__albs__investigate_build 52679
/mcp__albs__release_plan 52679
/mcp__albs__new_errata "CVE-2026-31431 CVE-2026-43284" "54743 54740 54757" kernel
```

`investigate_build` expands into the investigation workflow (`get_build_info` → `get_failed_tasks` → download/read the key logs in order), parameterized by the build ID. `release_plan` expands into the release-plan workflow (`get_build_info` → `get_products` → `create_release_plan`), and explicitly stops at the plan — it never commits/publishes. `new_errata` expands into the errata workflow (`preview_errata` → confirm → `create_errata` → `get_errata`).

## Example: investigating a failed build

Ask the agent: *"What went wrong in build 52679?"*

The agent will:

1. **`get_build_info(70368)`** — sees that only the i686 task failed; the other 7 arches built
2. **`get_failed_tasks(70368)`** — gets the log files, ★ marks the important ones
3. **`search_log(70368, "mock_build.441500.1785274367.log")`** — greps the 936-line / 600 KB
   log and returns the cause with context, in one call:
   ```
   >>> 826 | usr/lib/common/mech_openssl.c:2766:52: error: passing argument 5 of
             'EVP_PKEY_get_octet_string_param' from incompatible pointer type
       833 | note: expected 'size_t *' {aka 'unsigned int *'} but argument is of
             type 'CK_ULONG *' {aka 'long unsigned int *'}
   >>> 853 | make[1]: *** [Makefile:9851: ...mech_openssl.lo] Error 1
   ```
4. **`search_log(70368, "mock_root.441500.1785274367.log")`** — no matches: the chroot and
   dependencies were fine, so this is not a dependency failure
5. Reports: *"`CK_ULONG *` is `unsigned long *` while OpenSSL wants `size_t *`; on ILP32
   (i686) those are different types, so the 3.27.0 rebase only breaks on 32-bit."*

Had the search come up empty, the next move is `read_log_tail` and then the
`↑ earlier: ...` call it prints, walking the log upward a page at a time. Pages are
sized by the character budget, not a line count — 165 lines of this `mock_build`, or
350 of the same build's `mock_root` — and each one starts exactly where the previous
stopped, so nothing is skipped.

Note what step 3 replaces. `read_log_tail` on that log returns
`make: *** [Makefile:4615: all] Error 2` — the symptom, hundreds of lines below the real
error, because `make -j` keeps compiling after the first failure. Asking for enough tail to
reach the error instead returns 167 KB of gcc command lines and can exceed the caller's
result-size limit. `search_log` returns 4 KB with the answer at the top.

## Example: creating a build

Ask the agent: *"Build bash for AlmaLinux-9 from branch c9s"*

The agent will call:
```
create_build(packages=["bash"], platform="AlmaLinux-9", branch="c9s")
```

For multiple platforms at once:
```
create_build(packages=["bash"], platforms=["AlmaLinux-8", "AlmaLinux-9"], branch="c9s")
```

For external Git repos (e.g. GitHub), use `git_urls`:
```
create_build(git_urls=["https://github.com/ykohut/leapp-data.git"], platform="AlmaLinux-10", branch="devel-ng-0.23.0")
```

Architectures default to each platform's full list. When `arch_list` is specified with multiple platforms, it is validated against each platform individually.

## Example: creating a release plan

Ask the agent: *"Create a release plan for build 62316 on AlmaLinux-8."*

The agent will:

1. **`get_build_info(62316)`** — confirms the platform and that the build has completed tasks
2. **`get_products()`** — lists products so you can pick the target (e.g. `AlmaLinux`)
3. **`create_release_plan(build_id=62316, platform="AlmaLinux-8", product="AlmaLinux")`** — collects the completed build tasks, resolves the platform/product names to IDs, and creates a *scheduled* plan
4. Reports the plan (status, source packages, target repositories) and makes clear that **nothing was published** — it is only a plan

> The actual release (committing/publishing the plan) is intentionally **not** performed. Asking the agent to "release for real" routes to `commit_release`, which is blocked and explains that only plans are supported.

## Example: creating errata

Ask the agent: *"Make an advisory for kernel, CVE-2026-31431, builds https://build.almalinux.org/build/54743, 54740 and 54757."*

You give only the package (or module), the CVEs — or one RHSA — and the builds. The agent will:

1. **`preview_errata(builds=[...], cves=["CVE-2026-31431"], package="kernel")`** — shows, without creating anything:
   ```
   Issued: 2026-10-23  Updated: 2026-10-23

   ALSA-2026:A011  AlmaLinux-8  build #54743
     Title: kernel security update  (ALBS shows "Important: kernel security update")
     Module: none
     Source packages: kernel
     Packages: 94 RPM(s) (aarch64, i686, noarch, ppc64le, s390x, x86_64)
     Description: package text from ALSA-2026:87375 + CVE fix list

   ALSA-2026:A012  AlmaLinux-10  builds #54740, #54757
     ...
     Packages: 272 RPM(s) (aarch64, noarch, ppc64le, s390x, x86_64, x86_64_v2)

   References:
     CVE-2026-31431  Important  CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H  CWE-1288  ...
     bugzilla 2460538  https://bugzilla.redhat.com/show_bug.cgi?id=2460538

   Description:
     The kernel packages contain the Linux kernel, the core of any Linux operating system.

     Security Fix(es):

       * kernel: crypto: algif_aead - Revert to operating out-of-place (CVE-2026-31431)

     For more details about the security issue(s), ...
   ```
   54740 failed on aarch64 and 54757 is its retry, so both go into one AlmaLinux-10 advisory. **Every failed or cancelled build task is always reported** — in the preview, in the `create_errata` result and in error messages — even when a retry build covers it or the package is left out:
   ```
   !!! 1 build task(s) FAILED or were CANCELLED — each is listed below as `! ...`. Tell the user about every one.
     ! FAILED: build #54740 kernel aarch64 (task 411949) — rebuilt in build #54757; packages taken from there
   ```
   Without 54757 the same line ends with `— its packages are NOT in the advisory (add the retry build to include them)`.
2. Asks you to confirm, then **`create_errata(...)`** with the same arguments — it re-reads the latest `ALSA-2026:A###` right before creating, so the IDs are always the next free ones
3. **`get_errata("ALSA-2026:A011")`** — confirms ALBS stored it (ALBS creates advisories in the background)

How each field is filled:

| Field | Source |
|---|---|
| ID | `ALSA-<issue year>:A<next>` — highest AlmaLinux A-number of the year on any platform, +1; advisories numbered in platform order (8, 9, 10) |
| Dates | Today (issued = updated), unless `issued_date` / `updated_date` are given |
| Severity | Red Hat: the CVE's threat severity (the highest one for several CVEs) or the RHSA's aggregate severity |
| Title | `<package> security update` — the module stream for modular builds (`nodejs:24 security update`); with an RHSA, the RHSA title. ALBS adds the `Important:` prefix |
| Description | CVEs: the package text from the newest earlier security advisory for the package (same platform first, then any; another stream of the module if needed) + a `Security Fix(es):` list from the Red Hat bugzilla summaries. RHSA: its details text (Red Hat branding replaced) |
| CVE information, References | Each CVE (Red Hat CVE page, with CVSS v3, CWE, impact, public date) and its Red Hat bugzilla |
| Module | `name:stream` of a modular build (from its tasks' module metadata, `-devel` excluded); empty otherwise |
| Packages | Binary RPMs of the named package (or module) from the build(s) — no src/debuginfo/debugsource, other packages in the build left out. Without `package`: the packages Red Hat lists as affected (RHSA: its source packages) |

The preview adds a `NOTE` when Red Hat does not tie one of the given CVEs to the package (usually a wrong CVE ID) or when no earlier advisory has a package text to reuse.

**Duplicates.** The preview looks up existing advisories on the same platform that already reference the CVEs. One for the same package (same module stream, or same source RPM) is a duplicate — typically the ALSA imported from the RHSA that this build is a plain rebuild of:

```
!! DUPLICATE: ALSA-2026:87073 "Important: php:8.2 security update" already covers CVE-2026-109618, CVE-2026-109619 for php:8.2 on AlmaLinux-8
```

`create_errata` then creates nothing unless `force=True` (`albs create-errata --force`). The same CVE in another package's advisory (kernel vs kernel-rt, php:7.4 vs php:8.2) is only a `NOTE`.

## Tests

```bash
pip install -e ".[test]"

# Unit tests (no network, 347 tests)
pytest tests/test_client_unit.py tests/test_server_unit.py tests/test_cli_unit.py -v

# Integration tests (hits real ALBS API, read-only, 37 tests)
pytest tests/test_integration.py -v

# All tests
pytest -v
```

## Environment variables

| Variable | Description | Default |
|---|---|---|
| `ALBS_JWT_TOKEN` | JWT token for authenticated operations | — |
| `ALBS_LOG_DIR` | Directory for downloaded logs | `/tmp/albs-logs` |
