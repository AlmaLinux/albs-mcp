ALBS_URL = "https://build.almalinux.org"
ALBS_API = f"{ALBS_URL}/api/v1"
ALBS_LOGS_BASE = f"{ALBS_URL}/pulp/content/build_logs"

BUILD_TASK_STATUS = {
    0: "idle",
    1: "started",
    2: "completed",
    3: "failed",
    4: "excluded",
    5: "cancelled",
}

BUILD_TASK_STATUS_BY_NAME = {v: k for k, v in BUILD_TASK_STATUS.items()}

# Build task status that means "built successfully". A release plan may only
# include tasks in this state — half-built packages must never leak into a plan.
BUILD_TASK_COMPLETED = 2

SIGN_TASK_STATUS = {
    1: "idle",
    2: "in_progress",
    3: "completed",
    4: "failed",
}

# Release lifecycle status. A freshly created release PLAN is "scheduled";
# it only becomes published after a commit (the actual release), which this
# project intentionally does not perform.
RELEASE_STATUS = {
    1: "scheduled",
    2: "in_progress",
    3: "completed",
    4: "failed",
    5: "reverted",
}

SECURE_BOOT_PACKAGES = [
    "kernel",
    "kernel-rt",
    "grub2",
    "shim",
    "kmod",
    "kmod-kvdo",
    "kmod-redhat-oracleasm",
    "fwupd",
    "fwupd-efi",
    "fwupdate",
    "nvidia-open-kmod",
]

KEY_LOG_TYPES = ["mock_build", "mock_stderr", "mock_root"]

LOG_LINES_PER_CHUNK = 3000

# Per-line character cap for every log read. A mock_build log interleaves
# multi-KB gcc/libtool command lines whose bulk carries no diagnostic value,
# so a 150-line tail can weigh 170 KB and exceed the caller's result-size
# limit — the read is rejected and the real error is never seen. Clipping each
# line keeps a read bounded by lines, not by bytes. Pass max_line_chars=0 to
# opt out when a line is needed verbatim.
LOG_MAX_LINE_CHARS = 500

# Characters of lead-in kept before a match when a clipped line is long enough
# that the match itself would fall outside the window.
LOG_CLIP_LEAD_CHARS = 80

# Hard result-size budget for one log read, in characters (~10k tokens). Per-line
# clipping alone is not enough: even fully clipped, a whole mock_build log is
# ~150 KB and a mock_root ~115 KB. This budget is what makes a page: a read
# returns as many lines as fit, bottom-up for a tail, and reports the line it
# stopped at so the caller can continue from exactly there. So the page adapts to
# the log — the same budget is ~165 lines of mock_build or ~350 of mock_root —
# instead of a fixed line count that is too big for one and too small for the
# other. Pass max_chars=0 to lift it.
LOG_MAX_RESULT_CHARS = 40_000

# Matches reported by one search_log call. The first matches are the root
# cause; later ones are cascades, so the cap keeps the head of the list.
LOG_SEARCH_MAX_MATCHES = 40

# Context lines shown around each search_log match by default. A gcc
# diagnostic spans the `In function ...:` line above and the caret/`note:`
# block of several lines below, so the defaults are asymmetric.
LOG_SEARCH_BEFORE = 2
LOG_SEARCH_AFTER = 8

# How far back a `before` context line may come from. The boilerplate filter
# below can leave the last informative line hundreds of lines behind the match
# (a long stretch of compiler invocations); a line that distant belongs to an
# unrelated part of the build and only misleads, so it is dropped.
LOG_SEARCH_BEFORE_SPAN = 25

# Default search_log pattern: the failure signatures worth finding in any mock
# log, so one call locates the root cause without the caller guessing a regex.
# Ordered by failure class (compile, patch, dependency, rpm, %check, infra) and
# deliberately anchored — bare "error" would match every gcc -Werror flag on a
# command line.
LOG_ERROR_PATTERNS = [
    # compile / link
    r"\berror:",
    r"\bfatal error:",
    r"undefined reference to",
    r"collect2: error",
    r"make(\[\d+\])?: \*\*\*",
    # patch application
    r"Hunk #\d+ FAILED",
    r"can't find file to patch",
    r"malformed patch",
    # dependency resolution
    r"No matching package to install",
    r"nothing provides",
    r"cannot install the best candidate",
    r"Failed build dependencies",
    # rpm packaging
    r"RPM build errors",
    r"Bad exit status",
    r"Installed \(but unpackaged\) file",
    r"File not found",
    # build-system configuration
    r"configure: error:",
    r"CMake Error",
    # %check
    r"^FAIL:",
    r"^not ok\b",
    r"Assertion .* failed",
    # infrastructure
    r"Could not resolve host",
    r"Curl error",
    r"Temporary failure in name resolution",
    r"Segmentation fault",
    r"core dumped",
    r"[Oo]ut of memory",
    r"oom.kill",
    r"Killed$",
]

LOG_ERROR_PATTERN = "|".join(LOG_ERROR_PATTERNS)

# Boilerplate that makes up the bulk of a mock_build log and never carries
# diagnostic value: compiler/libtool invocations and recursive-make chatter.
# These are dropped from search_log CONTEXT only — a line that MATCHES the
# search is always reported, and gaps in the printed line numbers show where
# boilerplate was omitted.
LOG_NOISE_PATTERNS = [
    r"^/bin/sh \./libtool ",
    r"^libtool: (compile|link|install):",
    r"^make\[\d+\]: (Entering|Leaving) directory",
    r"^\s*(gcc|g\+\+|cc|c\+\+|clang) -",
    r"^(CC|CXX|LD|AR|GEN|CCLD)\s+\S",
]

LOG_NOISE_PATTERN = "|".join(LOG_NOISE_PATTERNS)

# ── EPEL build defaults ───────────────────────────────────────────────

EPEL_URL_PATTERN = "dl.fedoraproject.org/pub/epel"

EPEL_PLATFORM_FLAVORS: dict[str, list[str]] = {
    "AlmaLinux-10": ["EPEL-10", "EPEL-10_altarch"],
    "AlmaLinux-Kitten-10": ["EPEL-10", "EPEL-Kitten-10_altarch"],
}

EPEL_DEFAULT_ARCH = ["x86_64_v2"]

# ── Beta flavor map ───────────────────────────────────────────────────
#
# Per-platform beta flavor names. Not every platform has a beta flavor
# (e.g. AlmaLinux-Kitten-10 and CentOS are intentionally absent), so the
# mapping is explicit rather than a `<platform>-beta` pattern.
#
# Names MUST still be validated against the live ALBS API at use time:
# see ALBSClient.create_build, which calls get_flavors() and raises
# ValueError if any name here is no longer present on ALBS.
BETA_PLATFORM_FLAVORS: dict[str, list[str]] = {
    "AlmaLinux-8": ["AlmaLinux-8-beta"],
    "AlmaLinux-9": ["AlmaLinux-9-beta"],
    "AlmaLinux-10": ["AlmaLinux-10-beta"],
}
