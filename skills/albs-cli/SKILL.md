---
name: albs-cli
description: >-
  Use the `albs` CLI to work with AlmaLinux Build System (build.almalinux.org).
  Investigate build failures, create builds, sign packages via shell commands.
  Use whenever the user asks about ALBS builds, build failures, build logs,
  package building status, or wants to create/sign builds. Also use when the
  user says "albs", "build failed", "why did the build fail", "build a package",
  "sign a build", or mentions build IDs in the context of AlmaLinux.
---

# ALBS CLI

CLI for AlmaLinux Build System. Use `albs` commands via Shell when the ALBS MCP server is not enabled.

## Commands

```
albs platforms                              # list platforms and architectures
albs build-info BUILD_ID                    # build details, tasks, statuses
albs failed-tasks BUILD_ID                  # failed tasks with log files
albs build-logs BUILD_ID                    # list all log files on server
albs download-log BUILD_ID FILENAME         # download a log file
albs log-tail BUILD_ID FILENAME [-n LINES]  # last N lines (default 3000)
albs log-range BUILD_ID FILENAME START END  # specific line range
albs search [--project NAME] [--page N]     # search builds
albs sign-keys                              # list sign keys (requires JWT)
albs flavors                                # list platform flavors
albs create-build PLATFORM PKG [PKG...]     # create build (requires JWT)
albs sign-build BUILD_ID [--key-id N]       # sign build (requires JWT)
```

Authentication: `--token TOKEN` flag or `ALBS_JWT_TOKEN` env var.

## Investigating build failures (most common workflow)

Follow this exact order:

1. `albs build-info BUILD_ID` — see all tasks and statuses.
2. `albs failed-tasks BUILD_ID` — see failed tasks with log file names. Logs marked with ★ are key: mock_root, mock_stderr, mock_build.
3. `albs download-log BUILD_ID FILENAME` — download the key log. Start with mock_root (dependency issues), then mock_stderr, then mock_build.
4. `albs log-tail BUILD_ID FILENAME` — read from the end. Errors are almost always at the bottom. Default is 3000 lines.
5. If the root cause is not visible, use `albs log-range` to look at earlier sections.

IMPORTANT: mock_build logs can be 100k+ lines. NEVER read the whole file. Always use log-tail first.

## Creating builds

ASK the user for: package name(s), platform, and build method (branch/tag/SRPM). If architectures are not specified, do NOT ask — use platform defaults.

```bash
# From branch
albs create-build AlmaLinux-9 bash --branch c9s

# From tag (format: "pkg_name tag_name" in quotes)
albs create-build AlmaLinux-9 "bash imports/c9s/bash-5.1-1.el9" --from-tag

# From SRPM URL
albs create-build AlmaLinux-10 https://example.com/pkg.src.rpm --from-srpm

# Multiple packages
albs create-build AlmaLinux-9 bash glibc openssl --branch c9s

# Skip tests
albs create-build AlmaLinux-9 bash --branch c9s --skip-tests

# Independent tasks (packages build in parallel within the platform,
# instead of the default sequential per-platform chain)
albs create-build AlmaLinux-9 bash glibc openssl --branch c9s --independent-tasks
```

## Building EPEL packages

When building from EPEL SRPMs (dl.fedoraproject.org/pub/epel/):

1. ASK the user if they want to enable `--add-epel-dist`, UNLESS they already mentioned it.
2. Add correct EPEL flavors:
   - AlmaLinux-10: `--flavor EPEL-10 --flavor EPEL-10_altarch`
   - AlmaLinux-Kitten-10: `--flavor EPEL-10 --flavor EPEL-Kitten_altarch`
3. Use `--arch x86_64_v2` unless the user specified different architectures.

```bash
albs create-build AlmaLinux-10 https://dl.fedoraproject.org/.../pkg.src.rpm \
  --from-srpm --add-epel-dist --arch x86_64_v2 \
  --flavor EPEL-10 --flavor EPEL-10_altarch
```

## Signing builds

1. `albs build-info BUILD_ID` — present summary to user: platform, arches, packages, flavors.
2. `albs sign-keys` — show available keys.
3. If the build has EPEL*_altarch flavors and only x86_64_v2 — tell the user this is an EPEL-altarch build, suggest EPEL key.
4. ASK user to confirm key before signing.
5. `albs sign-build BUILD_ID --key-id N`

## Important

- Read-only commands work without authentication.
- Build creation, signing, and sign key listing require a JWT token.
- Build deletion is intentionally blocked.
- Platform names are case-sensitive (e.g. `AlmaLinux-Kitten-10`). Use `albs platforms` to verify.
