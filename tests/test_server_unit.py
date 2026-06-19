"""Unit tests for MCP server tools with mocked client."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import pytest

import albs_mcp._commands as commands_module
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
    client.read_log_tail = MagicMock(return_value=("error: fail", 5000, 4990))
    client.read_log_range = MagicMock(return_value=("line 100\nline 101", 5000))
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
    assert "lines 4990-5000 of 5000" in result
    assert "error: fail" in result


@pytest.mark.asyncio
async def test_read_log_tail_default_lines(mock_client):
    await read_log_tail(50000, "mock_build.log")
    mock_client.read_log_tail.assert_called_once_with(50000, "mock_build.log", 3000)


# ── read_log_range ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_log_range_tool(mock_client):
    result = await read_log_range(50000, "mock_build.log", 100, 102)
    assert "lines 100-102 of 5000" in result
    assert "line 100" in result


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
        ("error: boom", 5000, 4990),
    ])
    result = await read_log_tail(50000, "mock_build.log", 3000)
    mock_client.download_log.assert_awaited_once_with(50000, "mock_build.log")
    assert "error: boom" in result
    assert "lines 4990-5000 of 5000" in result


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
        ("line 100\nline 101", 5000),
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
    assert "read_log_tail" in text


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
