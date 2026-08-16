# Changelog

All notable changes to this project. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/).

## [2.1.0] — 2026-08-16

### Added — cron jobs as first-class coordination participants
- `coord_guard.sh`: zero-token, fail-open guard sourced as step 0 of cron wrapper
  scripts. Per-job policy `skip` (watchdogs: skip the tick instantly) or `wait`
  (backups: bounded wait for the holder, then proceed). Conflicted agentic jobs
  defer **before** any LLM starts. Guard proceeds unguarded on ANY internal error —
  a broken board never blocks a backup.
- `cron-guard` CLI verb backing the shell guard: registers an ephemeral cron
  session, atomically claims the job's declared resources, prints only the
  coordination id on stdout (safe for `no_agent` stdout-as-message jobs).
- Cron resource manifest (`cron_resources.json`): declares per-job resources,
  guard policy, and a `critical` flag. Missing or malformed manifest leaves every
  cron feature inert (fail-open).
- Claim-time **CRON ADVISORY**: claiming a resource an upcoming cron touches warns
  about the clash (job name, fire time, overlapping resources). 12-hour cron radar
  in `status` with per-job conflict flags.
- **Critical-cron decision brief**: critical jobs never defer silently — the
  advisory lists explicit options (finish & release / `wait-for-cron` / pause with
  user approval / trigger early) and instructs the session to ask the user.
- `wait-for-cron`: block (poll) until a job's fire is observed in the audit table.
- `cron-note`: responsibility ledger for pausing/resuming cron jobs; `done` nags a
  departing session about any cron it paused but never resumed.
- `cron_events` audit table: every guard decision (`deferred` / `guarded-run` /
  `fail-open`) is recorded.
- Crons appear as `[CRON JOB]` holders; `preempt` refuses to target them (pausing
  a scheduled job is a scheduler operation requiring user approval, not a peer
  negotiation).
- Polite holder notification when a cron defers: names the job, the held resource,
  and that the tick was skipped.

### Fixed
- **Rank fencing lost at the release boundary** (found by adversarial agent
  review): waiter rows now stay active across a release so queue order
  (rank, then FIFO) survives; replaced the 10-minute waiter liveness window with a
  30-second poll-freshness gate (`waiters.last_poll` column, additive migration) —
  only actively-polling waiters fence; paused resume spots remain exempt.
- **Children not notified on inherited re-rank** (same review): `prioritize` now
  diffs *effective* ranks including lineage-derived children and notifies every
  session whose effective rank moved.

### Portability / hardening
- UTF-8-safe stdio (`errors="replace"`): hostile consoles (C locale, cp1252,
  `PYTHONIOENCODING=ascii`) degrade to `?` placeholders instead of crashing.
- Windows path normalization in resource keys (drive-letter and UNC absolutes,
  case-insensitive canonicalization on Windows; identity on POSIX).
- Python 3.8–3.13 verified (removed a 3.11-only `fromisoformat` dependency;
  fixed a 3.13 `SyntaxWarning`).
- 100% function docstring coverage; `bandit` 0 issues; strict `ruff` profile
  (E,F,W,I,UP,SIM,ISC,RUF,B) 0 findings; `shellcheck` clean.

### Tests
- 38-check cron-leg suite (`selftest_cron.sh`) on scratch DB + scratch manifest +
  scratch cron store. Full matrix now 83 checks (19 + 26 + 38), run green on
  macOS and Ubuntu.

## [2.0.0] — 2026-08-16

### Added — user-set priority, preemption, pause/resume, subagent lineage
- `prioritize`: human-only priority ranks (bulk `--order "a=1,b=2"`, single,
  clear). Ranks are lexicographic tuples: `1 < 1a < 1b < 2 < unranked`.
- Subagent lineage: `register --parent <id> --slot <a-z>` derives child ranks
  (`1a`, `1b`) **live** from the parent's current rank; re-ranking a parent
  instantly re-ranks its children; parent outranks its own children.
- `preempt`: checkpoint-safe priority hand-off — requires a user-recorded rank,
  only affects strictly-lower-ranked holders, sends a deduplicated
  `USER-PRIORITY REQUEST`, and queues the requester as a waiter. Never seizes.
- `pause` / `resume`: paused claims stop blocking but keep a reserved resume spot
  and a checkpoint note returned verbatim on resume; paused claims still TTL-expire
  with a warning. `resume` re-acquires all paused claims atomically.
- **Fencing**: a claim on a free resource is `QUEUED` (rc 75) when a live,
  actively-polling waiter with strictly better rank — or equal rank and earlier
  queue position — waits in a conflicting mode. The release boundary honors the
  priority queue, not the poll lottery.
- FIFO tie-break at equal rank; ranked sessions order ahead of unranked.
- Urgent-alert channel: any board touch with unread `preempt_request`/`priority`
  notifications prints `⚠ N urgent notification(s) pending` on **stderr**.
- v1-schema databases upgrade in place via additive `ALTER TABLE` migrations
  (pinned-fixture test); with no ranks recorded, behavior is byte-for-byte v1.

### Tests
- 26-check priority/lineage/fencing/migration suite (`selftest_priority.sh`).

## [1.0.0] — 2026-08-15

### Added — the core cooperative board
- SQLite (WAL) registry: sessions / claims / waiters / notifications.
- `register`, `claim` (atomic all-or-nothing multi-resource, `shared`/`exclusive`
  modes, TTL), `check`, `wait`, `release`, `done`, `inbox`, `status`, `steal`.
- Typed resource keys (`file:` with directory-boundary conflict detection,
  `skill:`, `memory`, `ui:`, `box:`, `cron-store`) with path canonicalization.
- `claim --wait`: polite single-invocation polling; release notifications to
  waiters ("released by co-worker").
- Crash safety: per-claim TTL reaping with expired-holder warnings, stale-session
  cleanup (24 h), notification pruning (7 d). No daemon — all maintenance lazy.
- sysexits contract: rc 0 = acquired, rc 75 (`EX_TEMPFAIL`) = busy/queued.
- 19-check regression suite (`selftest.sh`).
