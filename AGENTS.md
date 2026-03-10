# albs-mcp project rules

MCP server for AlmaLinux Build System. Three source files: `constants.py` (config values), `client.py` (HTTP/API logic), `server.py` (MCP tool definitions and server instructions).

## Project structure

```
src/albs_mcp/
  constants.py   — URLs, status maps, package lists, EPEL defaults
  client.py      — ALBSClient: all HTTP calls to ALBS API and log file I/O
  server.py      — MCP tools (FastMCP), server instructions, CLI entrypoint
tests/
  test_client_unit.py   — unit tests for ALBSClient (mocked HTTP)
  test_server_unit.py   — unit tests for MCP tools (mocked client)
  test_integration.py   — integration tests against real ALBS API (read-only)
```

## Documentation

- Keep `README.md` in sync with the code. When adding, removing, or renaming tools or parameters, update the corresponding sections: "What it can do", "Tools reference", examples, and environment variables.
- Update test counts in the "Tests" section of `README.md` when adding new tests.
- Tool docstrings in `server.py` are exposed as MCP tool descriptions to the AI agent. Keep them accurate, concise, and up to date with the actual behavior.
- Server instructions (the `instructions` parameter in `FastMCP()`) guide the AI agent's decision-making: when to ask the user, what defaults to apply, how to handle EPEL builds, signing workflow, etc. Update them whenever tool semantics or workflows change.

## Testing

- Run `pytest tests/ -v` and ensure **all tests pass** before considering any change complete.
- Unit tests use mocked HTTP (`test_client_unit.py`) and mocked ALBSClient (`test_server_unit.py`). They require no network.
- Integration tests (`test_integration.py`) hit the real ALBS API at `build.almalinux.org`. They are read-only and safe to run, but require network access.
- Every new tool, parameter, or behavioral change must have corresponding unit tests.
- Async tests use `pytest-asyncio` with `asyncio_mode = "auto"` (configured in `pyproject.toml`). No need to decorate with `@pytest.mark.asyncio` explicitly.

## Code style

- Python 3.10+. Always use `from __future__ import annotations` at the top of each module.
- Type hints on all public functions and method signatures.
- Follow the existing file separation: constants and config in `constants.py`, all HTTP and file I/O in `client.py`, MCP tool definitions and formatting in `server.py`.
- No code duplication. Every HTTP call or piece of logic must live in exactly one place. `client.py` owns all HTTP calls; `server.py` MCP tools call client methods and format output. Follow the `get_platforms` / `get_flavors` pattern: client method fetches data, server tool formats it, other client methods reuse the same client method internally.
- No hardcoded secrets or tokens anywhere in the code. Tokens are read from the `ALBS_JWT_TOKEN` env var or `~/.albs/credentials` file at runtime.

## Fail-fast, no silent errors

- Never silently skip invalid input. If a user passes an unknown flavor name, platform, arch, or any identifier that doesn't match what the API returns — raise an error immediately with the list of valid options. No `if x in dict` filtering that hides mistakes.
- Never hardcode ALBS entity names (flavors, platforms, etc.) without verifying them against the live API. Names change — always validate dynamically.
- When values in `constants.py` are used as defaults (e.g. `EPEL_PLATFORM_FLAVORS`), they must match real ALBS data. After adding or changing default values, verify them against the API (e.g. `get_flavors`, `get_platforms`).

## MCP server management

- The MCP server config lives in `~/.cursor/mcp.json`. See `README.md` for the config snippet.
- After changing any source code in `src/albs_mcp/`, the package must be reinstalled and Cursor must be reloaded for changes to take effect.
- Platform names in ALBS are case-sensitive (e.g. `AlmaLinux-Kitten-10`, not `almalinux-kitten-10`). Always use `get_platforms` to verify exact names.

## Security

- Never log, print, or return JWT tokens in tool responses or error messages.
- Build deletion (`delete_build`) is intentionally blocked for safety. Do not remove or bypass this guard.
- Validate file paths in log operations (`_log_path`, `download_log`, `read_log_tail`, `read_log_range`) to prevent path traversal. The resolved path must stay inside the build log directory.
- Error messages returned to the MCP client should not expose internal filesystem paths, stack traces, or sensitive details.
