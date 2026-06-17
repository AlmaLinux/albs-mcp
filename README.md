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

- **Investigate build failures** — the main use case. Give the agent a build ID and it will walk through the logs (mock_root → mock_stderr → mock_build), reading from the end to find the error without wasting tokens on 100k+ line log files.
- **Get build details** — statuses of all tasks, packages, architectures, sign tasks.
- **List and search builds** — browse recent builds, filter by package name or status.
- **Get platforms** — dynamically fetched list of all platforms and their supported architectures.
- **Download and read logs** — any log file from any build, with smart pagination (tail first, then range). Reading auto-downloads the log if it isn't on disk yet.
- **Check sign status** — see whether sign tasks for a build completed or failed.
- **List products** — all release targets (products) with their platforms, official/community flag, and IDs.
- **View release plans** — status, source packages, and target repositories of any existing release.

### With a JWT token (authenticated)

- **Create builds** — specify packages, platform(s), branch/tag/SRPM. Supports multiple platforms in a single build (e.g. AlmaLinux-8 + AlmaLinux-9). Architectures default to each platform's full list unless you override. Supports custom Git URLs for repos outside `git.almalinux.org` (e.g. GitHub, GitLab). Supports all mkbuild.py options: linked builds, mock definitions, excludes, flavors, secureboot, modules, with/without.
- **Sign builds** — create sign tasks with a chosen key.
- **List sign keys** — see available keys with IDs and platform mappings.
- **Create release plans** — build a *scheduled* release plan for a build (which packages go to which repositories) targeting a chosen platform + product. **The actual release is never performed** — this only creates the plan; committing/publishing is intentionally blocked.
- **Delete builds** — intentionally blocked for safety.

### Log types

ALBS produces several log files per build task. The key ones for debugging:

| Log | What's inside |
|---|---|
| `mock_root` | Chroot setup, dependency resolution. Check first — if deps failed, nothing else matters. |
| `mock_stderr` | Stderr output from the build process. Often has the clearest error message. |
| `mock_build` | Full build log (can be 100k+ lines). Contains the complete rpmbuild output. Check last. |
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
# log-tail auto-downloads the log if needed (download-log is optional)
albs log-tail 52679 "mock_build.395391.1772974729.log" -n 200

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
| `get_build_info` | Build summary: every task with status, arch, package, git ref, log count |
| `get_failed_tasks` | Only failed tasks with their log files listed; key logs marked with ★ |
| `list_build_logs` | All log/config files available for a build on the server |
| `download_log` | Download a log file to local disk (`/tmp/albs-logs/<build_id>/`) |
| `read_log_tail` | Read last N lines of a log (default 3000 — errors are at the end); auto-downloads if needed |
| `read_log_range` | Read a specific line range from a log; auto-downloads if needed |
| `search_builds` | Browse builds by page, filter by package name or running status |
| `get_sign_task_status` | Status of a build's sign tasks (idle/in_progress/completed/failed) — use after `sign_build` |
| `get_products` | List all products (release targets): ID, name, official/community, platforms |
| `get_release_plan` | View an existing release: status, source packages, target repositories |

### Authenticated (JWT required)

| Tool | Description |
|---|---|
| `get_sign_keys` | List sign keys: ID, name, GPG keyid, active status, platform mappings |
| `create_build` | Create a build: packages or custom Git URLs + platform(s) + branch/tag/srpm, with all mock options |
| `sign_build` | Create a sign task for a build with a chosen key |
| `create_release_plan` | Create a *scheduled* release plan for a build + platform + product. **Never performs the actual release** — only the plan |
| `commit_release` | **Blocked** — performing the actual release is disabled; only plans are supported |
| `delete_build` | **Blocked** — disabled for safety |

## Prompts

MCP prompts are user-invoked workflow entry points. In clients like Claude Code they appear as slash commands (`/mcp__albs__<name>`); the user triggers them, not the agent.

| Prompt | Arguments | Description |
|---|---|---|
| `investigate_build` | `build_id` | Seeds the build-failure investigation workflow for a build ID. Equivalent to asking "why did build N fail?", but as a one-step parameterized command. |
| `release_plan` | `build_id` | Seeds the release-plan workflow for a build ID (confirm platform, pick product, create the plan). **Never performs the actual release.** |

Example (Claude Code):

```
/mcp__albs__investigate_build 52679
/mcp__albs__release_plan 52679
```

`investigate_build` expands into the investigation workflow (`get_build_info` → `get_failed_tasks` → download/read the key logs in order), parameterized by the build ID. `release_plan` expands into the release-plan workflow (`get_build_info` → `get_products` → `create_release_plan`), and explicitly stops at the plan — it never commits/publishes.

## Example: investigating a failed build

Ask the agent: *"What went wrong in build 52679?"*

The agent will:

1. **`get_build_info(52679)`** — sees 2 tasks: src completed, x86_64 failed
2. **`get_failed_tasks(52679)`** — gets 14 log files, ★ marks the important ones
3. **`download_log(52679, "mock_root.395391.1772974729.log")`** — downloads root log
4. **`read_log_tail(52679, "mock_root.395391.1772974729.log")`** — checks chroot setup: all clean
5. **`download_log(52679, "mock_stderr.395391.1772974729.log")`** — downloads stderr
6. **`read_log_tail(52679, "mock_stderr.395391.1772974729.log")`** — sees rpmbuild command
7. **`download_log(52679, "mock_build.395391.1772974729.log")`** — downloads full build log (236KB)
8. **`read_log_tail(52679, "mock_build.395391.1772974729.log", 200)`** — finds the error:
   ```
   gmake[1]: *** [libtransmission/CMakeFiles/transmission.dir/all] Error 2
   ```
9. Reports: *"Build failed due to a compilation error in libtransmission."*

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

## Tests

```bash
pip install -e ".[test]"

# Unit tests (no network)
pytest tests/test_client_unit.py tests/test_server_unit.py tests/test_cli_unit.py -v

# Integration tests (hits real ALBS API, read-only, 26 tests)
pytest tests/test_integration.py -v

# All tests
pytest -v
```

## Environment variables

| Variable | Description | Default |
|---|---|---|
| `ALBS_JWT_TOKEN` | JWT token for authenticated operations | — |
| `ALBS_LOG_DIR` | Directory for downloaded logs | `/tmp/albs-logs` |
