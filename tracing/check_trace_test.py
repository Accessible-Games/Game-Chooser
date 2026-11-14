"""Checks a session folder made by the trace test game (tests/catalog_fixtures/games/trace-test)."""
import json
from pathlib import Path

from tracing.kinds import WRITE_KINDS

# (op alternatives joined by "|", path suffix, must come from a child process of the game)
EXPECTED = [
    ("file_create|file_write", r"\saves\settings.ini", False),
    ("file_create|file_write", r"\AppData\Roaming\GameChooserTraceTest\roaming.txt", False),
    ("file_create|file_write", r"\AppData\Local\GameChooserTraceTest\local.txt", False),
    ("file_create|file_write", r"\Documents\GameChooserTraceTest\documents.txt", False),
    ("reg_set_value", r"\Software\GameChooserTraceTest\Level", True),
    ("rename", r"\saves\slot1.sav", False),
    ("delete", r"\saves\junk.txt", False),
    ("file_create|file_write", r"\AppData\Local\GameChooserTraceTest\child.txt", True),
    ("reg_delete_value", r"\Software\GameChooserTraceTest\Temp", True),
]

FORBIDDEN_PROCESSES = ("7z.exe", "game-chooser.exe", "python.exe")


def _describe(expected: tuple) -> str:
    op, suffix, child = expected
    return f"{op} {suffix}" + (" (by a child process)" if child else "")


def _matches(line: dict, expected: tuple, root_pid) -> bool:
    op, suffix, child = expected
    if line.get("op") not in op.split("|"):
        return False
    if child and line.get("pid") == root_pid:
        return False
    target = (line.get("new_path") if line.get("op") == "rename" else line.get("path")) or ""
    return target.lower().endswith(suffix.lower())


def check(session_dir: Path) -> list[str]:
    session_dir = Path(session_dir)
    session = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    with open(session_dir / "events.jsonl", encoding="utf-8") as events:
        lines = [json.loads(text) for text in events if text.strip()]
    problems: list[str] = []
    if session.get("problem"):
        problems.append(f"The session reports a problem: {session['problem']}")
    root_pid = session.get("root_pid")

    previous_index, previous = -1, None
    for expected in EXPECTED:
        index = next((n for n, line in enumerate(lines) if _matches(line, expected, root_pid)), None)
        if index is None:
            problems.append(f"Missing: {_describe(expected)}")
            continue
        if index < previous_index:
            problems.append(f"Out of order: {_describe(expected)} came before {_describe(previous)}")
            continue
        previous_index, previous = index, expected

    for line in lines:
        name = str(line.get("process", "")).lower()
        if name in FORBIDDEN_PROCESSES:
            problems.append(f"A line from {name} was attributed to the game: {line.get('op')} {line.get('path')}")
        if line.get("op") in WRITE_KINDS and line.get("t", 0) < 0:
            problems.append(f"A write came before the game started (t={line['t']}): {line.get('op')} "
                            f"{line.get('path')}")
    return problems


def report(session_dir: Path) -> int:
    """Print PASS or one problem per line. Exit code: 0 pass, 1 problems, 2 not a session folder."""
    session_dir = Path(session_dir)
    if not (session_dir / "session.json").is_file():
        print(f"{session_dir} is not a session folder (no session.json).")
        return 2
    problems = check(session_dir)
    print("\n".join(problems) if problems else "PASS")
    return 1 if problems else 0
