# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versioning follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [2.6.2] — 2026-11-05

### Fixed

- The managed memory rule now registers with
  `ID=$(python3 <cli> register --task '...' --surface <surface> | head -1)`.
  `register` prints the session ID followed by co-worker notes, so capturing
  its whole output put those notes into `$ID` whenever other sessions were
  active, and the next `claim --id "$ID"` failed. The installer upgrades a
  2.6.1 block in place, `status` still counts it as wired, and the
  post-install quick-start example has the same fix.

## [2.6.1] — 2026-11-04

### Changed

- Documented two claim-boundary facts in the skill's pitfalls. A job's footprint
  is wider than the resource set it declares: individual stages can run their own
  guards over resources the job never declares, so while a job you triggered is
  still running, claim nothing any of its stages touch — a held `skill:<name>`
  claim can silently defer such a stage (exit 75) while every other stage passes.
- Documented that the post-turn background skill review does not consult the
  board. A `skill:<name>` claim therefore never freezes that skill: re-read it
  immediately before every write, and treat a failed exact-match patch as proof
  the file moved rather than a reason to widen the match.

## [2.6.0] — 2026-09-25

### Added

- Added an interactive installer choice for keeping the current plugin state,
  installing or upgrading optional native integration, removing only that
  integration, or canceling. Non-interactive runs keep the current state unless
  an action is explicit.
- Added plugin-only removal that preserves the standalone board, claims, skills,
  receipt state, shared watchdog, unrelated plugins, and profile configuration.
  Recovery copies and exact state readback protect removal and repeated runs.
- Added `hermes_setup.py` for explicit, profile-scoped native-integration checks
  and setup. It validates the external `session-coord-native` checkout through
  Hermes Plugin Doctor, uses sanctioned plugin and configuration commands, and
  distinguishes on-disk configuration from activation after restart.
- Added an optional shared watchdog setup that delegates to the plugin-owned
  `watchdog-setup` command and refuses blind retries after uncertain creation.
- Added durable native wake records, exact receiver receipts, cancellation
  fencing, whole-request reacquisition, and script-only wake reconciliation.

### Changed

- Installer enrollment now uses versioned `board-v2` and `bot-board-v2` managed
  blocks. Recognized wire-v1 and bot-wire-v1 text is migrated with backups while
  customized, duplicated, or malformed blocks remain unchanged and return
  `ACTION NEEDED`.
- Default enrollment is board-only. Native yield instructions are written only
  after every selected profile proves the required host and plugin capabilities.
- Enrollment audits now require exact managed blocks, preserve file modes, and
  use compare-and-swap checks before replacing a carrier.
- Generated shell snippets preserve custom paths as single literal arguments.
- Native setup validates plugin identity, source cleanliness, installed revision,
  exact Boolean joined-delegation policy, and plugin-owned capability receipts.
- Documentation separates the stable framework-neutral board from optional,
  unreleased native continuation.

### Compatibility

- Existing board CLI contracts, SQLite state, cron manifests, script-only guards,
  custom paths, opt-outs, and legacy JSON contracts remain supported.
- Native continuation requires the separate `session-coord-native` plugin and a
  compatible Hermes host; board-only operation does not.

## [2.5.0] — 2026-09-19

### Added

- Added `heartbeat` to refresh a quiet holder's liveness without changing claims.
- Added stale-session reaping that releases abandoned claims with an audited
  `reaped` state and warns actors to verify the real resource.
- Added `coord_run.sh`, a one-command runner that registers, claims an atomic
  resource set, runs a command, and guarantees `done` on normal exit, failure, or
  termination.

### Changed

- The default stale-session window is 60 minutes and can be overridden with
  `HERMES_COORD_STALE_S`.
- `status` refreshes caller liveness, and cleanup includes reaped claims.
- `claim` rejects malformed session ids before they can create unusable sessions.

## [2.4.0] — 2026-08-31

### Added

- The installer now enrolls existing bot profiles through `SOUL.md` and non-bot
  profiles through their own memory stores.
- `status` reports persona profiles without the current bot block as `UNENROLLED`
  and non-bot profiles without the current board block as `UNWIRED`.

### Changed

- Bot enrollment text now limits claim-free access to the profile's internal
  memory, sessions, and cron store; files in shared locations still require claims.

## [2.3.3] — 2026-08-30

### Added

- Added the standard installable Hermes skill layout under
  `skills/multi-session-coordination/`, including scripts, references, templates,
  and examples.
- The full installer now copies the skill bundle and can wire the canonical board
  instruction into the default memory store.

### Changed

- Skill and memory enrollment are idempotent, preserve existing content, and back
  up a changed carrier before replacement.

## [2.3.2] — 2026-08-26

### Added

- `register --id` now accepts validated caller-supplied session ids and supports
  idempotent re-registration.

### Fixed

- Claims made with a previously unknown id now create a minimal session row so
  they can always be released with `release` or `done`.

## [2.3.1] — 2026-08-26

### Fixed

- Commands that target an existing session now resolve a full id or unique prefix
  and fail on missing or ambiguous matches instead of silently doing nothing.

## [2.3.0] — 2026-08-26

### Added

- Added `switch`, `enable`, `disable`, and `switch toggle` for globally bypassing
  coordination without uninstalling it or deleting state.
- Added a standard-library installer with dry-run, relocation, upgrade backup,
  state preservation, and optional self-verification.
- Added ready-to-use bot and subagent enrollment examples.

### Changed

- When coordination is disabled, board verbs and the shell guard preserve their
  success/output contracts while acting as fail-open no-ops.
- `HERMES_COORD_DISABLED` overrides the persistent disabled sentinel for one
  process tree.

## [2.2.0] — 2026-08-18

### Added

- Added shared coordination for persistent bot profiles, their sub-sessions, and
  their profile-specific scheduled jobs.
- Cron discovery now merges the default store with profile stores and identifies
  profile-owned jobs as `[bot:<profile>]`.
- Added bot and subagent protocol examples for explicit registration, atomic
  claims, and cleanup.

### Changed

- Duplicate cron job ids resolve deterministically, with the default store taking
  precedence and profile stores ordered by profile name.
- Missing, unreadable, or malformed profile stores remain fail-open.

## [2.1.0] — 2026-08-16

### Added

- Added `coord_guard.sh` and `cron-guard` so scheduled jobs can claim declared
  resources before starting model-backed work.
- Added a cron resource manifest, upcoming-job advisories, a 12-hour cron radar,
  `wait-for-cron`, `cron-note`, and a durable cron decision audit.
- Added user-set priorities, queue fencing across release boundaries, and
  inherited priority notifications for child sessions.

### Changed

- File resource keys support POSIX, Windows drive-letter, and UNC paths.
- UTF-8 output degrades safely on restricted consoles.
- The Python implementation supports Python 3.8 through 3.13.

## [2.0.0] — 2026-08-16

### Added

- Added user-set priority ranks, inherited subagent ranks, cooperative preemption,
  checkpointed pause/resume, and explicit break-glass release.
- Added FIFO queue fencing among equal-ranked waiters and urgent notifications for
  priority and preemption changes.
- Added additive migration from the v1 database schema.

## [1.0.0] — 2026-08-15

### Added

- Added the SQLite intention board with sessions, atomic resource claims, waiters,
  notifications, TTL expiry, and stale-session cleanup.
- Added `register`, `claim`, `check`, `wait`, `release`, `done`, `inbox`, `status`,
  and `steal` commands.
- Added typed resource keys with directory-boundary conflict detection and the
  exit-code contract of 0 for success and 75 for held or queued resources.
