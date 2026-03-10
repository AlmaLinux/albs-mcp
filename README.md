# albs-mcp

MCP server for [AlmaLinux Build System](https://build.almalinux.org).

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

The token is read from `~/.albs/credentials` by default in the build scripts.
You can also set it via env var:

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
      "args": ["--from", "git+https://github.com/almalinux/albs-mcp.git", "albs-mcp"]
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

## Tools

### Read-only (no auth)

| Tool | Description |
|---|---|
| `get_platforms` | All platforms and their architectures (fetched dynamically) |
| `get_build_info` | Build details: tasks, statuses, packages, architectures |
| `get_failed_tasks` | Failed tasks with log file listings |
| `list_build_logs` | All available log files for a build |
| `download_log` | Download a log file to local filesystem |
| `read_log_tail` | Read last N lines of a downloaded log (default 3000) |
| `read_log_range` | Read a specific line range from a downloaded log |
| `search_builds` | Search/list builds with filters |

### Authenticated (JWT required)

| Tool | Description |
|---|---|
| `get_sign_keys` | List available sign keys with IDs and platform mappings |
| `create_build` | Create a new build (packages, platform, branch/tag/srpm) |
| `sign_build` | Sign a completed build |
| `delete_build` | **Blocked** — disabled for safety |

## Log analysis workflow

1. `get_build_info(build_id=12345)` — see which tasks failed
2. `get_failed_tasks(build_id=12345)` — see available logs (★ = key logs)
3. `download_log(build_id=12345, filename="mock_build.395514.1773057957.log")` — save to disk
4. `read_log_tail(build_id=12345, filename="mock_build.395514.1773057957.log")` — read last 3000 lines
5. If the error is not at the end, use `read_log_range` to look earlier

Key logs for debugging: `mock_build`, `mock_stderr`, `mock_root`.

## Tests

```bash
pip install -e ".[test]"

# Unit tests (no network)
pytest tests/test_client_unit.py tests/test_server_unit.py -v

# Integration tests (hits real API, read-only)
pytest tests/test_integration.py -v

# All tests
pytest -v
```

## Environment variables

| Variable | Description | Default |
|---|---|---|
| `ALBS_JWT_TOKEN` | JWT token for authenticated operations | — |
| `ALBS_LOG_DIR` | Directory for downloaded logs | `/tmp/albs-logs` |
