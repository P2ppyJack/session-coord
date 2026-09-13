"""Wake-result rejection must be fail-closed and leave durable state unchanged."""

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

CLI = (
    Path(__file__).resolve().parents[1]
    / "skills/multi-session-coordination/scripts/session_coord.py"
)


def run_cli(env, *args, ok=True):
    proc = subprocess.run(
        [sys.executable, str(CLI), *args, "--json"],
        env=env,
        text=True,
        capture_output=True,
    )
    assert (proc.returncode == 0) is ok, (proc.stdout, proc.stderr)
    return json.loads([line for line in proc.stdout.splitlines() if line][-1])


@pytest.fixture
def leased(tmp_path):
    db, profile = tmp_path / "board.db", tmp_path / "profile"
    profile.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "HERMES_COORD_DB": str(db),
            "HERMES_COORD_DISABLED_FILE": str(tmp_path / "disabled"),
            "HERMES_COORD_DISABLED": "0",
            "HERMES_COORD_CRON_MANIFEST": str(tmp_path / "cron.json"),
            "HERMES_COORD_CRON_JOBS": str(tmp_path / "jobs.json"),
            "HERMES_COORD_PROFILES_DIR": str(tmp_path / "profiles"),
        }
    )
    env.pop("HERMES_HOME", None)
    env.pop("HERMES_SESSION_ID", None)
    target = {
        "profile_home": str(profile),
        "kind": "cli",
        "session_id": "native-waiter",
    }
    run_cli(env, "register", "--id", "holder", "--task", "holder", "--surface", "cli")
    run_cli(
        env,
        "register",
        "--id",
        "waiter",
        "--task",
        "waiter",
        "--surface",
        "cli",
        "--native-session-id",
        "native-waiter",
        "--wake-transport",
        "hermes",
        "--wake-target-json",
        json.dumps(target),
    )
    run_cli(env, "claim", "--id", "holder", "--res", "receipt-resource")
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text("checkpoint\n", encoding="utf-8")
    run_cli(
        env,
        "claim",
        "--id",
        "waiter",
        "--res",
        "receipt-resource",
        "--yield",
        "--checkpoint",
        str(checkpoint),
        ok=False,
    )
    run_cli(env, "release", "--id", "holder", "--res", "receipt-resource")
    event = run_cli(
        env,
        "wake-lease",
        "--worker",
        "test",
        "--transport",
        "hermes",
        "--profile-home",
        str(profile),
        "--native-session-id",
        "native-waiter",
        "--kind",
        "cli",
    )["event"]
    return env, db, event


def proof(event, outcome):
    value = {
        "event_id": event["event_id"],
        "native_session_id": event["native_session_id"],
        "profile_home": event["profile_home"],
        "status": outcome,
        "payload_hash": "durable-native-readback",
        "payload_sha256": "durable-native-readback",
    }
    if outcome == "not_sent":
        value.update(not_sent=True, submitted=False)
    return value


def snapshot(db):
    with closing(sqlite3.connect(str(db))) as conn:
        return (
            conn.execute(
                "SELECT status,attempt,lease_owner,last_error FROM wake_outbox"
            ).fetchall(),
            conn.execute(
                "SELECT outcome,receipt_json,result_at FROM wake_attempts"
            ).fetchall(),
            conn.execute(
                "SELECT status,canceled_at,cancel_reason FROM wait_episodes"
            ).fetchall(),
        )


@pytest.mark.parametrize("prior", ["leased", "unknown"])
@pytest.mark.parametrize("outcome", ["not_sent", "canceled"])
@pytest.mark.parametrize(
    "defect",
    [
        "event_id",
        "native_session_id",
        "profile_home",
        "missing_status",
        "wrong_status",
        "missing_durability",
        "inconsistent_durability",
        "wrong_disposition",
    ],
)
def test_invalid_receipt_is_rejected_without_mutation(leased, prior, outcome, defect):
    env, db, event = leased
    if prior == "unknown":
        run_cli(
            env,
            "wake-result",
            "--event",
            event["event_id"],
            "--attempt",
            "1",
            "--outcome",
            "unknown",
            "--receipt-json",
            "{}",
        )
    receipt = proof(event, outcome)
    if defect in {"event_id", "native_session_id", "profile_home"}:
        receipt[defect] += "-wrong"
    elif defect == "missing_status":
        receipt.pop("status")
    elif defect == "wrong_status":
        receipt["status"] = "canceled" if outcome == "not_sent" else "not_sent"
    elif defect == "missing_durability":
        receipt.pop("payload_hash")
    elif defect == "inconsistent_durability":
        receipt["payload_hash"] = "wrong-hash"
    elif outcome == "not_sent":
        receipt["submitted"] = True
    else:
        receipt["not_sent"] = True
    before = snapshot(db)
    run_cli(
        env,
        "wake-result",
        "--event",
        event["event_id"],
        "--attempt",
        "1",
        "--outcome",
        outcome,
        "--receipt-json",
        json.dumps(receipt),
        ok=False,
    )
    assert snapshot(db) == before


@pytest.mark.parametrize("prior", ["leased", "unknown"])
@pytest.mark.parametrize(
    "outcome,expected", [("not_sent", "pending"), ("canceled", "canceled")]
)
def test_exact_durable_disposition_receipt_succeeds(leased, prior, outcome, expected):
    env, _db, event = leased
    if prior == "unknown":
        run_cli(
            env,
            "wake-result",
            "--event",
            event["event_id"],
            "--attempt",
            "1",
            "--outcome",
            "unknown",
            "--receipt-json",
            "{}",
        )
    result = run_cli(
        env,
        "wake-result",
        "--event",
        event["event_id"],
        "--attempt",
        "1",
        "--outcome",
        outcome,
        "--receipt-json",
        json.dumps(proof(event, outcome)),
    )
    assert result["status"] == expected
