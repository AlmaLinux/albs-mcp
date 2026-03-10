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
