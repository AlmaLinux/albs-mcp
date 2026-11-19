from __future__ import annotations

import fnmatch
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
    BUILD_CONFIGS_URL,
    BUILD_TASK_COMPLETED,
    CVE_ID_PATTERN,
    LOG_CLIP_LEAD_CHARS,
    LOG_MAX_LINE_CHARS,
    LOG_MAX_RESULT_CHARS,
    LOG_NOISE_PATTERN,
    LOG_SEARCH_BEFORE_SPAN,
    LOG_SEARCH_MAX_MATCHES,
    MODULE_PREVIEW_TIMEOUT,
    OPT_IN_ARCHES,
    RH_ADVISORY_ID_PATTERN,
    RH_CSAF_BASE,
    RH_CVE_API,
    RH_VEX_BASE,
    SECURE_BOOT_PACKAGES,
)

# One log line with its 1-based number and whether it matched the search.
LogLine = tuple[int, str, bool]


def clip_line(text: str, limit: int, anchor: int = 0) -> str:
    """Clip *text* to *limit* characters, keeping the window around *anchor*.

    `anchor` is an index that MUST stay visible — the position of a regex match
    — so an error buried deep inside a multi-KB gcc command line is not clipped
    away. A `limit` of 0 (or a line already short enough) returns *text*
    unchanged. Clipped-away characters are reported inline so a reader can tell
    the line is not verbatim.
    """
    if limit <= 0 or len(text) <= limit:
        return text
    start = 0
    if anchor + LOG_CLIP_LEAD_CHARS > limit:
        start = min(anchor - LOG_CLIP_LEAD_CHARS, len(text) - limit)
        start = max(0, start)
    end = min(len(text), start + limit)
    out = text[start:end]
    if start:
        out = f"…[{start} chars clipped]{out}"
    if end < len(text):
        out = f"{out}…[{len(text) - end} chars clipped]"
    return out


def _trim_to_budget(window: deque[tuple[int, str]], max_chars: int) -> None:
    """Drop lines off the TOP of *window* until it fits `max_chars`.

    The bottom of a build log is the interesting end, so a page that overflows
    loses its oldest lines. One line always survives, even if it alone exceeds
    the budget — returning nothing would tell the caller nothing.
    """
    if max_chars <= 0 or not window:
        return
    size = sum(len(text) + 1 for _, text in window)
    while size > max_chars and len(window) > 1:
        _, text = window.popleft()
        size -= len(text) + 1


def _hunk_size(hunk: list[LogLine]) -> int:
    """Rendered character count of a hunk, one newline per line."""
    return sum(len(text) + 1 for _, text, _ in hunk)


def _extend_hunk(hunk: list[LogLine], block: list[LogLine]) -> None:
    """Append *block* to *hunk*, dropping lines it already reported.

    A `before` window can reach back into the preceding match's `after` window;
    without this each overlapping line would be printed twice.
    """
    last = hunk[-1][0] if hunk else 0
    hunk.extend(line for line in block if line[0] > last)


def extract_el_version(pkg_name: str) -> str | None:
    """Extract .elN suffix from a package name/tag/URL (e.g. '.el10' from '...-0.16-5.el10')."""
    cleaned = pkg_name.replace(".src.rpm", "").split("-")[-1]
    match = re.search(r"\.el\d{1,2}[^-]*", cleaned)
    return match.group(0) if match else None


def opt_in_arches(platform: str, arch_list: list[str]) -> list[str]:
    """Arches of *arch_list* that *platform* builds only when asked for.

    They are valid in an explicit arch_list but left out of the default one
    (see OPT_IN_ARCHES), e.g. riscv64 on AlmaLinux-10.
    """
    opt_in = OPT_IN_ARCHES.get(platform, [])
    return [a for a in arch_list if a in opt_in]


def default_arches(platform: str, arch_list: list[str]) -> list[str]:
    """Arches a build of *platform* gets when the caller passes no arch_list."""
    skip = opt_in_arches(platform, arch_list)
    return [a for a in arch_list if a not in skip]


def tag_branch(tag: str) -> str | None:
    """The dist-git branch an ``imports/`` or ``changed/`` tag was cut from.

    ``changed/a10-beta/openssh-9.9p1-20.el10_2.alma.1`` → ``a10-beta``; None
    for any other ref.
    """
    parts = tag.split("/", 2)
    if len(parts) == 3 and parts[0] in ("imports", "changed") and parts[1]:
        return parts[1]
    return None


def srpm_package_name(url: str) -> str:
    """Source package name of an SRPM URL or file name.

    ``.../gcc-toolset-14-gcc-14.2.1-1.el10.src.rpm`` → ``gcc-toolset-14-gcc``.
    """
    name = url.rstrip("/").rsplit("/", 1)[-1]
    name = name.removesuffix(".src.rpm").removesuffix(".nosrc.rpm")
    parts = name.rsplit("-", 2)
    return parts[0] if len(parts) == 3 else name


def _pattern_specificity(pattern: str) -> tuple[int, int]:
    """Sort key: more literal (non-wildcard) characters first, then longer."""
    literal = sum(1 for c in pattern if c not in "*?[]")
    return (-literal, -len(pattern))


def match_build_config(
    configs: dict[str, Any], package: str, branch: str
) -> tuple[str, dict[str, Any]] | None:
    """``(pattern, overrides)`` of *package* for *branch* in build_configs.json.

    The most specific fnmatch pattern matching *branch* wins (``*portable*``
    beats ``*``), the same rule releng-monitoring uses. None when the package
    has no entry or no pattern matches.
    """
    entry = configs.get(package)
    if not isinstance(entry, dict):
        return None
    for pattern in sorted(entry, key=_pattern_specificity):
        if fnmatch.fnmatchcase(branch, pattern):
            return pattern, entry[pattern]
    return None


def get_completed_task_ids(build_info: dict[str, Any]) -> list[int]:
    """IDs of build tasks that completed successfully.

    A release plan may only reference completed tasks; failed / excluded /
    still-running tasks are skipped.
    """
    return [
        t["id"]
        for t in build_info.get("tasks", [])
        if t.get("status") == BUILD_TASK_COMPLETED
    ]


def get_whole_package_task_ids(build_info: dict[str, Any]) -> list[int]:
    """Task IDs only for packages whose EVERY architecture task completed.

    Tasks are grouped by their source ref URL (one package → N arch tasks).
    A package is included only if all of its arch tasks completed; any package
    with a failed / excluded / still-running task is dropped entirely, so a
    "half-built" package never leaks into a release plan. Use this for a
    PARTIAL build (e.g. one superseded by a 'retry failed' build).
    """
    by_url: dict[str, list[dict[str, Any]]] = {}
    for t in build_info.get("tasks", []):
        url = (t.get("ref") or {}).get("url", "")
        by_url.setdefault(url, []).append(t)

    result: list[int] = []
    for tasks in by_url.values():
        if tasks and all(
            t.get("status") == BUILD_TASK_COMPLETED for t in tasks
        ):
            result.extend(t["id"] for t in tasks)
    return result


def _split_module(spec: str) -> tuple[str, str]:
    """'perl:5.26' -> ('perl', '5.26'); 'perl:' and 'perl' -> ('perl', '')."""
    name, _, stream = spec.strip().partition(":")
    return name.strip(), stream.strip()


def build_module_task(
    preview: dict[str, Any],
    dependency_streams: list[str] | None,
    module_platform_version: str,
    module_version: str | None = None,
) -> dict[str, Any]:
    """Turn a /builds/get_module_preview/ answer into a module build task.

    Mirrors what the ALBS web UI sends for a module (the BuildTaskModuleRef
    payload): the module template's dependencies become `enabled_modules`
    (written into the built modulemd), and `name:stream`, `name-devel:stream`
    plus every dependency that has a stream go to mock `module_enable` of
    each component ref.

    A template dependency without a stream (modulemd `perl: []`, i.e. "any
    stream") must be given one through `dependency_streams` — that picks
    which stream this build is for (one build per stream). Fail-fast:
    enabling it as bare `perl:` makes mock die with "missing groups or
    modules: perl:". A dependency_streams entry overrides the template
    stream; one for a module the template does not mention is only enabled
    in mock (like adding a module in the UI's module editor).
    """
    name = preview["module_name"]
    stream = preview["module_stream"]
    template = preview.get("enabled_modules") or {}
    buildtime = [_split_module(m) for m in template.get("buildtime", [])]
    runtime = [_split_module(m) for m in template.get("runtime", [])]
    template_names = {n for n, _ in buildtime + runtime}

    chosen: dict[str, str] = {}
    for spec in dependency_streams or []:
        dep, dep_stream = _split_module(spec)
        if not dep or not dep_stream:
            raise ValueError(
                f"Dependency stream {spec!r} must be in 'name:stream' form, "
                f"e.g. 'perl:5.26'."
            )
        if chosen.get(dep, dep_stream) != dep_stream:
            raise ValueError(
                f"Two streams given for module '{dep}': "
                f"{chosen[dep]} and {dep_stream}. A module build takes one "
                f"stream per dependency — create one build per stream."
            )
        chosen[dep] = dep_stream

    no_stream = sorted(
        {n for n, s in buildtime + runtime if not s and n not in chosen}
    )
    if no_stream:
        example = ", ".join(f'"{n}:<stream>"' for n in no_stream)
        raise ValueError(
            f"Module {name}:{stream} depends on {no_stream} without a stream "
            f"(modulemd lists them as `[]`, any stream). Pass the stream to "
            f"build against via dependency_streams=[{example}] — one build "
            f"per stream. Without a stream mock fails with "
            f"\"missing groups or modules: {no_stream[0]}:\"."
        )

    def resolve(deps: list[tuple[str, str]]) -> list[str]:
        out: list[str] = []
        for dep, dep_stream in deps:
            item = f"{dep}:{chosen.get(dep, dep_stream)}"
            if item not in out:
                out.append(item)
        return out

    enabled_modules = {"buildtime": resolve(buildtime), "runtime": resolve(runtime)}
    module_enable = [f"{name}:{stream}"]
    if "-devel" not in name:
        module_enable.append(f"{name}-devel:{stream}")
    extra = [f"{n}:{s}" for n, s in chosen.items() if n not in template_names]
    for item in enabled_modules["buildtime"] + enabled_modules["runtime"] + extra:
        if item not in module_enable:
            module_enable.append(item)

    refs: list[dict[str, Any]] = []
    for ref in preview.get("refs") or []:
        ref = dict(ref)
        mock_options = dict(ref.get("mock_options") or {})
        mock_options["module_enable"] = list(module_enable)
        ref["mock_options"] = mock_options
        ref["test_configuration"] = {}
        refs.append(ref)
    enabled_refs = [r for r in refs if r.get("enabled", True)]
    if not enabled_refs:
        raise ValueError(
            f"Module {name}:{stream} has no component to build: every "
            f"component ref is disabled (ALBS found them already built)."
        )
    absent = [r["url"] for r in enabled_refs if r.get("exist") is False]
    if absent:
        raise ValueError(
            f"Component branch '{refs[0]['git_ref']}' does not exist for: "
            f"{', '.join(absent)}."
        )

    return {
        "module_name": name,
        "module_stream": stream,
        "module_platform_version": module_platform_version,
        "module_version": module_version,
        "modules_yaml": preview["modules_yaml"],
        "enabled_modules": enabled_modules,
        "refs": refs,
        "git_ref": preview.get("git_ref"),
    }


class ALBSClient:
    def __init__(self, jwt_token: str | None = None, timeout: float = 30.0):
        self.jwt_token = jwt_token
        self._http = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
        self._log_dir = Path(os.environ.get("ALBS_LOG_DIR", "/tmp/albs-logs"))
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._platforms_cache: dict[str, list[str]] | None = None
        self._platform_ids_cache: dict[str, int] | None = None
        self._products_cache: dict[str, int] | None = None

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

    async def get_platform_ids(self) -> dict[str, int]:
        """Get {platform_name: platform_id} mapping, cached after first call.

        Needed to resolve a platform name to the numeric id a release plan
        expects.
        """
        if self._platform_ids_cache is None:
            platforms = await self.get_platforms()
            self._platform_ids_cache = {p["name"]: p["id"] for p in platforms}
        return self._platform_ids_cache

    # ── Products (no auth) ────────────────────────────────────────────

    async def get_products(self) -> list[dict[str, Any]]:
        """Get all products from ALBS.

        The endpoint returns a plain list of every product (it is not
        paginated when no pageNumber is passed); some deployments instead
        wrap it as {"products": [...]}, so both shapes are handled.
        """
        r = await self._http.get(f"{ALBS_API}/products/")
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else data.get("products", [])

    async def get_product_ids(self) -> dict[str, int]:
        """Get {product_name: product_id} mapping, cached after first call.

        Needed to resolve a product name to the numeric id a release plan
        expects.
        """
        if self._products_cache is None:
            products = await self.get_products()
            self._products_cache = {p["name"]: p["id"] for p in products}
        return self._products_cache

    async def get_build_configs(self) -> dict[str, Any]:
        """build_configs.json — per-package build overrides (no auth).

        Fetched on every call (it is ~20 KB) so a build never uses stale
        overrides.
        """
        r = await self._http.get(BUILD_CONFIGS_URL)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise ValueError("build_configs.json is not a JSON object")
        return data

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

    async def get_release(self, release_id: int) -> dict[str, Any]:
        """Get a single release with its computed plan and status. No auth."""
        r = await self._http.get(f"{ALBS_API}/releases/{release_id}/")
        r.raise_for_status()
        return r.json()

    # ── Errata (no auth for reads) ────────────────────────────────────

    async def query_errata(
        self,
        errata_id: str | None = None,
        errata_ids: list[str] | None = None,
        platform_id: int | None = None,
        page: int = 1,
        title: str | None = None,
        cve_id: str | None = None,
    ) -> dict[str, Any]:
        """One page (10 records, newest issued first) of /errata/query/. No auth.

        `errata_id`, `title` and `cve_id` are SUBSTRING matches on the server
        (SQL LIKE '%x%'): `title` also searches the original title, and
        cve_id "CVE-2026-3143" also finds CVE-2026-31431 — check the record's
        references for the exact CVE. `errata_ids` are exact IDs. Returns {"records", "total_records",
        "current_page"}, where total_records counts the whole filter.
        """
        params: dict[str, Any] = {"pageNumber": page}
        if errata_id:
            params["id"] = errata_id
        if errata_ids:
            params["ids"] = errata_ids
        if title:
            params["title"] = title
        if cve_id:
            params["cveId"] = cve_id
        if platform_id is not None:
            params["platformId"] = platform_id
        r = await self._http.get(f"{ALBS_API}/errata/query/", params=params)
        r.raise_for_status()
        return r.json()

    async def list_alma_errata_ids(self, year: int) -> list[str]:
        """Every AlmaLinux-issued advisory ID of `year`, on every platform.

        The `-YYYY:A` substring matches ALSA/ALBA/ALEA-YYYY:A### and none of
        the Red Hat-derived ALSA-YYYY:NNNN records. All pages are read: the
        query is ordered by issued date, so a back-dated advisory can carry
        the highest number on a later page.
        """
        ids: list[str] = []
        page = 1
        while True:
            data = await self.query_errata(errata_id=f"-{year}:A", page=page)
            records = data.get("records") or []
            ids.extend(r["id"] for r in records)
            if not records or len(ids) >= (data.get("total_records") or 0):
                return ids
            page += 1

    async def get_rh_cve(self, cve_id: str) -> dict[str, Any]:
        """Red Hat security data for one CVE (severity, CVSS, CWE, bugzilla,
        affected packages). 404 when Red Hat has no record of the CVE."""
        r = await self._http.get(f"{RH_CVE_API}/{cve_id}.json")
        r.raise_for_status()
        return r.json()

    async def get_rh_cve_vex(self, cve_id: str) -> dict[str, Any]:
        """CSAF VEX document of one CVE, e.g. .../vex/2026/cve-2026-31431.json.
        Published before the per-CVE JSON of get_rh_cve; 404 when Red Hat
        has no record of the CVE."""
        match = re.fullmatch(CVE_ID_PATTERN, cve_id)
        if not match:
            raise ValueError(f"Invalid CVE ID: {cve_id!r}")
        year = cve_id.split("-")[1]
        r = await self._http.get(f"{RH_VEX_BASE}/{year}/{cve_id.lower()}.json")
        r.raise_for_status()
        return r.json()

    async def get_rh_advisory(self, advisory_id: str) -> dict[str, Any]:
        """CSAF document of a Red Hat advisory, e.g. RHSA-2026:13566."""
        match = re.fullmatch(RH_ADVISORY_ID_PATTERN, advisory_id)
        if not match:
            raise ValueError(f"Invalid Red Hat advisory ID: {advisory_id!r}")
        name = advisory_id.lower().replace(":", "_")
        r = await self._http.get(f"{RH_CSAF_BASE}/{match.group(1)}/{name}.json")
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
        """Parse the Pulp directory listing for a build's logs.

        Pulp writes the hrefs relative (`href="./mock_build.1.2.log"`), so the
        leading `./` is stripped: the names are fed straight back into
        download_log / read_log_* , whose filename whitelist rejects anything
        carrying a path separator.
        """
        url = self._log_base_url(build_id) + "/"
        r = await self._http.get(url)
        r.raise_for_status()
        return re.findall(r'href="(?:\./)?([^"/]+\.(?:log|cfg))"', r.text)

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

    def _open_log(self, build_id: int, filename: str) -> Path:
        """Resolve a downloaded log's path, or raise if it is not on disk yet."""
        path = self._log_path(build_id, filename)
        if not path.exists():
            raise FileNotFoundError(
                f"Log not downloaded yet. Use download_log first: {filename}"
            )
        return path

    def read_log_tail(
        self,
        build_id: int,
        filename: str,
        lines: int,
        max_line_chars: int = LOG_MAX_LINE_CHARS,
        before_line: int | None = None,
        max_chars: int = LOG_MAX_RESULT_CHARS,
    ) -> tuple[str, int, int, int]:
        """Read a page of a log, bottom-up. Returns (content, total, first, last).

        With `before_line=None` the page ends at the last line of the file; with
        `before_line=N` it ends at line N-1, so passing back the `first` line of
        the previous page walks the log upward one page at a time with no gap
        and no arithmetic on the caller's side.

        The page holds at most `lines` lines and at most `max_chars` characters
        (0 lifts the budget), filled from the BOTTOM up — the end of a build log
        is the interesting end. `first` is the line actually reached, not the one
        requested, so a page cut short by the budget is continued exactly where
        it stopped instead of skipping the lines it dropped. Each line is
        clipped to `max_line_chars` (0 disables).

        Streams the file once, holding at most `lines` lines, so a 100k+ line
        mock_build log is never materialized.
        """
        path = self._open_log(build_id, filename)
        last_wanted = before_line - 1 if before_line else None
        total = 0
        window: deque[tuple[int, str]] = deque(maxlen=lines if lines > 0 else 0)
        with open(path, "r", errors="replace") as f:
            for lineno, raw in enumerate(f, start=1):
                total = lineno
                # Keep counting past the window so `total` is the real length.
                if last_wanted is not None and lineno > last_wanted:
                    continue
                window.append(
                    (lineno, clip_line(raw.rstrip("\n"), max_line_chars))
                )
        _trim_to_budget(window, max_chars)
        if not window:
            return "", total, 0, 0
        return (
            "\n".join(text for _, text in window),
            total,
            window[0][0],
            window[-1][0],
        )

    def read_log_range(
        self,
        build_id: int,
        filename: str,
        start_line: int,
        end_line: int,
        max_line_chars: int = LOG_MAX_LINE_CHARS,
        max_chars: int = LOG_MAX_RESULT_CHARS,
    ) -> tuple[str, int, int]:
        """Read a specific range. Returns (content, total_lines, last_line).

        Reads forward from `start_line`, so a range too large for `max_chars`
        (0 lifts the budget) stops early and reports `last_line` — the caller
        continues from `last_line + 1` rather than receiving an oversized result
        or silently losing the rest. Streams the file once, collecting only the
        requested window. Each line is clipped to `max_line_chars` (0 disables).
        """
        path = self._open_log(build_id, filename)
        s = max(0, start_line - 1)
        collected: list[str] = []
        size = 0
        last = 0
        total = 0
        with open(path, "r", errors="replace") as f:
            for i, line in enumerate(f):
                total = i + 1
                if not (s <= i < end_line):
                    continue
                if last and max_chars > 0 and size > max_chars:
                    continue  # budget spent; keep counting for `total`
                text = clip_line(line.rstrip("\n"), max_line_chars)
                collected.append(text)
                size += len(text) + 1
                last = i + 1
        return "\n".join(collected), total, last

    def search_log(
        self,
        build_id: int,
        filename: str,
        pattern: str,
        *,
        before: int = 0,
        after: int = 0,
        max_matches: int = LOG_SEARCH_MAX_MATCHES,
        max_line_chars: int = LOG_MAX_LINE_CHARS,
        max_chars: int = LOG_MAX_RESULT_CHARS,
        noise: str | None = LOG_NOISE_PATTERN,
    ) -> tuple[list[list[LogLine]], int, int, int]:
        """Grep a log. Returns (hunks, total_matches, total_lines, omitted).

        Each hunk is a run of lines — a match plus its `before` / `after`
        context — with hunks from neighbouring matches merged. Only the first
        `max_matches` matches are reported (the first error is the root cause;
        the rest are cascades), but `total_matches` counts every match in the
        file so the caller can say the report was capped.

        The regex is applied to the FULL line, then the line is clipped around
        the match, so a diagnostic hidden inside a multi-KB command line is
        still found and still visible.

        `noise` is a second regex of boilerplate — compiler invocations,
        recursive-make chatter — dropped from CONTEXT only; a matching line is
        always reported. `omitted` counts context lines dropped this way (pass
        noise=None to keep them). Reporting also stops once `max_chars` is spent
        (0 lifts the budget), which keeps a pathological pattern from returning
        an oversized result; `total_matches` still counts what was skipped.
        Streams the file once; memory is bounded by `before` + the reported hunks.
        """
        path = self._open_log(build_id, filename)
        rx = re.compile(pattern)
        noise_rx = re.compile(noise) if noise else None
        hunks: list[list[LogLine]] = []
        pending: deque[tuple[int, str]] = deque(maxlen=max(0, before))
        after_left = 0
        region_end = 0  # last line number covered by the hunk still being built
        total_matches = 0
        omitted = 0
        shown = 0
        size = 0
        total = 0
        with open(path, "r", errors="replace") as f:
            for lineno, raw in enumerate(f, start=1):
                total = lineno
                line = raw.rstrip("\n")
                match = rx.search(line)
                if match is not None:
                    total_matches += 1
                spent = max_chars > 0 and size >= max_chars
                if match is not None and shown < max_matches and not spent:
                    shown += 1
                    block: list[LogLine] = [
                        (n, clip_line(t, max_line_chars), False)
                        for n, t in pending
                        if lineno - n <= LOG_SEARCH_BEFORE_SPAN
                    ]
                    block.append(
                        (lineno, clip_line(line, max_line_chars, match.start()), True)
                    )
                    if hunks and block[0][0] <= region_end + 1:
                        before_merge = _hunk_size(hunks[-1])
                        _extend_hunk(hunks[-1], block)
                        size += _hunk_size(hunks[-1]) - before_merge
                    else:
                        hunks.append(block)
                        size += _hunk_size(block)
                    pending.clear()
                    after_left = after
                    region_end = lineno
                    continue
                is_noise = noise_rx is not None and noise_rx.search(line) is not None
                if after_left > 0:
                    # Count down over every line, boilerplate included, so the
                    # window stays a fixed distance from the match instead of
                    # wandering forward in search of printable lines.
                    after_left -= 1
                    region_end = lineno
                    if is_noise:
                        omitted += 1
                        continue
                    text = clip_line(line, max_line_chars)
                    _extend_hunk(hunks[-1], [(lineno, text, False)])
                    size += len(text) + 1
                    continue
                if not is_noise:
                    pending.append((lineno, line))
        return hunks, total_matches, total, omitted

    # ── Authenticated (JWT required) ──────────────────────────────────

    async def get_flavors(self) -> dict[str, int]:
        """Get {flavor_name: flavor_id} mapping from ALBS. Requires JWT.

        ALBS moved /platform_flavors/ behind authentication, so the request
        must include the Bearer token or the server responds with 403
        {"detail": "Not authenticated"}.
        """
        return {f["name"]: f["id"] for f in await self.get_flavor_records()}

    async def get_flavor_records(self) -> list[dict[str, Any]]:
        """Raw platform flavor records (id, name, modularity, data). Requires JWT."""
        r = await self._http.get(
            f"{ALBS_API}/platform_flavors/", headers=self._auth_headers
        )
        r.raise_for_status()
        return r.json()

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
        task_mock_options: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Create a build of *packages* (``{name_or_url: ref}`` each). Requires JWT.

        *task_mock_options* are per-task mock_options, by the package's key
        (build_configs overrides); ALBS applies them on top of the build's.
        """
        if not from_tag and not branch and not from_srpm:
            raise ValueError("At least one of branch, from_tag, or from_srpm must be set")
        if from_tag and branch:
            raise ValueError("from_tag and branch cannot be used together")

        platform_entries = await self._platform_entries(
            platforms, arch_list, independent_tasks
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
                opts = {
                    k: (dict(v) if isinstance(v, dict) else v)
                    for k, v in (task_mock_options or {}).get(pkg_name, {}).items()
                }
                if add_epel_dist and (from_tag or from_srpm):
                    dist = extract_el_version(pkg_name)
                    if dist:
                        opts.setdefault("definitions", {})["dist"] = (
                            f"{dist}.alma_altarch"
                        )
                if opts:
                    task["mock_options"] = opts
                tasks.append(task)

        data: dict[str, Any] = {
            "platforms": platform_entries,
            "tasks": tasks,
            "is_secure_boot": secureboot,
            "product_id": 1,
        }
        if linked_builds:
            data["linked_builds"] = linked_builds
        mock_options = self._build_mock_options(
            excludes, definitions, with_opts, without_opts, modules
        )
        if mock_options:
            data["mock_options"] = mock_options
        flav_ids = await self._flavor_ids(platforms, beta, additional_flavors)
        if flav_ids:
            data["platform_flavors"] = flav_ids
        return await self._post_build(data)

    async def get_module_preview(
        self,
        module_url: str,
        branch: str,
        platform: str,
        arch_list: list[str],
        flavor_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Ask ALBS how it would build a module branch. Requires JWT.

        POST /builds/get_module_preview/ reads modules.yaml of the module
        git branch and returns the component refs (with their branches and
        buildopts macros), the modulemd template (`modules_yaml`, module +
        -devel) and its dependencies (`enabled_modules`, "name:stream" —
        "name:" when the template allows any stream). Nothing is created.
        """
        payload = {
            "ref": {"url": module_url, "git_ref": branch, "ref_type": 1},
            "platform_name": platform,
            "platform_arches": arch_list,
            "flavors": flavor_ids or [],
        }
        r = await self._http.post(
            f"{ALBS_API}/builds/get_module_preview/",
            json=payload,
            headers=self._auth_headers,
            timeout=MODULE_PREVIEW_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()

    async def create_module_build(
        self,
        module_url: str,
        branch: str,
        platform: str,
        arch_list: list[str] | None = None,
        dependency_streams: list[str] | None = None,
        module_platform_version: str | None = None,
        module_version: str | None = None,
        beta: bool = False,
        additional_flavors: list[str] | None = None,
        excludes: list[str] | None = None,
        definitions: dict[str, str] | None = None,
        linked_builds: list[int] | None = None,
        independent_tasks: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Create a module build the way the ALBS web UI does. Requires JWT.

        Validates the platform/arches/flavors, takes the module preview for
        `branch`, resolves the dependency streams (build_module_task) and
        posts one module task. ALBS then computes version, context and the
        `.module_elX...` dist itself. Returns (created build, module task
        that was sent).
        """
        platform_entries = await self._platform_entries(
            [platform], arch_list, independent_tasks
        )
        flav_ids = await self._flavor_ids([platform], beta, additional_flavors)
        versions = await self._modularity_versions(platform, flav_ids)
        if not versions:
            raise ValueError(
                f"Platform '{platform}' has no modularity versions on ALBS — "
                f"module builds are not supported there."
            )
        if module_platform_version is None:
            module_platform_version = versions[-1]
        elif module_platform_version not in versions:
            raise ValueError(
                f"Unknown module_platform_version '{module_platform_version}' "
                f"for {platform}. Available: {versions}"
            )
        preview = await self.get_module_preview(
            module_url, branch, platform,
            platform_entries[0]["arch_list"], flav_ids,
        )
        task = build_module_task(
            preview, dependency_streams, module_platform_version, module_version
        )
        data: dict[str, Any] = {
            "platforms": platform_entries,
            "tasks": [task],
            "is_secure_boot": False,
            "product_id": 1,
        }
        if linked_builds:
            data["linked_builds"] = linked_builds
        mock_options = self._build_mock_options(excludes, definitions)
        if mock_options:
            data["mock_options"] = mock_options
        if flav_ids:
            data["platform_flavors"] = flav_ids
        return await self._post_build(data), task

    async def _post_build(self, data: dict[str, Any]) -> dict[str, Any]:
        r = await self._http.post(
            f"{ALBS_API}/builds/", json=data, headers=self._auth_headers
        )
        r.raise_for_status()
        return r.json()

    async def _platform_entries(
        self,
        platforms: list[str],
        arch_list: list[str] | None,
        independent_tasks: bool,
    ) -> list[dict[str, Any]]:
        """Validate platforms + arches against ALBS; build payload entries.

        Without an arch_list each platform gets its default arches: the full
        ALBS list minus its opt-in arches (riscv64 on AlmaLinux-10), which
        stay valid in an explicit arch_list.
        """
        platform_arches = await self.get_platform_arches()
        entries: list[dict[str, Any]] = []
        for plat in platforms:
            if plat not in platform_arches:
                raise ValueError(
                    f"Unknown platform '{plat}'. "
                    f"Available: {', '.join(sorted(platform_arches))}"
                )
            allowed = platform_arches[plat]
            arches = arch_list or default_arches(plat, allowed)
            bad = [a for a in arches if a not in allowed]
            if bad:
                raise ValueError(
                    f"Arch(es) {bad} not allowed for {plat}. Allowed: {allowed}"
                )
            entries.append({
                "name": plat,
                "arch_list": arches,
                "parallel_mode_enabled": True,
                "independent_tasks": independent_tasks,
            })
        return entries

    @staticmethod
    def _build_mock_options(
        excludes: list[str] | None = None,
        definitions: dict[str, str] | None = None,
        with_opts: list[str] | None = None,
        without_opts: list[str] | None = None,
        modules: list[str] | None = None,
    ) -> dict[str, Any]:
        """Build-level mock_options; empty values are left out."""
        options = {
            "yum_exclude": excludes,
            "definitions": definitions,
            "with": with_opts,
            "without": without_opts,
            "module_enable": modules,
        }
        return {k: v for k, v in options.items() if v}

    async def _flavor_ids(
        self,
        platforms: list[str],
        beta: bool,
        additional_flavors: list[str] | None,
    ) -> list[int]:
        """Resolve beta + named flavors to ALBS ids (validated, de-duplicated)."""
        if not beta and not additional_flavors:
            return []
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
        seen: set[int] = set()
        return [i for i in flav_ids if not (i in seen or seen.add(i))]

    async def _modularity_versions(
        self, platform: str, flavor_ids: list[int]
    ) -> list[str]:
        """Modularity platform versions ('8.4' … '8.10') usable for a module.

        Ordered so the last one is the default ALBS and its UI pick: the
        platform's newest, unless a selected flavor (e.g. a beta) brings its
        own versions — then that flavor's newest.
        """
        record = next(
            (p for p in await self.get_platforms() if p["name"] == platform),
            None,
        )
        if record is None or not record.get("modularity"):
            return []
        names = [v["name"] for v in (record.get("data") or {}).get("versions", [])]
        if flavor_ids:
            for flavor in await self.get_flavor_records():
                if flavor["id"] not in flavor_ids:
                    continue
                for v in (flavor.get("data") or {}).get("versions", []):
                    if v["name"] in names:
                        names.remove(v["name"])
                    names.append(v["name"])
        return names

    async def sign_build(self, build_id: int, sign_key_id: int = 4) -> dict[str, Any]:
        r = await self._http.post(
            f"{ALBS_API}/sign-tasks/",
            json={"build_id": build_id, "sign_key_id": sign_key_id},
            headers=self._auth_headers,
        )
        r.raise_for_status()
        return r.json()

    async def create_release(
        self,
        build_ids: list[int],
        build_task_ids: list[int],
        platform_id: int,
        product_id: int,
    ) -> dict[str, Any]:
        """Create a release PLAN in ALBS (status: scheduled). Requires JWT.

        Posts to /releases/new/, which creates the scheduled release record
        and computes the plan (which packages go to which repositories). It
        does NOT publish anything — publishing happens only when the plan is
        committed, which this client intentionally never does. Returns the
        created release dict (id, status, plan, ...).
        """
        payload = {
            "builds": build_ids,
            "build_tasks": build_task_ids,
            "platform_id": platform_id,
            "product_id": product_id,
        }
        r = await self._http.post(
            f"{ALBS_API}/releases/new/", json=payload, headers=self._auth_headers
        )
        r.raise_for_status()
        return r.json()

    async def create_errata(self, record: dict[str, Any]) -> dict[str, Any]:
        """Queue a new advisory on ALBS (POST /errata/new/). Requires JWT.

        ALBS only validates the payload and hands it to a background worker,
        so the response is {"ok": bool, "error": str | None} — the record
        shows up in /errata/query/ once the worker has stored it.
        """
        r = await self._http.post(
            f"{ALBS_API}/errata/new/", json=record, headers=self._auth_headers
        )
        r.raise_for_status()
        return r.json()

    async def close(self):
        await self._http.aclose()
