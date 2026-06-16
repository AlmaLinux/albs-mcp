from __future__ import annotations

import os
import re
from collections import deque
from pathlib import Path
from typing import Any

import httpx

from .constants import (
    ALBS_API,
    ALBS_LOGS_BASE,
    BETA_PLATFORM_FLAVORS,
    SECURE_BOOT_PACKAGES,
)


def extract_el_version(pkg_name: str) -> str | None:
    """Extract .elN suffix from a package name/tag/URL (e.g. '.el10' from '...-0.16-5.el10')."""
    cleaned = pkg_name.replace(".src.rpm", "").split("-")[-1]
    match = re.search(r"\.el\d{1,2}[^-]*", cleaned)
    return match.group(0) if match else None


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
        # The filename must be a plain log basename. Reject anything with a
        # path separator or percent-encoding *before* it is used: the same
        # name is interpolated into the download URL, and an encoded separator
        # (e.g. "%2f..%2f") could traverse on the remote host even though the
        # local write below is sandboxed. This whitelist blocks "/", "\\",
        # "%", spaces, and control/NUL bytes.
        if not re.fullmatch(r"[A-Za-z0-9._+-]+", filename or ""):
            raise ValueError(f"Invalid log filename: {filename!r}")
        build_dir = (self._log_dir / str(build_id)).resolve()
        build_dir.mkdir(parents=True, exist_ok=True)
        # Defense in depth: the resolved destination must stay inside this
        # build's log directory (also rejects "." and ".." which pass the
        # whitelist above).
        dest = (build_dir / filename).resolve()
        if dest == build_dir or not dest.is_relative_to(build_dir):
            raise ValueError(f"Invalid log filename: {filename!r}")
        return dest

    async def list_build_logs(self, build_id: int) -> list[str]:
        """Parse the Pulp directory listing for a build's logs."""
        url = self._log_base_url(build_id) + "/"
        r = await self._http.get(url)
        r.raise_for_status()
        return re.findall(r'href="([^"]+\.(?:log|cfg))"', r.text)

    async def download_log(self, build_id: int, filename: str) -> Path:
        dest = self._log_path(build_id, filename)
        url = f"{self._log_base_url(build_id)}/{filename}"
        # Download to a temp file and atomically rename, so an interrupted
        # download never leaves a partial file at `dest` that later reads
        # would silently treat as the complete log.
        tmp = dest.with_name(dest.name + ".part")
        try:
            async with self._http.stream("GET", url) as resp:
                resp.raise_for_status()
                with open(tmp, "wb") as f:
                    async for chunk in resp.aiter_bytes(8192):
                        f.write(chunk)
            tmp.replace(dest)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return dest

    def read_log_tail(self, build_id: int, filename: str, lines: int) -> tuple[str, int, int]:
        """Read last `lines` lines. Returns (content, total_lines, from_line).

        Streams the file once, keeping only the last `lines` lines in memory.
        mock_build logs can be 100k+ lines / hundreds of MB, so the whole file
        is never materialized.
        """
        path = self._log_path(build_id, filename)
        if not path.exists():
            raise FileNotFoundError(
                f"Log not downloaded yet. Use download_log first: {filename}"
            )
        total = 0
        tail: deque[str] = deque(maxlen=lines if lines > 0 else 0)
        with open(path, "r", errors="replace") as f:
            for line in f:
                total += 1
                tail.append(line.rstrip("\n"))
        start = max(0, total - lines)
        return "\n".join(tail), total, start + 1

    def read_log_range(
        self, build_id: int, filename: str, start_line: int, end_line: int
    ) -> tuple[str, int]:
        """Read a specific range. Returns (content, total_lines).

        Streams the file once, collecting only the requested line window
        instead of materializing the whole file.
        """
        path = self._log_path(build_id, filename)
        if not path.exists():
            raise FileNotFoundError(
                f"Log not downloaded yet. Use download_log first: {filename}"
            )
        s = max(0, start_line - 1)
        collected: list[str] = []
        total = 0
        with open(path, "r", errors="replace") as f:
            for i, line in enumerate(f):
                total += 1
                if s <= i < end_line:
                    collected.append(line.rstrip("\n"))
        return "\n".join(collected), total

    # ── Authenticated (JWT required) ──────────────────────────────────

    async def get_flavors(self) -> dict[str, int]:
        """Get {flavor_name: flavor_id} mapping from ALBS. Requires JWT.

        ALBS moved /platform_flavors/ behind authentication, so the request
        must include the Bearer token or the server responds with 403
        {"detail": "Not authenticated"}.
        """
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
        platforms: list[str],
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
        add_epel_dist: bool = False,
        independent_tasks: bool = False,
    ) -> dict[str, Any]:
        if not from_tag and not branch and not from_srpm:
            raise ValueError("At least one of branch, from_tag, or from_srpm must be set")
        if from_tag and branch:
            raise ValueError("from_tag and branch cannot be used together")

        platform_arches = await self.get_platform_arches()
        platform_entries: list[dict[str, Any]] = []
        for plat in platforms:
            if plat not in platform_arches:
                raise ValueError(
                    f"Unknown platform '{plat}'. "
                    f"Available: {', '.join(sorted(platform_arches))}"
                )
            allowed = platform_arches[plat]
            arches = arch_list or allowed
            bad = [a for a in arches if a not in allowed]
            if bad:
                raise ValueError(
                    f"Arch(es) {bad} not allowed for {plat}. Allowed: {allowed}"
                )
            platform_entries.append({
                "name": plat,
                "arch_list": arches,
                "parallel_mode_enabled": True,
                "independent_tasks": independent_tasks,
            })

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
                is_url = pkg_name.startswith(("http://", "https://"))
                task: dict[str, Any] = {
                    "url": pkg_name if (ref_type == 3 or is_url)
                    else f"https://git.almalinux.org/rpms/{pkg_name}.git",
                    "ref_type": ref_type,
                    "module_platform_version": "null",
                    "module_version": "null",
                }
                if ref_type != 3:
                    task["git_ref"] = pkg_tag if from_tag else branch
                if add_epel_dist and (from_tag or from_srpm):
                    dist = extract_el_version(pkg_name)
                    if dist:
                        task["mock_options"] = {
                            "definitions": {"dist": f"{dist}.alma_altarch"}
                        }
                tasks.append(task)

        data: dict[str, Any] = {
            "platforms": platform_entries,
            "tasks": tasks,
            "is_secure_boot": secureboot,
            "product_id": 1,
        }
        if linked_builds:
            data["linked_builds"] = linked_builds
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
        if beta or additional_flavors:
            flavors = await self.get_flavors()
            flav_ids: list[int] = []
            if beta:
                unsupported = [
                    p for p in platforms if p not in BETA_PLATFORM_FLAVORS
                ]
                if unsupported:
                    raise ValueError(
                        f"beta=True is not supported for platform(s) "
                        f"{unsupported}. Platforms with a known beta flavor: "
                        f"{sorted(BETA_PLATFORM_FLAVORS)}. "
                        f"Pass additional_flavors=[...] explicitly if you need "
                        f"a non-standard beta flavor."
                    )
                wanted: list[str] = []
                for plat in platforms:
                    wanted.extend(BETA_PLATFORM_FLAVORS[plat])
                # Validate the predefined names against the live API —
                # never trust constants without verifying (per AGENTS.md).
                missing = [n for n in wanted if n not in flavors]
                if missing:
                    raise ValueError(
                        f"Beta flavor(s) {missing} from BETA_PLATFORM_FLAVORS "
                        f"are not present on ALBS. The constant is stale — "
                        f"update src/albs_mcp/constants.py. "
                        f"Available: {sorted(flavors)}"
                    )
                flav_ids.extend(flavors[n] for n in wanted)
            if additional_flavors:
                unknown = [f for f in additional_flavors if f not in flavors]
                if unknown:
                    raise ValueError(
                        f"Unknown flavor(s): {unknown}. "
                        f"Available: {sorted(flavors)}"
                    )
                flav_ids.extend(flavors[f] for f in additional_flavors)
            # de-duplicate while preserving order
            seen: set[int] = set()
            deduped = [i for i in flav_ids if not (i in seen or seen.add(i))]
            if deduped:
                data.setdefault("platform_flavors", []).extend(deduped)

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
