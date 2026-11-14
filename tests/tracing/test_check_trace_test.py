from pathlib import Path

from tests.tracing.test_output import make_result
from tracing.check_trace_test import EXPECTED, check
from tracing.output import write_session

ROOT_PID = 100
CHILD_PID = 200
PROFILE = "C:\\Users\\p"
GAME = "C:\\games\\trace-test"


def _path(suffix: str) -> str:
    if suffix.startswith("\\Software"):
        return "HKCU" + suffix
    if suffix.startswith("\\saves"):
        return GAME + suffix
    return PROFILE + suffix


def good_lines() -> list[dict]:
    lines = [{"t": 0.0, "op": "process_start", "process": "cmd.exe", "pid": ROOT_PID}]
    for n, (op, suffix, child) in enumerate(EXPECTED, 1):
        lines.append({"t": float(n), "op": op.split("|")[0], "process": "reg.exe" if child else "cmd.exe",
                      "pid": CHILD_PID if child else ROOT_PID, "path": _path(suffix), "result": "SUCCESS"})
        if op == "rename":
            lines[-1]["path"] = GAME + "\\saves\\slot1.tmp"
            lines[-1]["new_path"] = _path(suffix)
    return lines


def session(tmp_path: Path, lines: list[dict], problem=None) -> Path:
    return write_session(make_result(lines=lines, problem=problem), tmp_path)


def test_a_complete_session_passes(tmp_path):
    assert check(session(tmp_path, good_lines())) == []


def test_missing_or_out_of_order_line_fails(tmp_path):
    lines = good_lines()
    i = next(n for n, line in enumerate(lines) if line["op"] == "rename")
    j = next(n for n, line in enumerate(lines) if line["op"] == "delete")
    lines[i], lines[j] = lines[j], lines[i]
    problems = check(session(tmp_path, lines))
    assert len(problems) == 1 and "slot1.sav" in problems[0]
    gone = [line for line in good_lines() if line["op"] != "delete"]
    problems = check(session(tmp_path / "b", gone))
    assert any("junk.txt" in p for p in problems)


def test_lines_from_game_chooser_or_7z_fail(tmp_path):
    for name in ("7z.exe", "game-chooser.exe", "python.exe"):
        lines = good_lines()
        lines.append({"t": 20.0, "op": "file_write", "process": name, "pid": 300, "path": "C:\\x",
                      "result": "SUCCESS"})
        problems = check(session(tmp_path / name, lines))
        assert any(name in p for p in problems)


def test_child_rule(tmp_path):
    lines = good_lines()
    for line in lines:
        if line["op"] == "reg_set_value":
            line["pid"] = ROOT_PID
    problems = check(session(tmp_path, lines))
    assert problems and any("Level" in p for p in problems)


def test_negative_time_write_and_session_problem_fail(tmp_path):
    lines = good_lines()
    lines[1]["t"] = -0.5
    assert any("before" in p for p in check(session(tmp_path / "a", lines)))
    problems = check(session(tmp_path / "b", good_lines(), problem="no root"))
    assert any("no root" in p for p in problems)
