# Upstream compatibility and integration decisions

Prepared by Hermes (agentic AI assistant) under the direction of Tobias Musser

## Decision

Retain a framework-neutral board, an optional external plugin, a generic host
interface proposal, and a separate joined-delegation proposal. Do not merge
unreleased gateway IPC or runtime-hook proposals as a dependency of this update.
Their applicable contracts and review lessons are incorporated into verification;
no code or commits from those proposals are represented as original work here.

The host candidate is pinned to Hermes base
`0b8daf30aae1d0b129ede9b857cac2158eb50324`. Upstream was also fetched at
`ad03f20dd61919ca2135d6904e787a94284aacaf`: the intervening changes affect six
Desktop files, not the Python host paths in these patches. This is a dated
compatibility observation, not permission to apply patches to arbitrary versions.

## Contract comparison

| Dimension | Exact gateway IPC, #70406 | Runtime hooks, #53626 | Native continuation proposal |
|---|---|---|---|
| Target | Exact profile, route key and expected session ID; rejects stale compression parents | Adapter/runtime event and session key | Host-proved profile/home/session/surface/lineage; plugin matches an immutable episode |
| Surfaces | Owner-local Unix gateway endpoint | Gateway platform adapters | Existing CLI, TUI/Desktop and gateway host paths; no new network endpoint |
| Busy behavior | Steer a running agent when supported, otherwise queue | Per-event queue/interrupt intent | Refuse busy/pending-human admission; leave work pending rather than interrupting |
| Acceptance meaning | Structured route acceptance, with queued/steered disposition | Event/control/lifecycle callbacks | Pre-model commit callback; plugin stores exact receiver receipt; not business completion |
| Retry boundary | Request identity and commit/cancellation gate | Not a durable delivery protocol | Definitive non-send can requeue; ambiguous admission is not blindly resent |
| User boundaries | Route and lifecycle validation at acceptance | Proposed interrupt, stop/drop and new-session callbacks | Synchronous source fence before Stop/new/switch/close invalidates the episode |
| Plugin registration | IPC is a trusted control-plane primitive, not the public plugin API | Review identified missing supported callback dispatch/registration paths | Normal plugin discovery calls `register_native_turn_source`; disposal is manager-owned |

### #70406: align and preserve, do not replace blindly

Reviewed head: `e090c251ee3a8af8e45d70c84e69501ee86d17dd`, open/unmerged when
checked. Its `_inject_exact_session_sync` implementation creates the protected
`_new_gateway_session_ipc_event`, validates the route, and returns acceptance after
steering or scheduling. Its review correctly required a real adapter rather than a
mock that accepted the wrong event subtype.

The continuation proposal does **not** manufacture that protected IPC subtype or
claim IPC privileges by copying metadata. It supplies a typed native lease to a
separate idle-only admission method, then uses the existing adapter background-turn
machinery. Existing plugin-injection and relay-routing regressions are run unchanged.
A new public-path integration test loads the real external plugin through normal
host discovery and uses the real `BasePlatformAdapter` path for gateway admission.

Before upstream publication, request maintainer agreement on whether the common
idle reservation/scheduling layer should be factored further. An IPC implementation
could become a transport adapter only if it supports idle-only refusal, synchronous
pre-model admission, exact receipt readback, and cancellation fencing without
weakening its own existing queue/steer behavior. The current IPC contract alone is
not an equivalent replacement. This packet does not certify a merged combination
of the two open PRs.

Source: [#70406](https://github.com/NousResearch/hermes-agent/pull/70406),
[review correction](https://github.com/NousResearch/hermes-agent/pull/70406#discussion_r3681406247).

### #53626: use its public-contract lesson, not its private callbacks

Reviewed head: `d1469ada5fe8a0497840c321d7bb27f995e09f1f`, open/unmerged when
checked. The reviewed implementation assigns `_runtime_control_handler` but lacks
a supported adapter dispatch entry point that invokes it. Lifecycle tests set a
private `_on_runtime_turn_start` attribute directly. The review requests remain
applicable to that head; a setter is not an exercised extension interface.

The native proposal therefore keeps one disposable public registration that owns
both `poll` and `on_user_boundary`. Tests cover actual plugin discovery, profile
isolation, disable/re-enable, unload with real pending work, cancellation after a
lease is offered, exact receipt readback, and whole-request reacquisition. CLI/TUI
public-path checks stop at the generic admission API; resident UI loops and model
execution remain separate acceptance gates.

Source: [#53626](https://github.com/NousResearch/hermes-agent/pull/53626),
[dispatch review](https://github.com/NousResearch/hermes-agent/pull/53626#discussion_r3584743109),
[registration review](https://github.com/NousResearch/hermes-agent/pull/53626#discussion_r3584743111).

## Existing main behavior

`PluginContext.inject_message()` remains unchanged. It serves a different contract:
CLI injection can interrupt, and gateway acceptance schedules asynchronous work.
This proposal must not silently change those semantics for existing plugins.
Current injection tests provide positive regression controls, not proof of every
possible third-party plugin combination.

## Separate follow-ups, not dependencies

- [#107665](https://github.com/NousResearch/hermes-agent/issues/107665): plugin-accessible
  turn halt could make cooperative yield deterministic. Coordinate with the existing
  volunteer; do not claim this update already implements forced suspension.
- [#56658](https://github.com/NousResearch/hermes-agent/pull/56658): remote-backend file
  state should be tested through the actual remote file tool. A registry-level probe
  with a local positive control is evidence for follow-up, not end-to-end certification.
- #79000 and #105133 are different orchestration/chat scopes. #71091 write-duration
  locking is complementary, not a substitute for task-long resource claims.
- Link #28690 and #78418 as related use cases; do not claim to close all their requirements.

## Publication gates

The earlier local acceptance matrix is documented in `VERIFICATION.md`. The native
host patch has additionally been forward-ported to upstream
`5dea46d13deec9549bdc2ea703ae9201d733c28d`, preserving newer branch-failure,
profile-completion replay, source-aware authorization and removed-activity behavior.
The native current-base regression gate passed 142 tests (one existing Windows-only
skip on macOS); separate joined-delegation regressions passed 52 tests.

These are proposed changes for review, not a stable integration release. Maintainer
architecture/whole-submission review and required hosted CI on the exact submitted
heads remain merge gates. Publication does not activate or install the changes.

---
Hermes analyzed and drafted; Tobias Musser supplied business context, adjudicated
judgment calls, and corrected conclusions.
