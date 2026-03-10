"""Unit tests for ALBSClient with mocked HTTP responses."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from albs_mcp.client import ALBSClient
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

    content, total, from_line = client.read_log_tail(12345, "mock_build.log", 10)
    assert total == 100
    assert from_line == 91
    assert "line 99" in content
    assert "line 90" in content
    assert "line 89" not in content


def test_read_log_tail_small_file(client, tmp_log_dir):
    log_dir = tmp_log_dir / "12345"
    log_dir.mkdir(parents=True)
    log_file = log_dir / "small.log"
    log_file.write_text("only one line")

    content, total, from_line = client.read_log_tail(12345, "small.log", 3000)
    assert total == 1
    assert from_line == 1
    assert content == "only one line"


def test_read_log_tail_not_downloaded(client):
    with pytest.raises(FileNotFoundError, match="not downloaded"):
        client.read_log_tail(99999, "nonexistent.log", 10)


# ── read_log_range ────────────────────────────────────────────────────

def test_read_log_range(client, tmp_log_dir):
    log_dir = tmp_log_dir / "12345"
    log_dir.mkdir(parents=True)
    log_file = log_dir / "mock_build.log"
    lines = [f"line {i}" for i in range(100)]
    log_file.write_text("\n".join(lines))

    content, total = client.read_log_range(12345, "mock_build.log", 50, 55)
    assert total == 100
    assert "line 49" in content
    assert "line 54" in content
    assert "line 55" not in content


def test_read_log_range_clamped(client, tmp_log_dir):
    log_dir = tmp_log_dir / "12345"
    log_dir.mkdir(parents=True)
    log_file = log_dir / "test.log"
    log_file.write_text("a\nb\nc")

    content, total = client.read_log_range(12345, "test.log", 1, 9999)
    assert total == 3
    assert content == "a\nb\nc"


def test_read_log_range_not_downloaded(client):
    with pytest.raises(FileNotFoundError, match="not downloaded"):
        client.read_log_range(99999, "missing.log", 1, 10)


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
        platform="AlmaLinux-9",
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
        platform="AlmaLinux-9",
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
        platform="AlmaLinux-9",
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
            packages=[{"bash": "None"}], platform="AlmaLinux-9"
        )


@pytest.mark.asyncio
async def test_create_build_both_branch_and_tag(client):
    client._platforms_cache = {"AlmaLinux-9": ["x86_64"]}
    with pytest.raises(ValueError, match="cannot be used together"):
        await client.create_build(
            packages=[{"bash": "None"}],
            platform="AlmaLinux-9",
            branch="c9s",
            from_tag=True,
        )


@pytest.mark.asyncio
async def test_create_build_bad_platform(client):
    client._platforms_cache = {"AlmaLinux-9": ["x86_64"]}
    with pytest.raises(ValueError, match="Unknown platform"):
        await client.create_build(
            packages=[{"bash": "None"}],
            platform="FedoraXYZ",
            branch="main",
        )


@pytest.mark.asyncio
async def test_create_build_bad_arch(client):
    client._platforms_cache = {"AlmaLinux-9": ["x86_64", "aarch64"]}
    with pytest.raises(ValueError, match="not allowed"):
        await client.create_build(
            packages=[{"bash": "None"}],
            platform="AlmaLinux-9",
            branch="c9s",
            arch_list=["riscv64"],
        )


@pytest.mark.asyncio
async def test_create_build_secureboot_required(client):
    client._platforms_cache = {"AlmaLinux-9": ["x86_64"]}
    with pytest.raises(ValueError, match="secureboot"):
        await client.create_build(
            packages=[{"kernel": "None"}],
            platform="AlmaLinux-9",
            branch="c9s",
        )


@pytest.mark.asyncio
async def test_create_build_secureboot_nosecureboot_override(client):
    client._platforms_cache = {"AlmaLinux-9": ["x86_64"]}
    create_resp = {"id": 99996, "created_at": "2026-03-10T00:00:00"}
    client._http.post = AsyncMock(return_value=_mock_response(create_resp))
    result = await client.create_build(
        packages=[{"kernel": "None"}],
        platform="AlmaLinux-9",
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
        platform="AlmaLinux-9",
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
        platform="AlmaLinux-9",
        branch="c9s",
        linked_builds=[100, 200],
    )
    call_data = client._http.post.call_args[1]["json"]
    assert call_data["linked_builds"] == [100, 200]


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
