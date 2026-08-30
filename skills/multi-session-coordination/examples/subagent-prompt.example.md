# Enrollment prompt for subagents (delegate/fan-out workers)

When an orchestrator session fans out subagents, the children **inherit no
environment** — so, exactly like bots, they must receive the coordination
protocol *in their prompt*. Children register with `--parent <id> --slot <a|b|…>`
so they get **lineage ranks**: a parent at rank 1 makes its children `1a`, `1b`
(parent outranks its own children; the whole family sorts between rank 1 and
rank 2; slots break sibling ties). Ranks derive live — re-ranking the parent
re-ranks the entire family.

Waiting is cheap: `claim --wait` polls inside a single call, so a blocked child
burns no model turns while it waits.

Paste this into each child's prompt, substituting the parent's coordination id
and a distinct slot per child (give slot `a` to the critical-path child):

---

```
Coordination: you are a subagent of coordination session <PARENT_ID>.
First, join the board (record your OWN id):

  SC=~/.hermes/scripts/session_coord.py
  CID=$(python3 $SC register --task "<your task>" --surface subagent \
        --parent <PARENT_ID> --slot <a|b|c> | head -1)

Before mutating any shared resource, claim it (wait politely if held):

  python3 $SC claim --id $CID --res "<key>" [--res "<key2>"] --wait --timeout 300

When your task is completely done, release everything:

  python3 $SC done --id $CID

If your inbox (`python3 $SC inbox --id $CID`) shows a USER-PRIORITY or preempt
request: finish the current atomic step, checkpoint your progress to a file,
run `python3 $SC pause --id $CID --note "<checkpoint file>"`, and report that
checkpoint path in your final summary.
```

---

Orchestrator-side rules:
- Register each child with a DIFFERENT `--slot`, in the order you want them to
  win contention.
- Children call `done` for their OWN `$CID` (their claims, not the parent's).
  Never share the parent's id with a child.
- If the PARENT is asked to pause, first stop/steer its children, then pause the
  parent's own claims.
- Pre-claim at the parent level any resource the whole family must hold across
  child boundaries.
