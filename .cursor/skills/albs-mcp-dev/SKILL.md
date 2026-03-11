---
name: albs-mcp-dev
description: Develop, test, and evaluate the ALBS MCP server for AlmaLinux Build System. Use this skill whenever making changes to albs-mcp source code, adding or modifying MCP tools, fixing tests, running unit tests or evals, investigating test failures, updating server instructions, or working on eval definitions. Also use when the user mentions "запусти тесты", "запусти evals", "добавь тул", "почини тест", "добавь eval кейс", "обнови инструкции", or any development task related to albs-mcp. Even for simple changes like renaming a parameter — this skill ensures the full test/eval/docs pipeline is followed.
---

# ALBS MCP Development

Skill for developing the ALBS MCP server. After ANY code change, run tests and evals yourself — never ask the user to do it.

## Project layout

```
src/albs_mcp/
  constants.py    — URLs, status maps, EPEL defaults
  client.py       — ALBSClient: all HTTP calls to ALBS API
  server.py       — MCP tools (FastMCP), server instructions, CLI
tests/
  test_client_unit.py    — client unit tests (mocked HTTP)
  test_server_unit.py    — server tool unit tests (mocked client)
  test_integration.py    — integration tests (real ALBS API, read-only)
```

## Development workflow

After ANY code change — no exceptions — do all of this yourself:

1. **Run unit tests**: `.venv/bin/python -m pytest tests/test_client_unit.py tests/test_server_unit.py -v`
2. **Run evals**: read `evals/evals.json` from this skill, verify ALL 20 cases against the current code (see "Running evals" below)
3. **Reinstall**: `.venv/bin/python -m pip install -e .`

Never skip evals. Never ask the user to run them. Every change can break agent workflows in non-obvious ways.

## Running evals

Eval definitions live in [evals/evals.json](evals/evals.json) inside this skill directory — 20 cases across 5 categories.

After any code change:

1. Read `evals/evals.json`
2. Read the current server instructions (`instructions=` in `FastMCP()`) and tool docstrings/signatures
3. For EACH eval case, verify every criterion against the code — would an agent following the current instructions satisfy this criterion?
4. Report results to the user: pass/fail per case, with explanation for any failures

Format the report as a table with PASS/FAIL per category, then details for any failing criteria.

## Adding a new MCP tool

Every new tool MUST have eval cases. Follow this checklist in order:

1. Add client method in `client.py`
2. Add `@mcp.tool()` in `server.py` with docstring
3. Add unit tests in both `test_client_unit.py` and `test_server_unit.py`
4. Update server instructions in `server.py` if the tool changes agent workflows
5. **Add eval case(s) to `evals/evals.json`** — at minimum one tool_selection eval (routes to the right tool) and one workflow eval if the tool is part of a multi-step workflow
6. Update `README.md` (tools reference table, examples)
7. Run: unit tests → ALL evals → reinstall

Step 5 is not optional. A tool without evals is not done.

## Adding a new eval case

Add directly to `evals/evals.json` in this skill:

```json
{
  "id": 21,
  "name": "my_new_case",
  "category": "tool_selection",
  "prompt": "User's natural language request",
  "expected_output": "Summary of correct agent behavior",
  "criteria": [
    "The agent calls my_new_tool with the correct arguments",
    "The agent does NOT call unrelated_tool"
  ],
  "files": []
}
```

Criteria tips: be specific ("calls X with param=Y"), include negative checks ("does NOT do Z without asking").

## Modifying server instructions

The `instructions=` parameter in `FastMCP()` controls agent decision-making. After changes:

1. Run ALL evals — any instruction change can affect any workflow
2. Add new eval cases if adding a new workflow pattern
