import json

from tracing.clock import TICKS_PER_SECOND, filetime_to_iso, filetime_to_local_stamp
from tracing.model import ProcInfo, SessionInfo
from tracing.output import KNOWN_LIMITS, safe_name, session_folder_name, write_session
from tracing.split import SessionResult

S = TICKS_PER_SECOND
T0 = 133_000_000_000_000_000


def make_result(lines=None, catalog_id="alien", title="Alien Outback: Deluxe!", problem=None):
    info = SessionInfo("sid", 100, None, T0 - S, T0 + S,
                       {"title": title, "catalog_id": catalog_id, "library_id": "l", "launch_path": "a.exe",
                        "exe": "g\\a.exe", "game_dir": "g"},
                       {"windows": "10", "local_appdata": "L", "user_profile": "U", "app_exe": "a.exe",
                        "app_exe_mtime": 1, "compiled": False}, (T0 + 2 * S,))
    root = ProcInfo(100, 50, T0, T0 + 5 * S, "C:\\g\\a.exe", "a.exe", True, True)
    child = ProcInfo(200, 100, T0 + S, None, "C:\\g\\b.exe", "b.exe", False, False)
    tree = {root.key: root, child.key: child}
    if lines is None:
        lines = [{"t": 0.0, "op": "process_start", "pid": 100}, {"t": 1.0, "op": "file_write", "pid": 100},
                 {"t": 2.0, "op": "mark", "n": 1}, {"t": 5.0, "op": "process_exit", "pid": 100}]
    return SessionResult(info, root, T0, "process_start", tree, lines, None, True, problem)


def test_safe_name():
    assert safe_name("Alien Outback: Deluxe!") == "Alien-Outback-Deluxe"
    assert safe_name("???") == "game"
    assert safe_name("..a.b..") == "a.b"
    assert len(safe_name("x" * 100)) == 60


def test_folder_name_uses_catalog_id_or_title_and_never_collides(tmp_path):
    result = make_result()
    stamp = filetime_to_local_stamp(result.info.launch_before)
    assert session_folder_name(result.info) == f"alien-{stamp}"
    first = write_session(result, tmp_path)
    second = write_session(result, tmp_path)
    third = write_session(result, tmp_path)
    assert first.name == f"alien-{stamp}" and second.name == f"alien-{stamp}-2"
    assert third.name == f"alien-{stamp}-3"
    untitled = make_result(catalog_id="")
    assert session_folder_name(untitled.info).startswith("Alien-Outback-Deluxe-")


def test_folder_name_sanitizes_the_catalog_id(tmp_path):
    stamp = filetime_to_local_stamp(make_result().info.launch_before)
    assert session_folder_name(make_result(catalog_id=r"..\..\x").info) == f"x-{stamp}"
    assert session_folder_name(make_result(catalog_id="a/b").info) == f"a-b-{stamp}"
    folder = write_session(make_result(catalog_id=r"..\..\x"), tmp_path)
    assert folder.parent == tmp_path


def test_session_json_fields(tmp_path):
    folder = write_session(make_result(problem="p"), tmp_path)
    data = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    assert data["format"] == 1 and data["session_id"] == "sid"
    assert data["game"]["title"] == "Alien Outback: Deluxe!" and data["env"]["windows"] == "10"
    assert data["start"] == filetime_to_iso(T0) and data["end"] is None
    assert data["still_running_at_capture_end"] is True and data["time_zero_source"] == "process_start"
    assert data["root_pid"] == 100 and data["problem"] == "p"
    assert data["event_count"] == 1 and data["known_limits"] == KNOWN_LIMITS
    assert len(KNOWN_LIMITS) == 2
    assert data["marks"] == [{"n": 1, "t": 2.0, "time": filetime_to_iso(T0 + 2 * S)}]
    root, child = data["processes"]
    assert root == {"pid": 100, "parent_pid": 50, "image_path": "C:\\g\\a.exe", "command_line": "a.exe",
                    "start": filetime_to_iso(T0), "end": filetime_to_iso(T0 + 5 * S), "start_t": 0.0,
                    "end_t": 5.0, "virtualized": True, "is_64bit": True}
    assert child["end"] is None and child["end_t"] is None and child["start_t"] == 1.0


def test_session_json_end_and_no_root(tmp_path):
    result = make_result()
    result.end = T0 + 5 * S
    result.still_running = False
    data = json.loads((write_session(result, tmp_path) / "session.json").read_text(encoding="utf-8"))
    assert data["end"] == filetime_to_iso(T0 + 5 * S)
    missing = make_result(lines=[])
    missing.root, missing.tree = None, {}
    data = json.loads((write_session(missing, tmp_path) / "session.json").read_text(encoding="utf-8"))
    assert data["root_pid"] == 100 and data["processes"] == []


def test_unicode_paths_and_binary_values_round_trip(tmp_path):
    path = "C:\\Users\\Zo\u00eb\\Documents\\Spiel\\save.dat"
    value = b"\x00\xff".hex()
    lines = [{"t": 1.0, "op": "reg_set_value", "pid": 100, "path": path, "value_data": value}]
    folder = write_session(make_result(lines=lines), tmp_path)
    raw = (folder / "events.jsonl").read_bytes()
    assert "\u00eb".encode("utf-8") in raw and b"\\u00eb" not in raw
    [line] = [json.loads(x) for x in raw.decode("utf-8").splitlines()]
    assert line["path"] == path and line["value_data"] == "00ff"


def test_lone_surrogate_path_round_trips_and_other_lines_stay_raw(tmp_path):
    bad = "C:\\Spiel\\\ud800.dat"
    good = "C:\\Users\\Zo\u00eb\\save.dat"
    lines = [{"t": 1.0, "op": "file_write", "pid": 100, "path": bad},
             {"t": 2.0, "op": "file_write", "pid": 100, "path": good}]
    result = make_result(lines=lines)
    result.info.game["title"] = "Zo\u00eb \ud800"
    folder = write_session(result, tmp_path)
    first, second = (folder / "events.jsonl").read_bytes().decode("utf-8").splitlines()
    assert json.loads(first)["path"] == bad and "\\ud800" in first
    assert json.loads(second)["path"] == good and "\u00eb" in second
    data = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    assert data["game"]["title"] == "Zo\u00eb \ud800"


def test_session_json_says_how_the_session_ended(tmp_path):
    data = json.loads((write_session(make_result(), tmp_path) / "session.json").read_text(encoding="utf-8"))
    assert data["ended_by"] is None and data["stop"] is None
    result = make_result()
    result.ended_by = "capture_end"
    data = json.loads((write_session(result, tmp_path) / "session.json").read_text(encoding="utf-8"))
    assert data["ended_by"] == "capture_end" and data["stop"] is None


def test_session_json_stop_fields(tmp_path):
    result = make_result()
    result.ended_by = "stop"
    result.stop = {"time": T0 + 30 * S, "close_games": True, "asked_to_close": [100], "ended": [200],
                   "not_closed": []}
    data = json.loads((write_session(result, tmp_path) / "session.json").read_text(encoding="utf-8"))
    assert data["ended_by"] == "stop"
    assert data["stop"] == {"time": filetime_to_iso(T0 + 30 * S), "t": 30.0, "close_games": True,
                            "asked_to_close": [100], "ended": [200], "not_closed": []}
    assert data["format"] == 1
