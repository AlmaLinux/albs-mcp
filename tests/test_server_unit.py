"""Unit tests for MCP server tools with mocked client."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

import albs_mcp.server as server_module
from albs_mcp.server import (
    get_build_info,
    get_failed_tasks,
    get_platforms,
    get_sign_keys,
    list_build_logs,
    download_log,
    read_log_tail,
    read_log_range,
    search_builds,
    create_build,
    sign_build,
    delete_build,
)


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
    server_module._client = None
    yield
    server_module._client = None


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
    server_module._client = client
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


@pytest.mark.asyncio
async def test_get_build_info_shows_all_tasks(mock_client):
    result = await get_build_info(50000)
    assert "300001" in result
    assert "300002" in result
    assert "300003" in result
    assert "x86_64" in result
    assert "aarch64" in result
    assert "s390x" in result


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
    assert "Error creating build" in result


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
