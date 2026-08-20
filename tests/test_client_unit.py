"""Unit tests for ALBSClient with mocked HTTP responses."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from albs_mcp.client import (
    ALBSClient,
    clip_line,
    extract_el_version,
    get_completed_task_ids,
    get_whole_package_task_ids,
)
from albs_mcp.constants import ALBS_API


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def tmp_log_dir(tmp_path):
    return tmp_path / "logs"


@pytest.fixture
def client(tmp_log_dir, monkeypatch):
    monkeypatch.setenv("ALBS_LOG_DIR", str(tmp_log_dir))
    return ALBSClient(jwt_token="test-token-123")


@pytest.fixture
def client_no_token(tmp_log_dir, monkeypatch):
    monkeypatch.setenv("ALBS_LOG_DIR", str(tmp_log_dir))
    return ALBSClient(jwt_token=None)


SAMPLE_BUILD = {
    "id": 12345,
    "created_at": "2026-03-01T10:00:00",
    "finished_at": "2026-03-01T11:00:00",
    "owner": {"id": 1, "username": "testuser", "email": "test@example.com"},
    "released": False,
    "cancel_testing": False,
    "tasks": [
        {
            "id": 100001,
            "status": 2,
            "arch": "x86_64",
            "ref": {
                "url": "https://git.almalinux.org/rpms/bash.git",
                "git_ref": "c9s",
                "ref_type": 1,
            },
            "artifacts": [
                {"id": 1, "name": "bash-5.1-1.el9.x86_64.rpm", "type": "rpm", "href": "/pulp/api/v3/content/rpm/packages/abc/"},
                {"id": 2, "name": "mock_build.100001.12345.log", "type": "build_log", "href": "/pulp/api/v3/content/file/files/def/"},
            ],
            "platform": {"id": 1, "type": "rpm", "name": "AlmaLinux-9", "arch_list": ["x86_64"]},
            "test_tasks": [],
        },
        {
            "id": 100002,
            "status": 3,
            "arch": "aarch64",
            "ref": {
                "url": "https://git.almalinux.org/rpms/bash.git",
                "git_ref": "c9s",
                "ref_type": 1,
            },
            "artifacts": [
                {"id": 3, "name": "mock_build.100002.12346.log", "type": "build_log", "href": "/pulp/api/v3/content/file/files/ghi/"},
                {"id": 4, "name": "mock_stderr.100002.12346.log", "type": "build_log", "href": "/pulp/api/v3/content/file/files/jkl/"},
                {"id": 5, "name": "mock_root.100002.12346.log", "type": "build_log", "href": "/pulp/api/v3/content/file/files/mno/"},
            ],
            "platform": {"id": 1, "type": "rpm", "name": "AlmaLinux-9", "arch_list": ["x86_64", "aarch64"]},
            "test_tasks": [],
        },
    ],
    "sign_tasks": [],
    "linked_builds": [],
    "mock_options": None,
    "platform_flavors": [],
    "release_id": None,
    "products": [],
}

SAMPLE_PLATFORMS = [
    {"id": 1, "name": "AlmaLinux-8", "distr_type": "rpm", "distr_version": "8", "arch_list": ["i686", "x86_64", "aarch64", "ppc64le", "s390x"]},
    {"id": 2, "name": "AlmaLinux-9", "distr_type": "rpm", "distr_version": "9", "arch_list": ["i686", "x86_64", "aarch64", "ppc64le", "s390x"]},
    {"id": 3, "name": "AlmaLinux-10", "distr_type": "rpm", "distr_version": "10", "arch_list": ["i686", "x86_64", "x86_64_v2", "aarch64", "ppc64le", "s390x", "riscv64"]},
]

SAMPLE_SIGN_KEYS = [
    {"id": 1, "name": "AlmaLinux-8", "description": "AL8 key", "keyid": "2AE81E8ACED7258B", "public_url": "https://example.com/key1", "inserted": "2024-01-01T00:00:00", "active": True, "platform_ids": [1]},
    {"id": 4, "name": "AlmaLinux-9", "description": "AL9 key", "keyid": "D36CB86CB86B3716", "public_url": "https://example.com/key4", "inserted": "2024-01-01T00:00:00", "active": True, "platform_ids": [2]},
]

SAMPLE_LOG_LISTING = """
<html>
<head><title>Index of build_logs/</title></head>
<body>
<a href="../">../</a>
<a href="mock_build.100002.12346.log">mock_build.100002.12346.log</a>
<a href="mock_stderr.100002.12346.log">mock_stderr.100002.12346.log</a>
<a href="mock_root.100002.12346.log">mock_root.100002.12346.log</a>
<a href="mock.100002.12346.cfg">mock.100002.12346.cfg</a>
<a href="albs.100002.12346.log">albs.100002.12346.log</a>
</body>
</html>
"""

# What ALBS actually serves today: nginx autoindex with relative hrefs.
SAMPLE_LOG_LISTING_RELATIVE = """
<html>
<head><title>Index of /pulp/content/build_logs/build-70368-build_log/</title></head>
<body bgcolor="white">
<hr><pre><a href="../">../</a>
<a href="./PULP_MANIFEST">PULP_MANIFEST</a>                      11-Aug-2026 08:23  142.7 kB
<a href="./mock_build.441500.1785274367.log">mock_build.441500.1785274367.log</a>  28-Jul-2026 21:34  602 kB
<a href="./mock.441500.1785274367.cfg">mock.441500.1785274367.cfg</a>        28-Jul-2026 21:32  5.4 kB
</pre><hr></body>
</html>
"""


def _mock_response(data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.text = json.dumps(data) if isinstance(data, (dict, list)) else data
    resp.raise_for_status = MagicMock()
    return resp


# ── get_build ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_build(client):
    client._http.get = AsyncMock(return_value=_mock_response(SAMPLE_BUILD))
    build = await client.get_build(12345)
    assert build["id"] == 12345
    assert len(build["tasks"]) == 2
    client._http.get.assert_called_once_with(f"{ALBS_API}/builds/12345/")


@pytest.mark.asyncio
async def test_get_build_tasks_have_correct_statuses(client):
    client._http.get = AsyncMock(return_value=_mock_response(SAMPLE_BUILD))
    build = await client.get_build(12345)
    assert build["tasks"][0]["status"] == 2
    assert build["tasks"][1]["status"] == 3


# ── search_builds ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_builds_default_page(client):
    resp_data = {"builds": [SAMPLE_BUILD], "total_builds": 1, "current_page": 1}
    client._http.get = AsyncMock(return_value=_mock_response(resp_data))
    result = await client.search_builds()
    client._http.get.assert_called_once_with(
        f"{ALBS_API}/builds", params={"pageNumber": 1}
    )
    assert result["total_builds"] == 1


@pytest.mark.asyncio
async def test_search_builds_with_filters(client):
    resp_data = {"builds": [], "total_builds": 0, "current_page": 2}
    client._http.get = AsyncMock(return_value=_mock_response(resp_data))
    await client.search_builds(page=2, project="bash", is_running=True)
    client._http.get.assert_called_once_with(
        f"{ALBS_API}/builds",
        params={"pageNumber": 2, "project": "bash", "is_running": "true"},
    )


# ── get_platforms ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_platforms(client):
    client._http.get = AsyncMock(return_value=_mock_response(SAMPLE_PLATFORMS))
    platforms = await client.get_platforms()
    assert len(platforms) == 3
    assert platforms[0]["name"] == "AlmaLinux-8"
    assert "x86_64" in platforms[0]["arch_list"]


@pytest.mark.asyncio
async def test_get_platform_arches_cached(client):
    client._http.get = AsyncMock(return_value=_mock_response(SAMPLE_PLATFORMS))
    arches1 = await client.get_platform_arches()
    arches2 = await client.get_platform_arches()
    assert arches1 is arches2
    assert client._http.get.call_count == 1


@pytest.mark.asyncio
async def test_get_platform_arches_mapping(client):
    client._http.get = AsyncMock(return_value=_mock_response(SAMPLE_PLATFORMS))
    arches = await client.get_platform_arches()
    assert "AlmaLinux-9" in arches
    assert "aarch64" in arches["AlmaLinux-9"]


# ── get_sign_tasks ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_sign_tasks(client):
    sign_data = [{"id": 1, "build_id": 12345, "status": 3, "sign_key": {"id": 4}}]
    client._http.get = AsyncMock(return_value=_mock_response(sign_data))
    result = await client.get_sign_tasks(12345)
    assert len(result) == 1
    assert result[0]["build_id"] == 12345


# ── list_build_logs ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_build_logs(client):
    resp = _mock_response(None)
    resp.text = SAMPLE_LOG_LISTING
    client._http.get = AsyncMock(return_value=resp)
    logs = await client.list_build_logs(12345)
    assert "mock_build.100002.12346.log" in logs
    assert "mock_stderr.100002.12346.log" in logs
    assert "mock.100002.12346.cfg" in logs
    assert len(logs) == 5


@pytest.mark.asyncio
async def test_list_build_logs_strips_the_relative_prefix(client):
    """Pulp serves hrefs as './name'; a listed name must be readable as-is.

    Without stripping, every returned name carries a path separator and is
    rejected by the _log_path whitelist, so listing a build's logs and then
    reading one fails.
    """
    resp = _mock_response(None)
    resp.text = SAMPLE_LOG_LISTING_RELATIVE
    client._http.get = AsyncMock(return_value=resp)
    logs = await client.list_build_logs(12345)
    assert logs == [
        "mock_build.441500.1785274367.log",
        "mock.441500.1785274367.cfg",
    ]
    for name in logs:
        client._log_path(12345, name)  # must not raise


# ── download_log ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_download_log(client, tmp_log_dir):
    log_content = b"line1\nline2\nline3\nerror: something broke\n"

    class FakeStream:
        def __init__(self):
            self.raise_for_status = MagicMock()
        async def aiter_bytes(self, chunk_size):
            yield log_content
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass

    def _fake_stream(method, url):
        return FakeStream()

    client._http.stream = _fake_stream
    path = await client.download_log(12345, "mock_build.100002.12346.log")
    assert path.exists()
    assert path.read_bytes() == log_content
    assert path.parent.name == "12345"


# ── read_log_tail ─────────────────────────────────────────────────────

def test_read_log_tail(client, tmp_log_dir):
    log_dir = tmp_log_dir / "12345"
    log_dir.mkdir(parents=True)
    log_file = log_dir / "mock_build.log"
    lines = [f"line {i}" for i in range(100)]
    log_file.write_text("\n".join(lines))

    content, total, first, last = client.read_log_tail(12345, "mock_build.log", 10)
    assert total == 100
    assert (first, last) == (91, 100)
    assert "line 99" in content
    assert "line 90" in content
    assert "line 89" not in content


def test_read_log_tail_small_file(client, tmp_log_dir):
    log_dir = tmp_log_dir / "12345"
    log_dir.mkdir(parents=True)
    log_file = log_dir / "small.log"
    log_file.write_text("only one line")

    content, total, first, last = client.read_log_tail(12345, "small.log", 3000)
    assert total == 1
    assert (first, last) == (1, 1)
    assert content == "only one line"


def test_read_log_tail_not_downloaded(client):
    with pytest.raises(FileNotFoundError, match="not downloaded"):
        client.read_log_tail(99999, "nonexistent.log", 10)


# ── path traversal guard (_log_path) ──────────────────────────────────

def test_log_path_allows_normal_filename(client, tmp_log_dir):
    path = client._log_path(12345, "mock_build.300002.222.log")
    assert path.name == "mock_build.300002.222.log"
    assert path.parent.name == "12345"


@pytest.mark.parametrize("evil", [
    "../../etc/passwd",
    "../12346/secret.log",
    "/etc/passwd",
    "subdir/../../escape.log",
    "x%2f..%2f..%2fsecret",   # encoded separator — would traverse on the URL
    "mock\x00.log",            # NUL byte
    "mock build.log",          # space
    "..",
    ".",
    "",
])
def test_log_path_rejects_traversal(client, evil):
    with pytest.raises(ValueError, match="Invalid log filename"):
        client._log_path(12345, evil)


@pytest.mark.asyncio
async def test_download_log_rejects_bad_filename_without_request(client):
    """A filename failing the guard must raise before any HTTP request fires."""
    called = False

    def _fake_stream(method, url):
        nonlocal called
        called = True
        raise AssertionError("HTTP request must not be made for a bad filename")

    client._http.stream = _fake_stream
    with pytest.raises(ValueError, match="Invalid log filename"):
        await client.download_log(12345, "x%2f..%2f..%2fsecret")
    assert called is False


def test_log_path_error_hides_absolute_path(client, tmp_log_dir):
    """The error echoes only the bad filename, not the resolved log directory."""
    try:
        client._log_path(12345, "../../etc/passwd")
    except ValueError as e:
        # The internal absolute log directory must not leak into the message.
        assert str(tmp_log_dir.resolve()) not in str(e)
        assert "../../etc/passwd" in str(e)


# ── read_log_range ────────────────────────────────────────────────────

def test_read_log_range(client, tmp_log_dir):
    log_dir = tmp_log_dir / "12345"
    log_dir.mkdir(parents=True)
    log_file = log_dir / "mock_build.log"
    lines = [f"line {i}" for i in range(100)]
    log_file.write_text("\n".join(lines))

    content, total, last = client.read_log_range(12345, "mock_build.log", 50, 55)
    assert (total, last) == (100, 55)
    assert "line 49" in content
    assert "line 54" in content
    assert "line 55" not in content


def test_read_log_range_clamped(client, tmp_log_dir):
    log_dir = tmp_log_dir / "12345"
    log_dir.mkdir(parents=True)
    log_file = log_dir / "test.log"
    log_file.write_text("a\nb\nc")

    content, total, last = client.read_log_range(12345, "test.log", 1, 9999)
    assert (total, last) == (3, 3)
    assert content == "a\nb\nc"


def test_read_log_range_not_downloaded(client):
    with pytest.raises(FileNotFoundError, match="not downloaded"):
        client.read_log_range(99999, "missing.log", 1, 10)


def test_read_log_range_stops_at_the_char_budget(client, tmp_log_dir):
    log_dir = tmp_log_dir / "12345"
    log_dir.mkdir(parents=True)
    (log_dir / "big.log").write_text("\n".join("x" * 100 for _ in range(100)))

    content, total, last = client.read_log_range(
        12345, "big.log", 1, 100, max_chars=500,
    )
    assert total == 100
    # Stopped early and said where, instead of returning all 10 KB.
    assert last < 100
    assert len(content) <= 700


# ── read_log_tail paging (bottom-up) ──────────────────────────────────

def test_read_log_tail_pages_upward_without_a_gap(client, tmp_log_dir):
    """Feeding `first` back as `before_line` must continue exactly one line up."""
    log_dir = tmp_log_dir / "12345"
    log_dir.mkdir(parents=True)
    (log_dir / "paged.log").write_text(
        "\n".join(f"line {i}" for i in range(1, 101))
    )

    seen: list[int] = []
    before: int | None = None
    for _ in range(10):
        content, total, first, last = client.read_log_tail(
            12345, "paged.log", 15, before_line=before,
        )
        assert total == 100
        if not content:
            break
        seen = list(range(first, last + 1)) + seen
        before = first
        if first == 1:
            break

    # Every line reported exactly once, in order, start to end.
    assert seen == list(range(1, 101))


def test_read_log_tail_before_line_excludes_that_line(client, tmp_log_dir):
    log_dir = tmp_log_dir / "12345"
    log_dir.mkdir(parents=True)
    (log_dir / "paged.log").write_text(
        "\n".join(f"line {i}" for i in range(1, 101))
    )

    content, total, first, last = client.read_log_tail(
        12345, "paged.log", 10, before_line=50,
    )
    assert (total, first, last) == (100, 40, 49)
    assert "line 49" in content
    assert "line 50" not in content


def test_read_log_tail_before_line_1_is_empty(client, tmp_log_dir):
    log_dir = tmp_log_dir / "12345"
    log_dir.mkdir(parents=True)
    (log_dir / "paged.log").write_text("a\nb\nc")

    content, total, first, last = client.read_log_tail(
        12345, "paged.log", 10, before_line=1,
    )
    assert content == ""
    assert total == 3  # the real length is still reported
    assert (first, last) == (0, 0)


def test_read_log_tail_budget_shrinks_the_page_from_the_top(client, tmp_log_dir):
    """The budget, not the line count, decides the page — and keeps the bottom."""
    log_dir = tmp_log_dir / "12345"
    log_dir.mkdir(parents=True)
    (log_dir / "wide.log").write_text("\n".join("y" * 100 for _ in range(50)))

    content, total, first, last = client.read_log_tail(
        12345, "wide.log", 50, max_chars=500,
    )
    assert (total, last) == (50, 50)  # the end of the log is always kept
    assert first > 1                  # ...the top of the page was dropped
    assert len(content) <= 500


def test_read_log_tail_budget_always_returns_one_line(client, tmp_log_dir):
    """A single line longer than the whole budget is still returned."""
    log_dir = tmp_log_dir / "12345"
    log_dir.mkdir(parents=True)
    (log_dir / "one.log").write_text("short\n" + "z" * 400)

    content, _total, first, last = client.read_log_tail(
        12345, "one.log", 50, max_line_chars=0, max_chars=10,
    )
    assert (first, last) == (2, 2)
    assert content == "z" * 400


def test_read_log_tail_budget_off(client, tmp_log_dir):
    log_dir = tmp_log_dir / "12345"
    log_dir.mkdir(parents=True)
    (log_dir / "wide.log").write_text("\n".join("y" * 100 for _ in range(50)))

    _content, _total, first, last = client.read_log_tail(
        12345, "wide.log", 50, max_chars=0,
    )
    assert (first, last) == (1, 50)


# ── clip_line ─────────────────────────────────────────────────────────

def test_clip_line_leaves_short_line_alone():
    assert clip_line("short", 100) == "short"


def test_clip_line_disabled_by_zero_limit():
    long = "x" * 5000
    assert clip_line(long, 0) == long


def test_clip_line_clips_tail_and_reports_the_loss():
    out = clip_line("y" * 700, 500)
    assert out.startswith("y" * 500)
    assert out.endswith("…[200 chars clipped]")


def test_clip_line_keeps_a_deep_match_visible():
    """An error 4000 chars into a gcc command line must survive clipping."""
    line = "gcc " + "-DFLAG " * 570 + "error: boom"
    anchor = line.index("error:")
    out = clip_line(line, 500, anchor)
    assert "error: boom" in out
    assert out.startswith("…[")


# ── search_log ────────────────────────────────────────────────────────

def _write_log(tmp_log_dir, name: str, lines: list[str], build_id: int = 12345):
    log_dir = tmp_log_dir / str(build_id)
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / name).write_text("\n".join(lines))


def test_search_log_finds_the_match_with_context(client, tmp_log_dir):
    _write_log(tmp_log_dir, "mock_build.log", [
        "configuring",
        "compiling foo.c",
        "foo.c:12:5: error: boom",
        "  12 | bad code",
        "     |     ^",
        "unrelated tail",
    ])
    hunks, matches, total, omitted = client.search_log(
        12345, "mock_build.log", r"\berror:", before=1, after=2,
    )
    assert (matches, total, omitted) == (1, 6, 0)
    assert [(n, m) for n, _, m in hunks[0]] == [
        (2, False), (3, True), (4, False), (5, False),
    ]


def test_search_log_reports_no_matches(client, tmp_log_dir):
    _write_log(tmp_log_dir, "mock_root.log", ["all fine", "still fine"])
    hunks, matches, total, omitted = client.search_log(
        12345, "mock_root.log", r"\berror:",
    )
    assert (hunks, matches, total) == ([], 0, 2)


def test_search_log_caps_matches_but_counts_them_all(client, tmp_log_dir):
    _write_log(tmp_log_dir, "many.log", [f"error: {i}" for i in range(50)])
    hunks, matches, total, _ = client.search_log(
        12345, "many.log", r"\berror:", max_matches=3,
    )
    assert matches == 50
    assert total == 50
    assert sum(1 for hunk in hunks for line in hunk if line[2]) == 3


def test_search_log_merges_neighbouring_matches_into_one_hunk(client, tmp_log_dir):
    _write_log(tmp_log_dir, "two.log", [
        "error: first", "middle", "error: second", "after",
    ])
    hunks, matches, _, _ = client.search_log(
        12345, "two.log", r"\berror:", before=2, after=2,
    )
    assert matches == 2
    # One hunk, and the shared "middle" line reported exactly once.
    assert len(hunks) == 1
    assert [n for n, _, _ in hunks[0]] == [1, 2, 3, 4]


def test_search_log_splits_distant_matches_into_separate_hunks(client, tmp_log_dir):
    _write_log(
        tmp_log_dir, "far.log",
        ["error: first"] + ["filler"] * 50 + ["error: second"],
    )
    hunks, matches, _, _ = client.search_log(
        12345, "far.log", r"\berror:", before=1, after=1,
    )
    assert matches == 2
    assert len(hunks) == 2


def test_search_log_drops_boilerplate_from_context_only(client, tmp_log_dir):
    _write_log(tmp_log_dir, "noisy.log", [
        "make[1]: Entering directory '/build'",
        "foo.c:1:1: error: boom",
        "libtool: compile:  gcc -DBIG " + "-DFLAG " * 500,
        "  1 | bad code",
    ])
    hunks, matches, _, omitted = client.search_log(
        12345, "noisy.log", r"\berror:", before=2, after=2,
    )
    assert matches == 1
    assert omitted == 1  # the libtool line, dropped from the after-window
    assert [n for n, _, _ in hunks[0]] == [2, 4]


def test_search_log_keeps_boilerplate_that_itself_matches(client, tmp_log_dir):
    """A noise-shaped line is still reported when it is the match itself."""
    _write_log(tmp_log_dir, "noisy2.log", [
        "make[1]: Entering directory '/build'",
        "make[1]: *** [Makefile:99: all] Error 2",
    ])
    hunks, matches, _, _ = client.search_log(
        12345, "noisy2.log", r"make(\[\d+\])?: \*\*\*",
    )
    assert matches == 1
    assert hunks[0][0][0] == 2


def test_search_log_ignores_a_far_away_before_line(client, tmp_log_dir):
    """Boilerplate filtering must not drag in context from 100 lines back."""
    _write_log(tmp_log_dir, "gap.log", [
        "meaningful setup line",
        *[f"libtool: compile:  gcc -c f{i}.c" for i in range(100)],
        "foo.c:1:1: error: boom",
    ])
    hunks, matches, _, _ = client.search_log(
        12345, "gap.log", r"\berror:", before=2, after=0,
    )
    assert matches == 1
    assert [n for n, _, _ in hunks[0]] == [102]


def test_search_log_clips_the_matched_line(client, tmp_log_dir):
    _write_log(tmp_log_dir, "long.log", ["error: " + "z" * 2000])
    hunks, _, _, _ = client.search_log(
        12345, "long.log", r"\berror:", max_line_chars=200,
    )
    text = hunks[0][0][1]
    assert text.startswith("error: ")
    assert "chars clipped]" in text
    assert len(text) < 300


def test_search_log_not_downloaded(client):
    with pytest.raises(FileNotFoundError, match="not downloaded"):
        client.search_log(99999, "missing.log", "error")


def test_search_log_rejects_traversal(client):
    with pytest.raises(ValueError, match="Invalid log filename"):
        client.search_log(12345, "../../etc/passwd", "error")


# ── auth_headers ──────────────────────────────────────────────────────

def test_auth_headers_with_token(client):
    assert client._auth_headers == {"authorization": "Bearer test-token-123"}


def test_auth_headers_without_token(client_no_token):
    with pytest.raises(PermissionError, match="JWT token required"):
        _ = client_no_token._auth_headers


# ── get_sign_keys ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_sign_keys(client):
    client._http.get = AsyncMock(return_value=_mock_response(SAMPLE_SIGN_KEYS))
    keys = await client.get_sign_keys()
    assert len(keys) == 2
    assert keys[0]["name"] == "AlmaLinux-8"
    assert keys[1]["keyid"] == "D36CB86CB86B3716"


@pytest.mark.asyncio
async def test_get_sign_keys_no_token(client_no_token):
    with pytest.raises(PermissionError):
        await client_no_token.get_sign_keys()


# ── get_flavors ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_flavors(client):
    flavor_data = [{"id": 7, "name": "beta"}, {"id": 8, "name": "EPEL"}]
    client._http.get = AsyncMock(return_value=_mock_response(flavor_data))
    flavors = await client.get_flavors()
    assert flavors == {"beta": 7, "EPEL": 8}


@pytest.mark.asyncio
async def test_get_flavors_no_token(client_no_token):
    with pytest.raises(PermissionError):
        await client_no_token.get_flavors()


# ── create_build ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_build_branch(client):
    client._platforms_cache = {"AlmaLinux-9": ["x86_64", "aarch64"]}
    create_resp = {"id": 99999, "created_at": "2026-03-10T00:00:00"}
    client._http.post = AsyncMock(return_value=_mock_response(create_resp))
    result = await client.create_build(
        packages=[{"bash": "None"}],
        platforms=["AlmaLinux-9"],
        branch="c9s",
    )
    assert result["id"] == 99999
    call_data = client._http.post.call_args[1]["json"]
    assert call_data["platforms"][0]["name"] == "AlmaLinux-9"
    assert call_data["tasks"][0]["git_ref"] == "c9s"
    assert call_data["tasks"][0]["ref_type"] == 1


@pytest.mark.asyncio
async def test_create_build_from_tag(client):
    client._platforms_cache = {"AlmaLinux-9": ["x86_64", "aarch64"]}
    create_resp = {"id": 99998, "created_at": "2026-03-10T00:00:00"}
    client._http.post = AsyncMock(return_value=_mock_response(create_resp))
    result = await client.create_build(
        packages=[{"bash": "imports/c9s/bash-5.1-1.el9"}],
        platforms=["AlmaLinux-9"],
        from_tag=True,
    )
    call_data = client._http.post.call_args[1]["json"]
    assert call_data["tasks"][0]["ref_type"] == 2
    assert call_data["tasks"][0]["git_ref"] == "imports/c9s/bash-5.1-1.el9"


@pytest.mark.asyncio
async def test_create_build_from_srpm(client):
    client._platforms_cache = {"AlmaLinux-9": ["x86_64"]}
    create_resp = {"id": 99997, "created_at": "2026-03-10T00:00:00"}
    client._http.post = AsyncMock(return_value=_mock_response(create_resp))
    await client.create_build(
        packages=[{"https://example.com/pkg.src.rpm": "None"}],
        platforms=["AlmaLinux-9"],
        from_srpm=True,
    )
    call_data = client._http.post.call_args[1]["json"]
    assert call_data["tasks"][0]["ref_type"] == 3
    assert call_data["tasks"][0]["url"] == "https://example.com/pkg.src.rpm"
    assert "git_ref" not in call_data["tasks"][0]


@pytest.mark.asyncio
async def test_create_build_no_branch_or_tag(client):
    client._platforms_cache = {"AlmaLinux-9": ["x86_64"]}
    with pytest.raises(ValueError, match="At least one"):
        await client.create_build(
            packages=[{"bash": "None"}], platforms=["AlmaLinux-9"]
        )


@pytest.mark.asyncio
async def test_create_build_both_branch_and_tag(client):
    client._platforms_cache = {"AlmaLinux-9": ["x86_64"]}
    with pytest.raises(ValueError, match="cannot be used together"):
        await client.create_build(
            packages=[{"bash": "None"}],
            platforms=["AlmaLinux-9"],
            branch="c9s",
            from_tag=True,
        )


@pytest.mark.asyncio
async def test_create_build_bad_platform(client):
    client._platforms_cache = {"AlmaLinux-9": ["x86_64"]}
    with pytest.raises(ValueError, match="Unknown platform"):
        await client.create_build(
            packages=[{"bash": "None"}],
            platforms=["FedoraXYZ"],
            branch="main",
        )


@pytest.mark.asyncio
async def test_create_build_bad_arch(client):
    client._platforms_cache = {"AlmaLinux-9": ["x86_64", "aarch64"]}
    with pytest.raises(ValueError, match="not allowed"):
        await client.create_build(
            packages=[{"bash": "None"}],
            platforms=["AlmaLinux-9"],
            branch="c9s",
            arch_list=["riscv64"],
        )


@pytest.mark.asyncio
async def test_create_build_secureboot_required(client):
    client._platforms_cache = {"AlmaLinux-9": ["x86_64"]}
    with pytest.raises(ValueError, match="secureboot"):
        await client.create_build(
            packages=[{"kernel": "None"}],
            platforms=["AlmaLinux-9"],
            branch="c9s",
        )


@pytest.mark.asyncio
async def test_create_build_secureboot_nosecureboot_override(client):
    client._platforms_cache = {"AlmaLinux-9": ["x86_64"]}
    create_resp = {"id": 99996, "created_at": "2026-03-10T00:00:00"}
    client._http.post = AsyncMock(return_value=_mock_response(create_resp))
    result = await client.create_build(
        packages=[{"kernel": "None"}],
        platforms=["AlmaLinux-9"],
        branch="c9s",
        nosecureboot=True,
    )
    assert result["id"] == 99996


@pytest.mark.asyncio
async def test_create_build_mock_options(client):
    client._platforms_cache = {"AlmaLinux-9": ["x86_64"]}
    create_resp = {"id": 99995, "created_at": "2026-03-10T00:00:00"}
    client._http.post = AsyncMock(return_value=_mock_response(create_resp))
    await client.create_build(
        packages=[{"bash": "None"}],
        platforms=["AlmaLinux-9"],
        branch="c9s",
        excludes=["pkg1", "pkg2"],
        definitions={"dist": ".el9"},
        with_opts=["tests"],
        without_opts=["docs"],
        modules=["nodejs:18"],
    )
    call_data = client._http.post.call_args[1]["json"]
    mock_opts = call_data["mock_options"]
    assert mock_opts["yum_exclude"] == ["pkg1", "pkg2"]
    assert mock_opts["definitions"] == {"dist": ".el9"}
    assert mock_opts["with"] == ["tests"]
    assert mock_opts["without"] == ["docs"]
    assert mock_opts["module_enable"] == ["nodejs:18"]


@pytest.mark.asyncio
async def test_create_build_linked_builds(client):
    client._platforms_cache = {"AlmaLinux-9": ["x86_64"]}
    create_resp = {"id": 99994, "created_at": "2026-03-10T00:00:00"}
    client._http.post = AsyncMock(return_value=_mock_response(create_resp))
    await client.create_build(
        packages=[{"bash": "None"}],
        platforms=["AlmaLinux-9"],
        branch="c9s",
        linked_builds=[100, 200],
    )
    call_data = client._http.post.call_args[1]["json"]
    assert call_data["linked_builds"] == [100, 200]


# ── create_build: beta flavor ─────────────────────────────────────────

SAMPLE_FLAVORS = [
    {"id": 7, "name": "AlmaLinux-9-beta"},
    {"id": 8, "name": "AlmaLinux-8-beta"},
    {"id": 51, "name": "AlmaLinux-10-beta"},
    {"id": 3, "name": "EPEL"},
]


@pytest.mark.asyncio
async def test_create_build_beta_adds_flavor_id(client):
    client._platforms_cache = {"AlmaLinux-9": ["x86_64"]}
    client._http.get = AsyncMock(return_value=_mock_response(SAMPLE_FLAVORS))
    client._http.post = AsyncMock(
        return_value=_mock_response({"id": 1, "created_at": "x"})
    )
    await client.create_build(
        packages=[{"bash": "None"}],
        platforms=["AlmaLinux-9"],
        branch="c9s",
        beta=True,
    )
    call_data = client._http.post.call_args[1]["json"]
    assert call_data["platform_flavors"] == [7]


@pytest.mark.asyncio
async def test_create_build_beta_multi_platform(client):
    client._platforms_cache = {
        "AlmaLinux-8": ["x86_64"],
        "AlmaLinux-9": ["x86_64"],
    }
    client._http.get = AsyncMock(return_value=_mock_response(SAMPLE_FLAVORS))
    client._http.post = AsyncMock(
        return_value=_mock_response({"id": 1, "created_at": "x"})
    )
    await client.create_build(
        packages=[{"bash": "None"}],
        platforms=["AlmaLinux-8", "AlmaLinux-9"],
        branch="c9s",
        beta=True,
    )
    call_data = client._http.post.call_args[1]["json"]
    # both beta flavor IDs included, in platform order
    assert call_data["platform_flavors"] == [8, 7]


@pytest.mark.asyncio
async def test_create_build_beta_combined_with_additional_flavors(client):
    client._platforms_cache = {"AlmaLinux-9": ["x86_64"]}
    client._http.get = AsyncMock(return_value=_mock_response(SAMPLE_FLAVORS))
    client._http.post = AsyncMock(
        return_value=_mock_response({"id": 1, "created_at": "x"})
    )
    await client.create_build(
        packages=[{"bash": "None"}],
        platforms=["AlmaLinux-9"],
        branch="c9s",
        beta=True,
        additional_flavors=["EPEL"],
    )
    call_data = client._http.post.call_args[1]["json"]
    assert call_data["platform_flavors"] == [7, 3]


@pytest.mark.asyncio
async def test_create_build_beta_dedupes_with_additional_flavors(client):
    client._platforms_cache = {"AlmaLinux-9": ["x86_64"]}
    client._http.get = AsyncMock(return_value=_mock_response(SAMPLE_FLAVORS))
    client._http.post = AsyncMock(
        return_value=_mock_response({"id": 1, "created_at": "x"})
    )
    await client.create_build(
        packages=[{"bash": "None"}],
        platforms=["AlmaLinux-9"],
        branch="c9s",
        beta=True,
        additional_flavors=["AlmaLinux-9-beta"],
    )
    call_data = client._http.post.call_args[1]["json"]
    assert call_data["platform_flavors"] == [7]


@pytest.mark.asyncio
async def test_create_build_beta_kitten_not_supported(client):
    """Kitten-10 has no beta flavor — beta=True must be rejected for it."""
    client._platforms_cache = {"AlmaLinux-Kitten-10": ["x86_64", "riscv64"]}
    client._http.get = AsyncMock(return_value=_mock_response(SAMPLE_FLAVORS))
    client._http.post = AsyncMock(
        return_value=_mock_response({"id": 1, "created_at": "x"})
    )
    with pytest.raises(ValueError, match="not supported for platform"):
        await client.create_build(
            packages=[{"bash": "None"}],
            platforms=["AlmaLinux-Kitten-10"],
            branch="kitten",
            beta=True,
        )
    client._http.post.assert_not_called()


@pytest.mark.asyncio
async def test_create_build_beta_unsupported_platform_raises(client):
    """Platforms missing from BETA_PLATFORM_FLAVORS must raise BEFORE any HTTP call."""
    client._platforms_cache = {"CentOS7": ["x86_64"]}
    client._http.get = AsyncMock(return_value=_mock_response(SAMPLE_FLAVORS))
    client._http.post = AsyncMock(
        return_value=_mock_response({"id": 1, "created_at": "x"})
    )
    with pytest.raises(ValueError, match="not supported for platform"):
        await client.create_build(
            packages=[{"bash": "None"}],
            platforms=["CentOS7"],
            branch="main",
            beta=True,
        )
    client._http.post.assert_not_called()


@pytest.mark.asyncio
async def test_create_build_beta_stale_constant_raises(client):
    """If BETA_PLATFORM_FLAVORS lists a name that ALBS no longer has, raise loudly."""
    client._platforms_cache = {"AlmaLinux-9": ["x86_64"]}
    # ALBS returns a flavor list missing AlmaLinux-9-beta → stale constant
    flavors_without_target = [{"id": 99, "name": "AlmaLinux-10-beta"}]
    client._http.get = AsyncMock(
        return_value=_mock_response(flavors_without_target)
    )
    client._http.post = AsyncMock(
        return_value=_mock_response({"id": 1, "created_at": "x"})
    )
    with pytest.raises(ValueError, match="stale"):
        await client.create_build(
            packages=[{"bash": "None"}],
            platforms=["AlmaLinux-9"],
            branch="c9s",
            beta=True,
        )
    client._http.post.assert_not_called()


@pytest.mark.asyncio
async def test_create_build_no_beta_no_flavor_fetch(client):
    """When beta=False and no additional_flavors, get_flavors must not be called."""
    client._platforms_cache = {"AlmaLinux-9": ["x86_64"]}
    client._http.get = AsyncMock()  # would fail if called (no return_value mock)
    client._http.post = AsyncMock(
        return_value=_mock_response({"id": 1, "created_at": "x"})
    )
    await client.create_build(
        packages=[{"bash": "None"}],
        platforms=["AlmaLinux-9"],
        branch="c9s",
    )
    client._http.get.assert_not_called()
    call_data = client._http.post.call_args[1]["json"]
    assert "platform_flavors" not in call_data


# ── extract_el_version ────────────────────────────────────────────────

def test_extract_el_version_from_tag():
    assert extract_el_version("imports/c9s/bash-5.1-1.el9") == ".el9"


def test_extract_el_version_from_tag_with_suffix():
    assert extract_el_version("imports/c10s/ipa-healthcheck-0.16-5.el10") == ".el10"


def test_extract_el_version_from_srpm_url():
    url = "https://dl.fedoraproject.org/pub/epel/10/Everything/source/tree/Packages/p/pkg-1.0-1.el10.src.rpm"
    assert extract_el_version(url) == ".el10"


def test_extract_el_version_from_srpm_url_el10_3():
    url = "https://dl.fedoraproject.org/pub/epel/10/Everything/source/tree/Packages/p/pkg-2.0-3.el10_3.src.rpm"
    assert extract_el_version(url) == ".el10_3"


def test_extract_el_version_from_srpm_url_el10_0():
    url = "https://dl.fedoraproject.org/pub/epel/10/Everything/source/tree/Packages/p/pkg-1.5-1.el10_0.src.rpm"
    assert extract_el_version(url) == ".el10_0"


def test_extract_el_version_no_match():
    assert extract_el_version("some-package-without-el") is None


def test_extract_el_version_el8():
    assert extract_el_version("pkg-1.0-1.el8_9") == ".el8_9"


# ── create_build: add_epel_dist ──────────────────────────────────────

# ── create_build: custom Git URLs ─────────────────────────────────────

@pytest.mark.asyncio
async def test_create_build_custom_git_url_branch(client):
    """Custom Git URL is used as-is instead of git.almalinux.org prefix."""
    client._platforms_cache = {"AlmaLinux-10": ["x86_64", "aarch64"]}
    create_resp = {"id": 77777, "created_at": "2026-04-16T00:00:00"}
    client._http.post = AsyncMock(return_value=_mock_response(create_resp))
    await client.create_build(
        packages=[{"https://github.com/ykohut/leapp-data.git": "None"}],
        platforms=["AlmaLinux-10"],
        branch="devel-ng-0.23.0",
    )
    call_data = client._http.post.call_args[1]["json"]
    task = call_data["tasks"][0]
    assert task["url"] == "https://github.com/ykohut/leapp-data.git"
    assert task["ref_type"] == 1
    assert task["git_ref"] == "devel-ng-0.23.0"


@pytest.mark.asyncio
async def test_create_build_custom_git_url_from_tag(client):
    """Custom Git URL with from_tag uses the URL as-is."""
    client._platforms_cache = {"AlmaLinux-10": ["x86_64"]}
    create_resp = {"id": 77776, "created_at": "2026-04-16T00:00:00"}
    client._http.post = AsyncMock(return_value=_mock_response(create_resp))
    await client.create_build(
        packages=[{"https://github.com/ykohut/leapp-data.git": "v0.23.0"}],
        platforms=["AlmaLinux-10"],
        from_tag=True,
    )
    call_data = client._http.post.call_args[1]["json"]
    task = call_data["tasks"][0]
    assert task["url"] == "https://github.com/ykohut/leapp-data.git"
    assert task["ref_type"] == 2
    assert task["git_ref"] == "v0.23.0"


@pytest.mark.asyncio
async def test_create_build_mixed_packages_and_git_urls(client):
    """Regular package and custom Git URL in the same build."""
    client._platforms_cache = {"AlmaLinux-10": ["x86_64"]}
    create_resp = {"id": 77775, "created_at": "2026-04-16T00:00:00"}
    client._http.post = AsyncMock(return_value=_mock_response(create_resp))
    await client.create_build(
        packages=[
            {"bash": "None"},
            {"https://github.com/ykohut/leapp-data.git": "None"},
        ],
        platforms=["AlmaLinux-10"],
        branch="c10s",
    )
    call_data = client._http.post.call_args[1]["json"]
    tasks = call_data["tasks"]
    assert tasks[0]["url"] == "https://git.almalinux.org/rpms/bash.git"
    assert tasks[1]["url"] == "https://github.com/ykohut/leapp-data.git"


@pytest.mark.asyncio
async def test_create_build_add_epel_dist_from_srpm(client):
    client._platforms_cache = {"AlmaLinux-10": ["x86_64_v2"]}
    create_resp = {"id": 88888, "created_at": "2026-03-10T00:00:00"}
    client._http.post = AsyncMock(return_value=_mock_response(create_resp))
    url = "https://dl.fedoraproject.org/pub/epel/10/Everything/source/tree/Packages/p/pkg-1.0-1.el10.src.rpm"
    await client.create_build(
        packages=[{url: "None"}],
        platforms=["AlmaLinux-10"],
        from_srpm=True,
        add_epel_dist=True,
    )
    call_data = client._http.post.call_args[1]["json"]
    task = call_data["tasks"][0]
    assert task["mock_options"] == {"definitions": {"dist": ".el10.alma_altarch"}}


@pytest.mark.asyncio
async def test_create_build_add_epel_dist_from_tag(client):
    client._platforms_cache = {"AlmaLinux-9": ["x86_64"]}
    create_resp = {"id": 88887, "created_at": "2026-03-10T00:00:00"}
    client._http.post = AsyncMock(return_value=_mock_response(create_resp))
    await client.create_build(
        packages=[{"imports/c9s/bash-5.1-1.el9": "imports/c9s/bash-5.1-1.el9"}],
        platforms=["AlmaLinux-9"],
        from_tag=True,
        add_epel_dist=True,
    )
    call_data = client._http.post.call_args[1]["json"]
    task = call_data["tasks"][0]
    assert task["mock_options"] == {"definitions": {"dist": ".el9.alma_altarch"}}


@pytest.mark.asyncio
async def test_create_build_add_epel_dist_no_el_version(client):
    """If dist suffix can't be extracted, no mock_options added."""
    client._platforms_cache = {"AlmaLinux-9": ["x86_64"]}
    create_resp = {"id": 88886, "created_at": "2026-03-10T00:00:00"}
    client._http.post = AsyncMock(return_value=_mock_response(create_resp))
    await client.create_build(
        packages=[{"https://example.com/pkg-nodist.src.rpm": "None"}],
        platforms=["AlmaLinux-9"],
        from_srpm=True,
        add_epel_dist=True,
    )
    call_data = client._http.post.call_args[1]["json"]
    task = call_data["tasks"][0]
    assert "mock_options" not in task


@pytest.mark.asyncio
async def test_create_build_add_epel_dist_ignored_for_branch(client):
    """add_epel_dist has no effect for branch builds."""
    client._platforms_cache = {"AlmaLinux-9": ["x86_64"]}
    create_resp = {"id": 88885, "created_at": "2026-03-10T00:00:00"}
    client._http.post = AsyncMock(return_value=_mock_response(create_resp))
    await client.create_build(
        packages=[{"bash": "None"}],
        platforms=["AlmaLinux-9"],
        branch="c9s",
        add_epel_dist=True,
    )
    call_data = client._http.post.call_args[1]["json"]
    task = call_data["tasks"][0]
    assert "mock_options" not in task


# ── create_build: multiple platforms ──────────────────────────────────

@pytest.mark.asyncio
async def test_create_build_multiple_platforms(client):
    """Build on two platforms produces two platform entries in the payload."""
    client._platforms_cache = {
        "AlmaLinux-8": ["x86_64", "aarch64"],
        "AlmaLinux-9": ["x86_64", "aarch64", "s390x"],
    }
    create_resp = {"id": 77770, "created_at": "2026-04-16T00:00:00"}
    client._http.post = AsyncMock(return_value=_mock_response(create_resp))
    result = await client.create_build(
        packages=[{"bash": "None"}],
        platforms=["AlmaLinux-8", "AlmaLinux-9"],
        branch="c9s",
    )
    assert result["id"] == 77770
    call_data = client._http.post.call_args[1]["json"]
    plat_names = [p["name"] for p in call_data["platforms"]]
    assert plat_names == ["AlmaLinux-8", "AlmaLinux-9"]
    assert call_data["platforms"][0]["arch_list"] == ["x86_64", "aarch64"]
    assert call_data["platforms"][1]["arch_list"] == ["x86_64", "aarch64", "s390x"]


@pytest.mark.asyncio
async def test_create_build_multiple_platforms_with_arch_list(client):
    """Explicit arch_list is validated against each platform."""
    client._platforms_cache = {
        "AlmaLinux-8": ["x86_64", "aarch64"],
        "AlmaLinux-9": ["x86_64", "aarch64", "s390x"],
    }
    create_resp = {"id": 77769, "created_at": "2026-04-16T00:00:00"}
    client._http.post = AsyncMock(return_value=_mock_response(create_resp))
    await client.create_build(
        packages=[{"bash": "None"}],
        platforms=["AlmaLinux-8", "AlmaLinux-9"],
        branch="c9s",
        arch_list=["x86_64"],
    )
    call_data = client._http.post.call_args[1]["json"]
    assert call_data["platforms"][0]["arch_list"] == ["x86_64"]
    assert call_data["platforms"][1]["arch_list"] == ["x86_64"]


@pytest.mark.asyncio
async def test_create_build_multiple_platforms_bad_arch(client):
    """Arch not allowed on one platform raises error for that platform."""
    client._platforms_cache = {
        "AlmaLinux-8": ["x86_64", "aarch64"],
        "AlmaLinux-9": ["x86_64", "aarch64", "s390x"],
    }
    with pytest.raises(ValueError, match="not allowed for AlmaLinux-8"):
        await client.create_build(
            packages=[{"bash": "None"}],
            platforms=["AlmaLinux-8", "AlmaLinux-9"],
            branch="c9s",
            arch_list=["s390x"],
        )


# ── create_build: independent_tasks ───────────────────────────────────

@pytest.mark.asyncio
async def test_create_build_independent_tasks_default_false(client):
    """By default, independent_tasks=False is sent per platform entry."""
    client._platforms_cache = {"AlmaLinux-9": ["x86_64"]}
    client._http.post = AsyncMock(
        return_value=_mock_response({"id": 1, "created_at": "x"})
    )
    await client.create_build(
        packages=[{"bash": "None"}],
        platforms=["AlmaLinux-9"],
        branch="c9s",
    )
    call_data = client._http.post.call_args[1]["json"]
    assert call_data["platforms"][0]["independent_tasks"] is False


@pytest.mark.asyncio
async def test_create_build_independent_tasks_true(client):
    """independent_tasks=True is propagated to every platform entry."""
    client._platforms_cache = {
        "AlmaLinux-8": ["x86_64"],
        "AlmaLinux-9": ["x86_64"],
    }
    client._http.post = AsyncMock(
        return_value=_mock_response({"id": 1, "created_at": "x"})
    )
    await client.create_build(
        packages=[{"bash": "None"}],
        platforms=["AlmaLinux-8", "AlmaLinux-9"],
        branch="c9s",
        independent_tasks=True,
    )
    call_data = client._http.post.call_args[1]["json"]
    for plat in call_data["platforms"]:
        assert plat["independent_tasks"] is True


# ── sign_build ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sign_build(client):
    sign_resp = {"id": 555, "build_id": 12345, "status": 1}
    client._http.post = AsyncMock(return_value=_mock_response(sign_resp))
    result = await client.sign_build(12345, sign_key_id=4)
    assert result["id"] == 555
    call_data = client._http.post.call_args[1]["json"]
    assert call_data == {"build_id": 12345, "sign_key_id": 4}


@pytest.mark.asyncio
async def test_sign_build_no_token(client_no_token):
    with pytest.raises(PermissionError):
        await client_no_token.sign_build(12345)


# ── log_path helpers ──────────────────────────────────────────────────

def test_log_base_url(client):
    assert client._log_base_url(12345) == (
        "https://build.almalinux.org/pulp/content/build_logs/build-12345-build_log"
    )


def test_log_path(client, tmp_log_dir):
    path = client._log_path(12345, "test.log")
    assert path == tmp_log_dir / "12345" / "test.log"
    assert path.parent.exists()


# ── get_platform_ids ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_platform_ids_mapping(client):
    client._http.get = AsyncMock(return_value=_mock_response(SAMPLE_PLATFORMS))
    ids = await client.get_platform_ids()
    assert ids == {"AlmaLinux-8": 1, "AlmaLinux-9": 2, "AlmaLinux-10": 3}


@pytest.mark.asyncio
async def test_get_platform_ids_cached(client):
    client._http.get = AsyncMock(return_value=_mock_response(SAMPLE_PLATFORMS))
    ids1 = await client.get_platform_ids()
    ids2 = await client.get_platform_ids()
    assert ids1 is ids2
    assert client._http.get.call_count == 1


# ── get_products / get_product_ids ────────────────────────────────────

SAMPLE_PRODUCTS = [
    {"id": 1, "name": "AlmaLinux", "is_community": False,
     "platforms": [{"name": "AlmaLinux-8"}, {"name": "AlmaLinux-9"}]},
    {"id": 613, "name": "epel-al", "is_community": True,
     "platforms": [{"name": "AlmaLinux-10"}]},
]


@pytest.mark.asyncio
async def test_get_products_plain_list(client):
    client._http.get = AsyncMock(return_value=_mock_response(SAMPLE_PRODUCTS))
    products = await client.get_products()
    assert len(products) == 2
    assert products[0]["name"] == "AlmaLinux"
    client._http.get.assert_called_once_with(f"{ALBS_API}/products/")


@pytest.mark.asyncio
async def test_get_products_wrapped_form(client):
    """A {'products': [...]} response is unwrapped to the inner list."""
    client._http.get = AsyncMock(
        return_value=_mock_response({"products": SAMPLE_PRODUCTS})
    )
    products = await client.get_products()
    assert [p["name"] for p in products] == ["AlmaLinux", "epel-al"]


@pytest.mark.asyncio
async def test_get_product_ids_mapping_and_cache(client):
    client._http.get = AsyncMock(return_value=_mock_response(SAMPLE_PRODUCTS))
    ids1 = await client.get_product_ids()
    ids2 = await client.get_product_ids()
    assert ids1 == {"AlmaLinux": 1, "epel-al": 613}
    assert ids1 is ids2
    assert client._http.get.call_count == 1


# ── get_release ───────────────────────────────────────────────────────

SAMPLE_RELEASE = {
    "id": 39229,
    "status": 1,
    "build_ids": [62316],
    "build_task_ids": [1, 2, 3],
    "plan": {
        "packages": [
            {"package": {"name": "openscap", "version": "1.3.14",
                         "release": "1.el8", "arch": "src"}},
            {"package": {"name": "openscap", "version": "1.3.14",
                         "release": "1.el8", "arch": "x86_64"}},
        ],
        "repositories": [
            {"id": 1, "name": "almalinux-8-appstream", "arch": "src"},
        ],
    },
    "product": {"name": "AlmaLinux"},
    "platform": {"name": "AlmaLinux-8"},
    "created_at": "2026-06-17T13:04:24",
}


@pytest.mark.asyncio
async def test_get_release(client):
    client._http.get = AsyncMock(return_value=_mock_response(SAMPLE_RELEASE))
    release = await client.get_release(39229)
    assert release["id"] == 39229
    assert release["status"] == 1
    client._http.get.assert_called_once_with(f"{ALBS_API}/releases/39229/")


# ── create_release ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_release_payload(client):
    client._http.post = AsyncMock(
        return_value=_mock_response({"id": 555, "status": 1, "plan": {}})
    )
    result = await client.create_release(
        build_ids=[62316],
        build_task_ids=[1, 2, 3],
        platform_id=1,
        product_id=4,
    )
    assert result["id"] == 555
    args, kwargs = client._http.post.call_args
    assert args[0] == f"{ALBS_API}/releases/new/"
    payload = kwargs["json"]
    assert payload == {
        "builds": [62316],
        "build_tasks": [1, 2, 3],
        "platform_id": 1,
        "product_id": 4,
    }
    assert kwargs["headers"] == {"authorization": "Bearer test-token-123"}


@pytest.mark.asyncio
async def test_create_release_no_token(client_no_token):
    with pytest.raises(PermissionError):
        await client_no_token.create_release(
            build_ids=[1], build_task_ids=[1], platform_id=1, product_id=1
        )


# ── completed task helpers ────────────────────────────────────────────

def test_get_completed_task_ids():
    info = {
        "tasks": [
            {"id": 1, "status": 2, "ref": {"url": "u/a"}},
            {"id": 2, "status": 3, "ref": {"url": "u/a"}},
            {"id": 3, "status": 2, "ref": {"url": "u/b"}},
        ]
    }
    assert get_completed_task_ids(info) == [1, 3]


def test_get_completed_task_ids_empty():
    assert get_completed_task_ids({"tasks": []}) == []


def test_get_whole_package_task_ids_drops_partial():
    """A package with any non-completed arch task is dropped entirely."""
    info = {
        "tasks": [
            # pkg-a: both arch tasks completed → both kept
            {"id": 1, "status": 2, "ref": {"url": "git/pkg-a.git"}},
            {"id": 2, "status": 2, "ref": {"url": "git/pkg-a.git"}},
            # pkg-b: one failed → whole package dropped
            {"id": 3, "status": 2, "ref": {"url": "git/pkg-b.git"}},
            {"id": 4, "status": 3, "ref": {"url": "git/pkg-b.git"}},
        ]
    }
    assert sorted(get_whole_package_task_ids(info)) == [1, 2]


def test_get_whole_package_task_ids_all_complete():
    info = {
        "tasks": [
            {"id": 1, "status": 2, "ref": {"url": "git/pkg-a.git"}},
            {"id": 2, "status": 2, "ref": {"url": "git/pkg-b.git"}},
        ]
    }
    assert sorted(get_whole_package_task_ids(info)) == [1, 2]
