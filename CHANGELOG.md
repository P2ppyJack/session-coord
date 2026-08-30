# Changelog

All notable changes to this project. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/).

## [Unreleased]

## [2.3.3] — 2026-08-30

### Added — official Hermes skill packaging

The repo now follows the standard Hermes skill layout, so the project is
installable as an **official skill** through the Skills Hub:

- **New layout**: everything the skill needs moved under
  `skills/multi-session-coordination/` — `SKILL.md` (canonical, house-standard
  frontmatter: author human-first `Tobias Musser (P2ppyJack)`, `platforms:
  [linux, macos]` audited against the POSIX selftests, ≤60-char description)
  plus `references/` (edit-history archive, publishing & CI notes),
  `templates/` (bot SOUL.md blurb, machine-generic), `scripts/` (the engine:
  CLI + guard + 4 selftest suites), and `examples/` (enrollment texts, cron
  manifest, wrapper).
- **Install paths** (README §8): `hermes skills install
  P2ppyJack/session-coord/skills/multi-session-coordination`, tap install, or
  direct-URL install of the raw SKILL.md. A hub install copies SKILL.md +
  referenced support files into `~/.hermes/skills/` (the CLI then lives in the
  skill's `scripts/`); `install.py` remains the full-wiring path.
- **`install.py` step 6**: installs the skill bundle itself (SKILL.md +
  references/ + templates/ + examples/ → `~/.hermes/skills/multi-session-coordination`,
  `--skill-dest` / `--no-skill`) with the same idempotent
  backup-on-change policy as the payload — one command now delivers scripts +
  skill + standing memory rule.
- **CI**: new "skill bundle layout + frontmatter" gate (checks the official
  skill contract: file placement, required frontmatter fields, ≤60-char
  description, referenced files exist in-bundle, no machine-local paths) plus
  an installer idempotency check that the skill bundle lands and a re-run
  creates no backups. All paths moved to the new layout.

### Compatibility

- No engine changes: `session_coord.py`, `coord_guard.sh`, the rc 0/75
  contract, and all 127 selftest checks are unchanged (suites re-run green
  from the new paths). The repo previously had no SKILL.md at all; the skill
  text that lived only in `~/.hermes/skills/` is now canonical in-repo.

## [2.3.2] — 2026-08-26

### Added — automatic agent wiring at install time

The board is advisory, so installing the scripts alone protected nobody:
sessions consult it only when their standing instructions say to. The
installer now wires that enrollment itself.

- `install.py` appends the canonical standing memory rule to the agent's
  memory store by default (`~/.hermes/memories/MEMORY.md`, i.e.
  `<HERMES_HOME>/memories/MEMORY.md`; `--memory-file` overrides,
  `--no-wire-memory` opts out). The append is byte-faithful to the store's
  own writer (entries joined by `\n§\n`) so the rule lands as a genuine
  standalone entry, carries the stable marker `session-coord (wire v1)` for
  idempotent re-runs, backs the previous file up to `<name>.bak-<ts>`, and
  fails open — a machine with no Hermes memory store prints the entry for
  manual placement and installs everything else anyway. `--check` previews
  the wiring step.
- `examples/memory-entry.example.md`: the canonical "always call it" entry
  with the three delivery paths (ask the agent / edit the file / installer)
  and an instruction block for an agent performing the install itself.
- README §8.1 "Wire it into the agent" and a threat-model row for
  non-participating sessions (the failure this closes).
- CI: installer smoke test now re-runs the installer against a scratch
  memory store and asserts exactly one wire marker survives.

## [2.3.2] — 2026-08-26

### Fixed — explicit session ids: registerable, and never orphaned

`register` accepted no `--id`, yet `claim`/`release` did — so a caller that
pre-minted a memorable id (`HERMES_COORD_ID=my-task-id`) hit a usage error on
`register` (easily missed with stderr suppressed), then **successfully
claimed** under that id anyway. The claims were orphans: `release --id`/`done
--id` refused with `no session matches`, and the resources stayed held until
TTL expiry. Claims must never be easier to make than to release. Two-sided fix:

- `register --id <explicit>` (default: env `HERMES_COORD_ID`) registers under
  the **caller's** id. Ids are validated (`4-64 chars: alnum . _ -`, alnum
  first; rc 2 on violation). Re-registering an existing explicit id is
  **idempotent** — refreshes task/surface/liveness and reactivates a `done`
  row instead of erroring. Auto-generated ids are unchanged when `--id` is
  omitted. Disabled-mode (master switch OFF) `register` echoes the explicit id
  so `ID=$(… register)` pipelines stay stable either way.
- **Orphan-proofing at the claim side:** a `claim` under a never-registered id
  auto-creates a minimal session row (`(auto-registered at claim)`), so every
  claimable id is guaranteed resolvable by `release`/`done` later — even if
  the caller skipped or fumbled `register`.

`selftest.sh` gains 5 checks (23 → 28; **127 total**).

## [2.3.1] — 2026-08-26

### Fixed — `--id` now resolves prefixes and fails loudly

The board *displays* 8-character session ids but *stores* the full 12, so
pasting a displayed id into `done`/`release`/`pause`/`resume`/`preempt`/`inbox`
ran `... WHERE id=?` against a value that matched **no row** — the command
printed its normal success line while changing nothing (a held claim silently
survived a `release`). These verbs now resolve `--id` up front to a real session
by full id **or unique prefix** (reusing the same resolver `prioritize` already
used), and **exit non-zero** on a no-match (`error: no session matches '…'`) or
an ambiguous prefix (`error: ambiguous prefix '…': …`). `register` (mints a new
id) and the read/self-attributed `claim`/`check`/`wait` verbs are deliberately
exempt. `selftest.sh` gains 4 checks (19 → 23; **122 total**).

## [2.3.0] — 2026-08-26

### Added — master switch (turn coordination off without uninstalling)

Coordination is cooperative: valuable when several actors share a machine, pure
overhead for a solo operator. Rather than force an uninstall, the whole layer
now has a single master switch that makes every verb a fail-open no-op while
leaving the code, the board data, and the cron wiring exactly in place.

- **New verbs**: `switch` (report state + what is deciding it), `enable`,
  `disable`, and `switch toggle`. The switch-management verbs always run, so you
  can never lock yourself out.
- **Fail-open no-op when OFF**: every coordination verb short-circuits while
  preserving its stdout contract — `register` still prints a synthetic id (so
  `ID=$(… register)` scripts keep working), `claim`/`check` return success/FREE,
  and `cron-guard` emits empty stdout so the shell guard reads "run unguarded."
  A disabled board behaves exactly as if coordination were never installed.
- **Two layers, one decision, identical precedence**: `HERMES_COORD_DISABLED`
  env override (both directions, one process tree) beats the persistent sentinel
  file (`~/.hermes/state/coordination_disabled`, override
  `HERMES_COORD_DISABLED_FILE`) beats the default (ON). `coord_guard.sh` honors
  the same switch **without spawning Python** (zero cost when off), so disabling
  instantly makes every cron wrapper run unguarded too — genuinely global.
- **28-check `selftest_toggle.sh`** proves: OFF never blocks a caller on any
  verb, ON restores full conflict detection, env overrides the sentinel both
  ways, and the shell guard honors the switch. The 90 pre-existing checks prove
  the enabled path is byte-for-byte unchanged. **118 checks total, all green.**

### Added — one-command installer

- **`install.py`** — cross-platform (Linux/macOS/Windows, Python ≥3.8), pure
  standard library, no network. Copies the CLI + guard + selftests, creates the
  state dir, and **self-verifies** by running the full selftest matrix (skip with
  `--no-verify`). `--check` is a dry run; `--dest`/`--state-dir` relocate it.
- **Never clobbers data**: an existing board DB and a customized
  `cron_resources.json` are left untouched; a locally-modified script is backed
  up to `<name>.bak-<ts>` before being replaced. Idempotent — safe to re-run to
  upgrade in place. Verified end-to-end (live claim survives re-install).

### Added — enrollment examples (were only in the private skill)

- **`examples/bot-soul-coordination.example.md`** and
  **`examples/subagent-prompt.example.md`** — ready-to-paste protocol texts for
  concurrent named agents and fan-out children, which inherit no environment and
  so must learn the protocol from their prompt. The README described this
  requirement (§3.16) but the repo did not previously ship the texts.

### CI

- `tests` matrix now also runs `selftest_toggle.sh` and an `install.py` smoke
  test; `lint` now runs ruff + bandit on `install.py` and shellcheck on
  `selftest_toggle.sh`.

### Docs

- New README §3.17 (the master switch) + §8 install rewrite leading with
  `install.py`; command-tour, threat-model, and testing tables updated (90 →
  118 checks). Enrollment examples linked from §3.16.
- README §3.16 field note from live deployment: the cron radar surfaces
  *schedules, not run health* — check the store's `last_status`/`failure_streak`
  when a radar entry matters. Concrete trap documented: bot-profile routines
  resolve bare `script:` filenames against the owning profile's own scripts
  directory, so a script placed in the shared/default scripts dir fails only at
  fire time, invisibly to the radar. Verify fixtures with one live fire.

## [2.2.0] — 2026-08-18

### Added — inter-bot deconfliction (concurrent named agents / bot profiles)

Coverage now extends to **bots**: persistent named agents that run as separate
profiles side by side with interactive sessions — including their sub-sessions
(bot-to-bot handoffs spawn fresh CLI runs) and their privately-stored scheduled
routines. Deconfliction is inter-bot by construction: one shared board, no
per-bot scopes — every actor sees every other actor's claims identically.

- **Per-profile cron store merge**: `cron_store_jobs()` scans the default cron
  store PLUS every `<profiles>/<name>/cron/jobs.json` (new
  `HERMES_COORD_PROFILES_DIR` override, default `~/.hermes/profiles`). A bot's
  routines now appear in the 12-hour radar, claim-time CRON ADVISORY warnings,
  `cron-guard` resolution, and `wait-for-cron` — exactly like default-store jobs.
- **`[bot:<profile>]` attribution**: profile jobs are name-tagged at load time
  (already-tagged names are left alone), so every radar row, advisory, and
  defer note names the owning bot with no extra lookups. Advisory and status
  rows carry a `profile` field.
- **Deterministic collision policy**: a job id present in both the default
  store and a profile store resolves to the default store (first-seen wins;
  profile scan order is sorted, so profile-vs-profile collisions are
  deterministic too).
- **Fail-open extended** (Rule 2): a missing, unreadable, or corrupt profile
  store contributes nothing and breaks nothing.
- **`resolve_cron_job` error clarity**: the no-match error now names both
  store locations.
- Documented the bot co-worker protocol (README §3.16): protocol travels in
  the bot's standing prompt (handoff runs inherit no env); bot-to-bot chat is
  negotiation (ETA, early release, batching) but never a lock — only a
  successful claim authorizes mutation; ranks stay human-set.

### Tests

- `selftest_cron.sh` grows a hermetic v2.2 BOT LEG (7 checks, suite 38 → 45;
  full matrix 83 → 90): profile-store radar visibility, advisory naming, guard
  defer/acquire against a bot routine, double-tag prevention, collision
  precedence, profile attribution, corrupt-store inertness. The suite pins
  `HERMES_COORD_PROFILES_DIR` to scratch so real profiles never leak in.

### Compatibility

- No schema changes. All v1/v2/v2.1 commands, outputs, and the rc 0/75
  contract are unchanged (19 + 26 v1/v2 suites re-run green). With no profiles
  directory present, behavior is byte-for-byte v2.1.

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
