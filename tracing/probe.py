"""The Process Monitor probe: one short capture of a known workload, to learn what the real PML holds.

It records Process Monitor's version, the capture's size and rate, the operations that reached the file (to show
the destructive filter worked) and every event of the workload's process tree with the raw detail bytes of
renames and SetDispositionInformationEx. Then it tries Process Monitor's own CSV export of the renames.
Everything goes into OUT_DIR/probe.json; capture.pml is never deleted.

Needs administrator rights: unelevated, it starts itself again elevated (a UAC prompt) and waits.

    python -m tracing.probe OUT_DIR WORKLOAD_BAT [--procmon PROCMON64_EXE]
"""
import argparse
import json
import subprocess
import sys
import time
import traceback
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from tracing import paths
from tracing.attribution import find_root, session_tree
from tracing.clock import now_filetime
from tracing.live import process_start_time
from tracing.model import ProcInfo, RawEvent, SessionInfo
from tracing.pml import capture_raw_details, read_capture
from tracing.procmon import WindowsProcmon, write_filter_config
from tracing.runfiles import RunDir

RAW_OPERATIONS = ("SetRenameInformationFile", "SetRenameInformationEx", "SetRenameInformationExBypassAccessCheck",
                  "SetDispositionInformationEx")
READY_TIMEOUT = 60
IDLE_SECONDS = 30
AFTER_WORKLOAD_SECONDS = 5
STOP_TIMEOUT = 300
EXPORT_TIMEOUT = 300

Relaunch = tuple[str, list[str], Path | None]  # exe, args, working folder


def module_relaunch(out_dir: Path, workload: Path, procmon: Path) -> Relaunch:
    """This module run from source: python -m tracing.probe, from the repo root."""
    return (sys.executable, ["-m", "tracing.probe", str(out_dir), str(workload), "--procmon", str(procmon)],
            paths.REPO_DIR)


def cli_relaunch(compiled: bool, out_dir: Path, workload: Path) -> Relaunch:
    """This program's own --trace-probe command."""
    exe, leading = paths.self_command(compiled)
    return exe, leading + ["--trace-probe", str(out_dir), "--workload", str(workload)], paths.app_dir(compiled)


def capture_files(out_dir: Path) -> list[Path]:
    """capture.pml, then the files Process Monitor rolled over to (capture-1.pml, ...), in order."""
    return RunDir(out_dir).capture_files()


def summarize(processes: list[ProcInfo], events: Iterator[RawEvent], session: SessionInfo) -> dict:
    """Operation counts over the whole capture, and the processes and events of the workload's tree."""
    root = find_root(processes, session)
    tree = session_tree(processes, root) if root is not None else {}
    counts: Counter = Counter()
    kept = []
    for event in events:
        counts[event.operation] += 1
        if event.proc_key in tree:
            kept.append({"operation": event.operation, "path": event.path, "result": event.result,
                         "details": event.details})
    return {"op_counts": dict(counts), "processes": [_proc_json(p) for p in tree.values()], "events": kept,
            "root_start_pml": root.start if root is not None else None}


def root_start_report(live: int | None, pml: int | None) -> dict:
    """The root's start read live right after Popen, the start in the PML, and pml - live in ticks."""
    delta = pml - live if live is not None and pml is not None else None
    return {"root_start_live": live, "root_start_pml": pml, "root_start_delta_ticks": delta}


def _proc_json(proc: ProcInfo) -> dict:
    return {"pid": proc.pid, "parent_pid": proc.parent_pid, "start": proc.start, "end": proc.end,
            "image_path": proc.image_path, "command_line": proc.command_line, "virtualized": proc.virtualized,
            "is_64bit": proc.is_64bit}


def run_probe(out_dir: Path, procmon: Path, workload: Path, relaunch: Relaunch | None = None) -> int:
    """Run the probe; unelevated, start `relaunch` elevated instead (default: --trace-probe) and wait for it."""
    out_dir = Path(out_dir).resolve()
    workload = Path(workload).resolve()
    for needed in (Path(procmon), workload):
        if not needed.exists():
            print(f"Not found: {needed}. (Process Monitor comes from scripts/fetch_procmon.py.)")
            return 1
    if _is_admin():
        return _probe_elevated(out_dir, Path(procmon).resolve(), workload)
    if relaunch is None:
        relaunch = cli_relaunch(paths.is_compiled(vars(sys.modules["__main__"])), out_dir, workload)
    return _relaunch_elevated(relaunch, out_dir)


def _is_admin() -> bool:
    import ctypes
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def _relaunch_elevated(relaunch: Relaunch, out_dir: Path) -> int:
    from engine.elevation import WindowsElevator
    from engine.errors import ElevationDeclined
    exe, args, cwd = relaunch
    try:
        process = WindowsElevator().start(exe, subprocess.list2cmdline(args), elevated=True, cwd=cwd, show=True)
    except ElevationDeclined:
        print("The probe needs administrator rights; the prompt was declined.")
        return 1
    try:
        code = process.wait(None)
    finally:
        process.close()
    print(f"The elevated probe finished with exit code {code}. Results: {out_dir}")
    return code


def _probe_elevated(out_dir: Path, procmon: Path, workload: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    control = WindowsProcmon(procmon)
    try:
        report = _capture_and_read(out_dir, procmon, workload, control)
        report["csv_export"] = _export_renames(out_dir, procmon)
        with open(out_dir / "probe.json", "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
        return 0
    except Exception:
        (out_dir / "probe-error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        return 1
    finally:
        if control.running():
            control.stop(60)


def _capture_and_read(out_dir: Path, procmon: Path, workload: Path, control: WindowsProcmon) -> dict:
    config = out_dir / "filter.pmc"
    capture = out_dir / "capture.pml"
    write_filter_config(config)
    control.start(capture, config)
    started = time.monotonic()
    if not control.wait_ready(READY_TIMEOUT):
        raise RuntimeError(f"Process Monitor was not ready within {READY_TIMEOUT} s: "
                           f"{getattr(control, 'not_ready_reason', None)}")
    time.sleep(IDLE_SECONDS)
    before = now_filetime()
    process = subprocess.Popen([str(workload)], cwd=workload.parent)
    after = now_filetime()
    root_start = process_start_time(process.pid)  # as the client reads it after a game's Popen
    workload_exit = process.wait()
    time.sleep(AFTER_WORKLOAD_SECONDS)
    stopped = control.stop(STOP_TIMEOUT)
    seconds = time.monotonic() - started
    if not stopped:
        raise RuntimeError(f"Process Monitor did not stop within {STOP_TIMEOUT} s")

    capture_raw_details(RAW_OPERATIONS)
    files = capture_files(out_dir)
    processes, events = read_capture(files)
    session = SessionInfo("probe", process.pid, root_start, before, after, {"workload": str(workload)}, {}, ())
    summary = summarize(processes, events, session)
    size = sum(p.stat().st_size for p in files)
    return {
        "procmon_version": _file_version(procmon),
        "capture_files": [p.name for p in files],
        "capture_bytes": size,
        "capture_seconds": seconds,
        "bytes_per_minute": size / seconds * 60 if seconds > 0 else None,
        "workload": {"path": str(workload), "pid": process.pid, "exit_code": workload_exit,
                     "launch_before": before, "launch_after": after},
        **summary,
        **root_start_report(root_start, summary["root_start_pml"]),
    }


def _export_renames(out_dir: Path, procmon: Path) -> dict:
    """Fallback-B evidence: Process Monitor's own CSV of the rename and disposition events."""
    config = out_dir / "renames.pmc"
    csv = out_dir / "renames.csv"
    write_filter_config(config, RAW_OPERATIONS, destructive=False)
    command = [str(procmon), "/AcceptEula", "/Quiet", "/LoadConfig", str(config),
               "/OpenLog", str(out_dir / "capture.pml"), "/SaveApplyFilter", "/SaveAs", str(csv)]
    try:
        code = subprocess.run(command, timeout=EXPORT_TIMEOUT).returncode
        timed_out = False
    except subprocess.TimeoutExpired:
        code, timed_out = None, True
    return {"command": command, "exit_code": code, "timed_out": timed_out, "csv_exists": csv.exists()}


def _file_version(exe: Path) -> str:
    import win32api
    info = win32api.GetFileVersionInfo(str(exe), "\\")
    ms, ls = info["FileVersionMS"], info["FileVersionLS"]
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m tracing.probe", description=__doc__.splitlines()[0])
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("workload", type=Path)
    parser.add_argument("--procmon", type=Path, default=paths.procmon_exe(False))
    args = parser.parse_args(argv)
    out_dir, workload, procmon = args.out_dir.resolve(), args.workload.resolve(), args.procmon.resolve()
    return run_probe(out_dir, procmon, workload, module_relaunch(out_dir, workload, procmon))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
