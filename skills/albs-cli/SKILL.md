---
name: albs-cli
description: >-
  Use the `albs` CLI to work with AlmaLinux Build System (build.almalinux.org).
  Investigate build failures, create builds, sign packages, create release plans
  via shell commands. Use whenever the user asks about ALBS builds, build
  failures, build logs, package building status, or wants to create/sign builds
  or create a release plan. Also use when the user says "albs", "build failed",
  "why did the build fail", "build a package", "sign a build", "release plan",
  or mentions build IDs in the context of AlmaLinux.
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
albs log-search BUILD_ID FILENAME [-e RE]   # grep a log for the failure, with context
albs log-tail BUILD_ID FILENAME             # a page from the end; --before-line N pages up
albs log-range BUILD_ID FILENAME START END  # specific line range
albs search [--project NAME] [--page N]     # search builds
albs sign-keys                              # list sign keys (requires JWT)
albs flavors                                # list platform flavors
albs create-build PLATFORM PKG [PKG...]     # create build (requires JWT)
albs sign-build BUILD_ID [--key-id N]       # sign build (requires JWT)
albs products                               # list products (release targets)
albs release-plan RELEASE_ID                # view an existing release plan
albs create-release-plan BUILD_ID \         # create a release plan (requires JWT)
  --platform NAME --product NAME            #   (never performs the actual release)
```

Authentication: `--token TOKEN` flag or `ALBS_JWT_TOKEN` env var.

## Investigating build failures (most common workflow)

Follow this exact order:

1. `albs build-info BUILD_ID` — see all tasks and statuses.
2. `albs failed-tasks BUILD_ID` — see failed tasks with log file names. Logs marked with ★ are key: mock_root, mock_stderr, mock_build.
3. `albs log-search BUILD_ID FILENAME` — grep the log for the standard failure signatures (compiler errors, failed patch hunks, unresolved BuildRequires, RPM errors, %check failures, OOM/network trouble) and get each hit with its line number and context. It auto-downloads, so `download-log` is optional. Run it on mock_root, mock_stderr, AND mock_build.
4. `albs log-tail BUILD_ID FILENAME` — see how the build terminated. Do NOT stop here: `make -j` keeps compiling after the first failure, so the tail of a mock_build log shows only `make: *** [Makefile:NNNN: all] Error 2` — the symptom. Reporting that as the root cause is a wrong answer.
5. `albs log-range BUILD_ID FILENAME START END` — widen the context around a line number log-search reported.
6. When the search finds nothing and you must READ the log, page it bottom-up: run `albs log-tail`, then copy the `↑ earlier:` command printed at the bottom of the output, and repeat. Pages are sized to the result budget and join up exactly — guessing a `log-range` window is how you miss the error.
7. Report the failing file, line, and diagnostic — not the make wrapper error.

IMPORTANT: mock_build logs can be 100k+ lines with single lines several KB long. NEVER read the whole file. Lines are clipped to 500 chars and each result to 40000 chars (`--max-line-chars 0` / `--max-chars 0` to lift); leave both on for broad reads and page instead.

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

## Creating release plans

This CLI can CREATE a release plan but NEVER performs the actual release (it does not commit/publish). Creating a plan is safe — ALBS records a "scheduled" release and computes which packages go where, but nothing is published.

1. `albs build-info BUILD_ID` — confirm the platform and that the build has completed tasks.
2. `albs products` — list products (release targets) so the user can pick one. ASK the user for the target platform and product.
3. `albs create-release-plan BUILD_ID --platform NAME --product NAME` — collects the completed build tasks automatically and creates the scheduled plan.
4. For a PARTIAL build superseded by a 'retry failed' build, add `--whole-packages-only` so only packages whose every arch task completed are included.
5. Report the plan (status, source packages, target repositories). Make clear that NOTHING was published — it is only a plan.
6. `albs release-plan RELEASE_ID` — view an existing plan later.

```bash
albs products
albs create-release-plan 62316 --platform AlmaLinux-8 --product AlmaLinux
```

The actual release (`albs commit-release`) is intentionally blocked — only plans are supported.

## Important

- Read-only commands work without authentication.
- Build/release-plan creation, signing, and sign key listing require a JWT token.
- `products`, `release-plan` (viewing) are read-only.
- Build deletion is intentionally blocked.
- Performing/committing a release is intentionally blocked — only release plans are created, never the actual release.
- Platform names are case-sensitive (e.g. `AlmaLinux-Kitten-10`). Use `albs platforms` to verify.
