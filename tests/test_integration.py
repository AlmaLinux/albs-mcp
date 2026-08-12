"""Integration tests against the real ALBS API (read-only).

These tests hit build.almalinux.org — run with:
    pytest tests/test_integration.py -v

Skip with:
    pytest tests/ -v --ignore=tests/test_integration.py
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from albs_mcp.client import ALBSClient
from albs_mcp.constants import LOG_ERROR_PATTERN, LOG_MAX_RESULT_CHARS

BUILD_ID = 52745
# A long-since completed release — safe to read.
RELEASE_ID = 39229


@pytest_asyncio.fixture
async def client(tmp_path):
    c = ALBSClient(jwt_token=None)
    c._log_dir = tmp_path / "logs"
    c._log_dir.mkdir()
    yield c
    await c.close()


# ── Platforms (dynamic arches) ────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_platforms_returns_data(client):
    platforms = await client.get_platforms()
    assert isinstance(platforms, list)
    assert len(platforms) > 0
    names = [p["name"] for p in platforms]
    assert "AlmaLinux-9" in names


@pytest.mark.asyncio
async def test_get_platforms_have_arch_list(client):
    platforms = await client.get_platforms()
    for p in platforms:
        assert "arch_list" in p
        assert isinstance(p["arch_list"], list)
        assert len(p["arch_list"]) > 0


@pytest.mark.asyncio
async def test_get_platform_arches_returns_dict(client):
    arches = await client.get_platform_arches()
    assert isinstance(arches, dict)
    assert "AlmaLinux-9" in arches
    assert "x86_64" in arches["AlmaLinux-9"]


@pytest.mark.asyncio
async def test_platform_arches_include_expected(client):
    arches = await client.get_platform_arches()
    al9 = arches.get("AlmaLinux-9", [])
    for expected in ["x86_64", "aarch64", "ppc64le", "s390x"]:
        assert expected in al9, f"{expected} not in AlmaLinux-9 arches"


@pytest.mark.asyncio
async def test_platform_arches_caching(client):
    a1 = await client.get_platform_arches()
    a2 = await client.get_platform_arches()
    assert a1 is a2


# ── Build info ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_build_returns_valid_structure(client):
    build = await client.get_build(BUILD_ID)
    assert build["id"] == BUILD_ID
    assert "tasks" in build
    assert "owner" in build
    assert "sign_tasks" in build
    assert "created_at" in build


@pytest.mark.asyncio
async def test_get_build_tasks_have_required_fields(client):
    build = await client.get_build(BUILD_ID)
    for task in build["tasks"]:
        assert "id" in task
        assert "status" in task
        assert "arch" in task
        assert "ref" in task
        assert "artifacts" in task
        assert "url" in task["ref"]


@pytest.mark.asyncio
async def test_get_build_artifacts_have_types(client):
    build = await client.get_build(BUILD_ID)
    for task in build["tasks"]:
        for artifact in task["artifacts"]:
            assert "name" in artifact
            assert "type" in artifact
            assert artifact["type"] in ("rpm", "build_log")


@pytest.mark.asyncio
async def test_get_build_nonexistent(client):
    with pytest.raises(Exception):
        await client.get_build(999999999)


# ── Search builds ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_builds_returns_results(client):
    data = await client.search_builds(page=1)
    builds = data if isinstance(data, list) else data.get("builds", [])
    assert len(builds) > 0


@pytest.mark.asyncio
async def test_search_builds_page_2(client):
    data = await client.search_builds(page=2)
    builds = data if isinstance(data, list) else data.get("builds", [])
    assert isinstance(builds, list)


# ── Sign tasks (public) ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_sign_tasks_returns_list(client):
    tasks = await client.get_sign_tasks(BUILD_ID)
    assert isinstance(tasks, list)


# ── Log listing ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_build_logs_returns_files(client):
    logs = await client.list_build_logs(BUILD_ID)
    assert isinstance(logs, list)
    assert len(logs) > 0
    log_extensions = {l.split(".")[-1] for l in logs}
    assert "log" in log_extensions or "cfg" in log_extensions


@pytest.mark.asyncio
async def test_list_build_logs_has_expected_types(client):
    logs = await client.list_build_logs(BUILD_ID)
    has_mock_build = any("mock_build" in l for l in logs)
    has_albs = any("albs." in l for l in logs)
    assert has_mock_build, "Expected mock_build logs"
    assert has_albs, "Expected albs logs"


@pytest.mark.asyncio
async def test_list_build_logs_nonexistent(client):
    with pytest.raises(Exception):
        await client.list_build_logs(999999999)


# ── Log download and reading ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_download_small_log(client):
    logs = await client.list_build_logs(BUILD_ID)
    albs_log = next(l for l in logs if l.startswith("albs."))
    path = await client.download_log(BUILD_ID, albs_log)
    assert path.exists()
    assert path.stat().st_size > 0
    content = path.read_text()
    assert len(content) > 0


@pytest.mark.asyncio
async def test_download_and_read_tail(client):
    logs = await client.list_build_logs(BUILD_ID)
    albs_log = next(l for l in logs if l.startswith("albs."))
    await client.download_log(BUILD_ID, albs_log)
    content, total, first, last = client.read_log_tail(BUILD_ID, albs_log, 5)
    assert total > 0
    assert 1 <= first <= last == total
    assert len(content) > 0


@pytest.mark.asyncio
async def test_download_and_read_range(client):
    logs = await client.list_build_logs(BUILD_ID)
    albs_log = next(l for l in logs if l.startswith("albs."))
    await client.download_log(BUILD_ID, albs_log)
    content, total, last = client.read_log_range(BUILD_ID, albs_log, 1, 3)
    assert total > 0
    assert last == min(3, total)
    assert len(content) > 0


@pytest.mark.asyncio
async def test_download_mock_cfg(client):
    logs = await client.list_build_logs(BUILD_ID)
    cfg = next((l for l in logs if l.endswith(".cfg")), None)
    if cfg:
        path = await client.download_log(BUILD_ID, cfg)
        assert path.exists()
        assert path.stat().st_size > 0


@pytest.mark.asyncio
async def test_read_not_downloaded_raises(client):
    with pytest.raises(FileNotFoundError, match="not downloaded"):
        client.read_log_tail(BUILD_ID, "nonexistent.log", 10)


@pytest.mark.asyncio
async def test_read_range_not_downloaded_raises(client):
    with pytest.raises(FileNotFoundError, match="not downloaded"):
        client.read_log_range(BUILD_ID, "nonexistent.log", 1, 10)


@pytest.mark.asyncio
async def test_read_log_tail_pages_a_real_log_bottom_up(client):
    """Paging the i686 mock_build log covers it with no gap and no overlap.

    Page sizes come from the char budget, so on this log (multi-KB gcc lines)
    a page is ~165 lines while a mock_root page is ~350 — the point of budgeting
    in characters rather than lines.
    """
    build_id, log = 70368, "mock_build.441500.1785274367.log"
    await client.download_log(build_id, log)
    seen: list[int] = []
    before = None
    for _ in range(20):
        content, total, first, last = client.read_log_tail(
            build_id, log, 3000, before_line=before,
        )
        if not content:
            break
        assert len(content) <= LOG_MAX_RESULT_CHARS
        seen = list(range(first, last + 1)) + seen
        before = first
        if first == 1:
            break
    assert seen == list(range(1, total + 1))
    assert total > 900


@pytest.mark.asyncio
async def test_search_log_finds_the_real_compile_error(client):
    """The i686 task of build 70368 fails on a 32-bit-only pointer mismatch.

    The log's tail only shows `make: *** [Makefile:4615: all] Error 2`; the real
    diagnostic sits ~100 lines earlier among multi-KB gcc command lines. This
    asserts search_log surfaces it, since that is the whole point of the tool.
    """
    build_id, log = 70368, "mock_build.441500.1785274367.log"
    await client.download_log(build_id, log)
    hunks, matches, total, _ = client.search_log(
        build_id, log, LOG_ERROR_PATTERN, before=1, after=8,
    )
    assert matches >= 3
    assert total > 900
    text = "\n".join(line for hunk in hunks for _, line, _ in hunk)
    assert "mech_openssl.c:2766" in text
    assert "incompatible pointer type" in text
    # Every reported line stays within the clip budget (plus the marker).
    assert max(len(line) for hunk in hunks for _, line, _ in hunk) < 600


@pytest.mark.asyncio
async def test_search_log_no_matches_on_a_clean_log(client):
    """A log with no failure signature reports zero matches, not a false hit."""
    logs = await client.list_build_logs(BUILD_ID)
    albs_log = next(l for l in logs if l.startswith("albs."))
    await client.download_log(BUILD_ID, albs_log)
    _hunks, matches, total, _ = client.search_log(
        BUILD_ID, albs_log, r"this-string-appears-in-no-log",
    )
    assert matches == 0
    assert total > 0


@pytest.mark.asyncio
async def test_search_log_not_downloaded_raises(client):
    with pytest.raises(FileNotFoundError, match="not downloaded"):
        client.search_log(BUILD_ID, "nonexistent.log", "error")


# ── Products (public) ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_products_returns_data(client):
    products = await client.get_products()
    assert isinstance(products, list)
    assert len(products) > 0
    for p in products:
        assert "id" in p
        assert "name" in p
    names = [p["name"] for p in products]
    assert "AlmaLinux" in names


@pytest.mark.asyncio
async def test_get_product_ids_mapping(client):
    ids = await client.get_product_ids()
    assert isinstance(ids, dict)
    assert "AlmaLinux" in ids
    assert isinstance(ids["AlmaLinux"], int)


@pytest.mark.asyncio
async def test_get_platform_ids_mapping(client):
    ids = await client.get_platform_ids()
    assert isinstance(ids, dict)
    assert "AlmaLinux-9" in ids
    assert isinstance(ids["AlmaLinux-9"], int)


# ── Releases (public) ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_release_returns_structure(client):
    release = await client.get_release(RELEASE_ID)
    assert release["id"] == RELEASE_ID
    assert "status" in release
    assert "plan" in release
    assert "build_ids" in release
    assert "build_task_ids" in release


@pytest.mark.asyncio
async def test_get_release_nonexistent(client):
    with pytest.raises(Exception):
        await client.get_release(999999999)
