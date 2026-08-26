# session-coord — cooperative coordination for concurrent AI agent sessions

[![tests](https://github.com/P2ppyJack/session-coord/actions/workflows/tests.yml/badge.svg)](https://github.com/P2ppyJack/session-coord/actions/workflows/tests.yml)

**One machine. Several AI agent sessions. Subagent fan-outs. Scheduled cron jobs.
Named bots with their own sub-sessions and routines. All touching the same files,
skills, GPU boxes, and state — at the same time.**

`session-coord` is a small, dependency-free SQLite-backed coordination board plus a CLI
(`session_coord.py`) and a zero-token cron guard (`coord_guard.sh`) that let all of those
actors behave like **polite co-workers instead of competitors**: they announce what they
are working on, wait for each other, hand resources over in priority order, and never
silently clobber each other's work.

It was built for [Hermes Agent](https://github.com/NousResearch/hermes-agent) sessions,
but the design is agent-framework-agnostic: anything that can run a CLI before touching a
shared resource can participate.

---

## Table of contents

1. [The problem, narrated](#1-the-problem-narrated)
2. [Design philosophy — the five rules everything follows](#2-design-philosophy)
3. [The protection logic, mechanism by mechanism](#3-the-protection-logic)
   - 3.1 Claims: task-scoped, atomic, advisory
   - 3.2 Waiting politely (and cheaply)
   - 3.3 Crash safety: TTLs, reaping, stale sessions
   - 3.4 Human-set priority — and why agents cannot set it
   - 3.5 Preemption that never destroys work
   - 3.6 Pause / resume with checkpoints
   - 3.7 Fencing: why a *free* resource can still say "not yet"
   - 3.8 Fairness: FIFO tie-breaks, liveness gates
   - 3.9 Subagent lineage: family priority that re-ranks live
   - 3.10 The urgent-alert channel
   - 3.11 Steal — the break-glass path
   - 3.12 Cron jobs as first-class co-workers
   - 3.13 The zero-token cron guard
   - 3.14 Critical crons: never defer silently
   - 3.15 Responsibility tracking: a paused job must be somebody's problem
   - 3.16 Bots (concurrent named agents) and inter-bot deconfliction
4. [Threat-model summary table](#4-threat-model-summary)
5. [What this deliberately is NOT](#5-what-this-deliberately-is-not)
6. [Quality: how this was tested and scanned](#6-quality-testing-and-scans)
7. [Compatibility and migration policy](#7-compatibility-and-migration)
8. [Install and quick start](#8-install-and-quick-start)
9. [Command tour](#9-command-tour)

---

## 1. The problem, narrated

Modern agent setups stop being "one chat window" very quickly. A realistic afternoon:

- A **desktop session** is refactoring a shared helper script.
- A **second session** (started from a phone) wants to update the *same* skill's docs.
- The desktop session **fans out subagents**, each with its own terminal.
- At 23:00 a **nightly backup cron** wants to tar up the very directory being refactored.
- A **cost watchdog cron** wants to scale down the same GPU fleet another session is
  deliberately pre-warming.

The storage layer underneath (SQLite WAL, atomic file writes) keeps individual writes
from corrupting — but nothing coordinates **intent**. Nobody knows what anybody else is
*working on*. The failure modes are silent and expensive:

- Two sessions edit one file; the slower writer wins; the faster session's work vanishes.
- A backup archives a half-rewritten script tree.
- A watchdog "helpfully" tears down infrastructure another session just paid to warm up.
- Two sessions drive the same GPU box into memory exhaustion.

Every one of those is a *collision of intentions*, not of bytes. `session-coord` is an
intention board: cheap to consult, safe to ignore in an emergency, and rich enough to
encode priority, hand-offs, pausing, and scheduled (cron) actors.

## 2. Design philosophy

Five rules shaped every mechanism below. When a design decision looks odd, one of these
is usually the reason.

**Rule 1 — Co-workers, not lock cops.** The board is *advisory*. It never wraps your
syscalls, never intercepts writes, never makes you deadlock against a kernel object. A
claim is a public statement, a queue position, and a promise of notification — like
telling the office "I'm in the conference room until 3". Cooperation is enforced by the
agents' shared protocol (check before touching; wait when held), not by force. This keeps
the failure domain tiny: the worst a broken board can do is *not help*.

**Rule 2 — Fail-open, always.** A coordination system that can block real work during its
own outage is worse than none. Every integration point degrades to "proceed as if the
board didn't exist": the cron guard proceeds if the DB is corrupt or the CLI missing; a
malformed cron manifest makes the cron features inert; advisory lookups that fail print
nothing rather than raising. **A broken board must never block a backup.**

**Rule 3 — Authority stays human.** Agents *cannot* rank themselves. Priority ranks enter
the system through exactly one door — the human operator (`prioritize`). An unranked
session cannot preempt anything; a ranked one only preempts strictly-lower ranks. Without
this rule, every agent eventually decides its own task is the important one; with it, the
board is an instrument of the user's judgment, not the agents'.

**Rule 4 — Never lose finished work.** Preemption is a *request*, not a seizure. The
holder is asked to checkpoint, pause, and yield; the paused session keeps a reserved
resume spot and its checkpoint note comes back verbatim on resume. Long-running work is
expected to be checkpoint-safe, and the board's job is to make yielding *cheap*, so that
being polite is never punished with lost progress.

**Rule 5 — Zero tokens for decisions that need no judgment.** Anything decidable by pure
logic runs as plain bash/Python *before* any LLM spins up. The cron guard is a sourced
shell snippet: a conflicted agentic cron job defers before the model ever loads —
deterministic, auditable, and free.

## 3. The protection logic

### 3.1 Claims: task-scoped, atomic, advisory

A session `register`s (getting a short id), then `claim`s the resources its **current
task** needs, and `release`s them (or calls `done`) when that task finishes — not when
the session ends. Task scoping matters because agent sessions are long-lived and do many
unrelated things; holding "everything I've ever touched" would starve the rest of the
office.

Resource keys are a small taxonomy rather than free text, so different actors naming the
same thing collide correctly:

| Key form | Meaning |
|---|---|
| `file:/path` | A file — or a directory, which covers everything under it |
| `skill:<name>` | An agent skill (docs + scripts treated as one unit) |
| `memory` | The agent's persistent memory store |
| `ui:desktop` | The desktop UI (only one actor should drive it) |
| `box:<host>` | A whole machine (GPU box) for mutating work |
| `cron-store` | The cron job registry itself |

Paths are canonicalized (symlinks, `~`, case-insensitive filesystems, Windows drive/UNC
forms) so `file:~/x` and `file:/home/u/x` are the same claim.

**Multi-resource claims are atomic all-or-nothing.** If a task needs three resources and
one is held, it acquires *none* of them. Partial acquisition is how two sessions end up
each holding half of what the other needs — the classic deadly embrace. Refusing partial
grants makes that impossible at the granularity the board controls.

Claims are **exclusive by default**, with a shared/read mode for co-reading. Return codes
follow sysexits: `0` = claimed, `75` (`EX_TEMPFAIL`) = busy-try-later. Scripts branch on
the code; humans read the text.

### 3.2 Waiting politely (and cheaply)

When a claim is refused, the output names the holder, its task, and *when the claim
expires* — enough for the refused session to make an informed choice. `claim --wait`
registers you as a **waiter** and polls internally (single CLI invocation, ~4 s cadence)
until the resource frees or a timeout hits. One tool call, no token-burning retry loops
by the calling LLM.

When a holder releases, every waiter gets an inbox **notification** ("released by
co-worker, you may claim now") — the concrete implementation of "ask the other session
to let you know when it's done". The inbox (`inbox --id`) is a read-once ledger: unread
notifications are printed, marked read, and gone from the urgent path.

### 3.3 Crash safety: TTLs, reaping, stale sessions

Sessions crash, laptops sleep, terminals get closed. Nothing on the board may outlive its
usefulness:

- Every claim carries a **TTL** (default 90 min, settable per claim). Expired claims are
  reaped lazily by any board touch, with an `EXPIRED` notification to the former holder.
- Sessions unseen for 24 h are marked stale and their claims released.
- Notification history is pruned after 7 days.

Lazy reaping (on next board touch) instead of a daemon keeps the system daemon-free —
there is nothing to install, supervise, or forget to restart. The cost — expiry can be
noticed a little late — is acceptable for advisory coordination.

### 3.4 Human-set priority — and why agents cannot set it

`prioritize --order "sessA=1,sessB=2"` records the **user's** ranking. Ranks are
lexicographic tuples: `1 < 1a < 1b < 2 < unranked`. Everything downstream (queue
ordering, fencing, preemption rights) derives from this one table.

The deliberate omission is the point: **there is no API for a session to rank itself.**
`preempt` hard-refuses requesters without a user-recorded rank. This single rule prevents
the entire class of "agents negotiating importance with each other" failure modes —
politeness collapses quickly when every actor claims to be the priority.

When ranks change, every session whose *effective* rank moved — including children who
inherited the change through a parent (§3.9) — gets a `PRIORITY UPDATE` notification, so
nobody acts on a stale picture of the pecking order.

### 3.5 Preemption that never destroys work

`preempt --id <ranked-session> --res <resource>` does **not** take anything. It:

1. Verifies the requester outranks the holder (else refuses, listing who refused).
2. Sends the holder a `USER-PRIORITY REQUEST` notification asking it to checkpoint,
   pause, and yield.
3. Registers the requester as a waiter — queued in rank order like everyone else.
4. Deduplicates: repeated preempts against the same holder/resource don't spam the inbox.

The holder yields *at a safe point* (that's what checkpoints are for), so preemption
cannot corrupt half-written state. And because crons are not interactive co-workers,
`preempt` refuses to target a cron holder outright — pausing a scheduled job is a
scheduler operation requiring user approval (§3.14), not a peer negotiation.

### 3.6 Pause / resume with checkpoints

`pause --id H --note "<checkpoint>"` flips H's held claims to *paused*: they stop
blocking others, but H keeps a **reserved resume spot** in the queue and the board stores
the checkpoint note. Other waiters are told the resource freed and that H will resume
after them. `resume --id H` re-acquires **all** paused claims atomically (respecting
current holders and fencing), then prints the checkpoint note back verbatim: *"Your
checkpoint from pause: …"* — the session picks up exactly where it left off.

Paused claims still expire (TTL from pause time) with a warning notification — a resume
spot is a courtesy, not a permanent lien. If the spot lapses, resume says so honestly
instead of pretending.

### 3.7 Fencing: why a *free* resource can still say "not yet"

The subtlest protection, and the one that makes priority real. Consider: a high-priority
session is waiting for `file:X`; the holder releases; but before the waiter's next poll
tick, an unrelated low-priority session claims `file:X`. Rank meant nothing — the race
went to whoever polled luckiest.

Fencing closes that gap: a claim on a **free** resource is refused (`QUEUED`, rc 75) when
another *live, actively-polling* waiter with **strictly better rank** — or equal rank and
earlier queue position — is waiting for it in a conflicting mode. The release boundary
honors the queue, not the poll lottery.

Two guardrails keep fencing honest:

- **Liveness (30 s):** only waiters whose poll heartbeat is fresh can fence. A crashed
  waiter stops fencing within half a minute — the dead must not block the living. (Paused
  sessions' resume spots are exempt: they are *supposed* to be quiet.)
- **Queue order survives releases:** waiter rows deliberately stay active across the
  release so rank + FIFO ordering carries over the boundary. This was validated by an
  adversarial review that caught the original implementation dropping rank order at
  exactly that point (§6).

### 3.8 Fairness: FIFO tie-breaks

Equal-rank waiters acquire in first-come-first-served order, and unranked sessions are
simply the last tier of the same ordering. Combined with fencing this yields a total,
starvation-free order: rank first, then arrival time. A lower-priority session is never
starved *by accident* — only ever explicitly out-ranked by the human.

### 3.9 Subagent lineage: family priority that re-ranks live

Orchestrator sessions fan out subagents. Children register with `--parent <id> --slot a|b|…`
and get **derived ranks**: parent rank 1 → children `1a`, `1b` — ordered within the
family by the parent's slot assignment, and the whole family sits between rank 1 and
rank 2. Two properties matter:

- **A parent outranks its own children** (the orchestrator can always reclaim a resource
  from its workers).
- **Derivation is live.** Re-ranking the parent to 3 instantly makes the children
  effectively `3a`, `3b` — no re-registration, and every child whose effective rank moved
  is notified. The user re-ranks one id; the whole tree follows.

### 3.10 The urgent-alert channel

A busy session might not read its inbox for a long stretch. So any board touch
(`claim`/`check`/`release`/`status`) by a session with unread `preempt_request` or
`priority` notifications prints `⚠ N urgent notification(s) pending` — on **stderr**, so
it never corrupts machine-parsed stdout. The next natural board interaction surfaces the
urgency; no polling loop needed.

### 3.11 Steal — the break-glass path

`steal` force-releases someone else's claim. It exists because humans sometimes must
override (a hung session holding the deploy path at 2 a.m.). It is loud by design: the
victim gets a notification naming who stole what and why, and the action is labeled
`FORCE-RELEASED` in output. The protocol treats it as an incident, not a tool of first
resort — everyday flow is preempt → pause → resume.

### 3.12 Cron jobs as first-class co-workers

Scheduled jobs are sessions that nobody is watching. Left out of coordination, they are
the perfect clobbering machine: they fire at fixed times, with no awareness, into
whatever state the interactive sessions left. `session-coord` brings them onto the board
from both directions:

- **Sessions see crons coming.** A manifest (`cron_resources.json`) declares which
  resources each cron job touches, its guard policy, and whether it is *critical*. When a
  session claims a resource a cron will want, the claim output appends a **CRON
  ADVISORY**: which job, when it fires, what clashes. `status` shows a 12-hour cron radar
  with conflict flags. No surprises at 23:00.
- **Crons see sessions.** When a cron fires, its guard (§3.13) checks the board; if an
  interactive session holds what the job needs, the job defers or skips — and drops a
  polite inbox note to the holder: *"cron '<name>' fired, found you holding <resource>,
  politely skipped this tick."*
- Crons appear on the board as `[CRON JOB]` holders, `preempt` refuses to target them,
  and every guard decision is written to a `cron_events` audit table.
- **Fail-open (Rule 2):** no manifest, malformed manifest, missing DB → cron features are
  inert and jobs run exactly as before.

### 3.13 The zero-token cron guard

`coord_guard.sh` is sourced as **step 0** of a cron wrapper script:

```bash
source ~/.hermes/scripts/coord_guard.sh   # exits 75 (deferred) on conflict
```

It is deterministic shell + one Python CLI call — the decision to defer happens **before
any LLM starts**, so a conflicted agentic job costs zero tokens to skip. Policies are
per-job: a backup *waits* up to 15 minutes for the holder to finish (backups should
eventually run); a fleet watchdog *skips instantly* (it will tick again in minutes; a
session deliberately holding the fleet is exactly when the watchdog must not act). Every
defer/skip/run is audited in `cron_events`, and the guard proceeds on ANY internal error.

### 3.14 Critical crons: never defer silently

Some jobs must not quietly miss a tick (the nightly backup being the canonical case). The
manifest marks them `critical`, and the claim-time advisory switches from informational
to a **decision brief** addressed to the session (and through it, the user):

> This is a CRITICAL job — do NOT let it defer silently. Choose: **(a)** finish & release
> before it fires, **(b)** `wait-for-cron --job <id>` (block until it has run), **(c)**
> pause the job — *with the user's approval* — booked via `cron-note`, **(d)** trigger it
> early. Ask the user which.

Options that change the schedule (pause, trigger-early) explicitly route through human
approval — Rule 3 again. `wait-for-cron` gives the opposite direction: the session parks
itself until the job's fire is observed in `cron_events`, then continues.

### 3.15 Responsibility tracking: a paused job must be somebody's problem

The most dangerous state in scheduled automation is *paused and forgotten* — a paused
job does not fire **at all**. So pausing a cron through this protocol books the
responsibility on the board (`cron-note`): who paused it, why, and that resuming is owed.
When that session later calls `done`, the board checks the ledger and **nags**: it lists
any cron the session noted `paused` but never `resumed`, refusing to let the departure be
silent. The failure mode is reduced from "backup silently off for a month" to "impossible
to walk away without being told".

### 3.16 Bots (concurrent named agents) and inter-bot deconfliction

Some agent stacks run **named persistent agents** ("bots") side by side: each bot is a
full profile with its own memory, sessions, and — crucially — its own cron store of
scheduled routines (`<profiles>/<bot>/cron/jobs.json`). Bots spawn sub-sessions
(bot-to-bot handoffs run as fresh CLI invocations) and message each other, which
multiplies the concurrency without adding any built-in collision control.

`session-coord` treats bots as ordinary co-workers, and the coverage is **inter-bot by
construction**: there are no per-bot scopes. One shared board sees every actor —
interactive sessions, subagents, cron guards, every bot, and every bot-spawned
sub-session. Bot A's `claim`/`status`/exit-75 sees bot B's holds identically to a
human session's. Three mechanisms make bots first-class:

- **Per-profile cron radar.** The store scanner merges the default cron store with
  every profile store (`HERMES_COORD_PROFILES_DIR`, default `~/.hermes/profiles`).
  A bot's routines surface in the radar, claim advisories, and the zero-token guard
  exactly like default-store jobs. Id collisions resolve default-store-first; a
  corrupt profile store contributes nothing (Rule 2: fail-open).

  *Field note (live deployment):* the radar reads **schedules, not run health** — a
  routine can show as scheduled while every fire has failed (the store's
  `last_status`/`failure_streak` are where health lives; check them when a radar
  entry matters to your decision). One concrete trap: schedulers typically resolve
  a routine's bare `script:` filename against the **owning profile's** scripts
  directory, not a shared one — a bot routine whose script sits in the default
  scripts dir fails only at fire time, invisibly to the radar. Deploy the script
  into the bot's own profile and verify with one live fire, not by reading the
  schedule.
- **Attribution everywhere.** Profile jobs are tagged `[bot:<profile>]` at load time,
  so every advisory, defer note, and radar row names the owning bot with no extra
  lookups. Bots register with `--surface bot:<name>` so board rows and HELD messages
  identify them.
- **Protocol by prompt, not by env.** Bot handoff runs inherit no environment, so the
  coordination protocol travels in the bot's standing prompt/persona file (the same
  way subagents receive it). Chat between bots is *negotiation* — ask a holder's ETA,
  request early release, offer to batch work — but **chat is never a lock**: only a
  successful claim authorizes mutation, because messages are neither atomic nor able
  to interrupt a mid-turn bot. Ranks stay human-set; bots never self-prioritize.

The exact enrollment texts ship in the repo so you don't have to reinvent them:
[`examples/bot-soul-coordination.example.md`](examples/bot-soul-coordination.example.md)
(paste into a bot's persona) and
[`examples/subagent-prompt.example.md`](examples/subagent-prompt.example.md)
(paste into a fan-out child's prompt).

### 3.17 The master switch: turn coordination off without uninstalling

Coordination is *cooperative* — valuable when several actors share a machine,
pure overhead when you are the only one running. Rather than force an
uninstall, the whole layer has a single **master switch** that makes every verb
a fail-open no-op while leaving the code, the board data, and the cron wiring
exactly in place.

```bash
session_coord.py switch            # report state + what is deciding it
session_coord.py disable           # turn the whole board OFF (persistent)
session_coord.py enable            # turn it back ON
session_coord.py switch toggle     # flip it
```

**What OFF means.** While disabled, every coordination verb short-circuits to a
friendly no-op that *never blocks a caller* and preserves each verb's stdout
contract, so a disabled board behaves exactly like "coordination was never
installed": `register` still prints a synthetic id (so `ID=$(… register)`
scripts keep working), `claim`/`check` return success/FREE, and — critically —
`cron-guard` emits empty stdout so the shell guard reads "no id → run
unguarded." The switch-management verbs (`switch`/`enable`/`disable`) always
run, so you can never lock yourself out.

**Two layers, one decision.** The Python CLI and the shell cron guard
(`coord_guard.sh`) read the switch through **identical precedence**, and the
guard does it *without spawning Python* (zero cost when off):

1. **`HERMES_COORD_DISABLED`** (environment) — an explicit override in **both**
   directions, scoped to one process tree. Truthy (`1`, `true`, `yes`, `on`, or
   any other non-empty value) forces OFF; `0`/`false`/`no`/`off` forces ON.
   Ideal for a one-shot run or a test: `HERMES_COORD_DISABLED=1 ./my-wrapper.sh`.
2. **Sentinel file** (`~/.hermes/state/coordination_disabled`, override with
   `HERMES_COORD_DISABLED_FILE`) — the persistent switch that `disable`/`enable`
   write and remove. This is what survives reboots.
3. **Default: ON.** Absent both, coordination is enabled.

Because the guard honors the same sentinel, disabling coordination instantly
makes every cron wrapper run unguarded too — the switch is genuinely global,
not merely session-scoped. `switch` reports exactly which of the three is
currently deciding, and warns if an env override is masking your sentinel.

## 4. Threat-model summary

| Failure mode (without coordination) | Protection (section) |
|---|---|
| Two sessions edit the same file/skill; last writer wins | Claims + check-before-touch protocol (3.1) |
| Token-burning retry loops while waiting | Single-call `--wait` polling + release notifications (3.2) |
| Crashed session holds resources forever | TTL reaping, stale-session cleanup (3.3) |
| Agents self-declare importance | User-only ranks; unranked preempt refused (3.4) |
| Preemption corrupts half-written work | Request→checkpoint→pause→yield flow (3.5, 3.6) |
| High-priority waiter loses the release race to a lucky poller | Fencing at the release boundary (3.7) |
| Dead waiter blocks the living | 30 s poll-freshness liveness gate (3.7) |
| Equal-priority starvation | FIFO tie-break (3.8) |
| Subagent swarm outranks or fights its orchestrator | Lineage ranks, parent-beats-child, live inheritance (3.9) |
| Busy session misses a preempt request | stderr urgent-alert on any board touch (3.10) |
| Hung session at 2 a.m. | `steal` break-glass, loud + audited (3.11) |
| Cron fires into a session's half-done work | Zero-token guard defers/skips + polite note (3.12, 3.13) |
| Session's work destroyed by a scheduled job it never saw coming | Claim-time cron advisories + 12 h radar (3.12) |
| Critical job silently misses its window | Critical decision brief, user-in-the-loop (3.14) |
| Paused cron forgotten forever | Responsibility ledger + done-time nag (3.15) |
| Concurrent bots race each other's (or a session's) work | One shared board, no per-bot scopes; protocol in the bot's standing prompt (3.16) |
| A bot's scheduled routine fires unseen from its private cron store | Per-profile store merge + `[bot:]`-attributed radar/advisories/guard (3.16) |
| Bots "agree" in chat, then both mutate | Chat is negotiation, never a lock: only a claim authorizes mutation (3.16) |
| Coordination is pure overhead for a solo operator, tempting a risky uninstall | Master switch: fail-open no-op, code/data/wiring left in place (3.17) |
| Coordination system itself breaks | Fail-open everywhere; guard proceeds on any error (Rule 2) |

## 5. What this deliberately is NOT

- **Not mandatory locking.** A rogue process that never consults the board is not
  stopped. The trust model is *cooperating agents on one user's machine* — the board
  removes accids, not adversaries.
- **Not distributed.** One machine, one SQLite file (WAL). Multi-host coordination is a
  different problem with different failure modes.
- **Not a scheduler.** It coordinates *around* your cron system; it never fires jobs
  itself (trigger-early is delegated to your scheduler, with user approval).
- **Not a daemon.** Nothing runs in the background; all maintenance is lazy, on board
  touches.

Complementary prior art: mkdir-atomic single-flight locks (mutual exclusion of one job
with itself) still apply *inside* a job; `session-coord` coordinates *across* different
actors. They compose: single-flight guards re-entrancy, the board guards intent.

## 6. Quality: testing and scans

Everything below ships in the repo (`scripts/selftest*.sh`) and is re-runnable in one
command each; nothing is claimed that a clone cannot re-verify.

**Regression / compatibility / feature suites — 118 checks, all green:**

| Suite | Checks | What it proves |
|---|---|---|
| `selftest.sh` (v1 regression) | 19 | Original claim/wait/release/steal/expiry semantics unchanged |
| `selftest_priority.sh` (v2) | 26 | Ranks, fencing, FIFO, lineage, pause/resume, preempt dedupe, urgent alerts, **pinned v1-schema fixture migration** |
| `selftest_cron.sh` (v2.1+v2.2) | 45 | Guard defer/skip/fail-open, advisories, radar conflict flags, `wait-for-cron` fire detection, pause ledger + done-time nag, **bot leg: profile-store merge, `[bot:]` attribution, collision precedence, corrupt-store inertness** |
| `selftest_toggle.sh` (v2.3) | 28 | **Master switch**: OFF is a fail-open no-op on every verb (register still yields an id, cron-guard stdout stays empty), ON restores conflict detection, env overrides the sentinel both directions, and the shell guard honors the switch — plus the enabled path proven unchanged by the 90 checks above |

**Platforms actually executed, not assumed:** the full 118-check matrix runs green on
macOS (Apple Silicon) and Ubuntu Linux (byte-identical file verified by checksum before
the run). The CLI was additionally executed under real Python 3.8, 3.9, 3.11, 3.12, and
3.13 interpreters. Hostile-console behavior (C locale / `PYTHONIOENCODING=ascii` with
non-ASCII task names) degrades to `?` placeholders instead of crashing. Windows path
forms (drive-letter, UNC) are normalized and unit-verified; bash components target
POSIX/Git-Bash.

**Compatibility discipline:** versioned, additive-only schema migrations; a database
created by v1 opens and works under v2.3 (pinned-fixture test); with no ranks, no cron
manifest, and no profiles directory, v2.3 behavior is byte-for-byte v1. The master switch
(v2.3) adds no schema and defaults to ON, so an existing board is unaffected until you
flip it. The production board this was developed
against was live-migrated in place with zero data loss.

**Adversarial review:** before release, an independent agent was given the spec and told
to break the implementation against a scratch board. It found two real ordering bugs
(rank fencing dropped at the release boundary; children not notified on inherited
re-rank). Both were fixed, repro scripts added, and the full matrix re-run green.

**Static analysis / security scans (results current as of release):**

- `bandit` — 0 issues (the two dynamic-identifier SQL sites are hardcoded-table
  migrations, annotated and audited; all value interpolation is parameterized).
- `ruff` strict profile (`E,F,W,I,UP,SIM,ISC,RUF,B`) — 0 findings at line-length 100.
- `shellcheck` — clean on the cron guard.
- No network access, no subprocess/shell-out, no `eval`/`exec`, stdlib only.
- Docstring coverage: 100% of functions.

**Change history:** every file carries an in-file `EDIT HISTORY` block (newest-first,
dated, attributed), plus the repository `CHANGELOG.md`.

## 7. Compatibility and migration

- **Schema:** additive `ALTER TABLE` migrations only, applied idempotently on open. Old
  boards upgrade in place; new columns default to inert values.
- **Behavior:** features are opt-in by data. No ranks recorded → no fencing differences.
  No cron manifest → no cron behavior. Downgrade-safe: v1 code ignores v2 columns.
- **Interfaces:** rc 0/75 contract and all v1 output markers (`CLAIMED`, `HELD`,
  `FREE:`, `RELEASED:`, …) are frozen; new information is appended, never reworded.

## 8. Install and quick start

**One-command install (recommended).** Cross-platform, pure standard library,
no network. It copies the CLI + guard + selftests into place, creates the state
directory, **never touches an existing board DB or cron manifest**, backs up any
locally-modified script before replacing it, and then proves itself by running
the full selftest matrix:

```bash
python3 install.py                 # install or upgrade into ~/.hermes
python3 install.py --check         # dry run: show what WOULD change, touch nothing
python3 install.py --dest /opt/coord/bin --state-dir /var/lib/coord   # custom paths
python3 install.py --seed-manifest # also drop an example cron manifest if none exists
```

Re-running is safe: an existing install is upgraded in place (unchanged files
are reported `unchanged`; a changed file is backed up to `<name>.bak-<ts>`
first). The installer never flips the master switch and never deletes anything.

**Manual install** (if you prefer to place files yourself):

```bash
# 1. Drop the CLI + guard somewhere on PATH (stdlib only, Python ≥3.8)
cp scripts/session_coord.py scripts/coord_guard.sh ~/.hermes/scripts/

# 2. A session's lifecycle
SID=$(python3 session_coord.py register --task "refactor helper lib" --surface desktop)
python3 session_coord.py claim  --id "$SID" --res file:~/project/lib --res skill:my-skill
#   ... work ...
python3 session_coord.py done   --id "$SID"       # release everything, notify waiters

# 3. Wire the cron guard into a wrapper (optional, fail-open) — see examples/wrapper.example.sh
source ~/.hermes/scripts/coord_guard.sh           # exits 75 if it should defer

# 4. Tell the board what your crons touch (optional)
cp examples/cron_resources.example.json ~/.hermes/state/cron_resources.json

# 5. Verify everything on your machine
bash scripts/selftest.sh && bash scripts/selftest_priority.sh && \
  bash scripts/selftest_cron.sh && bash scripts/selftest_toggle.sh
```

**Turning it off.** Coordination is overhead when you run solo. Disable the
whole layer without uninstalling — every verb and every cron guard becomes a
fail-open no-op (see §3.17):

```bash
python3 session_coord.py disable    # or: enable / switch / switch toggle
HERMES_COORD_DISABLED=1 ./one-shot-wrapper.sh   # off for just this run
```

**Enrollment for bots and subagents.** Concurrent named agents and fan-out
children inherit no environment, so they learn the protocol from their prompt.
Ready-to-paste texts ship in `examples/bot-soul-coordination.example.md` and
`examples/subagent-prompt.example.md`.

## 9. Command tour

| Command | Purpose |
|---|---|
| `register` | Join the board (`--rank`, `--parent/--slot` for subagents) |
| `claim` / `check` / `release` / `done` | The core loop; `claim --wait` queues politely |
| `status` | Whole-office view: holders, waiters, ranks, 12 h cron radar |
| `inbox` | Read and clear your notifications |
| `prioritize` | **User-only**: set/clear ranks, bulk `--order "a=1,b=2"` |
| `preempt` | Ask a lower-ranked holder to checkpoint and yield |
| `pause` / `resume` | Checkpoint-safe yielding with a reserved resume spot |
| `steal` | Break-glass force release (loud, audited) |
| `cron-guard` | The guard's board query (used by `coord_guard.sh`) |
| `wait-for-cron` | Block until a scheduled job has actually fired |
| `cron-note` | Book responsibility for pausing/resuming a cron |
| `switch` / `enable` / `disable` | **Master switch**: report, or turn the whole board off/on (fail-open no-op while off) |

---

**License:** MIT © 2026 [Tobias Musser](https://github.com/P2ppyJack)

*Built and battle-tested inside a live multi-session Hermes Agent setup — the board this
code was developed against was coordinating the very sessions that wrote it.*
