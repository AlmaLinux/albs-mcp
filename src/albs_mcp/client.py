from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import httpx

from .constants import (
    ALBS_API,
    ALBS_LOGS_BASE,
    SECURE_BOOT_PACKAGES,
)


class ALBSClient:
    def __init__(self, jwt_token: str | None = None, timeout: float = 30.0):
        self.jwt_token = jwt_token
        self._http = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
        self._log_dir = Path(os.environ.get("ALBS_LOG_DIR", "/tmp/albs-logs"))
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._platforms_cache: dict[str, list[str]] | None = None

    @property
    def _auth_headers(self) -> dict[str, str]:
        if not self.jwt_token:
            raise PermissionError(
                "JWT token required. Pass --token or set ALBS_JWT_TOKEN."
            )
        return {"authorization": f"Bearer {self.jwt_token}"}

    # ── Public (no auth) ──────────────────────────────────────────────

    async def get_platforms(self) -> list[dict[str, Any]]:
        """Get all platforms with their arch_list from ALBS."""
        r = await self._http.get(f"{ALBS_API}/platforms/")
        r.raise_for_status()
        return r.json()

    async def get_platform_arches(self) -> dict[str, list[str]]:
        """Get {platform_name: arch_list} mapping, cached after first call."""
        if self._platforms_cache is None:
            platforms = await self.get_platforms()
            self._platforms_cache = {
                p["name"]: p["arch_list"] for p in platforms
            }
        return self._platforms_cache

    async def get_build(self, build_id: int) -> dict[str, Any]:
        r = await self._http.get(f"{ALBS_API}/builds/{build_id}/")
        r.raise_for_status()
        return r.json()

    async def search_builds(
        self,
        page: int = 1,
        project: str | None = None,
        is_running: bool | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"pageNumber": page}
        if project:
            params["project"] = project
        if is_running is not None:
            params["is_running"] = str(is_running).lower()
        r = await self._http.get(f"{ALBS_API}/builds", params=params)
        r.raise_for_status()
        return r.json()

    async def get_sign_tasks(self, build_id: int) -> list[dict[str, Any]]:
        r = await self._http.get(
            f"{ALBS_API}/sign-tasks/", params={"build_id": build_id}
        )
        r.raise_for_status()
        return r.json()

    # ── Log helpers ───────────────────────────────────────────────────

    def _log_base_url(self, build_id: int) -> str:
        return f"{ALBS_LOGS_BASE}/build-{build_id}-build_log"

    def _log_path(self, build_id: int, filename: str) -> Path:
        build_dir = self._log_dir / str(build_id)
        build_dir.mkdir(parents=True, exist_ok=True)
        return build_dir / filename

    async def list_build_logs(self, build_id: int) -> list[str]:
        """Parse the Pulp directory listing for a build's logs."""
        url = self._log_base_url(build_id) + "/"
        r = await self._http.get(url)
        r.raise_for_status()
        return re.findall(r'href="([^"]+\.(?:log|cfg))"', r.text)

    async def download_log(self, build_id: int, filename: str) -> Path:
        url = f"{self._log_base_url(build_id)}/{filename}"
        dest = self._log_path(build_id, filename)
        async with self._http.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(8192):
                    f.write(chunk)
        return dest

    def read_log_tail(self, build_id: int, filename: str, lines: int) -> tuple[str, int, int]:
        """Read last `lines` lines. Returns (content, total_lines, from_line)."""
        path = self._log_path(build_id, filename)
        if not path.exists():
            raise FileNotFoundError(
                f"Log not downloaded yet. Use download_log first: {filename}"
            )
        all_lines = path.read_text(errors="replace").splitlines()
        total = len(all_lines)
        start = max(0, total - lines)
        return "\n".join(all_lines[start:]), total, start + 1

    def read_log_range(
        self, build_id: int, filename: str, start_line: int, end_line: int
    ) -> tuple[str, int]:
        """Read a specific range. Returns (content, total_lines)."""
        path = self._log_path(build_id, filename)
        if not path.exists():
            raise FileNotFoundError(
                f"Log not downloaded yet. Use download_log first: {filename}"
            )
        all_lines = path.read_text(errors="replace").splitlines()
        total = len(all_lines)
        s = max(0, start_line - 1)
        e = min(total, end_line)
        return "\n".join(all_lines[s:e]), total

    # ── Authenticated (JWT required) ──────────────────────────────────

    async def get_flavors(self) -> dict[str, int]:
        r = await self._http.get(
            f"{ALBS_API}/platform_flavors/", headers=self._auth_headers
        )
        r.raise_for_status()
        return {f["name"]: f["id"] for f in r.json()}

    async def get_sign_keys(self) -> list[dict[str, Any]]:
        """Get available sign keys. Requires JWT."""
        r = await self._http.get(
            f"{ALBS_API}/sign-keys/", headers=self._auth_headers
        )
        r.raise_for_status()
        return r.json()

    async def create_build(
        self,
        packages: list[dict[str, str]],
        platform: str,
        arch_list: list[str] | None = None,
        branch: str | None = None,
        from_tag: bool = False,
        from_srpm: bool = False,
        beta: bool = False,
        secureboot: bool = False,
        nosecureboot: bool = False,
        excludes: list[str] | None = None,
        definitions: dict[str, str] | None = None,
        linked_builds: list[int] | None = None,
        additional_flavors: list[str] | None = None,
        with_opts: list[str] | None = None,
        without_opts: list[str] | None = None,
        modules: list[str] | None = None,
    ) -> dict[str, Any]:
        if not from_tag and not branch and not from_srpm:
            raise ValueError("At least one of branch, from_tag, or from_srpm must be set")
        if from_tag and branch:
            raise ValueError("from_tag and branch cannot be used together")

        platform_arches = await self.get_platform_arches()
        if platform not in platform_arches:
            raise ValueError(
                f"Unknown platform '{platform}'. "
                f"Available: {', '.join(sorted(platform_arches))}"
            )

        allowed = platform_arches[platform]
        arches = arch_list or allowed
        bad = [a for a in arches if a not in allowed]
        if bad:
            raise ValueError(
                f"Arch(es) {bad} not allowed for {platform}. Allowed: {allowed}"
            )

        if not nosecureboot:
            for pkg in packages:
                name = list(pkg.keys())[0]
                if name in SECURE_BOOT_PACKAGES and not secureboot:
                    raise ValueError(
                        f"Package '{name}' requires --secureboot. "
                        f"Use nosecureboot=True to override."
                    )

        ref_type = 3 if from_srpm else (2 if from_tag else 1)
        tasks = []
        for pkg in packages:
            for pkg_name, pkg_tag in pkg.items():
                task: dict[str, Any] = {
                    "url": pkg_name if ref_type == 3
                    else f"https://git.almalinux.org/rpms/{pkg_name}.git",
                    "ref_type": ref_type,
                    "module_platform_version": "null",
                    "module_version": "null",
                }
                if ref_type != 3:
                    task["git_ref"] = pkg_tag if from_tag else branch
                tasks.append(task)

        data: dict[str, Any] = {
            "platforms": [
                {
                    "name": platform,
                    "arch_list": arches,
                    "parallel_mode_enabled": True,
                }
            ],
            "tasks": tasks,
            "is_secure_boot": secureboot,
            "product_id": 1,
        }
        if linked_builds:
            data["linked_builds"] = linked_builds
        if beta:
            data["platform_flavors"] = []
        if excludes:
            data.setdefault("mock_options", {})["yum_exclude"] = excludes
        if definitions:
            data.setdefault("mock_options", {})["definitions"] = definitions
        if with_opts:
            data.setdefault("mock_options", {})["with"] = with_opts
        if without_opts:
            data.setdefault("mock_options", {})["without"] = without_opts
        if modules:
            data.setdefault("mock_options", {})["module_enable"] = modules
        if additional_flavors:
            flavors = await self.get_flavors()
            flav_ids = [flavors[f] for f in additional_flavors if f in flavors]
            data.setdefault("platform_flavors", []).extend(flav_ids)

        r = await self._http.post(
            f"{ALBS_API}/builds/", json=data, headers=self._auth_headers
        )
        r.raise_for_status()
        return r.json()

    async def sign_build(self, build_id: int, sign_key_id: int = 4) -> dict[str, Any]:
        r = await self._http.post(
            f"{ALBS_API}/sign-tasks/",
            json={"build_id": build_id, "sign_key_id": sign_key_id},
            headers=self._auth_headers,
        )
        r.raise_for_status()
        return r.json()

    async def close(self):
        await self._http.aclose()
