import subprocess
import sys
from pathlib import Path

from tracing import paths, probe
from tracing.model import ProcInfo, RawEvent, SessionInfo

REPO = Path(__file__).resolve().parent.parent.parent


def _proc(pid, parent, start, image):
    return ProcInfo(pid, parent, start, None, image, image, False, True)


def _event(proc, operation, path, details=None):
    return RawEvent(proc.start + 1, proc.pid, proc.start, operation, path, "SUCCESS", details or {})


def test_summarize_keeps_the_workload_tree_and_counts_everything():
    explorer = _proc(100, 4, 10, "explorer.exe")
    cmd = _proc(200, 100, 5000, "cmd.exe")
    child = _proc(300, 200, 6000, "reg.exe")
    other = _proc(400, 100, 5500, "other.exe")
    events = [
        _event(cmd, "Process Start", ""),
        _event(cmd, "WriteFile", r"C:\Games\TraceTest\saves\slot1.sav", {"_raw": {"io": "00"}}),
        _event(child, "RegSetValue", r"HKCU\Software\TraceTest\Volume"),
        _event(other, "WriteFile", r"C:\elsewhere.txt"),
    ]
    session = SessionInfo("probe", 200, None, 4990, 5010, {}, {}, ())
    report = probe.summarize([explorer, cmd, child, other], iter(events), session)
    assert report["op_counts"] == {"Process Start": 1, "WriteFile": 2, "RegSetValue": 1}
    assert {p["pid"] for p in report["processes"]} == {200, 300}
    assert report["events"] == [
        {"operation": "Process Start", "path": "", "result": "SUCCESS", "details": {}},
        {"operation": "WriteFile", "path": r"C:\Games\TraceTest\saves\slot1.sav", "result": "SUCCESS",
         "details": {"_raw": {"io": "00"}}},
        {"operation": "RegSetValue", "path": r"HKCU\Software\TraceTest\Volume", "result": "SUCCESS",
         "details": {}},
    ]


def test_summarize_without_the_workload_root():
    explorer = _proc(100, 4, 10, "explorer.exe")
    session = SessionInfo("probe", 999, None, 4990, 5010, {}, {}, ())
    report = probe.summarize([explorer], iter([_event(explorer, "WriteFile", "x")]), session)
    assert report["op_counts"] == {"WriteFile": 1}
    assert report["processes"] == [] and report["events"] == []
    assert report["root_start_pml"] is None


def test_summarize_reports_the_pml_root_start_found_by_window():
    cmd = _proc(200, 100, 5000, "cmd.exe")
    session = SessionInfo("probe", 200, 5003, 4990, 5010, {}, {}, ())  # the live start is a few ticks off
    report = probe.summarize([cmd], iter([]), session)
    assert report["root_start_pml"] == 5000 and [p["pid"] for p in report["processes"]] == [200]


def test_root_start_report():
    assert probe.root_start_report(5003, 5000) == {
        "root_start_live": 5003, "root_start_pml": 5000, "root_start_delta_ticks": -3}
    assert probe.root_start_report(None, 5000)["root_start_delta_ticks"] is None
    assert probe.root_start_report(5003, None)["root_start_delta_ticks"] is None


def test_relaunch_commands(tmp_path):
    out = tmp_path / "probe"
    bat = tmp_path / "game.bat"
    exe, args, cwd = probe.module_relaunch(out, bat, tmp_path / "Procmon64.exe")
    assert exe == sys.executable
    assert args == ["-m", "tracing.probe", str(out), str(bat), "--procmon", str(tmp_path / "Procmon64.exe")]
    assert cwd == REPO

    exe, args, cwd = probe.cli_relaunch(False, out, bat)
    lead_exe, lead_args = paths.self_command(False)
    assert exe == lead_exe
    assert args == lead_args + ["--trace-probe", str(out), "--workload", str(bat)]
    assert cwd == paths.app_dir(False)


def test_tracing_modules_import_nothing_heavy():
    code = ("import sys, tracing.paths, tracing.procmon, tracing.pml, tracing.probe\n"
            "heavy = [m for m in ('procmon_parser', 'win32job', 'win32api', 'wx') if m in sys.modules]\n"
            "print(heavy)\n"
            "sys.exit(1 if heavy else 0)\n")
    result = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_missing_procmon_or_workload_stops_before_any_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "_is_admin", lambda: (_ for _ in ()).throw(AssertionError("asked for admin")))
    bat = tmp_path / "game.bat"
    bat.write_text("@echo off\n")
    assert probe.run_probe(tmp_path / "out", tmp_path / "missing.exe", bat) == 1
    assert probe.run_probe(tmp_path / "out", bat, tmp_path / "missing.bat") == 1
    assert not (tmp_path / "out").exists()
