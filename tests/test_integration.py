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


# ── Errata (public reads, Red Hat security data) ──────────────────────
#
# ALSA-2026:A001 (AlmaLinux-8, build 54743) and ALSA-2026:A003
# (AlmaLinux-10, builds 54740 + its aarch64 retry 54757) are released
# AlmaLinux advisories for CVE-2026-31431 — the previews below must
# reproduce their package lists.

@pytest.mark.asyncio
async def test_query_errata_exact_id(client):
    data = await client.query_errata(errata_ids=["ALSA-2026:A001"], platform_id=1)
    assert data["total_records"] == 1
    assert data["records"][0]["id"] == "ALSA-2026:A001"


@pytest.mark.asyncio
async def test_list_alma_errata_ids(client):
    ids = await client.list_alma_errata_ids(2025)
    assert {f"ALSA-2025:A00{i}" for i in range(1, 7)} <= set(ids)
    assert all(":A" in i for i in ids)


@pytest.mark.asyncio
async def test_get_rh_cve(client):
    data = await client.get_rh_cve("CVE-2026-31431")
    assert data["name"] == "CVE-2026-31431"
    assert data["threat_severity"] == "Important"


@pytest.mark.asyncio
async def test_get_rh_advisory(client):
    data = await client.get_rh_advisory("RHSA-2026:13566")
    assert data["document"]["tracking"]["id"] == "RHSA-2026:13566"
    assert any(v["cve"] == "CVE-2026-31431" for v in data["vulnerabilities"])


@pytest.mark.asyncio
async def test_preview_errata_reproduces_released_advisories(client):
    import albs_mcp._commands as commands_module

    commands_module._client = client
    try:
        result = await commands_module.preview_errata(
            [54743, 54740, 54757], cves=["CVE-2026-31431"], package="kernel",
            issued_date="2026-10-23",
        )
    finally:
        commands_module._client = None
    assert "AlmaLinux-8  build #54743" in result
    assert "AlmaLinux-10  builds #54740, #54757" in result
    assert "Title: kernel security update" in result
    assert "Packages: 94 RPM(s)" in result   # = ALSA-2026:A001
    assert "Packages: 272 RPM(s)" in result  # = ALSA-2026:A003
    assert "CVE-2026-31431  Important" in result
    # Package text taken from an earlier kernel advisory, as in A001.
    assert "Description: package text from ALSA-" in result
    assert (
        "The kernel packages contain the Linux kernel, the core of any Linux "
        "operating system." in result
    )
    # These advisories already exist — the preview flags them.
    assert '!! DUPLICATE: ALSA-2026:A001 "Important: kernel security update"' in result
    assert '!! DUPLICATE: ALSA-2026:A003 "Important: kernel security update"' in result
    # 54740's aarch64 failure is reported although 54757 rebuilt it.
    assert (
        "! FAILED: build #54740 kernel aarch64 (task 411949) — rebuilt in "
        "build #54757" in result
    )


@pytest.mark.asyncio
async def test_preview_errata_from_rhsa(client):
    import albs_mcp._commands as commands_module

    commands_module._client = client
    try:
        result = await commands_module.preview_errata(
            [54740, 54757], rhsa="RHSA-2026:13566", issued_date="2026-10-23"
        )
    finally:
        commands_module._client = None
    assert "Title: kernel security update" in result
    assert "Description: details text of RHSA-2026:13566" in result
    assert "CVE-2026-31431  Important" in result
    assert "Packages: 272 RPM(s)" in result


@pytest.mark.asyncio
async def test_preview_errata_flags_existing_rhsa_advisory(client):
    """Build 94160 is the php:8.2 rebuild that ALSA-2026:87073 (the imported
    RHSA) already covers for these CVEs; the php:7.4 advisory with the same
    CVEs is another module stream, so only a NOTE."""
    import albs_mcp._commands as commands_module

    commands_module._client = client
    try:
        result = await commands_module.preview_errata(
            [94160], cves=["CVE-2026-109618", "CVE-2026-109619"],
            issued_date="2026-10-23",
        )
    finally:
        commands_module._client = None
    assert "Module: php:8.2" in result
    assert (
        '!! DUPLICATE: ALSA-2026:87073 "Important: php:8.2 security update" '
        "already covers CVE-2026-109618, CVE-2026-109619" in result
    )
    assert "php:7.4 security update" in result
