"""Tracing's command line: the recorder, the split command and the probe, and the entry point's questions.

Nothing heavy loads at import: Process Monitor's parser, win32 and wx are imported inside the functions that need
them, so the elevated recorder never loads wx and a normal start pays nothing.
"""
import argparse
import shutil
import time
from pathlib import Path

from tracing import paths
from tracing.runfiles import RunDir, leftover_runs
from tracing.splitrun import split_run_dir

COMMANDS = ("--trace-recorder", "--trace-split", "--trace-probe")
DEFAULT_WORKLOAD = paths.REPO_DIR / "tests" / "catalog_fixtures" / "games" / "trace-test" / "trace-test.bat"

SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WAIT_TIMEOUT = 0x102
STILL_ACTIVE = 259


def tracing_wanted(argv: list[str], compiled: bool) -> bool:
    return compiled or "--tracing" in argv


def traces_dir_override(argv: list[str]) -> str | None:
    """The value of --traces-dir PATH (or --traces-dir=PATH), or None."""
    for i, arg in enumerate(argv):
        if arg == "--traces-dir":
            return argv[i + 1] if i + 1 < len(argv) else None
        if arg.startswith("--traces-dir="):
            return arg.split("=", 1)[1]
    return None


def attach_app(frame, compiled: bool, argv: list[str]):
    """Hook the tracing glue into the main window (wx; loaded only here)."""
    from tracing.app_glue import attach
    return attach(frame, compiled, argv)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="game-chooser", allow_abbrev=False, add_help=False)
    parser.add_argument("--trace-recorder", metavar="RUN")
    parser.add_argument("--app-pid", type=int)
    parser.add_argument("--procmon")
    parser.add_argument("--trace-split", nargs="*", metavar="RUN")
    parser.add_argument("--trace-probe", metavar="OUT")
    parser.add_argument("--workload")
    parser.add_argument("--traces-dir")
    return parser


def run_command(argv: list[str], compiled: bool) -> int | None:
    """Run the tracing command in argv and return its exit code; None when argv holds none."""
    if not any(arg.split("=", 1)[0] in COMMANDS for arg in argv):
        return None
    args, _unknown = _parser().parse_known_args(argv)
    if args.trace_recorder is not None:
        return _recorder(args, compiled)
    if args.trace_split is not None:
        return _split(args, compiled)
    return _run_probe(Path(args.trace_probe), paths.procmon_exe(compiled),
                      Path(args.workload) if args.workload else DEFAULT_WORKLOAD)


def _split(args, compiled: bool) -> int:
    from tracing.pml import read_capture
    traces_dir = paths.traces_dir(compiled, args.traces_dir)
    runs = [RunDir(Path(r)) for r in args.trace_split] or leftover_runs(traces_dir, _is_alive)
    if not runs:
        print("No runs to split.")
    failed = False
    for run in runs:
        try:
            result = split_run_dir(run, traces_dir, read_capture)
        except Exception as error:
            print(f"{run.path}: {error}")
            failed = True
            continue
        for folder in result.folders:
            print(f"Wrote {folder}")
        for problem in result.problems:
            print(f"Problem: {problem}")
            failed = True
    return 1 if failed else 0


def _run_probe(out_dir: Path, procmon: Path, workload: Path) -> int:
    from tracing.probe import run_probe
    return run_probe(out_dir, procmon, workload)


def _open_process(access: int, pid: int):
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    return kernel32.OpenProcess(access, False, pid)


def _is_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes
    handle = _open_process(PROCESS_QUERY_LIMITED_INFORMATION, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        kernel32 = ctypes.windll.kernel32
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == STILL_ACTIVE
    finally:
        _close(handle)


def _close(handle) -> None:
    import ctypes
    ctypes.windll.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    ctypes.windll.kernel32.CloseHandle(handle)


def _app_alive_check(pid: int):
    """Open the app's handle at once: a pid already gone is never alive; otherwise alive until it is signalled."""
    import ctypes
    handle = _open_process(SYNCHRONIZE, pid)
    if not handle:
        return lambda: False
    ctypes.windll.kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    return lambda: ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT


def _recorder(args, compiled: bool) -> int:
    from tracing.live import windows_snapshot
    from tracing.pml import read_capture
    from tracing.procmon import WindowsProcmon
    from tracing.recorder import Recorder
    if args.app_pid is None or not args.procmon:
        print("--trace-recorder needs --app-pid and --procmon.")
        return 2
    run = RunDir(Path(args.trace_recorder))
    traces_dir = run.path.parent.parent
    recorder = Recorder(
        run, _app_alive_check(args.app_pid), WindowsProcmon(Path(args.procmon)), windows_snapshot,
        lambda r: split_run_dir(r, traces_dir, read_capture), lambda: leftover_runs(traces_dir, _is_alive),
        lambda: shutil.disk_usage(run.path).free, time.monotonic, time.sleep)
    return recorder.run()
