"""A malformed subprocess result must fail explicitly, even under python -O."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace


SCRIPT = Path(__file__).resolve().parents[1] / "skills/multi-session-coordination/scripts/coord_resume_watchdog.py"


def test_missing_process_result_is_a_reported_failure(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("watchdog_result_probe", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    board = tmp_path / "board.py"
    board.write_text("pass\n")
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: None)
    args = SimpleNamespace(board_script=board, lock_file=tmp_path / "watch.lock", timeout=1, json=False)
    assert module._run(args) == 1
    assert "no process result" in capsys.readouterr().err
