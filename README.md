# albs-mcp

MCP server for [AlmaLinux Build System](https://build.almalinux.org) (ALBS).

Gives AI coding assistants (Cursor, Claude Desktop, etc.) direct access to ALBS — investigate build failures, create builds, sign packages, all through natural language.

## What it can do

### Without a token (read-only)

- **Investigate build failures** — the main use case. Give the agent a build ID and it will walk through the logs (mock_root → mock_stderr → mock_build), reading from the end to find the error without wasting tokens on 100k+ line log files.
- **Get build details** — statuses of all tasks, packages, architectures, sign tasks.
- **List and search builds** — browse recent builds, filter by package name or status.
- **Get platforms** — dynamically fetched list of all platforms and their supported architectures.
- **Download and read logs** — any log file from any build, with smart pagination (tail first, then range).

### With a JWT token (authenticated)

- **Create builds** — specify packages, platform, branch/tag/SRPM. Architectures default to the platform's full list unless you override. Supports all mkbuild.py options: linked builds, mock definitions, excludes, flavors, secureboot, modules, with/without.
- **Sign builds** — create sign tasks with a chosen key.
- **List sign keys** — see available keys with IDs and platform mappings.
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

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv pip install git+https://github.com/AlmaLinux/albs-mcp.git
```

## Quick start

### Read-only (no token)

```bash
albs-mcp
```

### With JWT token (enables build creation + signing)

```bash
albs-mcp --token YOUR_JWT_TOKEN
```

The token can also be set via env var:

```bash
ALBS_JWT_TOKEN=xxx albs-mcp
```

## Cursor / Claude Desktop config

### Read-only

```json
{
  "mcpServers": {
    "albs": {
      "command": "albs-mcp"
    }
  }
}
```

### With uvx (no install needed)

```json
{
  "mcpServers": {
    "albs": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/AlmaLinux/albs-mcp.git", "albs-mcp"]
    }
  }
}
```

### With token

```json
{
  "mcpServers": {
    "albs": {
      "command": "albs-mcp",
      "args": ["--token", "YOUR_JWT_TOKEN"]
    }
  }
}
```

## Tools reference

### Read-only (no auth)

| Tool | Description |
|---|---|
| `get_platforms` | All platforms and their architectures, fetched dynamically from ALBS |
| `get_build_info` | Build summary: every task with status, arch, package, git ref, log count |
| `get_failed_tasks` | Only failed tasks with their log files listed; key logs marked with ★ |
| `list_build_logs` | All log/config files available for a build on the server |
| `download_log` | Download a log file to local disk (`/tmp/albs-logs/<build_id>/`) |
| `read_log_tail` | Read last N lines of a downloaded log (default 3000 — errors are at the end) |
| `read_log_range` | Read a specific line range from a downloaded log |
| `search_builds` | Browse builds by page, filter by package name or running status |

### Authenticated (JWT required)

| Tool | Description |
|---|---|
| `get_sign_keys` | List sign keys: ID, name, GPG keyid, active status, platform mappings |
| `create_build` | Create a build: packages + platform + branch/tag/srpm, with all mock options |
| `sign_build` | Create a sign task for a build with a chosen key |
| `delete_build` | **Blocked** — disabled for safety |

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

Architectures default to the full platform list (i686, x86_64, aarch64, ppc64le, s390x).

## Tests

```bash
pip install -e ".[test]"

# Unit tests (no network, 63 tests)
pytest tests/test_client_unit.py tests/test_server_unit.py -v

# Integration tests (hits real ALBS API, read-only, 21 tests)
pytest tests/test_integration.py -v

# All 84 tests
pytest -v
```

## Environment variables

| Variable | Description | Default |
|---|---|---|
| `ALBS_JWT_TOKEN` | JWT token for authenticated operations | — |
| `ALBS_LOG_DIR` | Directory for downloaded logs | `/tmp/albs-logs` |
