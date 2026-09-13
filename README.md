# session-coord — cooperative coordination for concurrent agents

[![tests](https://github.com/P2ppyJack/session-coord/actions/workflows/tests.yml/badge.svg)](https://github.com/P2ppyJack/session-coord/actions/workflows/tests.yml)

`session-coord` is a dependency-free SQLite intention board for concurrent agent
sessions, subagents, bots, and scheduled jobs that share one machine. Actors
register, claim resources for a task, wait or yield when another actor is ahead,
and notify waiters on release. The board is advisory: it coordinates intent but
does not intercept file or process operations.

The board and its CLI are framework-neutral. The repository also contains a
standard Hermes skill bundle and an **optional, unreleased** setup helper for a
separately supplied Hermes native-continuation plugin.

## Why this update

Concurrent agents can overwrite the same files, race on a shared service, or
resume from a checkpoint after another actor has changed the resource. A lock
alone does not tell a stopped conversation when it is safe to continue.

This update separates **who may work** from **how a conversation resumes**:

| Layer | Responsibility | Why it stays separate |
|---|---|---|
| Standalone board and skill | Atomic resource claims, priorities, queues, checkpoints, and durable wake records | Useful without Hermes or a plugin; no model or network dependency for board operations |
| Optional `session-coord-native` plugin | Match a wake to the exact profile/session and track receiver receipts | Host-specific delivery can be added, upgraded, or removed without deleting coordination state |
| Proposed generic Hermes host interface | Admit plugin-supplied work at an idle turn boundary, with cancellation and session fencing | Reusable by other plugins; no board-specific logic or new model tool in core |
| Separate joined-delegation option | Run children in parallel but return to the parent only after all outcomes arrive | Prevents premature integration without serializing independent child work |

### From conflict to continuation

1. An actor requests its **complete resource set**. Claims are all-or-nothing;
   priority and queue order determine eligibility.
2. With verified native integration, a conflicting actor records a checkpoint,
   yields, and **ends its turn**. It does not poll, continue modifying the
   resource, or assume it owns a partial claim.
3. Release or TTL reconciliation makes eligible waits available as durable wake
   events. The script-only watchdog can reconcile expiry and interrupted
   delivery; it does not launch an offline Hermes session.
4. The plugin checks the exact target and current episode. The host admits a
   continuation only at a safe idle boundary; user cancellation or a session
   change fences stale work rather than redirecting it elsewhere.
5. The resumed actor revalidates the **entire original claim** before using the
   checkpoint. Another conflict means it yields again, not that permission is
   assumed from an old notification.

Receiver receipts distinguish accepted delivery from a definitive non-send and
an uncertain outcome. A lost acknowledgement is reconciled rather than blindly
resent. **Admission is not task completion**: a delivered prompt does not prove
the resumed work succeeded, and this is not a claim of exactly-once business
effects.

### Practical benefits and limits

- **Less idle agent work:** a yielded parent consumes no further model turns
  while waiting. Board/watchdog operations need no model calls; actual resumed
  work still uses the configured model. No benchmarked cost saving is claimed.
- **Recoverable interruptions:** durable claims, checkpoints, events, and
  receipts make release/expiry recovery inspectable instead of relying only on
  an in-memory notification.
- **Reversible adoption:** keep standalone operation by default, add the plugin
  on a later installer run, or remove only the plugin while retaining user state.
- **Smaller host maintenance burden:** the external plugin owns the integration;
  installation does not patch Hermes source or rewrite a running system prompt.

This remains **cooperative coordination**, not an OS lock, sandbox, or forced
process suspension. A non-participating actor can still modify a resource.
Without the plugin, actors use manual coordination or bounded shell waits;
automatic native continuation is unavailable. The native components remain
unreleased until their compatibility and runtime acceptance gates are met.

## Compatibility with upstream work

The native interface is a proposal, not a replacement for existing plugin injection.
[UPSTREAM-COMPATIBILITY.md](UPSTREAM-COMPATIBILITY.md) compares the pinned contracts
with Hermes PRs #70406 (exact gateway IPC) and #53626 (runtime hooks). The update
preserves existing injection behavior, adopts real public-registration/adapter tests,
and does not depend on merging either proposal. Queue/steer acceptance is not the
same guarantee as idle-only, receipt-backed continuation. Turn-halt and remote-file
state improvements remain separate follow-ups.

## Stable board quick start

```bash
git clone https://github.com/P2ppyJack/session-coord.git
cd session-coord
python3 install.py --check
python3 install.py
```

The default installer:

- copies the CLI, wake-state helper, cron guard, script-only reconciliation
  watchdog, and selftests to `~/.hermes/scripts/`;
- copies the skill bundle to `~/.hermes/skills/multi-session-coordination/`;
- creates the state directory but never overwrites the board database or cron
  manifest;
- installs board-only managed enrollment in the default memory store and
  existing profile carriers;
- runs every bundled selftest against temporary databases unless `--no-verify`
  is used.

With the default **keep** choice, it does **not** import Hermes, install a
plugin, edit Hermes configuration, schedule the watchdog, start a model, or
restart a process. `--check` previews changes without prompting.

Use the board:

```bash
SC="$HOME/.hermes/scripts/session_coord.py"
python3 "$SC" status
ID=$(python3 "$SC" register --task "update project" --surface cli | head -1)
python3 "$SC" claim --id "$ID" --res "file:$HOME/project" --task "update project"
# Continue only after CLAIMED. If HELD or QUEUED, do not mutate the resource.
python3 "$SC" done --id "$ID"
```

A long-lived shell actor may add one bounded `--wait --timeout SECONDS` call.
Board-only enrollment never tells an agent to use `--yield`, because a yielded
agent needs a proven native consumer to resume it.

## Installation and upgrades

### Requirements

- Python 3.8 or newer; the board uses only the standard library.
- A POSIX shell for `coord_guard.sh` and the shell selftests. The Python CLI also
  runs on Windows; the installer uses Git Bash when available and reports a
  verification skip when no working Bash exists.

### Optional-plugin choice and reruns

On an interactive terminal, `python3 install.py` explains that the board works
without a plugin, and offers:

```text
1. Do not install/change the plugin (keep the current setup; default)
2. Install or upgrade the optional plugin
3. Remove only the plugin; keep the board, claims, skills and other settings
0. Cancel without changes
```

**Keep** means no plugin changes: it does not uninstall an existing plugin.
Without the plugin, coordination is manual; automatic continuation into a
running Hermes session requires the plugin and a compatible host. Installing
native integration also enables waiting for every delegated agent's report;
removing the plugin deliberately preserves that shared delegation setting.
No choice restarts a running process or starts a stopped application.

The choice is asked again on each interactive rerun. Choose **2** to add the
plugin to an existing standalone installation, or update an installed plugin
from a reviewed newer checkout. Choose **3** to remove only native integration.
When installing, the prompt asks for the reviewed plugin checkout path if it
was not supplied. Non-interactive runs default to **keep**, never to install or
remove. Equivalent explicit commands are:

```bash
python3 install.py --plugin keep
python3 install.py --plugin install --plugin-path /path/to/session-coord-native
python3 install.py --plugin remove
python3 install.py --plugin remove --check
```

Plugin changes target `default` unless `--profile NAME` is supplied; repeat
that flag to select additional profiles. Plugin-only removal **bypasses the
board install/update path**. It disables and removes just `session-coord-native`
through Hermes, removes only its exact managed native instruction block, and
leaves the standalone board, database, claims, skills, receipt state, shared
watchdog, unrelated plugins, and other profiles intact. Pending waits are not
cancelled or replayed: recover them manually if native continuation is removed.
A repeated removal of an already-absent plugin is a no-op.

Before removal, a recovery copy is verified under the selected profile's
`state/session-coord-native-backups/`. Custom or malformed native instructions,
redirected plugin paths, and ambiguous configuration are preserved and reported
as `ACTION NEEDED`. Partial failures return nonzero and retain recovery evidence.
Close/restart resident Hermes processes yourself to unload the plugin; on-disk
removal does not revoke code already loaded into a running process.

The plugin checkout's `install.py` delegates to this same installer; it no
longer has a competing copy/force-overwrite implementation. If the checkouts are
not siblings, pass `--board-installer /path/to/session-coord/install.py` to that
entry point. Legacy `--force`, `--enable`, and `--json` flags on that old entry
point are refused with instructions; use the canonical choices above, or
`hermes_setup.py remove --profile default --json` for a structured removal report.

### Installer options

```text
python3 install.py [--check] [--no-verify]
  [--dest DIR] [--state-dir DIR]
  [--memory-file FILE] [--profiles-dir DIR]
  [--skill-dest DIR] [--seed-manifest]
  [--no-wire-memory] [--no-wire-bots] [--no-wire-profiles]
  [--no-skill] [--plugin keep|install|remove] [--plugin-path DIR]
  [--profile NAME ...] [--hermes EXECUTABLE] [--hermes-arg TOKEN ...]
```

`HERMES_HOME`, `HERMES_SCRIPTS_DIR`, `HERMES_STATE_DIR`, and
`HERMES_COORD_PROFILES_DIR` provide equivalent path defaults. Explicit flags win.
Paths containing spaces and shell metacharacters are treated as literal paths.

### Upgrade safety

The installer distinguishes source code from user state:

- differing installed scripts and skill files are backed up before replacement;
- `session_coordination.db`, `cron_resources.json`, the disabled sentinel, and
  unrelated files are preserved;
- existing file permissions are preserved where a carrier is rewritten;
- profiles outside the selected path are not inspected or changed;
- opt-outs apply in both install and `--check` modes.

Enrollment is versioned with exact managed blocks:

```text
<!-- BEGIN session-coord managed board-v2 -->
...
<!-- END session-coord managed board-v2 -->

<!-- BEGIN session-coord managed bot-board-v2 -->
...
<!-- END session-coord managed bot-board-v2 -->
```

Exact text shipped as `session-coord (wire v1)` and
`session-coord (bot-wire v1)` is migrated surgically, including the newer
local-native variant. Surrounding memory, personality, and configuration stay
byte-for-byte intact. Every changed existing carrier gets a `.bak-<timestamp>`
copy. A customized, duplicated, or malformed legacy/managed block is preserved
and reported as `ACTION NEEDED`; it is neither replaced nor counted current.
Rerunning after a successful migration is byte-idempotent.

Canonical manual copies are in:

- `skills/multi-session-coordination/examples/memory-entry.example.md`
- `skills/multi-session-coordination/templates/bot-soul-coordination.md`

### Skill-only install

The repository uses the standard third-party skill layout:

```bash
hermes skills install P2ppyJack/session-coord/skills/multi-session-coordination
# or
hermes skills tap add P2ppyJack/session-coord
hermes skills install P2ppyJack/session-coord/multi-session-coordination
```

A skill-only install places the CLI under the installed skill's `scripts/`
directory. It does not perform system-wide board enrollment; use `install.py` or
copy the managed examples explicitly.

## Optional Hermes native continuation — unreleased

Native continuation requires two separately supplied, compatible components:

1. a Hermes host with the generic native-turn-source registrar and the documented
   joined-delegation policy helper;
2. the external plugin named `session-coord-native`.

These capabilities are not part of the stable board release, and this repository
does not claim upstream acceptance or activation in an already running process.
The helper proves the real registration API with Hermes Plugin Doctor; a stored
config key or version string is not accepted as capability proof.

The supplied plugin path must be a clean Git worktree with a committed HEAD.
Hermes's sanctioned plugin installer clones a Git URL, so this restriction keeps
the bytes checked by Doctor identical to the immutable bytes installed from the
local `file://` URL.

### Read-only check

Select profiles explicitly; repeat `--profile` or use `--all-profiles`:

```bash
python3 hermes_setup.py check \
  --profile default \
  --plugin-path /absolute/path/to/session-coord-native

python3 hermes_setup.py check \
  --all-profiles \
  --plugin-path /absolute/path/to/session-coord-native \
  --json
```

`check` performs no installation, configuration, enrollment write, scheduling,
or restart. For every selected profile it verifies:

- the profile resolves through `hermes -p PROFILE config path`;
- `hermes -p PROFILE plugins doctor /ABS/PLUGIN --ci` exercises real plugin
  registration;
- the installed plugin, enablement state, exact Boolean
  `delegation.wait_for_all`, and plugin-owned `native-check --json` agree;
- board and native managed enrollment are current.

A failure in any profile makes the global result not ready.

### Explicit setup

```bash
python3 hermes_setup.py setup \
  --profile default \
  --profile research \
  --plugin-path /absolute/path/to/session-coord-native
```

Before the first mutation, the helper resolves and Doctor-checks every selected
profile and refuses globally on an unsupported host, unavailable profile,
unverifiable plugin checkout, stale board enrollment, or customized native block.
It then uses only sanctioned Hermes operations:

1. install or upgrade the immutable plugin revision;
2. Doctor the installed copy;
3. enable `session-coord-native` without built-in tool override;
4. call `session-coord native-check --json` and require real native-source and
   joined-policy support;
5. set `delegation.wait_for_all` to `true` through `hermes config set` only when
   needed;
6. require an exact JSON Boolean `true` from `hermes config get ... --json` and
   repeat `native-check`;
7. after every selected profile succeeds, write the separate managed native
   enrollment block.

Malformed JSON, wrong JSON types such as `"true"` or `1`, a command timeout, or
partial persistence stops further mutation. The report distinguishes
`configured_not_enrolled`, `partial`, and `action_needed` states; it never treats
a partially configured set as ready. A healthy rerun performs checks only and
creates no extra enrollment backup.

The plugin reports activation as `fresh_process_only`. Successful setup therefore
ends as **configured; restart required**. The helper never restarts CLI, TUI,
Desktop, or gateway processes and never changes model/provider settings.

Native setup intentionally uses the canonical board installed at the default
profile's `scripts/session_coord.py`. A custom `install.py --dest` remains valid
for board-only use but is not accepted for native setup because the external
consumer path is not part of the current `native-check` receipt. Use
`--hermes /path/to/hermes` to supply one executable path as a single argv token.
Repeat `--hermes-arg TOKEN` only for launcher forms such as
`python hermes_stub.py`; every value remains a separate argv token. No shell
command strings are accepted or evaluated.

### Optional shared watchdog

The default installer only copies `coord_resume_watchdog.py`. Scheduling is a
separate explicit choice:

```bash
python3 hermes_setup.py setup \
  --profile default \
  --plugin-path /absolute/path/to/session-coord-native \
  --watchdog
```

`--watchdog` requires `default` to be selected. The helper invokes the plugin's
sanctioned command only through the canonical owner profile:

```text
hermes -p default session-coord watchdog-setup --json
hermes -p default session-coord watchdog-setup --json --check
```

There is one machine-wide job, not one per profile. Check mode is read-only. The
plugin owns duplicate/drift detection and scheduler readback. An uncertain create
is never retried; the helper performs a read-only reconciliation and reports the
observed partial state and recovery command. The watchdog is script-only: it runs
`wake-reconcile --json` without a model or network call.

See `skills/multi-session-coordination/references/automatic-resume.md` for the
receipt, cancellation, target, and recovery invariants.

### Setup and upgrade recovery

- **`ACTION NEEDED` for an enrollment:** keep the carrier unchanged, compare the
  managed span with the canonical example, and reconcile user-owned edits before
  rerunning. Backups are adjacent `.bak-<timestamp>` copies.
- **Source checkout not clean:** Doctor still examines the supplied bytes in
  check mode, but setup refuses to install a different committed snapshot. Make
  a clean committed copy and rerun; the supplied repository is never changed.
- **Installed plugin has local edits:** the installed checkout is preserved. Move
  or reconcile those changes before asking setup to replace it.
- **Plugin installed/enabled but policy failed:** no native enrollment is written.
  Correct the reported config/capability fault and rerun `check`, then `setup`.
- **Configuration persisted but another selected profile failed:** the report
  marks the completed profile `configured_not_enrolled`. Fix the failing profile
  and rerun the same complete profile set; do not paste native instructions.
- **Watchdog create uncertain:** run `check --watchdog`. Never create another job
  until the plugin's read-only check resolves the scheduler state.
- **Setup succeeded:** start a fresh Hermes process. The helper never restarts a
  resident process and never reports live activation for it.

## Coordination model

### Resource keys

| Key | Scope |
|---|---|
| `file:/absolute/path` | A file or a directory and its descendants |
| `skill:<name>` | One skill while it is being edited |
| `memory` | The machine's main agent memory store |
| `ui:desktop` | Exclusive foreground desktop control |
| `box:<host>` | Mutating work on a remote machine |
| `cron-store` | Scheduled-job registry mutation |
| `res:<name>` | An agreed custom resource |

Claims are atomic across repeated `--res` arguments. If any requested resource
is unavailable, none of the set is granted. Directory claims overlap descendant
paths after canonicalization.

### Priority and fairness

Only the user assigns priority through `prioritize`. Agents must not self-rank.
Among equal ranks, the earliest live waiter wins. A free resource can remain
fenced for the better-ranked or earlier waiter. Abandoned ordinary waiters stop
fencing after their liveness window; paused resume spots remain explicit.

`preempt` requests cooperation. A holder finishes its current atomic write,
checkpoints, pauses, and releases. `steal` is a loud, audited break-glass command
and requires explicit user approval.

### TTL and failure behavior

Claims expire after their TTL and stale rows are reaped. Expiry is not proof that
the real resource is safe: inspect it before the first mutation after an expired
holder. Size `--ttl` to long work and refresh the same idempotent claim after long
interruptions.

The board fails open at the CLI boundary so its own outage cannot strand an
unrelated backup. Agents should still report that they are operating without
coordination and avoid shared mutations until the board is healthy.

### Bots and profiles

A profile with `SOUL.md` is treated as a bot enrollment carrier. A non-bot
profile uses its own `memories/MEMORY.md`. `status` reports persona-bearing
profiles whose exact bot managed block is absent as `UNENROLLED`, and non-bot
profiles with an existing but non-current memory carrier as `UNWIRED`. A marker
substring alone does not clear either audit.

Bots' own profile-internal memory, sessions, and cron store are claim-free. Files
they create in shared locations are still shared and require claims.

### Cron jobs

`cron_resources.json` maps job ids to resources, a `wait` or `skip` policy, and a
critical flag. A wrapper sources `coord_guard.sh` before any work:

```bash
. "$HOME/.hermes/scripts/coord_guard.sh"
coord_guard <job-id> wait 900 90 || { [ $? -eq 75 ] && exit 0; }
```

The guard is shell-only and runs before an agent/model. Manifest drift is a
safety defect: update a job's complete resource list whenever its footprint
changes. A critical job must not be silently deferred; finish and release, run it
early, or record an explicit pause/resume responsibility.

## Command reference

| Command | Purpose |
|---|---|
| `status [--json]` | Sessions, claims, queues, cron radar, enrollment audit |
| `register --task T [--surface S] [--parent P --slot a]` | Join the board |
| `claim --id ID --res K [--res K...] [--wait]` | Atomically request resources |
| `check --res K` / `wait --res K` | Inspect or block for availability |
| `release --id ID [--res K]` / `done --id ID` | Release one/all and notify |
| `inbox --id ID` | Read release, expiry, and preemption notices |
| `prioritize --session ID --rank N` | Record user-set priority |
| `preempt --id ID --res K` | Ask a lower-priority holder to pause |
| `pause --id ID --note TEXT` / `resume --id ID` | Manual cooperative pause/resume |
| `steal --id ID --res K --reason TEXT` | Explicit break-glass release |
| `cron-guard`, `cron-note`, `wait-for-cron` | Scheduled-job coordination |
| `switch`, `enable`, `disable` | Report or change the board master switch |

Legacy command and JSON contracts remain available. Native-only flags and
commands remain documented in the advanced reference but must not be used as an
automatic-resume promise without successful optional setup.

## Verification

All suites use temporary databases and stores:

```bash
bash skills/multi-session-coordination/scripts/selftest.sh
bash skills/multi-session-coordination/scripts/selftest_priority.sh
bash skills/multi-session-coordination/scripts/selftest_cron.sh
bash skills/multi-session-coordination/scripts/selftest_toggle.sh
python3 skills/multi-session-coordination/scripts/selftest_wakes.py
python3 -m pytest -q tests
```

The CI workflow defines board and portable installer/helper tests for Linux,
macOS, and Windows across supported Python versions; a workflow definition is
not evidence that an unpublished revision passed on those platforms.
External Hermes/plugin integration may
skip when those separately supplied components are absent; a skip is not proof
of native activation.

After installation, verify the managed block and a scratch claim:

```bash
python3 "$HOME/.hermes/scripts/session_coord.py" status
ID=$(python3 "$HOME/.hermes/scripts/session_coord.py" register --task verify)
python3 "$HOME/.hermes/scripts/session_coord.py" claim --id "$ID" --res res:verify
python3 "$HOME/.hermes/scripts/session_coord.py" done --id "$ID"
```

## License and attribution

MIT licensed. Copyright © 2026 Tobias Musser. The project is maintained by
Tobias Musser (P2ppyJack), with implementation assistance from Hermes Agent.

Prepared by Hermes (agentic AI assistant) under the direction of Tobias Musser

Hermes analyzed and drafted; Tobias Musser supplied business context, adjudicated
judgment calls, and corrected conclusions.
