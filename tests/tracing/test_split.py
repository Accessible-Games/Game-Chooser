from tracing.clock import TICKS_PER_SECOND
from tracing.model import ProcInfo, RawEvent, SessionInfo
from tracing.split import MAX_VALUE_CHARS, PROBLEM_NO_ROOT, split_run

S = TICKS_PER_SECOND


def proc(pid, ppid, start, end=None, image="C:\\Windows\\System32\\cmd.exe", virtualized=False):
    return ProcInfo(pid, ppid, start, end, image, image + " /c", virtualized, True)


def info(root_pid=100, before=int(9.9 * S), after=int(10.1 * S), marks=(), root_start=None):
    return SessionInfo("s", root_pid, root_start, before, after,
                       {"title": "T", "catalog_id": "c", "library_id": "l", "launch_path": "a.exe",
                        "exe": "g\\a.exe", "game_dir": "g"},
                       {"windows": "10", "local_appdata": "C:\\Users\\u\\AppData\\Local", "user_profile": "U",
                        "app_exe": "a.exe", "app_exe_mtime": 1, "compiled": False}, marks)


def ev(p, seconds, op="WriteFile", path="C:\\g\\a.sav", result="SUCCESS", **details):
    return RawEvent(int(seconds * S), p.pid, p.start, op, path, result, details)


ROOT = proc(100, 50, 10 * S)
CHILD = proc(200, 100, 11 * S, image="C:\\Windows\\System32\\reg.exe")
CHOOSER = proc(50, 1, 1 * S, image="C:\\app\\game-chooser.exe")
SEVENZ = proc(60, 50, 5 * S, image="C:\\app\\bin\\7z.exe")
PROCS = [CHOOSER, SEVENZ, ROOT, CHILD]


def run(events, sessions=None, procs=PROCS):
    return split_run(procs, iter(events), sessions or [info()])


def ops(result):
    return [line["op"] for line in result.lines]


def test_events_are_attributed_to_the_game_tree_only():
    [r] = run([ev(ROOT, 12), ev(CHILD, 12), ev(CHOOSER, 12), ev(SEVENZ, 12)])
    writes = [x for x in r.lines if x["op"] == "file_write"]
    assert sorted(x["pid"] for x in writes) == [100, 200]


def test_time_since_launch_uses_the_root_start():
    [r] = run([ev(ROOT, 12.5)])
    assert r.time_zero == 10 * S and r.time_zero_source == "process_start"
    assert [x["t"] for x in r.lines if x["op"] == "file_write"] == [2.5]


def test_rename_carries_both_names():
    [r] = run([ev(ROOT, 12, op="SetRenameInformationFile", path="C:\\g\\s.tmp", FileName="C:\\g\\s.sav")])
    line = next(x for x in r.lines if x["op"] == "rename")
    assert line["path"] == "C:\\g\\s.tmp" and line["new_path"] == "C:\\g\\s.sav"
    [r] = run([ev(ROOT, 12, op="SetRenameInformationFile", path="C:\\g\\s.tmp")])
    assert next(x for x in r.lines if x["op"] == "rename")["new_path"] is None


def test_registry_value_line():
    [r] = run([ev(ROOT, 12, op="RegSetValue", path="HKCU\\Software\\A\\v", Type="REG_SZ", Data="3")])
    line = next(x for x in r.lines if x["op"] == "reg_set_value")
    assert line["value_type"] == "REG_SZ" and line["value_data"] == "3"


def test_registry_value_data_conversions():
    big = "x" * 1000
    [r] = run([ev(ROOT, 12, op="RegSetValue", path="HKCU\\a", Type="REG_BINARY", Data=b"\x01\xff"),
               ev(ROOT, 13, op="RegSetValue", path="HKCU\\b", Type="REG_MULTI_SZ", Data=["a", "b"]),
               ev(ROOT, 14, op="RegSetValue", path="HKCU\\c", Type="REG_SZ", Data=big)])
    data = [x["value_data"] for x in r.lines if x["op"] == "reg_set_value"]
    assert data == ["01ff", "a\nb", "x" * MAX_VALUE_CHARS]


def test_marks_sit_among_writes_in_time_order():
    [r] = run([ev(ROOT, 11), ev(ROOT, 13)], [info(marks=(12 * S,))])
    assert ops(r) == ["process_start", "process_start", "file_write", "mark", "file_write"]
    mark = next(x for x in r.lines if x["op"] == "mark")
    assert mark["n"] == 1 and mark["t"] == 2.0


def test_consecutive_writes_merge():
    events = [ev(ROOT, 12, Length=10), ev(ROOT, 12.1, Length=10), ev(ROOT, 12.2, Length=10),
              ev(ROOT, 12.3, path="C:\\g\\b.sav", Length=5), ev(ROOT, 12.4, Length=10)]
    [r] = run(events)
    writes = [x for x in r.lines if x["op"] == "file_write"]
    assert len(writes) == 3
    first = writes[0]
    assert first["count"] == 3 and first["bytes"] == 30 and first["t"] == 2.0 and first["t_end"] == 2.2
    assert writes[1]["count"] == 1 and writes[1]["bytes"] == 5
    assert writes[2]["count"] == 1


def test_overlapping_sessions_get_their_own_events():
    root2 = proc(300, 50, 11 * S)
    procs = PROCS + [root2]
    s1 = info(marks=(12 * S,))
    s2 = info(root_pid=300, before=int(10.9 * S), after=int(11.1 * S), marks=(12 * S,))
    r1, r2 = run([ev(ROOT, 12, path="C:\\g\\one"), ev(root2, 12, path="C:\\g\\two")], [s1, s2], procs)
    assert [x["path"] for x in r1.lines if x["op"] == "file_write"] == ["C:\\g\\one"]
    assert [x["path"] for x in r2.lines if x["op"] == "file_write"] == ["C:\\g\\two"]
    assert "mark" in ops(r1) and "mark" in ops(r2)


def test_session_end_and_still_running():
    root = proc(100, 50, 10 * S, end=20 * S)
    child = proc(200, 100, 11 * S, end=30 * S)
    [r] = run([], procs=[root, child])
    assert r.end == 30 * S and r.still_running is False
    exits = [x for x in r.lines if x["op"] == "process_exit"]
    assert sorted(x["pid"] for x in exits) == [100, 200]
    [r] = run([], procs=[root, proc(200, 100, 11 * S)])
    assert r.end is None and r.still_running is True
    # a process that exited has its exit line even while another one of the session is still running
    assert [x["pid"] for x in r.lines if x["op"] == "process_exit"] == [100]


def test_process_start_lines():
    [r] = run([])
    line = next(x for x in r.lines if x["op"] == "process_start" and x["pid"] == 200)
    assert line == {"t": 1.0, "op": "process_start", "pid": 200, "parent_pid": 100, "process": "reg.exe",
                    "image_path": CHILD.image_path, "command_line": CHILD.command_line}


def test_missing_root_writes_problem():
    [r] = run([ev(ROOT, 12)], [info(root_pid=999)])
    assert r.problem == PROBLEM_NO_ROOT and r.lines == [] and r.root is None
    assert r.time_zero == info().launch_before and r.time_zero_source == "launch_call"


def test_virtualized_process_gets_virtual_store_path():
    root = proc(100, 50, 10 * S, virtualized=True)
    [r] = run([ev(root, 12, op="RegSetValue", path="HKLM\\SOFTWARE\\Acme\\x", Type="REG_SZ", Data="1")],
              procs=[root])
    line = next(x for x in r.lines if x["op"] == "reg_set_value")
    assert line["virtual_store_path"] == "HKCU\\Software\\Classes\\VirtualStore\\MACHINE\\SOFTWARE\\Acme\\x"
    [r] = run([ev(ROOT, 12, op="RegSetValue", path="HKLM\\SOFTWARE\\Acme\\x", Type="REG_SZ", Data="1")])
    assert "virtual_store_path" not in next(x for x in r.lines if x["op"] == "reg_set_value")


def test_a_session_found_by_exe_never_takes_another_sessions_root():
    exe = "C:\\Games\\X\\game.exe"
    game = {"title": "T", "catalog_id": "c", "library_id": "l", "launch_path": "game.exe", "exe": exe,
            "game_dir": "C:\\Games\\X"}
    # B launched first through a slow administrator prompt (no pid; its root starts at 15 s); A launched at 12 s
    b = SessionInfo("s001-0", 0, None, 11 * S, 15 * S, game, info().env, ())
    a = SessionInfo("s002-500", 500, 12 * S, int(11.9 * S), int(12.1 * S), game, info().env, ())
    a_root = proc(500, 50, 12 * S, image=exe)
    b_root = proc(600, 50, 15 * S, image=exe)
    a_child = proc(501, 500, 13 * S, image="C:\\Windows\\System32\\reg.exe")
    rb, ra = split_run([CHOOSER, a_root, b_root, a_child],
                       iter([ev(a_root, 14), ev(b_root, 16, path="C:\\g\\b.sav")]), [b, a])
    assert ra.root is a_root and rb.root is b_root
    assert set(ra.tree) == {a_root.key, a_child.key} and set(rb.tree) == {b_root.key}
    assert [line["path"] for line in rb.lines if line["op"] == "file_write"] == ["C:\\g\\b.sav"]
