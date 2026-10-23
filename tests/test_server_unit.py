"""Unit tests for MCP server tools with mocked client."""
from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import pytest

import albs_mcp._commands as commands_module
from albs_mcp.constants import (
    LOG_ERROR_PATTERN,
    LOG_MAX_LINE_CHARS,
    LOG_MAX_RESULT_CHARS,
)
from albs_mcp.server import (
    mcp,
    get_build_info,
    get_failed_tasks,
    get_platforms,
    get_sign_keys,
    get_sign_task_status,
    list_build_logs,
    download_log,
    read_log_tail,
    read_log_range,
    search_log,
    search_builds,
    create_build,
    sign_build,
    delete_build,
    investigate_build,
    get_products,
    get_release_plan,
    create_release_plan,
    commit_release,
    release_plan,
    get_errata,
    preview_errata,
    create_errata,
    new_errata,
)


def _http_status_error(code: int) -> httpx.HTTPStatusError:
    """Build an httpx.HTTPStatusError for a given status code (for mocks)."""
    request = httpx.Request("GET", "https://build.almalinux.org/api/v1/x/")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError(f"HTTP {code}", request=request, response=response)


SAMPLE_BUILD = {
    "id": 50000,
    "created_at": "2026-03-01T10:00:00",
    "finished_at": "2026-03-01T11:00:00",
    "owner": {"id": 1, "username": "builder", "email": "b@example.com"},
    "released": False,
    "tasks": [
        {
            "id": 300001,
            "status": 2,
            "arch": "x86_64",
            "ref": {
                "url": "https://git.almalinux.org/rpms/glibc.git",
                "git_ref": "c9s",
            },
            "artifacts": [
                {"name": "glibc.rpm", "type": "rpm"},
                {"name": "mock_build.300001.111.log", "type": "build_log"},
            ],
            "platform": {"id": 1, "name": "AlmaLinux-9"},
            "test_tasks": [],
        },
        {
            "id": 300002,
            "status": 3,
            "arch": "aarch64",
            "ref": {
                "url": "https://git.almalinux.org/rpms/glibc.git",
                "git_ref": "c9s",
            },
            "artifacts": [
                {"name": "mock_build.300002.222.log", "type": "build_log"},
                {"name": "mock_stderr.300002.222.log", "type": "build_log"},
                {"name": "mock_root.300002.222.log", "type": "build_log"},
                {"name": "albs.300002.222.log", "type": "build_log"},
                {"name": "mock.300002.222.cfg", "type": "build_log"},
            ],
            "platform": {"id": 1, "name": "AlmaLinux-9"},
            "test_tasks": [],
        },
        {
            "id": 300003,
            "status": 3,
            "arch": "s390x",
            "ref": {
                "url": "https://git.almalinux.org/rpms/glibc.git",
                "git_ref": "c9s",
            },
            "artifacts": [],
            "platform": {"id": 1, "name": "AlmaLinux-9"},
            "test_tasks": [],
        },
    ],
    "sign_tasks": [
        {"id": 777, "status": 3},
    ],
    "linked_builds": [],
    "mock_options": None,
    "platform_flavors": [],
}


@pytest.fixture(autouse=True)
def reset_client():
    """Reset the global client before each test."""
    commands_module._client = None
    yield
    commands_module._client = None


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.get_build = AsyncMock(return_value=SAMPLE_BUILD)
    client.get_platforms = AsyncMock(return_value=[
        {"name": "AlmaLinux-9", "arch_list": ["x86_64", "aarch64", "s390x"]},
        {"name": "AlmaLinux-10", "arch_list": ["x86_64", "aarch64"]},
    ])
    client.get_sign_keys = AsyncMock(return_value=[
        {"id": 4, "name": "AL9-key", "keyid": "ABC123", "active": True, "description": "Main key", "platform_ids": [2]},
    ])
    client.list_build_logs = AsyncMock(return_value=[
        "mock_build.300002.222.log",
        "mock_stderr.300002.222.log",
        "mock_root.300002.222.log",
        "albs.300002.222.log",
        "mock.300002.222.cfg",
    ])
    client.search_builds = AsyncMock(return_value={
        "builds": [SAMPLE_BUILD],
        "total_builds": 1,
        "current_page": 1,
    })
    client.download_log = AsyncMock(return_value=Path("/tmp/test/mock_build.log"))
    client.read_log_tail = MagicMock(
        return_value=("error: fail", 5000, 4990, 5000)
    )
    client.read_log_range = MagicMock(
        return_value=("line 100\nline 101", 5000, 102)
    )
    client.search_log = MagicMock(return_value=(
        [[
            (826, "foo.c: In function 'bar':", False),
            (827, "foo.c:12:5: error: boom", True),
        ]],
        1, 936, 3,
    ))
    client.create_build = AsyncMock(return_value={"id": 99999, "created_at": "2026-03-10T00:00:00"})
    client.sign_build = AsyncMock(return_value={"id": 888, "status": 1})
    client.get_platform_ids = AsyncMock(return_value={
        "AlmaLinux-9": 2, "AlmaLinux-10": 3,
    })
    client.get_products = AsyncMock(return_value=[
        {"id": 1, "name": "AlmaLinux", "is_community": False,
         "platforms": [{"name": "AlmaLinux-9"}]},
        {"id": 613, "name": "epel-al", "is_community": True,
         "platforms": [{"name": "AlmaLinux-10"}]},
    ])
    client.get_product_ids = AsyncMock(return_value={
        "AlmaLinux": 1, "epel-al": 613,
    })
    client.create_release = AsyncMock(return_value={
        "id": 4242,
        "status": 1,
        "build_ids": [50000],
        "build_task_ids": [300001],
        "plan": {
            "packages": [
                {"package": {"name": "glibc", "version": "2.34",
                             "release": "1.el9", "arch": "src"}},
            ],
            "repositories": [
                {"id": 1, "name": "almalinux-9-baseos", "arch": "src"},
            ],
        },
        "product": {"name": "AlmaLinux"},
        "platform": {"name": "AlmaLinux-9"},
    })
    client.get_release = AsyncMock(return_value={
        "id": 4242,
        "status": 3,
        "build_ids": [50000],
        "build_task_ids": [300001],
        "plan": {
            "packages": [
                {"package": {"name": "glibc", "version": "2.34",
                             "release": "1.el9", "arch": "src"}},
            ],
            "repositories": [
                {"id": 1, "name": "almalinux-9-baseos", "arch": "src"},
            ],
        },
        "product": {"name": "AlmaLinux"},
        "platform": {"name": "AlmaLinux-9"},
    })
    commands_module._client = client
    return client


# ── get_platforms ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_platforms_tool(mock_client):
    result = await get_platforms()
    assert "AlmaLinux-9" in result
    assert "AlmaLinux-10" in result
    assert "x86_64" in result


# ── get_build_info ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_build_info_tool(mock_client):
    result = await get_build_info(50000)
    assert "Build #50000" in result
    assert "builder" in result
    assert "completed" in result
    assert "failed" in result
    assert "glibc" in result
    assert "sign_task_id=777" in result
    assert "Platform:" in result
    assert "AlmaLinux-9" in result
    assert "Architectures:" in result


@pytest.mark.asyncio
async def test_get_build_info_shows_all_tasks(mock_client):
    result = await get_build_info(50000)
    assert "300001" in result
    assert "300002" in result
    assert "300003" in result
    assert "x86_64" in result
    assert "aarch64" in result
    assert "s390x" in result


@pytest.mark.asyncio
async def test_get_build_info_shows_flavors(mock_client):
    build_with_flavors = {
        **SAMPLE_BUILD,
        "platform_flavors": [
            {"name": "EPEL-10"},
            {"name": "EPEL-10_altarch"},
        ],
    }
    mock_client.get_build = AsyncMock(return_value=build_with_flavors)
    result = await get_build_info(50000)
    assert "Flavors:" in result
    assert "EPEL-10" in result
    assert "EPEL-10_altarch" in result


@pytest.mark.asyncio
async def test_get_build_info_shows_linked_builds(mock_client):
    build_with_linked = {
        **SAMPLE_BUILD,
        "linked_builds": [61886, 62250],
    }
    mock_client.get_build = AsyncMock(return_value=build_with_linked)
    result = await get_build_info(50000)
    assert "Linked builds: 61886, 62250" in result


@pytest.mark.asyncio
async def test_get_build_info_no_linked_builds(mock_client):
    result = await get_build_info(50000)
    assert "Linked builds:" not in result


@pytest.mark.asyncio
async def test_get_build_info_secure_boot_disabled(mock_client):
    # SAMPLE_BUILD tasks have no is_secure_boot field → disabled.
    result = await get_build_info(50000)
    assert "Secure Boot: disabled" in result


@pytest.mark.asyncio
async def test_get_build_info_secure_boot_enabled(mock_client):
    sb_build = {
        **SAMPLE_BUILD,
        "tasks": [
            {**t, "is_secure_boot": True} for t in SAMPLE_BUILD["tasks"]
        ],
    }
    mock_client.get_build = AsyncMock(return_value=sb_build)
    result = await get_build_info(50000)
    assert "Secure Boot: enabled" in result
    # Uniform across all tasks → no per-arch qualifier.
    assert "Secure Boot: enabled\n" in result + "\n"


@pytest.mark.asyncio
async def test_get_build_info_secure_boot_mixed(mock_client):
    tasks = [dict(t) for t in SAMPLE_BUILD["tasks"]]
    tasks[0]["is_secure_boot"] = True  # x86_64 only
    mixed_build = {**SAMPLE_BUILD, "tasks": tasks}
    mock_client.get_build = AsyncMock(return_value=mixed_build)
    result = await get_build_info(50000)
    assert "Secure Boot: enabled (x86_64)" in result


@pytest.mark.asyncio
async def test_get_build_info_shows_global_mock_options(mock_client):
    build = {
        **SAMPLE_BUILD,
        "mock_options": {"definitions": {"test": "1"}},
    }
    mock_client.get_build = AsyncMock(return_value=build)
    result = await get_build_info(50000)
    assert "Global Mock Options: definitions: test=1" in result


@pytest.mark.asyncio
async def test_get_build_info_no_global_mock_options(mock_client):
    # SAMPLE_BUILD has mock_options=None → no line at all.
    result = await get_build_info(50000)
    assert "Global Mock Options:" not in result


@pytest.mark.asyncio
async def test_get_build_info_shows_project_mock_options_uniform(mock_client):
    tasks = [
        {**t, "mock_options": {"definitions": {"test222": "22"}}}
        for t in SAMPLE_BUILD["tasks"]
    ]
    build = {**SAMPLE_BUILD, "tasks": tasks}
    mock_client.get_build = AsyncMock(return_value=build)
    result = await get_build_info(50000)
    # Uniform across all tasks → single build-level line, no per-task noise.
    assert "Project Mock Options: definitions: test222=22" in result
    assert "mock=[" not in result


@pytest.mark.asyncio
async def test_get_build_info_project_mock_options_per_task_when_mixed(mock_client):
    tasks = [dict(t) for t in SAMPLE_BUILD["tasks"]]
    tasks[0]["mock_options"] = {"definitions": {"a": "1"}}
    tasks[1]["mock_options"] = {"definitions": {"b": "2"}}
    # tasks[2] has no mock options at all.
    build = {**SAMPLE_BUILD, "tasks": tasks}
    mock_client.get_build = AsyncMock(return_value=build)
    result = await get_build_info(50000)
    # Not uniform → annotate per task, no consolidated build-level line.
    assert "Project Mock Options:" not in result
    assert "mock=[definitions: a=1]" in result
    assert "mock=[definitions: b=2]" in result


@pytest.mark.asyncio
async def test_get_build_info_mock_options_formats_lists(mock_client):
    build = {
        **SAMPLE_BUILD,
        "mock_options": {
            "definitions": {"dist": ".el9"},
            "yum_exclude": ["foo", "bar"],
            "with": ["python3"],
            "module_enable": [],
        },
    }
    mock_client.get_build = AsyncMock(return_value=build)
    result = await get_build_info(50000)
    assert "definitions: dist=.el9" in result
    assert "yum_exclude: foo, bar" in result
    assert "with: python3" in result
    # Empty values are dropped, not shown as "module_enable: ".
    assert "module_enable" not in result


# ── get_failed_tasks ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_failed_tasks_tool(mock_client):
    result = await get_failed_tasks(50000)
    assert "2 failed task(s)" in result
    assert "300002" in result
    assert "300003" in result
    assert "300001" not in result or "completed" not in result.split("300001")[0]


@pytest.mark.asyncio
async def test_get_failed_tasks_marks_key_logs(mock_client):
    result = await get_failed_tasks(50000)
    assert "mock_build.300002.222.log ★" in result
    assert "mock_stderr.300002.222.log ★" in result
    assert "mock_root.300002.222.log ★" in result


@pytest.mark.asyncio
async def test_get_failed_tasks_shows_no_logs(mock_client):
    result = await get_failed_tasks(50000)
    assert "(no logs available)" in result


@pytest.mark.asyncio
async def test_get_failed_tasks_none_failed(mock_client):
    no_fail_build = {**SAMPLE_BUILD, "tasks": [SAMPLE_BUILD["tasks"][0]]}
    mock_client.get_build = AsyncMock(return_value=no_fail_build)
    result = await get_failed_tasks(50000)
    assert "no failed tasks" in result


# ── list_build_logs ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_build_logs_tool(mock_client):
    result = await list_build_logs(50000)
    assert "5 log file(s)" in result
    assert "mock_build.300002.222.log ★" in result
    assert "mock.300002.222.cfg" in result


@pytest.mark.asyncio
async def test_list_build_logs_empty(mock_client):
    mock_client.list_build_logs = AsyncMock(return_value=[])
    result = await list_build_logs(50000)
    assert "No logs found" in result


# ── download_log ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_download_log_tool(mock_client, tmp_path):
    fake_path = tmp_path / "mock_build.log"
    fake_path.write_text("line1\nline2\nline3\n")
    mock_client.download_log = AsyncMock(return_value=fake_path)
    result = await download_log(50000, "mock_build.log")
    assert "Downloaded:" in result
    assert "Total lines:" in result
    assert "read_log_tail" in result


# ── read_log_tail ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_log_tail_tool(mock_client):
    result = await read_log_tail(50000, "mock_build.log", 3000)
    assert "lines 4990-5000 of 5,000" in result
    assert "error: fail" in result


@pytest.mark.asyncio
async def test_read_log_tail_default_lines(mock_client):
    await read_log_tail(50000, "mock_build.log")
    mock_client.read_log_tail.assert_called_once_with(
        50000, "mock_build.log", 3000, LOG_MAX_LINE_CHARS, None,
        LOG_MAX_RESULT_CHARS,
    )


# ── read_log_range ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_log_range_tool(mock_client):
    result = await read_log_range(50000, "mock_build.log", 100, 102)
    assert "lines 100-102 of 5,000" in result
    assert "line 100" in result


# ── read_log_tail paging footer ───────────────────────────────────────

@pytest.mark.asyncio
async def test_read_log_tail_prints_the_call_for_the_page_above(mock_client):
    """The footer spells out the next call, so paging needs no arithmetic."""
    result = await read_log_tail(50000, "mock_build.log")
    assert (
        '↑ earlier: read_log_tail(50000, "mock_build.log", before_line=4990)'
        in result
    )


@pytest.mark.asyncio
async def test_read_log_tail_footer_uses_the_line_actually_reached(mock_client):
    """A page cut short by the budget resumes where it stopped, not where asked."""
    mock_client.read_log_tail = MagicMock(
        return_value=("cut short", 5000, 4997, 5000)
    )
    result = await read_log_tail(50000, "mock_build.log", 3000)
    assert "before_line=4997" in result


@pytest.mark.asyncio
async def test_read_log_tail_says_when_it_reached_the_top(mock_client):
    mock_client.read_log_tail = MagicMock(return_value=("all of it", 42, 1, 42))
    result = await read_log_tail(50000, "mock_root.log")
    assert "↑ start of log." in result
    assert "before_line" not in result


@pytest.mark.asyncio
async def test_read_log_tail_empty_page_is_explained(mock_client):
    mock_client.read_log_tail = MagicMock(return_value=("", 936, 0, 0))
    result = await read_log_tail(50000, "mock_build.log", before_line=1)
    assert "nothing before line 1" in result
    assert "already the start of the log" in result


@pytest.mark.asyncio
async def test_read_log_range_reports_the_budget_cut(mock_client):
    mock_client.read_log_range = MagicMock(return_value=("a\nb", 936, 510))
    result = await read_log_range(50000, "mock_build.log", 1, 936)
    assert "size budget reached at line 510" in result
    assert 'read_log_range(50000, "mock_build.log", 511, 936)' in result


@pytest.mark.asyncio
async def test_read_log_range_no_cut_no_footer(mock_client):
    mock_client.read_log_range = MagicMock(return_value=("a\nb", 936, 102))
    result = await read_log_range(50000, "mock_build.log", 100, 102)
    assert "size budget" not in result


# ── search_log ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_log_tool_marks_matches(mock_client):
    result = await search_log(50000, "mock_build.log")
    assert ">>> 827 | foo.c:12:5: error: boom" in result
    assert "    826 | foo.c: In function 'bar':" in result
    assert "1 match(es)" in result
    assert "936 lines" in result
    assert "3 boilerplate context line(s) omitted" in result


@pytest.mark.asyncio
async def test_search_log_defaults_to_the_failure_signatures(mock_client):
    await search_log(50000, "mock_build.log")
    pattern = mock_client.search_log.call_args.args[2]
    assert pattern == LOG_ERROR_PATTERN
    assert "default build-failure signatures" in await search_log(
        50000, "mock_build.log"
    )


@pytest.mark.asyncio
async def test_search_log_passes_an_explicit_pattern_through(mock_client):
    result = await search_log(50000, "mock_build.log", "Hunk #1 FAILED")
    assert mock_client.search_log.call_args.args[2] == "Hunk #1 FAILED"
    assert "Hunk #1 FAILED" in result.splitlines()[0]


@pytest.mark.asyncio
async def test_search_log_reports_the_cap(mock_client):
    mock_client.search_log = MagicMock(
        return_value=([[(1, "error: a", True)]], 50, 900, 0)
    )
    result = await search_log(50000, "mock_build.log")
    assert "50 match(es), showing the first 1" in result


@pytest.mark.asyncio
async def test_search_log_no_matches_suggests_a_next_step(mock_client):
    mock_client.search_log = MagicMock(return_value=([], 0, 1171, 0))
    result = await search_log(50000, "mock_root.log")
    assert "no matches in 1,171 lines" in result
    assert "read_log_tail" in result


@pytest.mark.asyncio
async def test_search_log_rejects_a_bad_regex(mock_client):
    mock_client.search_log = MagicMock(side_effect=re.error("bad group"))
    result = await search_log(50000, "mock_build.log", "error:[")
    assert result.startswith("Error: invalid regex")
    assert "bad group" in result


@pytest.mark.asyncio
async def test_search_log_error_no_stack_trace(mock_client):
    mock_client.search_log = MagicMock(
        side_effect=ValueError("Invalid log filename: '../x'")
    )
    result = await search_log(50000, "../x")
    assert "Error during searching log ../x" in result
    assert "Traceback" not in result


# ── search_builds ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_builds_tool(mock_client):
    result = await search_builds(page=1)
    assert "page 1" in result
    assert "#50000" in result
    assert "glibc" in result


@pytest.mark.asyncio
async def test_search_builds_shows_failed_count(mock_client):
    result = await search_builds()
    assert "2 failed" in result


def _multi_pkg_build() -> dict:
    """A build whose matching package sorts LAST — the truncation trap."""
    def task(pkg: str, nvr: str) -> dict:
        return {
            "id": 1,
            "status": 2,
            "arch": "x86_64",
            "ref": {
                "url": f"https://git.almalinux.org/rpms/{pkg}.git",
                "git_ref": f"imports/c10s/{nvr}",
            },
            "artifacts": [],
            "platform": {"id": 1, "name": "AlmaLinux-Kitten-10"},
            "test_tasks": [],
        }

    return {
        "id": 76538,
        "created_at": "2026-08-18T15:36:03",
        "finished_at": "2026-08-18T18:44:51",
        "owner": {"id": 1, "username": "builder", "email": "b@example.com"},
        "released": True,
        "tasks": [
            task("dovecot", "dovecot-2.3.21-21.el10"),
            task("gnome-control-center", "gnome-control-center-47.7-14.el10"),
            task("grubby", "grubby-8.40-84.el10.alma.1"),
            task("openssh", "openssh-9.9p1-28.el10.alma.1"),
            task("mingw-glib2", "mingw-glib2-2.89.2-1.el10"),
        ],
        "sign_tasks": [],
        "linked_builds": [],
        "mock_options": None,
        "platform_flavors": [],
    }


@pytest.mark.asyncio
async def test_search_builds_shows_package_versions(mock_client):
    mock_client.search_builds = AsyncMock(
        return_value={"builds": [_multi_pkg_build()], "total_builds": 1}
    )
    result = await search_builds()
    assert "dovecot-2.3.21-21.el10" in result
    assert "[released]" in result


@pytest.mark.asyncio
async def test_search_builds_never_hides_the_matched_package(mock_client):
    """The package the filter matched must be shown, not truncated away."""
    mock_client.search_builds = AsyncMock(
        return_value={"builds": [_multi_pkg_build()], "total_builds": 1}
    )
    result = await search_builds(project="mingw-glib2")
    assert "match: mingw-glib2-2.89.2-1.el10" in result
    # …and it is not the one swallowed by the "(+N more)" summary.
    assert "(+1 more)" in result


@pytest.mark.asyncio
async def test_search_builds_branch_ref_has_no_fake_version(mock_client):
    """A plain-branch git_ref (c9s) must not be printed as a version."""
    result = await search_builds()
    assert "glibc" in result
    assert "glibc-c9s" not in result


# ── get_sign_keys ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_sign_keys_tool(mock_client):
    result = await get_sign_keys()
    assert "id=4" in result
    assert "AL9-key" in result
    assert "ABC123" in result
    assert "Main key" in result


@pytest.mark.asyncio
async def test_get_sign_keys_empty(mock_client):
    mock_client.get_sign_keys = AsyncMock(return_value=[])
    result = await get_sign_keys()
    assert "No sign keys available" in result


@pytest.mark.asyncio
async def test_get_sign_keys_auth_error(mock_client):
    mock_client.get_sign_keys = AsyncMock(side_effect=PermissionError("no token"))
    result = await get_sign_keys()
    assert "Auth error" in result


# ── create_build ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_build_tool(mock_client):
    result = await create_build(
        packages=["bash"],
        platform="AlmaLinux-9",
        branch="c9s",
    )
    assert "Build created successfully" in result
    assert "99999" in result
    assert "build.almalinux.org/build/99999" in result


@pytest.mark.asyncio
async def test_create_build_from_tag_tool(mock_client):
    result = await create_build(
        packages=["bash imports/c9s/bash-5.1-1.el9"],
        platform="AlmaLinux-9",
        from_tag=True,
    )
    assert "Build created successfully" in result
    call_args = mock_client.create_build.call_args[1]
    assert call_args["from_tag"] is True
    assert call_args["packages"] == [{"bash": "imports/c9s/bash-5.1-1.el9"}]


@pytest.mark.asyncio
async def test_create_build_auth_error(mock_client):
    mock_client.create_build = AsyncMock(side_effect=PermissionError("no jwt"))
    result = await create_build(packages=["bash"], platform="AlmaLinux-9", branch="c9s")
    assert "Auth error" in result


@pytest.mark.asyncio
async def test_create_build_validation_error(mock_client):
    mock_client.create_build = AsyncMock(side_effect=ValueError("bad arch"))
    result = await create_build(packages=["bash"], platform="AlmaLinux-9", branch="c9s")
    assert result.startswith("Error")
    assert "bad arch" in result


# ── create_build: git_urls ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_build_git_urls(mock_client):
    result = await create_build(
        git_urls=["https://github.com/ykohut/leapp-data.git"],
        platform="AlmaLinux-10",
        branch="devel-ng-0.23.0",
    )
    assert "Build created successfully" in result
    call_args = mock_client.create_build.call_args[1]
    assert call_args["packages"] == [{"https://github.com/ykohut/leapp-data.git": "None"}]


@pytest.mark.asyncio
async def test_create_build_git_urls_from_tag(mock_client):
    result = await create_build(
        git_urls=["https://github.com/ykohut/leapp-data.git v0.23.0"],
        platform="AlmaLinux-10",
        from_tag=True,
    )
    assert "Build created successfully" in result
    call_args = mock_client.create_build.call_args[1]
    assert call_args["packages"] == [
        {"https://github.com/ykohut/leapp-data.git": "v0.23.0"}
    ]


@pytest.mark.asyncio
async def test_create_build_git_urls_with_packages(mock_client):
    result = await create_build(
        packages=["bash"],
        git_urls=["https://github.com/ykohut/leapp-data.git"],
        platform="AlmaLinux-10",
        branch="c10s",
    )
    assert "Build created successfully" in result
    call_args = mock_client.create_build.call_args[1]
    pkgs = call_args["packages"]
    assert {"bash": "None"} in pkgs
    assert {"https://github.com/ykohut/leapp-data.git": "None"} in pkgs


@pytest.mark.asyncio
async def test_create_build_git_urls_no_packages_no_urls():
    result = await create_build(
        platform="AlmaLinux-10",
        branch="c10s",
    )
    assert "Error" in result
    assert "packages or git_urls" in result


@pytest.mark.asyncio
async def test_create_build_git_urls_with_from_srpm():
    result = await create_build(
        git_urls=["https://github.com/ykohut/leapp-data.git"],
        platform="AlmaLinux-10",
        from_srpm=True,
    )
    assert "Error" in result
    assert "from_srpm" in result


@pytest.mark.asyncio
async def test_create_build_git_urls_from_tag_missing_tag():
    result = await create_build(
        git_urls=["https://github.com/ykohut/leapp-data.git"],
        platform="AlmaLinux-10",
        from_tag=True,
    )
    assert "Error" in result
    assert "url tag" in result


# ── create_build: platforms (multi-platform) ──────────────────────────

@pytest.mark.asyncio
async def test_create_build_platforms_list(mock_client):
    """Using platforms= list instead of platform= string."""
    result = await create_build(
        packages=["bash"],
        platforms=["AlmaLinux-9", "AlmaLinux-10"],
        branch="c9s",
    )
    assert "Build created successfully" in result
    call_args = mock_client.create_build.call_args[1]
    assert call_args["platforms"] == ["AlmaLinux-9", "AlmaLinux-10"]


@pytest.mark.asyncio
async def test_create_build_platform_and_platforms_merged(mock_client):
    """platform + platforms are merged (deduped) before passing to client."""
    result = await create_build(
        packages=["bash"],
        platform="AlmaLinux-8",
        platforms=["AlmaLinux-9", "AlmaLinux-8"],
        branch="c9s",
    )
    assert "Build created successfully" in result
    call_args = mock_client.create_build.call_args[1]
    assert call_args["platforms"] == ["AlmaLinux-8", "AlmaLinux-9"]


@pytest.mark.asyncio
async def test_create_build_no_platform_no_platforms():
    """Omitting both platform and platforms returns an error."""
    result = await create_build(
        packages=["bash"],
        branch="c9s",
    )
    assert "Error" in result
    assert "platform or platforms" in result


# ── create_build: skip_tests ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_build_skip_tests(mock_client):
    result = await create_build(
        packages=["bash"],
        platform="AlmaLinux-9",
        branch="c9s",
        skip_tests=True,
    )
    assert "Build created successfully" in result
    assert "__spec_check_template" in result
    call_args = mock_client.create_build.call_args[1]
    assert call_args["definitions"] == {"__spec_check_template": "exit 0;"}


@pytest.mark.asyncio
async def test_create_build_skip_tests_merges_definitions(mock_client):
    result = await create_build(
        packages=["bash"],
        platform="AlmaLinux-9",
        branch="c9s",
        skip_tests=True,
        definitions='{"dist": ".el9"}',
    )
    assert "Build created successfully" in result
    call_args = mock_client.create_build.call_args[1]
    assert call_args["definitions"] == {
        "dist": ".el9",
        "__spec_check_template": "exit 0;",
    }


@pytest.mark.asyncio
async def test_create_build_definitions_dict(mock_client):
    """MCP path: pass definitions as a dict (no JSON string juggling)."""
    result = await create_build(
        packages=["bash"],
        platform="AlmaLinux-9",
        branch="c9s",
        definitions={"dist_name": "almalinux"},
    )
    assert "Build created successfully" in result
    call_args = mock_client.create_build.call_args[1]
    assert call_args["definitions"] == {"dist_name": "almalinux"}


@pytest.mark.asyncio
async def test_create_build_skip_tests_merges_definitions_dict(mock_client):
    """skip_tests merging works when definitions is supplied as a dict."""
    result = await create_build(
        packages=["bash"],
        platform="AlmaLinux-9",
        branch="c9s",
        skip_tests=True,
        definitions={"dist": ".el9"},
    )
    assert "Build created successfully" in result
    call_args = mock_client.create_build.call_args[1]
    assert call_args["definitions"] == {
        "dist": ".el9",
        "__spec_check_template": "exit 0;",
    }


# ── create_build: EPEL params (AI passes explicitly) ─────────────────

EPEL_SRPM = "https://dl.fedoraproject.org/pub/epel/10/Everything/source/tree/Packages/p/pkg-1.0-1.el10.src.rpm"


@pytest.mark.asyncio
async def test_create_build_epel_no_auto_detection(mock_client):
    """EPEL URLs should NOT trigger automatic arch/flavor changes."""
    await create_build(
        packages=[EPEL_SRPM],
        platform="almalinux-10",
        from_srpm=True,
    )
    call_args = mock_client.create_build.call_args[1]
    assert call_args["arch_list"] is None
    assert call_args["additional_flavors"] is None


@pytest.mark.asyncio
async def test_create_build_epel_flavors_passed_explicitly(mock_client):
    """AI passes EPEL flavors explicitly after consulting the user."""
    result = await create_build(
        packages=[EPEL_SRPM],
        platform="almalinux-10",
        from_srpm=True,
        flavors=["EPEL-10", "EPEL-10_altarch"],
        arch_list=["x86_64_v2"],
    )
    assert "Build created successfully" in result
    call_args = mock_client.create_build.call_args[1]
    assert call_args["additional_flavors"] == ["EPEL-10", "EPEL-10_altarch"]
    assert call_args["arch_list"] == ["x86_64_v2"]


# ── create_build: add_epel_dist ──────────────────────────────────────

@pytest.mark.asyncio
async def test_create_build_add_epel_dist_from_srpm(mock_client):
    result = await create_build(
        packages=[EPEL_SRPM],
        platform="almalinux-10",
        from_srpm=True,
        add_epel_dist=True,
    )
    assert "add-epel-dist" in result
    call_args = mock_client.create_build.call_args[1]
    assert call_args["add_epel_dist"] is True


@pytest.mark.asyncio
async def test_create_build_add_epel_dist_from_tag(mock_client):
    result = await create_build(
        packages=["bash imports/c9s/bash-5.1-1.el9"],
        platform="AlmaLinux-9",
        from_tag=True,
        add_epel_dist=True,
    )
    assert "add-epel-dist" in result
    call_args = mock_client.create_build.call_args[1]
    assert call_args["add_epel_dist"] is True


@pytest.mark.asyncio
async def test_create_build_add_epel_dist_requires_tag_or_srpm(mock_client):
    result = await create_build(
        packages=["bash"],
        platform="AlmaLinux-9",
        branch="c9s",
        add_epel_dist=True,
    )
    assert "Error" in result
    assert "from_tag or from_srpm" in result
    mock_client.create_build.assert_not_called()


# ── create_build: independent_tasks ──────────────────────────────────

@pytest.mark.asyncio
async def test_create_build_independent_tasks(mock_client):
    """independent_tasks=True is forwarded to the client and noted in output."""
    result = await create_build(
        packages=["bash"],
        platform="AlmaLinux-9",
        branch="c9s",
        independent_tasks=True,
    )
    assert "Build created successfully" in result
    assert "independent_tasks" in result
    call_args = mock_client.create_build.call_args[1]
    assert call_args["independent_tasks"] is True


@pytest.mark.asyncio
async def test_create_build_independent_tasks_default(mock_client):
    """When omitted, independent_tasks defaults to False and is not advertised."""
    result = await create_build(
        packages=["bash"],
        platform="AlmaLinux-9",
        branch="c9s",
    )
    assert "Build created successfully" in result
    assert "independent_tasks" not in result
    call_args = mock_client.create_build.call_args[1]
    assert call_args["independent_tasks"] is False


# ── sign_build ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sign_build_tool(mock_client):
    result = await sign_build(50000)
    assert "Sign task created" in result
    assert "888" in result


@pytest.mark.asyncio
async def test_sign_build_custom_key(mock_client):
    await sign_build(50000, sign_key_id=7)
    mock_client.sign_build.assert_called_once_with(50000, 7)


@pytest.mark.asyncio
async def test_sign_build_auth_error(mock_client):
    mock_client.sign_build = AsyncMock(side_effect=PermissionError("no jwt"))
    result = await sign_build(50000)
    assert "Auth error" in result


# ── delete_build ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_build_blocked(mock_client):
    result = await delete_build(50000)
    assert "blocked" in result.lower()


# ── graceful errors in read-only tools ────────────────────────────────

@pytest.mark.asyncio
async def test_get_build_info_not_found(mock_client):
    mock_client.get_build = AsyncMock(side_effect=_http_status_error(404))
    result = await get_build_info(999999)
    assert result.startswith("Error")
    assert "not found" in result.lower()
    assert "999999" in result


@pytest.mark.asyncio
async def test_get_failed_tasks_not_found(mock_client):
    mock_client.get_build = AsyncMock(side_effect=_http_status_error(404))
    result = await get_failed_tasks(999999)
    assert result.startswith("Error")
    assert "999999" in result


@pytest.mark.asyncio
async def test_list_build_logs_not_found(mock_client):
    mock_client.list_build_logs = AsyncMock(side_effect=_http_status_error(404))
    result = await list_build_logs(999999)
    assert result.startswith("Error")
    assert "999999" in result


@pytest.mark.asyncio
async def test_search_builds_network_error(mock_client):
    mock_client.search_builds = AsyncMock(side_effect=httpx.ConnectError("boom"))
    result = await search_builds()
    assert result.startswith("Error")
    assert "reach ALBS" in result


@pytest.mark.asyncio
async def test_read_log_tail_error_no_stack_trace(mock_client):
    mock_client.read_log_tail = MagicMock(
        side_effect=ValueError("Invalid log filename: '../x'")
    )
    result = await read_log_tail(50000, "../x", 10)
    assert result.startswith("Error")
    mock_client.download_log.assert_not_called()
    # The message must be a single clean line, not a multi-line stack trace.
    assert "Traceback" not in result
    assert "\n" not in result


# ── auto-download in read_log_* ───────────────────────────────────────

@pytest.mark.asyncio
async def test_read_log_tail_auto_downloads(mock_client):
    """When the log is not on disk, read_log_tail downloads it, then reads."""
    mock_client.read_log_tail = MagicMock(side_effect=[
        FileNotFoundError("Log not downloaded yet"),
        ("error: boom", 5000, 4990, 5000),
    ])
    result = await read_log_tail(50000, "mock_build.log", 3000)
    mock_client.download_log.assert_awaited_once_with(50000, "mock_build.log")
    assert "error: boom" in result
    assert "lines 4990-5000 of 5,000" in result


@pytest.mark.asyncio
async def test_read_log_tail_no_download_when_present(mock_client):
    """If the log is already on disk, no download happens."""
    result = await read_log_tail(50000, "mock_build.log", 3000)
    mock_client.download_log.assert_not_called()
    assert "error: fail" in result


@pytest.mark.asyncio
async def test_read_log_range_auto_downloads(mock_client):
    mock_client.read_log_range = MagicMock(side_effect=[
        FileNotFoundError("Log not downloaded yet"),
        ("line 100\nline 101", 5000, 102),
    ])
    result = await read_log_range(50000, "mock_build.log", 100, 102)
    mock_client.download_log.assert_awaited_once_with(50000, "mock_build.log")
    assert "line 100" in result


# ── get_sign_task_status ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_sign_task_status_tool(mock_client):
    mock_client.get_sign_tasks = AsyncMock(return_value=[
        {"id": 1, "build_id": 50000, "status": 3, "sign_key": {"id": 4}},
        {"id": 2, "build_id": 50000, "status": 4, "sign_key_id": 9,
         "error_message": "boom"},
    ])
    result = await get_sign_task_status(50000)
    assert "sign_task_id=1" in result
    assert "completed" in result
    assert "sign_key_id=4" in result
    assert "sign_task_id=2" in result
    assert "failed" in result
    assert "sign_key_id=9" in result
    assert "boom" in result


@pytest.mark.asyncio
async def test_get_sign_task_status_none(mock_client):
    mock_client.get_sign_tasks = AsyncMock(return_value=[])
    result = await get_sign_task_status(50000)
    assert "no sign tasks" in result.lower()


@pytest.mark.asyncio
async def test_get_sign_task_status_error(mock_client):
    mock_client.get_sign_tasks = AsyncMock(side_effect=_http_status_error(404))
    result = await get_sign_task_status(999999)
    assert result.startswith("Error")
    assert "999999" in result


# ── investigate_build (prompt) ────────────────────────────────────────

def test_investigate_build_prompt_content():
    """The prompt interpolates the build id and seeds the investigation order."""
    text = investigate_build("52679")
    assert "52679" in text
    assert "get_build_info(52679)" in text
    assert "get_failed_tasks(52679)" in text
    # Investigation ordering hints must survive in the seeded prompt.
    assert text.index("mock_root") < text.index("mock_stderr") < text.index("mock_build")
    # Search first, then read around what it found.
    assert text.index("search_log") < text.index("read_log_tail")
    # And never accept the make wrapper error as the root cause.
    assert "symptom" in text


@pytest.mark.asyncio
async def test_investigate_build_prompt_registered():
    """The prompt is registered on the MCP server with a build_id argument."""
    prompts = await mcp.list_prompts()
    by_name = {p.name: p for p in prompts}
    assert "investigate_build" in by_name
    arg_names = [a.name for a in (by_name["investigate_build"].arguments or [])]
    assert arg_names == ["build_id"]


@pytest.mark.asyncio
async def test_investigate_build_prompt_renders_via_server():
    """Rendering through get_prompt coerces the string arg into the message."""
    result = await mcp.get_prompt("investigate_build", {"build_id": "12345"})
    assert len(result.messages) == 1
    msg = result.messages[0]
    assert msg.role == "user"
    assert "12345" in msg.content.text


# ── get_products ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_products_tool(mock_client):
    result = await get_products()
    assert "AlmaLinux" in result
    assert "epel-al" in result
    assert "official" in result
    assert "community" in result
    assert "id=1" in result


@pytest.mark.asyncio
async def test_get_products_empty(mock_client):
    mock_client.get_products = AsyncMock(return_value=[])
    result = await get_products()
    assert "No products" in result


@pytest.mark.asyncio
async def test_get_products_error(mock_client):
    mock_client.get_products = AsyncMock(side_effect=_http_status_error(500))
    result = await get_products()
    assert result.startswith("Error")


# ── create_release_plan ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_release_plan_tool(mock_client):
    result = await create_release_plan(50000, "AlmaLinux-9", "AlmaLinux")
    assert "Release plan #4242 created" in result
    assert "scheduled" in result
    assert "glibc-2.34-1.el9" in result
    assert "almalinux-9-baseos" in result
    # The plan must announce that nothing was published.
    assert "only a release PLAN" in result
    # Completed task (status 2) collected, failed ones (status 3) skipped.
    payload = mock_client.create_release.call_args[1]
    assert payload["build_task_ids"] == [300001]
    assert payload["platform_id"] == 2
    assert payload["product_id"] == 1
    assert payload["build_ids"] == [50000]


@pytest.mark.asyncio
async def test_create_release_plan_unknown_platform(mock_client):
    result = await create_release_plan(50000, "Nonexistent", "AlmaLinux")
    assert result.startswith("Error")
    assert "unknown platform" in result
    mock_client.create_release.assert_not_called()


@pytest.mark.asyncio
async def test_create_release_plan_unknown_product(mock_client):
    result = await create_release_plan(50000, "AlmaLinux-9", "no-such-product")
    assert result.startswith("Error")
    assert "unknown product" in result
    mock_client.create_release.assert_not_called()


@pytest.mark.asyncio
async def test_create_release_plan_no_completed_tasks(mock_client):
    # A build where every task failed → nothing to release.
    mock_client.get_build = AsyncMock(return_value={
        "tasks": [{"id": 1, "status": 3, "ref": {"url": "u/x"}}],
    })
    result = await create_release_plan(50000, "AlmaLinux-9", "AlmaLinux")
    assert result.startswith("Error")
    assert "no completed tasks" in result
    mock_client.create_release.assert_not_called()


@pytest.mark.asyncio
async def test_create_release_plan_extra_builds(mock_client):
    await create_release_plan(
        50000, "AlmaLinux-9", "AlmaLinux", build_ids=[50001, 50000]
    )
    payload = mock_client.create_release.call_args[1]
    # primary first, extras de-duplicated (50000 already present)
    assert payload["build_ids"] == [50000, 50001]


@pytest.mark.asyncio
async def test_create_release_plan_auth_error(mock_client):
    mock_client.create_release = AsyncMock(
        side_effect=PermissionError("JWT token required")
    )
    result = await create_release_plan(50000, "AlmaLinux-9", "AlmaLinux")
    assert result.startswith("Auth error")


@pytest.mark.asyncio
async def test_create_release_plan_requires_token_fails_fast(mock_client):
    """No token → immediate auth error, before any API read calls."""
    mock_client.jwt_token = None
    result = await create_release_plan(50000, "AlmaLinux-9", "AlmaLinux")
    assert result.startswith("Auth error")
    assert "JWT token" in result
    # Must not have done any work: no platform/product/build lookups, no POST.
    mock_client.get_platform_ids.assert_not_called()
    mock_client.get_product_ids.assert_not_called()
    mock_client.get_build.assert_not_called()
    mock_client.create_release.assert_not_called()


@pytest.mark.asyncio
async def test_create_release_plan_refetches_when_plan_missing(mock_client):
    """If /new/ returns only an id, the full release is fetched for display."""
    mock_client.create_release = AsyncMock(return_value={"id": 4242})
    result = await create_release_plan(50000, "AlmaLinux-9", "AlmaLinux")
    mock_client.get_release.assert_awaited_once_with(4242)
    assert "glibc-2.34-1.el9" in result


# ── get_release_plan ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_release_plan_tool(mock_client):
    result = await get_release_plan(4242)
    assert "Release plan #4242" in result
    assert "completed" in result
    assert "glibc-2.34-1.el9" in result
    # Viewing an existing plan must NOT show the "created" publish note.
    assert "only a release PLAN" not in result


@pytest.mark.asyncio
async def test_get_release_plan_not_found(mock_client):
    mock_client.get_release = AsyncMock(side_effect=_http_status_error(404))
    result = await get_release_plan(999999)
    assert result.startswith("Error")
    assert "999999" in result


# ── commit_release (blocked) ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_commit_release_blocked(mock_client):
    result = await commit_release(4242)
    assert "blocked" in result.lower()
    assert "only creates release plans" in result


# ── release_plan (prompt) ─────────────────────────────────────────────

def test_release_plan_prompt_content():
    text = release_plan("52679")
    assert "52679" in text
    assert "get_build_info(52679)" in text
    assert "get_products()" in text
    assert "create_release_plan(52679" in text
    # Must steer the agent away from the actual release.
    assert "commit_release" in text
    assert "only create the plan" in text.lower()


@pytest.mark.asyncio
async def test_release_plan_prompt_registered():
    prompts = await mcp.list_prompts()
    by_name = {p.name: p for p in prompts}
    assert "release_plan" in by_name
    arg_names = [a.name for a in (by_name["release_plan"].arguments or [])]
    assert arg_names == ["build_id"]


@pytest.mark.asyncio
async def test_release_plan_prompt_renders_via_server():
    result = await mcp.get_prompt("release_plan", {"build_id": "52679"})
    assert len(result.messages) == 1
    msg = result.messages[0]
    assert msg.role == "user"
    assert "52679" in msg.content.text


# ═══════════════════════════════════════════════════════════════════════
#  Errata
# ═══════════════════════════════════════════════════════════════════════

AL8 = (1, "AlmaLinux-8")
AL9 = (9, "AlmaLinux-9")
AL10 = (50, "AlmaLinux-10")


def _rpm(name, version, release, arch, epoch="0", meta=True):
    return {
        "name": f"{name}-{version}-{release}.{arch}.rpm",
        "type": "rpm",
        "meta": {
            "name": name, "epoch": epoch, "version": version,
            "release": release, "arch": arch,
        } if meta else None,
    }


def _task(task_id, arch, repo, status=2, platform=AL8, artifacts=(),
          modules=(), git_ref="changed/a8/x"):
    return {
        "id": task_id, "arch": arch, "status": status,
        "platform": {"id": platform[0], "name": platform[1]},
        "ref": {
            "url": f"https://git.almalinux.org/rpms/{repo}.git",
            "git_ref": git_ref,
        },
        "rpm_modules": [{"name": n, "stream": s} for n, s in modules],
        "artifacts": list(artifacts),
    }


def _kernel_build(build_id, platform=AL8, arches=("x86_64", "aarch64"),
                  failed=(), with_kmod=True, kmod_status=2):
    """kernel (affected) + kmod-foo (not affected) for one platform.

    kernel-doc is noarch: built on every arch, metadata on the first copy
    only — the way ALBS reports it.
    """
    tasks = []
    for i, arch in enumerate(arches):
        status = 3 if arch in failed else 2
        artifacts = [] if status == 3 else [
            {"name": "kernel-5.14.0-1.el8.src.rpm", "type": "rpm", "meta": None},
            _rpm("kernel", "5.14.0", "1.el8", arch),
            _rpm("kernel-core", "5.14.0", "1.el8", arch),
            _rpm("kernel-debuginfo", "5.14.0", "1.el8", arch),
            _rpm("kernel-debugsource", "5.14.0", "1.el8", arch),
            _rpm("kernel-doc", "5.14.0", "1.el8", "noarch", meta=(i == 0)),
            {"name": "mock_build.1.log", "type": "build_log", "meta": None},
        ]
        tasks.append(
            _task(build_id * 10 + i, arch, "kernel", status, platform, artifacts)
        )
    if with_kmod:
        tasks.append(_task(build_id * 10 + 9, "x86_64", "kmod-foo", kmod_status,
                           platform, [
            {"name": "kmod-foo-1.0-1.el8.src.rpm", "type": "rpm", "meta": None},
            _rpm("kmod-foo", "1.0", "1.el8", "x86_64"),
        ] if kmod_status == 2 else []))
    return {"id": build_id, "tasks": tasks}


def _nodejs_module_build(build_id):
    modules = [("nodejs", "24"), ("nodejs-devel", "24")]
    release = "1.module_el8.10.0+4325+63556250.alma.1"
    return {"id": build_id, "tasks": [
        _task(build_id * 10, "src", "nodejs", 2, AL8, [
            {"name": f"nodejs-24.21.0-{release}.src.rpm", "type": "rpm",
             "meta": None},
        ], git_ref="a8-stream-24"),
        _task(build_id * 10 + 1, "x86_64", "nodejs", 2, AL8, [
            _rpm("nodejs", "24.21.0", release, "x86_64", epoch="1"),
            _rpm("nodejs-devel", "24.21.0", release, "x86_64", epoch="1"),
            _rpm("v8-13.6-devel", "13.6.233.17", release, "x86_64", epoch="3"),
        ], modules=modules, git_ref="a8-stream-24"),
        _task(build_id * 10 + 2, "i686", "nodejs", 4, AL8, [],
              modules=modules, git_ref="a8-stream-24"),
    ]}


ERRATA_BUILDS = {
    60001: _kernel_build(60001, AL8),
    60002: _kernel_build(60002, AL10, arches=("x86_64", "aarch64"),
                         failed=("aarch64",), with_kmod=False),
    60003: _kernel_build(60003, AL10, arches=("aarch64",), with_kmod=False),
    60004: _kernel_build(60004, AL9),
    60005: _nodejs_module_build(60005),
    60006: {"id": 60006, "tasks": [
        _task(600061, "x86_64", "kernel", 1, AL8),
    ]},
    60008: _kernel_build(60008, AL8, kmod_status=3),
    60009: _kernel_build(60009, AL8, arches=("x86_64",), failed=("x86_64",),
                         with_kmod=False),
    60010: _kernel_build(60010, AL9, kmod_status=5),
    60007: {"id": 60007, "tasks": [
        _task(600071, "x86_64", "kernel", 2, AL8, [
            _rpm("kernel", "5.14.0", "1.el8", "x86_64"),
            _rpm("kernel-doc", "5.14.0", "1.el8", "noarch", meta=False),
        ]),
    ]},
}

RH_CVE_KERNEL = {
    "name": "CVE-2026-31431",
    "threat_severity": "Important",
    "public_date": "2026-04-22T00:00:00Z",
    "bugzilla": {
        "description": "kernel: crypto: algif_aead - Revert to operating out-of-place",
        "id": "2460538",
        "url": "https://bugzilla.redhat.com/show_bug.cgi?id=2460538",
    },
    "cvss3": {
        "cvss3_scoring_vector": "CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H",
    },
    "cwe": "CWE-1288",
    "package_state": [
        {"package_name": "kernel-rt"},
        {"package_name": "rhel9/some-container"},
    ],
    "affected_release": [{"package": "kernel-0:6.12.0-124.55.1.el10_1"}],
}

RH_CVE_LOW = {
    "name": "CVE-2026-11111",
    "threat_severity": "Low",
    "public_date": "2026-01-01T00:00:00Z",
    "bugzilla": {"description": "kernel: minor thing", "id": "2000001",
                 "url": "https://bugzilla.redhat.com/show_bug.cgi?id=2000001"},
    "package_state": [{"package_name": "kernel"}],
}

RH_CVE_NODEJS = {
    "name": "CVE-2026-19534",
    "threat_severity": "Important",
    "public_date": "2026-09-04T17:10:05Z",
    "bugzilla": {"description": "undici: Denial of Service", "id": "2528759",
                 "url": "https://bugzilla.redhat.com/show_bug.cgi?id=2528759"},
    "package_state": [{"package_name": "nodejs:26/nodejs"}],
    "affected_release": [
        {"package": "nodejs:24-8100020260921144227.6d880403"},
    ],
}

RH_CSAF_KERNEL = {
    "document": {
        "tracking": {"id": "RHSA-2026:13566"},
        "title": "Red Hat Security Advisory: kernel security and bug fix update",
        "aggregate_severity": {"text": "Important"},
        "notes": [
            {"category": "summary",
             "text": "An update for kernel is now available."},
            {"category": "general",
             "text": "The kernel packages contain the Linux kernel.\n\n"
                     "Security Fix(es):\n\n"
                     "* kernel: nfsd: fix heap overflow on Red Hat Enterprise "
                     "Linux 10 (CVE-2026-31402)"},
        ],
    },
    "product_tree": {"branches": [{"branches": [
        {"product": {"product_identification_helper": {
            "purl": "pkg:rpm/redhat/kernel@6.12.0-124.55.1.el10_1?arch=src"}}},
        {"product": {"product_identification_helper": {
            "purl": "pkg:rpm/redhat/kernel@6.12.0-124.55.1.el10_1?arch=x86_64"}}},
    ]}]},
    "vulnerabilities": [
        {
            "cve": "CVE-2026-31402",
            "cwe": {"id": "CWE-787"},
            "release_date": "2026-04-03T00:00:00+00:00",
            "title": "kernel: nfsd: fix heap overflow",
            "ids": [{"system_name": "Red Hat Bugzilla ID", "text": "2454844"}],
            "references": [{
                "summary": "RHBZ#2454844",
                "url": "https://bugzilla.redhat.com/show_bug.cgi?id=2454844",
            }],
            "scores": [{"cvss_v3": {"vectorString": "CVSS:3.1/AV:L/AC:L"}}],
            "threats": [{"category": "impact", "details": "Important"}],
        },
        {
            "cve": "CVE-2026-23270",
            "release_date": "2026-03-18T00:00:00+00:00",
            "title": "kernel: act_ct use-after-free",
            "threats": [{"category": "impact", "details": "Moderate"}],
        },
    ],
}

def _earlier(errata_id, platform_id, title, description):
    return {"id": errata_id, "platform_id": platform_id,
            "original_title": title, "title": None, "description": None,
            "original_description": description}


# Newest first, as /errata/query/ returns them.
EARLIER_ADVISORIES = [
    # Bug fix advisory: no "Security Fix(es):" list, so no package text.
    _earlier("ALBA-2026:9001", 1, "Moderate: kernel bug fix update",
             "For detailed information see the AlmaLinux Release Notes."),
    # kernel-rt contains "kernel" but is a different package.
    _earlier("ALSA-2026:9000", 1, "Important: kernel-rt security update",
             "The kernel-rt packages provide the Real Time Linux Kernel.\n\n"
             "Security Fix(es):\n\n* kernel: x (CVE-2026-1)"),
    _earlier("ALSA-2026:8999", 1,
             "Important: kernel security, bug fix, and enhancement update",
             "The kernel packages contain the Linux kernel.  \n\n"
             "Security Fix(es):\n\n* kernel: y (CVE-2026-2)"),
    _earlier("ALSA-2026:8998", 9, "Important: kernel security update",
             "The AL9 kernel packages.\n\nSecurity Fix(es):\n\n* z"),
    # Another stream of the nodejs module.
    _earlier("ALSA-2026:8997", 1, "Important: nodejs:22 security update",
             "Node.js is a software development platform.\n\n"
             "Security Fix(es):\n\n* undici: z (CVE-2026-3)"),
]

ERRATA_RECORD = {
    "id": "ALSA-2026:A001",
    "platform_id": 1,
    "issued_date": "2026-05-01T00:00:00",
    "updated_date": "2026-05-01T00:00:00",
    "title": None,
    "original_title": "Important: kernel security update",
    "description": None,
    "original_description": "Security Fix(es):\n\n* kernel: thing (CVE-2026-31431)",
    "module": "",
    "severity": "Important",
    "release_status": "released",
    "references": [
        {"ref_type": "ErrataReferenceType.cve", "ref_id": "CVE-2026-31431"},
        {"ref_type": "ErrataReferenceType.bugzilla", "ref_id": "2460538"},
        {"ref_type": "ErrataReferenceType.self_ref", "ref_id": "ALSA-2026:A001"},
    ],
    "packages": [
        {"name": "kernel", "source_srpm": "kernel",
         "albs_packages": [{"build_id": 54743}]},
        {"name": "kernel-core", "source_srpm": "kernel",
         "albs_packages": [{"build_id": 54743}]},
    ],
}


@pytest.fixture
def errata_client(mock_client):
    cves = {
        d["name"]: d for d in (RH_CVE_KERNEL, RH_CVE_LOW, RH_CVE_NODEJS)
    }

    async def get_rh_cve(cve_id):
        if cve_id not in cves:
            raise _http_status_error(404)
        return cves[cve_id]

    mock_client.jwt_token = "token"
    mock_client.get_build = AsyncMock(side_effect=lambda b: ERRATA_BUILDS[b])
    mock_client.get_rh_cve = AsyncMock(side_effect=get_rh_cve)
    mock_client.get_rh_advisory = AsyncMock(return_value=RH_CSAF_KERNEL)
    mock_client.list_alma_errata_ids = AsyncMock(return_value=[
        f"ALSA-2026:A{i:03d}" for i in range(1, 11)
    ] + ["ALBA-2026:A004", "ALSA-2025:A042"])
    existing = []  # advisories already on ALBS, for the duplicate check

    async def query_errata(errata_id=None, errata_ids=None, platform_id=None,
                           page=1, title=None, cve_id=None):
        if cve_id:
            # Substring match, like the server's SQL LIKE.
            found = [
                r for r in existing
                if any(cve_id.lower() in ref["ref_id"].lower()
                       for ref in r["references"])
                and platform_id in (None, r["platform_id"])
            ]
            return {"records": found[(page - 1) * 10:page * 10],
                    "total_records": len(found)}
        if not title:
            return {"records": [], "total_records": 0}
        found = [
            r for r in EARLIER_ADVISORIES
            if title in r["original_title"]
            and platform_id in (None, r["platform_id"])
        ]
        return {"records": found[(page - 1) * 10:page * 10],
                "total_records": len(found)}

    mock_client.query_errata = AsyncMock(side_effect=query_errata)
    mock_client.existing_advisories = existing
    mock_client.create_errata = AsyncMock(return_value={"ok": True})
    mock_client.get_platform_ids = AsyncMock(return_value={
        "AlmaLinux-8": 1, "AlmaLinux-9": 9, "AlmaLinux-10": 50,
    })
    return mock_client


def _posted(client) -> list[dict]:
    return [c.args[0] for c in client.create_errata.call_args_list]


# ── preview_errata ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_preview_errata_cve(errata_client):
    result = await preview_errata(
        [60001], issued_date="2026-10-23", cves=["CVE-2026-31431"]
    )
    assert "NOTHING has been created" in result
    # Next free number of the year, shared by every platform and type.
    assert "ALSA-2026:A011  AlmaLinux-8  build #60001" in result
    assert "Title: kernel security update" in result
    assert '"Important: kernel security update"' in result
    assert "Module: none" in result
    assert "Source packages: kernel" in result
    assert "Left out (not affected): kmod-foo" in result
    # kernel + kernel-core on 2 arches + one noarch kernel-doc; no
    # src/debuginfo/debugsource, nothing from kmod-foo.
    assert "Packages: 5 RPM(s) (aarch64, noarch, x86_64)" in result
    assert "kmod-foo," not in result
    assert "CVE-2026-31431  Important  CVSS:3.1/AV:L" in result
    assert "CWE-1288" in result
    assert "bugzilla 2460538" in result
    assert (
        "* kernel: crypto: algif_aead - Revert to operating out-of-place "
        "(CVE-2026-31431)" in result
    )
    errata_client.create_errata.assert_not_called()
    errata_client.list_alma_errata_ids.assert_awaited_once_with(2026)


@pytest.mark.asyncio
async def test_preview_errata_orders_platforms_and_numbers_sequentially(
    errata_client,
):
    result = await preview_errata(
        [60004, "https://build.almalinux.org/build/60001/"], issued_date="2026-10-23", cves=["CVE-2026-31431"],
    )
    # AlmaLinux-8 first, like ALSA-2026:A001 (8) / A002 (9) / A003 (10).
    assert result.index("ALSA-2026:A011  AlmaLinux-8") < result.index(
        "ALSA-2026:A012  AlmaLinux-9"
    )


@pytest.mark.asyncio
async def test_preview_errata_merges_retry_build(errata_client):
    """A build with a failed arch plus its retry build → ONE advisory."""
    result = await preview_errata(
        [60002, 60003], issued_date="2026-10-23", cves=["CVE-2026-31431"]
    )
    assert "ALSA-2026:A011  AlmaLinux-10  builds #60002, #60003" in result
    assert "ALSA-2026:A012" not in result
    assert "Packages: 5 RPM(s) (aarch64, noarch, x86_64)" in result
    # The failure is still reported even though the retry build covers it.
    assert "!!! 1 build task(s) FAILED or were CANCELLED" in result
    assert (
        "! FAILED: build #60002 kernel aarch64 (task 600021) — rebuilt in "
        "build #60003; packages taken from there" in result
    )


@pytest.mark.asyncio
async def test_preview_errata_warns_about_failed_arch(errata_client):
    result = await preview_errata([60002], issued_date="2026-10-23", cves=["CVE-2026-31431"])
    assert "!!! 1 build task(s) FAILED or were CANCELLED" in result
    assert (
        "! FAILED: build #60002 kernel aarch64 (task 600021) — its packages "
        "are NOT in the advisory (add the retry build to include them)"
        in result
    )
    assert "Packages: 3 RPM(s) (noarch, x86_64)" in result


@pytest.mark.asyncio
async def test_preview_errata_reports_failure_of_left_out_package(errata_client):
    """A failed task is reported even when its package is not in the advisory."""
    result = await preview_errata([60008], issued_date="2026-10-23", cves=["CVE-2026-31431"])
    assert "Left out (not affected): kmod-foo" in result
    assert "!!! 1 build task(s) FAILED or were CANCELLED" in result
    assert (
        "! FAILED: build #60008 kmod-foo x86_64 (task 600089) — not affected, "
        "left out of the advisory anyway" in result
    )


@pytest.mark.asyncio
async def test_preview_errata_reports_cancelled_task(errata_client):
    result = await preview_errata([60010], issued_date="2026-10-23", cves=["CVE-2026-31431"])
    assert "! CANCELLED: build #60010 kmod-foo x86_64 (task 600109)" in result


@pytest.mark.asyncio
async def test_preview_errata_no_failures_no_banner(errata_client):
    result = await preview_errata([60001], issued_date="2026-10-23", cves=["CVE-2026-31431"])
    assert "!!!" not in result
    assert "FAILED" not in result


@pytest.mark.asyncio
async def test_preview_errata_all_failed_names_the_failures(errata_client):
    result = await preview_errata([60009], issued_date="2026-10-23", cves=["CVE-2026-31431"])
    assert result.startswith("Error")
    assert "produced no completed binary packages" in result
    assert "build #60009 kernel x86_64 (task 600090, failed)" in result


@pytest.mark.asyncio
async def test_preview_errata_error_still_names_failures(errata_client):
    """Even when nothing is affected, the failed tasks are reported."""
    result = await preview_errata([60008], issued_date="2026-10-23", cves=["CVE-2026-19534"])
    assert result.startswith("Error")
    assert "none of its source packages" in result
    assert "build #60008 kmod-foo x86_64 (task 600089, failed)" in result


@pytest.mark.asyncio
async def test_preview_errata_modular_build(errata_client):
    result = await preview_errata([60005], issued_date="2026-10-23", cves=["CVE-2026-19534"])
    assert "Module: nodejs:24" in result
    assert "Title: nodejs:24 security update" in result
    # Every package built for the module, including v8 with its own epoch.
    assert "Packages: 3 RPM(s) (x86_64)" in result
    assert "v8-13.6-devel" in result


@pytest.mark.asyncio
async def test_preview_errata_highest_cve_severity_wins(errata_client):
    result = await preview_errata(
        [60001], issued_date="2026-10-23", cves=["CVE-2026-11111", "CVE-2026-31431"]
    )
    assert "Severity: Important" in result
    assert "CVE-2026-11111  Low" in result


@pytest.mark.asyncio
async def test_preview_errata_rhsa(errata_client):
    result = await preview_errata([60001], issued_date="2026-10-23", rhsa="rhsa-2026:13566")
    errata_client.get_rh_advisory.assert_awaited_once_with("RHSA-2026:13566")
    assert "Source: RHSA-2026:13566" in result
    # Everything comes from the RHSA — the title too.
    assert "Title: kernel security and bug fix update" in result
    assert "Description: details text of RHSA-2026:13566" in result
    assert not any(
        c.kwargs.get("title") for c in errata_client.query_errata.await_args_list
    )
    assert "Severity: Important" in result
    # Description is the RHSA details note, debranded.
    assert "The kernel packages contain the Linux kernel." in result
    assert "on AlmaLinux (CVE-2026-31402)" in result
    assert "Red Hat Enterprise Linux" not in result
    assert "CVE-2026-31402  Important  CVSS:3.1/AV:L/AC:L  CWE-787" in result
    assert "public 2026-04-03T00:00:00Z" in result
    assert "CVE-2026-23270  Moderate" in result
    assert "bugzilla 2454844" in result


@pytest.mark.asyncio
async def test_preview_errata_nothing_affected(errata_client):
    result = await preview_errata([60005], issued_date="2026-10-23", cves=["CVE-2026-31431"])
    assert result.startswith("Error")
    assert "none of its source packages ['nodejs:24']" in result
    assert "package=" in result


@pytest.mark.asyncio
async def test_preview_errata_package_selects_and_names(errata_client):
    """The named package picks its RPMs out of a build with other packages."""
    result = await preview_errata(
        [60001], issued_date="2026-10-23", cves=["CVE-2026-31431"],
        package="kmod-foo",
    )
    assert "Source packages: kmod-foo" in result
    assert "Left out (not affected): kernel" in result
    assert "Title: kmod-foo security update" in result


@pytest.mark.asyncio
async def test_preview_errata_unknown_package(errata_client):
    result = await preview_errata(
        [60001], issued_date="2026-10-23", cves=["CVE-2026-31431"],
        package="no-such-pkg",
    )
    assert result.startswith("Error")
    assert "is package 'no-such-pkg'" in result
    assert "kmod-foo" in result  # lists what the build does have


@pytest.mark.asyncio
async def test_preview_errata_overrides_title_and_description(errata_client):
    result = await preview_errata(
        [60001], issued_date="2026-10-23", updated_date="2026-10-24", cves=["CVE-2026-31431"],
        title="kernel security and bug fix update", description="Custom text.",
    )
    assert "Title: kernel security and bug fix update" in result
    assert "  Custom text." in result
    assert "Issued: 2026-10-23  Updated: 2026-10-24" in result


@pytest.mark.asyncio
async def test_preview_errata_running_build(errata_client):
    result = await preview_errata([60006], issued_date="2026-10-23", cves=["CVE-2026-31431"])
    assert result.startswith("Error")
    assert "still running" in result


@pytest.mark.asyncio
async def test_preview_errata_package_without_metadata(errata_client):
    result = await preview_errata([60007], issued_date="2026-10-23", cves=["CVE-2026-31431"])
    assert result.startswith("Error")
    assert "kernel-doc-5.14.0-1.el8.noarch.rpm" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs, needle", [
    ({}, "exactly one"),
    ({"cves": ["CVE-2026-31431"], "rhsa": "RHSA-2026:13566"}, "exactly one"),
    ({"cves": ["CVE-26-1"]}, "invalid CVE"),
    ({"rhsa": "RHSA-2026"}, "invalid Red Hat advisory"),
])
async def test_preview_errata_bad_source(errata_client, kwargs, needle):
    result = await preview_errata([60001], issued_date="2026-10-23", **kwargs)
    assert result.startswith("Error")
    assert needle in result
    errata_client.get_build.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("issued, updated, needle", [
    ("23/10/2026", None, "YYYY-MM-DD"),
    ("2026-10-23", "2026-10-22", "before issued_date"),
])
async def test_preview_errata_bad_dates(errata_client, issued, updated, needle):
    result = await preview_errata(
        [60001], cves=["CVE-2026-31431"], issued_date=issued,
        updated_date=updated,
    )
    assert result.startswith("Error")
    assert needle in result


@pytest.mark.asyncio
async def test_preview_errata_bad_build_ref(errata_client):
    result = await preview_errata(
        ["https://example.com/x"], issued_date="2026-10-23", cves=["CVE-2026-31431"]
    )
    assert result.startswith("Error")
    assert "not a build ID" in result


@pytest.mark.asyncio
async def test_preview_errata_unknown_cve(errata_client):
    result = await preview_errata([60001], issued_date="2026-10-23", cves=["CVE-2099-99999"])
    assert result.startswith("Error")
    assert "CVE-2099-99999" in result
    assert "404" in result


@pytest.mark.asyncio
async def test_preview_errata_id_year_follows_issued_date(errata_client):
    errata_client.list_alma_errata_ids = AsyncMock(return_value=[])
    result = await preview_errata([60001], issued_date="2027-01-05", cves=["CVE-2026-31431"])
    errata_client.list_alma_errata_ids.assert_awaited_once_with(2027)
    assert "ALSA-2027:A001" in result


# ── create_errata ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_errata_payload(errata_client):
    result = await create_errata(
        [60001], issued_date="2026-10-23", cves=["CVE-2026-31431"]
    )
    assert result.startswith("Created 1 advisory")
    assert "ALSA-2026:A011  AlmaLinux-8  build #60001" in result
    assert (
        "https://build.almalinux.org/errata?id=ALSA-2026:A011&platform_id=1"
        in result
    )
    assert 'get_errata("ALSA-2026:A011")' in result
    # The IDs are checked for clashes right before creating.
    errata_client.query_errata.assert_any_await(errata_ids=["ALSA-2026:A011"])
    [payload] = _posted(errata_client)
    assert payload["id"] == "ALSA-2026:A011"
    assert payload["platform_id"] == 1
    assert payload["issued_date"] == "2026-10-23"
    assert payload["updated_date"] == "2026-10-23"
    assert payload["title"] == "kernel security update"
    assert payload["severity"] == "Important"
    assert payload["module"] == ""
    assert payload["devel_module"] is False
    assert payload["description"] == (
        "The kernel packages contain the Linux kernel.\n\n"
        "Security Fix(es):\n\n"
        "  * kernel: crypto: algif_aead - Revert to operating out-of-place "
        "(CVE-2026-31431)\n\n"
        "For more details about the security issue(s), including the impact, "
        "a CVSS score, acknowledgments, and other related information, refer "
        "to the CVE page(s) listed in the References section."
    )
    assert payload["references"] == [
        {
            "href": "https://access.redhat.com/security/cve/CVE-2026-31431",
            "ref_id": "CVE-2026-31431",
            "ref_type": "cve",
            "title": "CVE-2026-31431",
            "cve": {
                "id": "CVE-2026-31431",
                "cvss3": "CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H",
                "cwe": "CWE-1288",
                "impact": "Important",
                "public": "2026-04-22T00:00:00Z",
            },
        },
        {
            "href": "https://bugzilla.redhat.com/show_bug.cgi?id=2460538",
            "ref_id": "2460538",
            "ref_type": "bugzilla",
        },
    ]
    names = sorted((p["name"], p["arch"]) for p in payload["packages"])
    assert names == [
        ("kernel", "aarch64"), ("kernel", "x86_64"),
        ("kernel-core", "aarch64"), ("kernel-core", "x86_64"),
        ("kernel-doc", "noarch"),
    ]
    assert payload["packages"][0] == {
        "name": "kernel", "epoch": 0, "version": "5.14.0", "release": "1.el8",
        "arch": "aarch64", "reboot_suggested": False, "build_id": 60001,
    }


@pytest.mark.asyncio
async def test_create_errata_modular_payload(errata_client):
    await create_errata([60005], issued_date="2026-10-23", cves=["CVE-2026-19534"])
    [payload] = _posted(errata_client)
    assert payload["module"] == "nodejs:24"
    assert payload["title"] == "nodejs:24 security update"
    epochs = {p["name"]: p["epoch"] for p in payload["packages"]}
    assert epochs == {"nodejs": 1, "nodejs-devel": 1, "v8-13.6-devel": 3}


@pytest.mark.asyncio
async def test_create_errata_several_builds(errata_client):
    result = await create_errata(
        [60001, 60004, 60002, 60003], issued_date="2026-10-23", cves=["CVE-2026-31431"]
    )
    posted = _posted(errata_client)
    assert [(p["id"], p["platform_id"]) for p in posted] == [
        ("ALSA-2026:A011", 1), ("ALSA-2026:A012", 9), ("ALSA-2026:A013", 50),
    ]
    assert {p["build_id"] for p in posted[2]["packages"]} == {60002, 60003}
    assert "Created 3 advisory" in result
    # The covered aarch64 failure of 60002 is still in the creation report.
    assert "!!! 1 build task(s) FAILED or were CANCELLED" in result
    assert (
        "    ! FAILED: build #60002 kernel aarch64 (task 600021) — rebuilt in "
        "build #60003" in result
    )


@pytest.mark.asyncio
async def test_create_errata_requires_token_fails_fast(errata_client):
    errata_client.jwt_token = None
    result = await create_errata([60001], issued_date="2026-10-23", cves=["CVE-2026-31431"])
    assert result.startswith("Auth error")
    errata_client.get_rh_cve.assert_not_called()
    errata_client.get_build.assert_not_called()
    errata_client.create_errata.assert_not_called()


@pytest.mark.asyncio
async def test_create_errata_id_taken(errata_client):
    search = errata_client.query_errata.side_effect

    async def query_errata(**kw):
        if kw.get("errata_ids"):
            return {"records": [{"id": "ALSA-2026:A011"}], "total_records": 1}
        return await search(**kw)

    errata_client.query_errata = AsyncMock(side_effect=query_errata)
    result = await create_errata([60001], issued_date="2026-10-23", cves=["CVE-2026-31431"])
    assert result.startswith("Error")
    assert "ALSA-2026:A011" in result
    assert "Nothing was created" in result
    errata_client.create_errata.assert_not_called()


@pytest.mark.asyncio
async def test_create_errata_partial_failure(errata_client):
    errata_client.create_errata = AsyncMock(side_effect=[
        {"ok": True}, _http_status_error(500), {"ok": True},
    ])
    result = await create_errata(
        [60001, 60004, 60002], issued_date="2026-10-23", cves=["CVE-2026-31431"]
    )
    assert result.startswith("Error: creating ALSA-2026:A012 failed")
    assert "HTTP 500" in result
    assert "Already created: ALSA-2026:A011 (AlmaLinux-8, build #60001)" in result
    assert "Not attempted: ALSA-2026:A013 (AlmaLinux-10, build #60002)" in result
    assert errata_client.create_errata.await_count == 2
    assert (
        "  ! ALSA-2026:A013: FAILED: build #60002 kernel aarch64 (task 600021)"
        in result
    )


@pytest.mark.asyncio
async def test_create_errata_not_ok(errata_client):
    errata_client.create_errata = AsyncMock(
        return_value={"ok": False, "error": "bad module"}
    )
    result = await create_errata([60001], issued_date="2026-10-23", cves=["CVE-2026-31431"])
    assert result.startswith("Error: creating ALSA-2026:A011 failed: bad module")


@pytest.mark.asyncio
async def test_create_errata_auth_error(errata_client):
    errata_client.create_errata = AsyncMock(side_effect=_http_status_error(403))
    result = await create_errata([60001], issued_date="2026-10-23", cves=["CVE-2026-31431"])
    assert result.startswith("Error: creating ALSA-2026:A011 failed")
    assert "not authorized" in result


# ── get_errata ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_errata_tool(errata_client):
    errata_client.query_errata = AsyncMock(return_value={
        "records": [ERRATA_RECORD], "total_records": 1,
    })
    result = await get_errata("alsa-2026:a001")
    errata_client.query_errata.assert_awaited_once_with(
        errata_ids=["ALSA-2026:A001"], platform_id=None
    )
    assert "Advisory ALSA-2026:A001 — AlmaLinux-8" in result
    assert "Title: Important: kernel security update" in result
    assert "Release status: released" in result
    assert "Issued: 2026-05-01" in result
    assert (
        "References: CVE-2026-31431, bugzilla 2460538, self_ref ALSA-2026:A001"
        in result
    )
    assert "Packages: 2 (source: kernel), builds: #54743" in result
    assert "* kernel: thing (CVE-2026-31431)" in result


@pytest.mark.asyncio
async def test_get_errata_platform_filter(errata_client):
    errata_client.query_errata = AsyncMock(return_value={
        "records": [ERRATA_RECORD], "total_records": 1,
    })
    await get_errata("ALSA-2026:A001", "AlmaLinux-8")
    errata_client.query_errata.assert_awaited_once_with(
        errata_ids=["ALSA-2026:A001"], platform_id=1
    )


@pytest.mark.asyncio
async def test_get_errata_unknown_platform(errata_client):
    result = await get_errata("ALSA-2026:A001", "AlmaLinux-99")
    assert result.startswith("Error")
    assert "unknown platform" in result
    errata_client.query_errata.assert_not_called()


@pytest.mark.asyncio
async def test_get_errata_not_found(errata_client):
    result = await get_errata("ALSA-2026:A099")
    assert result.startswith("Error")
    assert "not found" in result
    assert "retry in a few seconds" in result


# ── helpers ───────────────────────────────────────────────────────────

def test_rh_affected_parses_package_forms():
    names, modules = commands_module._rh_affected(
        ["kernel", "nodejs:26/nodejs", "rhel9/postgresql-15", None],
        [
            "kernel-rt-0:6.12.0-124.55.1.el10_1",
            "nodejs:22-8100020260928121333.6d880403",
            "rhdh/rhdh-hub-rhel9:1789554285",
            "grafana12-4-main-12.4.10-0.2.hum1",
            None,
        ],
    )
    assert names == {"kernel", "nodejs", "kernel-rt"}
    assert modules == {"nodejs:26", "nodejs:22"}


def test_next_errata_number_counts_only_this_years_alma_ids():
    ids = ["ALSA-2026:A009", "ALBA-2026:A012", "ALSA-2026:1234",
           "ALSA-2025:A099"]
    assert commands_module._next_errata_number(ids, 2026) == 13
    assert commands_module._next_errata_number([], 2026) == 1


def test_parse_build_ref():
    parse = commands_module._parse_build_ref
    assert parse(54743) == 54743
    assert parse("54743") == 54743
    assert parse("https://build.almalinux.org/build/54743") == 54743
    assert parse("https://build.almalinux.org/build/54743/") == 54743
    with pytest.raises(ValueError):
        parse("build 54743")


# ── new_errata (prompt) ───────────────────────────────────────────────

def test_new_errata_prompt_content():
    text = new_errata("CVE-2026-31431 CVE-2026-43284", "54743 54757", "kernel")
    assert "for kernel fixing CVE-2026-31431 CVE-2026-43284" in text
    assert "54743 54757" in text
    assert 'package="kernel"' in text
    assert "filled automatically" in text
    assert "preview_errata" in text
    assert "create_errata" in text
    assert "get_errata" in text
    assert "EVERY failed or cancelled build task" in text


@pytest.mark.asyncio
async def test_new_errata_prompt_registered():
    prompts = await mcp.list_prompts()
    by_name = {p.name: p for p in prompts}
    assert "new_errata" in by_name
    arg_names = [a.name for a in (by_name["new_errata"].arguments or [])]
    assert arg_names == ["advisory", "builds", "package"]
    required = {a.name: a.required for a in by_name["new_errata"].arguments}
    assert required == {"advisory": True, "builds": True, "package": False}


def test_new_errata_prompt_rhsa_without_package():
    text = new_errata("RHSA-2026:13566", "54740 54757")
    assert "package=" not in text
    assert "RHSA-2026:13566" in text


@pytest.mark.asyncio
async def test_new_errata_prompt_renders_via_server():
    result = await mcp.get_prompt(
        "new_errata", {"advisory": "RHSA-2026:13566", "builds": "54740"}
    )
    assert len(result.messages) == 1
    assert "RHSA-2026:13566" in result.messages[0].content.text


# ── errata: automatic fields ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_preview_errata_dates_default_to_today(errata_client):
    import datetime as dt

    today = dt.date.today()
    errata_client.list_alma_errata_ids = AsyncMock(return_value=[])
    result = await preview_errata([60001], cves=["CVE-2026-31431"])
    assert f"Issued: {today}  Updated: {today}" in result
    assert f"ALSA-{today.year}:A001" in result
    errata_client.list_alma_errata_ids.assert_awaited_once_with(today.year)


@pytest.mark.asyncio
async def test_preview_errata_package_text_from_earlier_advisory(errata_client):
    result = await preview_errata(
        [60001], cves=["CVE-2026-31431"], package="kernel",
        issued_date="2026-10-23",
    )
    # Same platform, exact package; the newer bug fix advisory (no fix list)
    # and kernel-rt are skipped.
    assert "Description: package text from ALSA-2026:8999 + CVE fix list" in result
    assert (
        "  The kernel packages contain the Linux kernel.\n\n"
        "  Security Fix(es):\n\n"
        "    * kernel: crypto: algif_aead" in result
    )
    assert "Real Time" not in result


@pytest.mark.asyncio
async def test_preview_errata_package_text_prefers_same_platform(errata_client):
    result = await preview_errata(
        [60004], cves=["CVE-2026-31431"], issued_date="2026-10-23"
    )
    assert "package text from ALSA-2026:8998" in result
    assert "The AL9 kernel packages." in result


@pytest.mark.asyncio
async def test_preview_errata_package_text_any_platform_fallback(errata_client):
    # No earlier AlmaLinux-10 kernel advisory → the newest one anywhere.
    result = await preview_errata(
        [60003], cves=["CVE-2026-31431"], issued_date="2026-10-23"
    )
    assert "package text from ALSA-2026:8999" in result


@pytest.mark.asyncio
async def test_preview_errata_package_text_other_module_stream(errata_client):
    result = await preview_errata(
        [60005], cves=["CVE-2026-19534"], package="nodejs",
        issued_date="2026-10-23",
    )
    # "nodejs" selects the nodejs:24 module; the title names the stream.
    assert "Module: nodejs:24" in result
    assert "Title: nodejs:24 security update" in result
    assert "package text from ALSA-2026:8997" in result
    assert "Node.js is a software development platform." in result


@pytest.mark.asyncio
async def test_preview_errata_no_earlier_advisory_note(errata_client):
    result = await preview_errata(
        [60001], cves=["CVE-2026-31431"], package="kmod-foo",
        issued_date="2026-10-23",
    )
    assert (
        "NOTE: no earlier security advisory found for kmod-foo, so there is "
        "no package text — pass description=... to add it" in result
    )
    assert "Description:\n  Security Fix(es):" in result


@pytest.mark.asyncio
async def test_preview_errata_several_cves_one_advisory(errata_client):
    result = await preview_errata(
        [60001], cves=["CVE-2026-31431", "CVE-2026-11111"], package="kernel",
        issued_date="2026-10-23",
    )
    assert "Errata preview: 1 advisory(ies)" in result
    # One fix per line, two-space indent, no blank line between them
    # (the preview indents the whole description by two more spaces).
    assert (
        "    * kernel: crypto: algif_aead - Revert to operating out-of-place "
        "(CVE-2026-31431)\n"
        "    * kernel: minor thing (CVE-2026-11111)\n\n"
        "  For more details" in result
    )
    assert "bugzilla 2460538" in result
    assert "bugzilla 2000001" in result


@pytest.mark.asyncio
async def test_preview_errata_notes_cve_not_tied_to_package(errata_client):
    result = await preview_errata(
        [60005], cves=["CVE-2026-19534", "CVE-2026-31431"], package="nodejs:24",
        issued_date="2026-10-23",
    )
    assert (
        "NOTE: Red Hat does not list nodejs:24 as affected by CVE-2026-31431"
        in result
    )
    assert "affected by CVE-2026-19534" not in result


def test_cve_from_rh_ignores_not_affected_states():
    info = commands_module._cve_from_rh({
        **RH_CVE_LOW,
        "package_state": [
            {"package_name": "kernel", "fix_state": "Not affected"},
            {"package_name": "kernel-rt", "fix_state": "Affected"},
        ],
    })
    assert info["packages"] == {"kernel-rt"}


def test_title_subjects():
    subjects = commands_module._title_subjects
    assert subjects("Important: kernel security update") == ["kernel"]
    assert subjects(
        "Important: nodejs:24 security, bug fix, and enhancement update"
    ) == ["nodejs:24"]
    assert subjects("Moderate: python3.11 and python3.12 security update") == [
        "python3.11", "python3.12",
    ]
    assert subjects("Low: a, b, and c bug fix update") == ["a", "b", "c"]


def test_description_intro():
    intro = commands_module._description_intro
    assert intro(
        "The kernel packages contain the Linux kernel.  \n\n"
        "Security Fix(es):\n\n* x"
    ) == "The kernel packages contain the Linux kernel."
    assert intro("For detailed information see the Release Notes.") is None
    assert intro("Security Fix(es):\n\n* x") is None
    assert intro(None) is None


# ── errata: duplicate check ───────────────────────────────────────────

def _existing(errata_id, platform_id, title, cves, source="kernel", module=""):
    return {
        "id": errata_id, "platform_id": platform_id, "original_title": title,
        "title": None, "module": module,
        "references": [
            {"ref_type": "ErrataReferenceType.cve", "ref_id": c} for c in cves
        ],
        "packages": [{"name": source, "source_srpm": source}],
    }


@pytest.mark.asyncio
async def test_preview_errata_flags_duplicate(errata_client):
    errata_client.existing_advisories.append(_existing(
        "ALSA-2026:16195", 1, "Important: kernel security update",
        ["cve-2026-31431"],  # stored lower-case — still the same CVE
    ))
    result = await preview_errata(
        [60001], cves=["CVE-2026-31431"], issued_date="2026-10-23"
    )
    assert "!! DUPLICATE: 1 advisory(ies) here would repeat existing ones" in result
    assert (
        '  !! DUPLICATE: ALSA-2026:16195 "Important: kernel security update" '
        "already covers CVE-2026-31431 for kernel on AlmaLinux-8" in result
    )
    errata_client.query_errata.assert_any_await(
        cve_id="CVE-2026-31431", platform_id=1, page=1
    )


@pytest.mark.asyncio
async def test_preview_errata_same_cve_other_package_is_a_note(errata_client):
    errata_client.existing_advisories.append(_existing(
        "ALSA-2026:16196", 1, "Important: kernel-rt security update",
        ["CVE-2026-31431"], source="kernel-rt",
    ))
    result = await preview_errata(
        [60001], cves=["CVE-2026-31431"], issued_date="2026-10-23"
    )
    assert "DUPLICATE" not in result
    assert (
        '  NOTE: CVE-2026-31431 also in ALSA-2026:16196 "Important: kernel-rt '
        'security update" (another package on AlmaLinux-8)' in result
    )


@pytest.mark.asyncio
async def test_preview_errata_other_module_stream_is_not_duplicate(errata_client):
    errata_client.existing_advisories.append(_existing(
        "ALSA-2026:87080", 1, "Important: nodejs:22 security update",
        ["CVE-2026-19534"], source="nodejs", module="nodejs:22",
    ))
    result = await preview_errata(
        [60005], cves=["CVE-2026-19534"], issued_date="2026-10-23"
    )
    assert "DUPLICATE" not in result
    assert "also in ALSA-2026:87080" in result


@pytest.mark.asyncio
async def test_preview_errata_duplicate_ignores_cve_substring_and_platform(
    errata_client,
):
    errata_client.existing_advisories.extend([
        # CVE-2026-314310 contains CVE-2026-31431 but is another CVE.
        _existing("ALSA-2026:1", 1, "Important: kernel security update",
                  ["CVE-2026-314310"]),
        # Same CVE and package, but AlmaLinux-9.
        _existing("ALSA-2026:2", 9, "Important: kernel security update",
                  ["CVE-2026-31431"]),
    ])
    result = await preview_errata(
        [60001], cves=["CVE-2026-31431"], issued_date="2026-10-23"
    )
    assert "DUPLICATE" not in result
    assert "ALSA-2026:1 " not in result
    assert "ALSA-2026:2 " not in result


@pytest.mark.asyncio
async def test_create_errata_refuses_duplicate(errata_client):
    errata_client.existing_advisories.append(_existing(
        "ALSA-2026:16195", 1, "Important: kernel security update",
        ["CVE-2026-31431"],
    ))
    result = await create_errata(
        [60001, 60002], cves=["CVE-2026-31431"], issued_date="2026-10-23"
    )
    assert result.startswith(
        "Error: nothing was created — these advisories would duplicate"
    )
    assert "ALSA-2026:A011 (AlmaLinux-8, build #60001):" in result
    assert "ALSA-2026:16195" in result
    assert "force=True" in result
    # The failed build task of 60002 is reported in the error as well.
    assert "! ALSA-2026:A012: FAILED: build #60002 kernel aarch64" in result
    errata_client.create_errata.assert_not_called()


@pytest.mark.asyncio
async def test_create_errata_force_creates_duplicate(errata_client):
    errata_client.existing_advisories.append(_existing(
        "ALSA-2026:16195", 1, "Important: kernel security update",
        ["CVE-2026-31431"],
    ))
    result = await create_errata(
        [60001], cves=["CVE-2026-31431"], issued_date="2026-10-23", force=True
    )
    assert result.startswith("Created 1 advisory")
    assert (
        "!! created despite (force=True): DUPLICATE: ALSA-2026:16195" in result
    )
    errata_client.create_errata.assert_awaited_once()


def test_normalize_fix_lists():
    norm = commands_module._normalize_fix_lists
    assert norm(
        "Intro.\n\nSecurity Fix(es):\n\n* a (CVE-1)\n\n* b (CVE-2)\n\n"
        "For more details."
    ) == (
        "Intro.\n\nSecurity Fix(es):\n\n  * a (CVE-1)\n  * b (CVE-2)\n\n"
        "For more details."
    )
    # Two lists stay separate paragraphs; already-indented items are kept.
    assert norm(
        "Security Fix(es):\n\n  * a\n\nBug Fix(es):\n\n* b\n\n* c"
    ) == "Security Fix(es):\n\n  * a\n\nBug Fix(es):\n\n  * b\n  * c"


@pytest.mark.asyncio
async def test_preview_errata_rhsa_details_one_fix_per_line(errata_client):
    csaf = {**RH_CSAF_KERNEL, "document": {**RH_CSAF_KERNEL["document"]}}
    csaf["document"]["notes"] = [{
        "category": "general",
        "text": "The kernel packages.\n\nSecurity Fix(es):\n\n"
                "* kernel: a (CVE-2026-31402)\n\n* kernel: b (CVE-2026-23270)"
                "\n\nFor more details.",
    }]
    errata_client.get_rh_advisory = AsyncMock(return_value=csaf)
    await create_errata([60001], rhsa="RHSA-2026:13566", issued_date="2026-10-23")
    [payload] = _posted(errata_client)
    assert payload["description"] == (
        "The kernel packages.\n\nSecurity Fix(es):\n\n"
        "  * kernel: a (CVE-2026-31402)\n  * kernel: b (CVE-2026-23270)\n\n"
        "For more details."
    )
