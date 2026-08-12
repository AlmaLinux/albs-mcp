"""Unit tests for the CLI interface with mocked command functions."""
from __future__ import annotations

import os
import sys
from io import StringIO
from unittest.mock import AsyncMock, patch

import pytest

from albs_mcp.cli import build_parser, _init
from albs_mcp.constants import (
    LOG_MAX_LINE_CHARS,
    LOG_MAX_RESULT_CHARS,
    LOG_SEARCH_AFTER,
    LOG_SEARCH_BEFORE,
    LOG_SEARCH_MAX_MATCHES,
)


@pytest.fixture(autouse=True)
def _restore_env():
    """_init writes ALBS_* straight into os.environ (outside monkeypatch);
    snapshot and restore so CLI tests don't leak env vars into other test
    files (e.g. the integration client picking up a bogus ALBS_LOG_DIR)."""
    keys = ("ALBS_LOG_DIR", "ALBS_JWT_TOKEN")
    saved = {k: os.environ.get(k) for k in keys}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _invoke(args: list[str]) -> tuple[int, str]:
    """Run the CLI with the given args, return (exit_code, stdout+stderr)."""
    parser = build_parser()
    parsed = parser.parse_args(args)
    if not hasattr(parsed, "func"):
        return 1, ""
    _init(parsed)
    buf = StringIO()
    err_buf = StringIO()
    try:
        with patch("sys.stdout", buf), patch("sys.stderr", err_buf):
            parsed.func(parsed)
        return 0, buf.getvalue()
    except SystemExit as e:
        return e.code or 0, buf.getvalue() + err_buf.getvalue()


# ── help ──────────────────────────────────────────────────────────────


def test_cli_no_command():
    code, _ = _invoke([])
    assert code == 1


def test_parser_has_subcommands():
    parser = build_parser()
    for action in parser._subparsers._group_actions:
        if hasattr(action, "choices") and action.choices:
            choices = list(action.choices.keys())
            break
    else:
        choices = []
    assert "platforms" in choices
    assert "build-info" in choices
    assert "failed-tasks" in choices
    assert "search" in choices
    assert "create-build" in choices
    assert "sign-build" in choices


# ── platforms ─────────────────────────────────────────────────────────


def test_platforms():
    with patch("albs_mcp._commands.get_platforms", new_callable=AsyncMock) as mock:
        mock.return_value = "Platforms (2):\n  AlmaLinux-9  arches: x86_64"
        code, out = _invoke(["platforms"])
    assert code == 0
    assert "AlmaLinux-9" in out
    mock.assert_awaited_once()


# ── build-info ────────────────────────────────────────────────────────


def test_build_info():
    with patch("albs_mcp._commands.get_build_info", new_callable=AsyncMock) as mock:
        mock.return_value = "Build #50000\nPlatform: AlmaLinux-9"
        code, out = _invoke(["build-info", "50000"])
    assert code == 0
    assert "Build #50000" in out
    mock.assert_awaited_once_with(50000)


def test_build_info_missing_arg():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["build-info"])


# ── failed-tasks ──────────────────────────────────────────────────────


def test_failed_tasks():
    with patch("albs_mcp._commands.get_failed_tasks", new_callable=AsyncMock) as mock:
        mock.return_value = "Build #50000: 2 failed task(s)"
        code, out = _invoke(["failed-tasks", "50000"])
    assert code == 0
    assert "2 failed task(s)" in out
    mock.assert_awaited_once_with(50000)


# ── build-logs ────────────────────────────────────────────────────────


def test_build_logs():
    with patch("albs_mcp._commands.list_build_logs", new_callable=AsyncMock) as mock:
        mock.return_value = "Build #50000: 3 log file(s)"
        code, out = _invoke(["build-logs", "50000"])
    assert code == 0
    assert "3 log file(s)" in out


# ── download-log ──────────────────────────────────────────────────────


def test_download_log():
    with patch("albs_mcp._commands.download_log", new_callable=AsyncMock) as mock:
        mock.return_value = "Downloaded: /tmp/albs-logs/50000/mock_build.log"
        code, out = _invoke(["download-log", "50000", "mock_build.log"])
    assert code == 0
    assert "Downloaded:" in out
    mock.assert_awaited_once_with(50000, "mock_build.log")


# ── log-tail ──────────────────────────────────────────────────────────


def test_log_tail_default():
    with patch("albs_mcp._commands.read_log_tail", new_callable=AsyncMock) as mock:
        mock.return_value = "=== mock.log | lines 1-100 of 100 ==="
        code, out = _invoke(["log-tail", "50000", "mock.log"])
    assert code == 0
    mock.assert_awaited_once_with(
        50000, "mock.log", 3000, LOG_MAX_LINE_CHARS, None, LOG_MAX_RESULT_CHARS,
    )


def test_log_tail_custom_lines():
    with patch("albs_mcp._commands.read_log_tail", new_callable=AsyncMock) as mock:
        mock.return_value = "tail output"
        code, out = _invoke(["log-tail", "50000", "mock.log", "-n", "500"])
    assert code == 0
    mock.assert_awaited_once_with(
        50000, "mock.log", 500, LOG_MAX_LINE_CHARS, None, LOG_MAX_RESULT_CHARS,
    )


def test_log_tail_pages_upward():
    with patch("albs_mcp._commands.read_log_tail", new_callable=AsyncMock) as mock:
        mock.return_value = "page 2"
        code, out = _invoke([
            "log-tail", "50000", "mock.log", "--before-line", "772",
        ])
    assert code == 0
    mock.assert_awaited_once_with(
        50000, "mock.log", 3000, LOG_MAX_LINE_CHARS, 772, LOG_MAX_RESULT_CHARS,
    )


def test_log_tail_budget_can_be_lifted():
    with patch("albs_mcp._commands.read_log_tail", new_callable=AsyncMock) as mock:
        mock.return_value = "everything"
        code, out = _invoke([
            "log-tail", "50000", "mock.log", "--max-chars", "0",
        ])
    assert code == 0
    mock.assert_awaited_once_with(
        50000, "mock.log", 3000, LOG_MAX_LINE_CHARS, None, 0,
    )


# ── log-range ─────────────────────────────────────────────────────────


def test_log_range():
    with patch("albs_mcp._commands.read_log_range", new_callable=AsyncMock) as mock:
        mock.return_value = "=== mock.log | lines 50-100 of 5000 ==="
        code, out = _invoke(["log-range", "50000", "mock.log", "50", "100"])
    assert code == 0
    mock.assert_awaited_once_with(
        50000, "mock.log", 50, 100, LOG_MAX_LINE_CHARS, LOG_MAX_RESULT_CHARS,
    )


# ── log-search ────────────────────────────────────────────────────────


def test_log_search_defaults():
    with patch("albs_mcp._commands.search_log", new_callable=AsyncMock) as mock:
        mock.return_value = ">>> 826 | foo.c:1:1: error: boom"
        code, out = _invoke(["log-search", "50000", "mock_build.log"])
    assert code == 0
    mock.assert_awaited_once_with(
        50000, "mock_build.log", None,
        LOG_SEARCH_BEFORE, LOG_SEARCH_AFTER, LOG_SEARCH_MAX_MATCHES,
        LOG_MAX_LINE_CHARS, LOG_MAX_RESULT_CHARS,
    )


def test_log_search_custom_pattern_and_context():
    with patch("albs_mcp._commands.search_log", new_callable=AsyncMock) as mock:
        mock.return_value = "search output"
        code, out = _invoke([
            "log-search", "50000", "mock_build.log",
            "-e", "Hunk #1 FAILED", "-B", "5", "-A", "0",
            "-m", "1", "--max-line-chars", "0",
        ])
    assert code == 0
    mock.assert_awaited_once_with(
        50000, "mock_build.log", "Hunk #1 FAILED", 5, 0, 1, 0,
        LOG_MAX_RESULT_CHARS,
    )


# ── search ────────────────────────────────────────────────────────────


def test_search_default():
    with patch("albs_mcp._commands.search_builds", new_callable=AsyncMock) as mock:
        mock.return_value = "Builds (page 1): 5 result(s)"
        code, out = _invoke(["search"])
    assert code == 0
    assert "page 1" in out
    mock.assert_awaited_once_with(1, None, None)


def test_search_with_filters():
    with patch("albs_mcp._commands.search_builds", new_callable=AsyncMock) as mock:
        mock.return_value = "Builds (page 2): 1 result(s)"
        code, out = _invoke(["search", "--page", "2", "--project", "bash", "--running"])
    assert code == 0
    mock.assert_awaited_once_with(2, "bash", True)


def test_search_no_running():
    with patch("albs_mcp._commands.search_builds", new_callable=AsyncMock) as mock:
        mock.return_value = "Builds (page 1): 3 result(s)"
        code, out = _invoke(["search", "--no-running"])
    assert code == 0
    mock.assert_awaited_once_with(1, None, False)


# ── sign-keys ─────────────────────────────────────────────────────────


def test_sign_keys():
    with patch("albs_mcp._commands.get_sign_keys", new_callable=AsyncMock) as mock:
        mock.return_value = "Sign keys:\n  id=4  name=AL9-key"
        code, out = _invoke(["sign-keys"])
    assert code == 0
    assert "AL9-key" in out


# ── sign-status ───────────────────────────────────────────────────────


def test_sign_status():
    with patch("albs_mcp._commands.get_sign_task_status", new_callable=AsyncMock) as mock:
        mock.return_value = "Build #50000: 1 sign task(s)\n  sign_task_id=1  [completed]"
        code, out = _invoke(["sign-status", "50000"])
    assert code == 0
    assert "sign_task_id=1" in out
    mock.assert_awaited_once_with(50000)


# ── flavors ───────────────────────────────────────────────────────────


def test_flavors():
    with patch("albs_mcp._commands.get_flavors", new_callable=AsyncMock) as mock:
        mock.return_value = "Platform flavors (3):\n  id=  7  beta"
        code, out = _invoke(["flavors"])
    assert code == 0
    assert "beta" in out


# ── create-build ──────────────────────────────────────────────────────


def test_create_build_branch():
    with patch("albs_mcp._commands.create_build", new_callable=AsyncMock) as mock:
        mock.return_value = "Build created successfully!\nBuild ID: 99999"
        code, out = _invoke([
            "create-build", "AlmaLinux-9", "bash", "--branch", "c9s",
        ])
    assert code == 0
    assert "99999" in out
    mock.assert_awaited_once()
    call_kw = mock.call_args[1]
    assert call_kw["platform"] == "AlmaLinux-9"
    assert call_kw["packages"] == ["bash"]
    assert call_kw["branch"] == "c9s"


def test_create_build_from_srpm():
    srpm = "https://example.com/pkg-1.0-1.el10.src.rpm"
    with patch("albs_mcp._commands.create_build", new_callable=AsyncMock) as mock:
        mock.return_value = "Build created successfully!"
        code, out = _invoke([
            "create-build", "AlmaLinux-10", srpm,
            "--from-srpm", "--add-epel-dist",
            "--arch", "x86_64_v2",
            "--flavor", "EPEL-10", "--flavor", "EPEL-10_altarch",
        ])
    assert code == 0
    call_kw = mock.call_args[1]
    assert call_kw["from_srpm"] is True
    assert call_kw["add_epel_dist"] is True
    assert call_kw["arch_list"] == ["x86_64_v2"]
    assert call_kw["flavors"] == ["EPEL-10", "EPEL-10_altarch"]


def test_create_build_multiple_packages():
    with patch("albs_mcp._commands.create_build", new_callable=AsyncMock) as mock:
        mock.return_value = "Build created successfully!"
        code, out = _invoke([
            "create-build", "AlmaLinux-9", "bash", "glibc", "openssl",
            "--branch", "c9s",
        ])
    assert code == 0
    call_kw = mock.call_args[1]
    assert call_kw["packages"] == ["bash", "glibc", "openssl"]


def test_create_build_skip_tests():
    with patch("albs_mcp._commands.create_build", new_callable=AsyncMock) as mock:
        mock.return_value = "Build created successfully!"
        code, out = _invoke([
            "create-build", "AlmaLinux-9", "bash",
            "--branch", "c9s", "--skip-tests",
        ])
    assert code == 0
    call_kw = mock.call_args[1]
    assert call_kw["skip_tests"] is True


def test_create_build_git_url():
    with patch("albs_mcp._commands.create_build", new_callable=AsyncMock) as mock:
        mock.return_value = "Build created successfully!"
        code, out = _invoke([
            "create-build", "AlmaLinux-10",
            "--git-url", "https://github.com/ykohut/leapp-data.git",
            "--branch", "devel-ng-0.23.0",
        ])
    assert code == 0
    call_kw = mock.call_args[1]
    assert call_kw["git_urls"] == ["https://github.com/ykohut/leapp-data.git"]
    assert call_kw["packages"] is None
    assert call_kw["branch"] == "devel-ng-0.23.0"


def test_create_build_git_url_with_packages():
    with patch("albs_mcp._commands.create_build", new_callable=AsyncMock) as mock:
        mock.return_value = "Build created successfully!"
        code, out = _invoke([
            "create-build", "AlmaLinux-10", "bash",
            "--git-url", "https://github.com/ykohut/leapp-data.git",
            "--branch", "c10s",
        ])
    assert code == 0
    call_kw = mock.call_args[1]
    assert call_kw["packages"] == ["bash"]
    assert call_kw["git_urls"] == ["https://github.com/ykohut/leapp-data.git"]


def test_create_build_multiple_git_urls():
    with patch("albs_mcp._commands.create_build", new_callable=AsyncMock) as mock:
        mock.return_value = "Build created successfully!"
        code, out = _invoke([
            "create-build", "AlmaLinux-10",
            "--git-url", "https://github.com/user/repo1.git",
            "--git-url", "https://github.com/user/repo2.git",
            "--branch", "main",
        ])
    assert code == 0
    call_kw = mock.call_args[1]
    assert call_kw["git_urls"] == [
        "https://github.com/user/repo1.git",
        "https://github.com/user/repo2.git",
    ]


def test_create_build_no_packages_no_git_url():
    """CLI allows no packages (nargs='*'), but _commands validates."""
    with patch("albs_mcp._commands.create_build", new_callable=AsyncMock) as mock:
        mock.return_value = "Error: at least one of packages or git_urls must be provided."
        code, out = _invoke(["create-build", "AlmaLinux-10", "--branch", "c10s"])
    assert code == 1
    assert "Error" in out


def test_create_build_add_platform():
    with patch("albs_mcp._commands.create_build", new_callable=AsyncMock) as mock:
        mock.return_value = "Build created successfully!"
        code, out = _invoke([
            "create-build", "AlmaLinux-8", "bash",
            "--add-platform", "AlmaLinux-9",
            "--branch", "c9s",
        ])
    assert code == 0
    call_kw = mock.call_args[1]
    assert call_kw["platform"] == "AlmaLinux-8"
    assert call_kw["platforms"] == ["AlmaLinux-9"]


def test_create_build_multiple_add_platforms():
    with patch("albs_mcp._commands.create_build", new_callable=AsyncMock) as mock:
        mock.return_value = "Build created successfully!"
        code, out = _invoke([
            "create-build", "AlmaLinux-8", "bash",
            "--add-platform", "AlmaLinux-9",
            "--add-platform", "AlmaLinux-10",
            "--branch", "c9s",
        ])
    assert code == 0
    call_kw = mock.call_args[1]
    assert call_kw["platform"] == "AlmaLinux-8"
    assert call_kw["platforms"] == ["AlmaLinux-9", "AlmaLinux-10"]


def test_create_build_no_add_platform():
    """Without --add-platform, platforms should be None."""
    with patch("albs_mcp._commands.create_build", new_callable=AsyncMock) as mock:
        mock.return_value = "Build created successfully!"
        code, out = _invoke([
            "create-build", "AlmaLinux-9", "bash", "--branch", "c9s",
        ])
    assert code == 0
    call_kw = mock.call_args[1]
    assert call_kw["platforms"] is None


def test_create_build_independent_tasks_flag():
    """--independent-tasks sets independent_tasks=True on the command call."""
    with patch("albs_mcp._commands.create_build", new_callable=AsyncMock) as mock:
        mock.return_value = "Build created successfully!"
        code, out = _invoke([
            "create-build", "AlmaLinux-9", "bash",
            "--branch", "c9s", "--independent-tasks",
        ])
    assert code == 0
    call_kw = mock.call_args[1]
    assert call_kw["independent_tasks"] is True


def test_create_build_independent_tasks_default_false():
    """Without --independent-tasks, the flag is False."""
    with patch("albs_mcp._commands.create_build", new_callable=AsyncMock) as mock:
        mock.return_value = "Build created successfully!"
        code, out = _invoke([
            "create-build", "AlmaLinux-9", "bash", "--branch", "c9s",
        ])
    assert code == 0
    call_kw = mock.call_args[1]
    assert call_kw["independent_tasks"] is False


# ── sign-build ────────────────────────────────────────────────────────


def test_sign_build_default_key():
    with patch("albs_mcp._commands.sign_build", new_callable=AsyncMock) as mock:
        mock.return_value = "Sign task created for build #50000"
        code, out = _invoke(["sign-build", "50000"])
    assert code == 0
    assert "Sign task created" in out
    mock.assert_awaited_once_with(50000, 4)


def test_sign_build_custom_key():
    with patch("albs_mcp._commands.sign_build", new_callable=AsyncMock) as mock:
        mock.return_value = "Sign task created"
        code, out = _invoke(["sign-build", "50000", "--key-id", "7"])
    assert code == 0
    mock.assert_awaited_once_with(50000, 7)


# ── token / log-dir options ──────────────────────────────────────────


def test_token_option(monkeypatch):
    monkeypatch.delenv("ALBS_JWT_TOKEN", raising=False)
    with patch("albs_mcp._commands.get_sign_keys", new_callable=AsyncMock) as mock:
        mock.return_value = "Sign keys:\n  id=4"
        code, out = _invoke(["--token", "my-secret", "sign-keys"])
    assert code == 0


def test_log_dir_option(monkeypatch):
    monkeypatch.delenv("ALBS_LOG_DIR", raising=False)
    with patch("albs_mcp._commands.get_platforms", new_callable=AsyncMock) as mock:
        mock.return_value = "Platforms (1):"
        code, out = _invoke(["--log-dir", "/custom/logs", "platforms"])
    assert code == 0


# ── exit codes ────────────────────────────────────────────────────────


def test_error_string_returns_exit_code_1():
    with patch("albs_mcp._commands.get_sign_keys", new_callable=AsyncMock) as mock:
        mock.return_value = "Auth error: no token provided"
        code, out = _invoke(["sign-keys"])
    assert code == 1
    assert "Auth error" in out


def test_exception_returns_exit_code_1():
    with patch("albs_mcp._commands.get_platforms", new_callable=AsyncMock) as mock:
        mock.side_effect = RuntimeError("connection failed")
        code, out = _invoke(["platforms"])
    assert code == 1
    assert "connection failed" in out


# ── products / release plans ──────────────────────────────────────────


def test_release_subcommands_registered():
    parser = build_parser()
    for action in parser._subparsers._group_actions:
        if hasattr(action, "choices") and action.choices:
            choices = list(action.choices.keys())
            break
    else:
        choices = []
    assert "products" in choices
    assert "release-plan" in choices
    assert "create-release-plan" in choices
    assert "commit-release" in choices


def test_products():
    with patch("albs_mcp._commands.get_products", new_callable=AsyncMock) as mock:
        mock.return_value = "Products (2):\n  id=1 AlmaLinux"
        code, out = _invoke(["products"])
    assert code == 0
    assert "AlmaLinux" in out
    mock.assert_awaited_once()


def test_release_plan_view():
    with patch("albs_mcp._commands.get_release_plan", new_callable=AsyncMock) as mock:
        mock.return_value = "Release plan #4242\nStatus: completed"
        code, out = _invoke(["release-plan", "4242"])
    assert code == 0
    assert "Release plan #4242" in out
    mock.assert_awaited_once_with(4242)


def test_create_release_plan():
    with patch(
        "albs_mcp._commands.create_release_plan", new_callable=AsyncMock
    ) as mock:
        mock.return_value = "Release plan #4242 created"
        code, out = _invoke([
            "create-release-plan", "50000",
            "--platform", "AlmaLinux-9", "--product", "AlmaLinux",
        ])
    assert code == 0
    assert "Release plan #4242 created" in out
    call_kw = mock.call_args[1]
    assert call_kw["build_id"] == 50000
    assert call_kw["platform"] == "AlmaLinux-9"
    assert call_kw["product"] == "AlmaLinux"
    assert call_kw["build_ids"] is None
    assert call_kw["whole_packages_only"] is False


def test_create_release_plan_with_extras_and_whole():
    with patch(
        "albs_mcp._commands.create_release_plan", new_callable=AsyncMock
    ) as mock:
        mock.return_value = "Release plan #1 created"
        code, out = _invoke([
            "create-release-plan", "50000",
            "--platform", "AlmaLinux-9", "--product", "epel-al",
            "--add-build", "50001", "--add-build", "50002",
            "--whole-packages-only",
        ])
    assert code == 0
    call_kw = mock.call_args[1]
    assert call_kw["build_ids"] == [50001, 50002]
    assert call_kw["whole_packages_only"] is True


def test_create_release_plan_requires_platform_and_product():
    """Missing required --platform/--product is an argparse error (exit 2)."""
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["create-release-plan", "50000"])
    assert exc.value.code == 2


def test_commit_release_blocked():
    with patch("albs_mcp._commands.commit_release", new_callable=AsyncMock) as mock:
        mock.return_value = "Committing a release ... is intentionally blocked."
        code, out = _invoke(["commit-release", "4242"])
    assert code == 0
    assert "blocked" in out.lower()
    mock.assert_awaited_once_with(4242)
