# Standing memory entry — the "always call it" wire-in

The coordination board is **advisory** (README Rule 1): it never intercepts
writes, so a session that never checks it gets no protection at all. The
line between "installed" and "actually protecting you" is this memory entry
— agent memory is injected into every session, every turn, so a rule living
there fires on *every* task instead of only when a skill happens to load.

The block below is the canonical enrollment text for an agent's **standing
memory** (the store the agent reads at the start of every session). Keep it
verbatim; the `(wire v1)` marker makes re-running idempotent (an installer,
an upgrade, or a re-paste will recognize it and skip instead of duplicating).

---

## Canonical entry (paste-ready)

The entry is deliberately one line — Hermes memory stores join entries with
`§` delimiters, and a wrapped paste would break the rule into fragments.
Copy the whole line:

```
STANDING RULE — session-coord (wire v1): ALWAYS deconflict via the coordination board before touching shared resources (files, skills, memory, cron store, remote boxes, desktop UI) — any concurrent session/bot/cron may be mid-task. First: `python3 ~/.hermes/scripts/session_coord.py status`. Then register once (`ID=$(python3 ~/.hermes/scripts/session_coord.py register --task '...')`); claim before writes (`python3 ~/.hermes/scripts/session_coord.py claim --id $ID --res <keys> --wait`); `python3 ~/.hermes/scripts/session_coord.py done --id $ID` at task end. Full protocol: skill multi-session-coordination. Off-switch: `python3 ~/.hermes/scripts/session_coord.py disable`.
```

If the coordinator was installed to a non-default directory, substitute the
real path to `session_coord.py` (the repo's `install.py --wire-memory` does
this substitution for you).

## How a human adds it

Pick one:

1. **Ask your agent.** Paste the block into any chat with the words: *"Add
   this to your memory verbatim"* — the agent's memory tool persists it into
   the store injected every turn.
2. **Edit the file directly.** Append the block to the agent's memory file —
   for Hermes Agent that is `~/.hermes/memories/MEMORY.md` (agent notes) on
   macOS/Linux. (Note: the `hermes memory` CLI configures the external
   memory *provider* — it does **not** add entries. Editing the file, or
   asking the agent, is the supported path.)
3. **Run the installer.** `python3 install.py` writes the entry
   automatically; `--no-wire-memory` skips it; `--memory-file` points at a
   non-default store.

Each profile/bot has its own memory: **every active agent profile** that
shares this machine needs enrollment. `install.py` handles existing ones —
non-bot profiles get this entry in their own `memories/MEMORY.md`, bots get
the persona blurb (`bot-soul-coordination.example.md`) instead. For actors
created after the install, re-run `install.py` (idempotent) or paste
manually; `session_coord.py status` flags unenrolled/unwired profiles.

## If you are an agent performing this install

1. Copy the files (`python3 install.py`, or the manual steps in README §8).
2. Add the canonical entry above to your own memory store verbatim —
   you have a memory tool for this; substitute the real script path if
   `--dest` was used. If the entry already exists (installer reports
   `wired (already present)`), just verify it.
3. Run `python3 ~/.hermes/scripts/session_coord.py status` once — this
   creates the board and proves the CLI works.
4. From now on, at the start of every task that touches shared resources:
   check the board, register once, claim before writing, `done` at the end
   (README §8, or load the fuller protocol from the skill
   `multi-session-coordination`).

## Verify

```bash
python3 ~/.hermes/scripts/session_coord.py status   # board exists and answers
grep -c "session-coord (wire v1)" ~/.hermes/memories/MEMORY.md   # exactly 1
```

## Remove

Delete the block. The board keeps working for anyone who still follows the
protocol; only new sessions stop self-enrolling.
