## Shared-resource coordination (Hermes co-worker protocol)

You share this machine with the user's interactive sessions, other bots, and
scheduled (cron) jobs. A coordination board tracks who is using shared
resources. You MUST consult it before mutating anything shared.

**Shared resources include** (adapt to your setup): remote GPU boxes
(`box:<host>`), the desktop UI (`ui:desktop`), singleton apps that allow only
one client at a time, shared project directories (`file:~/...`), a shared
skills/scripts tree, and the machine's MAIN agent memory store.

**The ONLY claim-free exemption** is your own profile's INTERNAL stores — the
memory, sessions, and cron store under your profile directory — because no
other actor writes those. The exemption is exactly that list. Anything else,
INCLUDING files and directories you yourself created (output dirs, reports,
scripts you maintain), lives in shared space: another actor can legitimately
touch it, so register and claim it like everything else. Your profile memory
is NOT the shared memory store above — don't let "mine" blur that line.

**Native target.** Automatic yield requires this run's actual native session id
and absolute profile home. `register` auto-captures `HERMES_SESSION_ID` and
`HERMES_HOME` when present. If either is absent, obtain the real values from the
native launcher and use the explicit form below; never guess. Enrollment alone
does not create a receiver target.

**Protocol** (register once per task, checkpoint, claim before mutating,
release at the end):

```bash
SC=~/.hermes/scripts/session_coord.py     # wherever you installed the CLI
CID=$(python3 $SC register --task "<what you are doing>" --surface "bot:<botname>" \
  --native-session-id "<ACTUAL_NATIVE_SESSION_ID>" --wake-transport hermes \
  --wake-target-json '{"profile_home":"<ABSOLUTE_PROFILE_HOME>","kind":"bot","session_id":"<ACTUAL_NATIVE_SESSION_ID>"}' \
  | head -1)
# Write an EXISTING ABSOLUTE checkpoint file before this call.
python3 $SC claim --id "$CID" --res "<key>" --res "<key2>" --task "<task>" \
  --yield --checkpoint "<ABSOLUTE_CHECKPOINT_FILE>"
# rc 0 = full set held; rc 75 = episode parked: STOP, do not poll or mutate
# ... do the whole task ...
python3 $SC done --id "$CID"
```

Rules:
- Claim EVERYTHING the task will touch up front in ONE call (atomic — this is
  what prevents deadlock). Hold for the whole task; release with `done` at the
  end, never between individual writes.
- Exit 75 = the immutable full request is parked. Stop immediately; do not poll,
  mutate, or use more model turns. `--wait` is only for legacy shell actors.
  NEVER proceed against a held resource or `steal` without user approval.
- Repeat `--res` for every key; never join resource keys with commas. Resume
  only from a native targeted prompt containing the exact
  `continue --id <board-id> --event <event-id>` command.
- If your `inbox --id $CID` shows a preempt/priority request: finish the
  current atomic step, refresh the durable checkpoint, run `pause --id $CID
  --yield --checkpoint "<ABSOLUTE_CHECKPOINT_FILE>"`, and stop.
- Priorities are set by the USER only. Never rank or preempt on your own
  judgment.
- If exit 75 names a `bot:` holder, you may @mention that bot to negotiate
  (ask its ETA, request early release, offer to batch your change into its
  run) — but **chat is never a lock**. Only a successful claim authorizes
  mutation, no matter what was agreed in conversation.
- If the board itself errors, say so and do NOT mutate shared resources blind.
- Explicit Stop/cancel calls `cancel-wait`; a dead/stopped holder is recovered
  by TTL reconciliation. TTL is not proof that the real resource is idle:
  inspect it before the first resumed mutation.

Enrollment marker: session-coord (bot-wire v1) — installer idempotence + board
audit; do not remove this line.
