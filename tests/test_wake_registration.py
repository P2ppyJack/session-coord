"""Registration metadata is additive and bound to the receiver, not its actor."""
from __future__ import annotations

import json
import runpy
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/multi-session-coordination/scripts/session_coord_wakes.py"
parse_target = runpy.run_path(str(SCRIPT))["parse_registration_target"]


@pytest.mark.parametrize("mode", ["automatic", "legacy", "explicit", "child"])
def test_registration_preserves_receiver_identity_and_supplied_metadata(tmp_path, mode):
    home = str((tmp_path / "custom-home").resolve())
    receiver = "parent-native" if mode == "child" else "receiver-native"
    actor = "child-native" if mode == "child" else receiver
    target: dict[str, Any] = {"profile_home": home, "kind": "cli", "session_id": receiver}
    if mode == "child":
        target.update(kind="subagent_parent", parent_session_id=receiver)
    if mode == "explicit":
        target.update(profile="chosen-profile", compression_lineage=["ancestor", receiver])
    raw = None if mode == "automatic" else json.dumps(target)
    native, transport, captured, captured_home, error = parse_target(
        raw, actor, None, "cli", "parent-board" if mode == "child" else None,
        env={"HERMES_HOME": home, "HERMES_SESSION_ID": actor},
    )
    assert error is None
    assert native == actor
    assert transport == "hermes"
    assert captured_home == home
    assert captured["session_id"] == receiver
    assert captured["compression_lineage"] == target.get("compression_lineage", [receiver])
    if "profile" in target:
        assert captured["profile"] == target["profile"]
    else:
        assert "profile" not in captured, "do not guess a profile label from a path"
    for key, value in target.items():
        assert captured[key] == value


@pytest.mark.parametrize("metadata", [
    {"profile": None}, {"profile": ""}, {"profile": 1},
    {"compression_lineage": None}, {"compression_lineage": []},
    {"compression_lineage": "receiver"}, {"compression_lineage": ["foreign"]},
    {"compression_lineage": ["receiver", 1]},
])
def test_invalid_explicit_metadata_is_rejected_not_repaired(tmp_path, metadata):
    target = {"profile_home": str(tmp_path), "kind": "cli", "session_id": "receiver", **metadata}
    result = parse_target(json.dumps(target), "receiver", "hermes", "cli", None,
                          env={"HERMES_SESSION_ID": "receiver"})
    assert result[-1] is not None
