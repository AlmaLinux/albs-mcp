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

SIGN_TASK_STATUS = {
    1: "idle",
    2: "in_progress",
    3: "completed",
    4: "failed",
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
