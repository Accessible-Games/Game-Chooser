"""Tracing's command line: the recorder, the split command and the probe, and the entry point's questions.

Nothing heavy loads at import: Process Monitor's parser, win32 and wx are imported inside the functions that need
them, so the elevated recorder never loads wx and a normal start pays nothing.
"""
import argparse
import shutil
import time
from pathlib import Path

from tracing import messages, paths
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


class _ArgError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _ArgError(message)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="game-chooser", allow_abbrev=False, add_help=False)
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
    try:
        args, _unknown = _parser().parse_known_args(argv)
    except _ArgError as error:
        return _recorder_failed_early(argv, error) if "--trace-recorder" in argv else _usage(error)
    if args.trace_recorder is not None:
        return _recorder(args, compiled)
    if args.trace_split is not None:
        return _split(args, compiled)
    return _run_probe(Path(args.trace_probe), paths.procmon_exe(compiled),
                      Path(args.workload) if args.workload else DEFAULT_WORKLOAD)


def _usage(error) -> int:
    print(f"Bad command line: {error}")
    return 2


def _recorder_failed_early(argv: list[str], error) -> int:
    """The recorder's arguments did not parse: record the failure in the run folder if its path is readable."""
    index = argv.index("--trace-recorder")
    if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
        return _usage(error)
    return _record_early_failure(Path(argv[index + 1]), error)


def _record_early_failure(run_path: Path, error) -> int:
    """Best effort, never raises: status.json says failed and recorder.log holds the traceback."""
    import traceback
    run = RunDir(run_path)
    text = "".join(traceback.format_exception(error)) if isinstance(error, BaseException) else str(error)
    try:
        run.write_status("failed", error=messages.RECORDER_ERROR.format(error=error))
    except Exception:
        pass
    try:
        with open(run.log_path, "a", encoding="utf-8") as log:
            log.write(text + "\n")
    except Exception:
        pass
    print(f"The recorder could not start: {error}")
    return 2


def _split(args, compiled: bool) -> int:
    from tracing.pml import read_capture
    traces_dir = paths.traces_dir(compiled, args.traces_dir)
    runs = [RunDir(Path(r)) for r in args.trace_split] or leftover_runs(traces_dir, _is_alive)
    if not runs:
        print("No runs to split.")
    failed = False
    for run in runs:
        if run.is_split() and not run.capture_files():
            print(f"{run.path}: already split, nothing to do")
            continue
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
    if args.app_pid is None or not args.procmon:
        return _record_early_failure(Path(args.trace_recorder), "--trace-recorder needs --app-pid and --procmon.")
    try:
        recorder = _build_recorder(args)
    except Exception as error:
        return _record_early_failure(Path(args.trace_recorder), error)
    return recorder.run()


def _build_recorder(args) -> "Recorder":
    from tracing.live import windows_snapshot
    from tracing.pml import read_capture
    from tracing.procmon import WindowsProcmon
    from tracing.recorder import Recorder
    run = RunDir(Path(args.trace_recorder))
    traces_dir = run.path.parent.parent
    return Recorder(
        run, _app_alive_check(args.app_pid), WindowsProcmon(Path(args.procmon)), windows_snapshot,
        lambda r: split_run_dir(r, traces_dir, read_capture), lambda: leftover_runs(traces_dir, _is_alive),
        lambda: shutil.disk_usage(run.path).free, time.monotonic, time.sleep)
