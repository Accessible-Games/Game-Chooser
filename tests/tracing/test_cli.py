import subprocess
import sys
from pathlib import Path

import pytest

import tracing.cli as cli
from tracing.runfiles import RunDir, new_run
from tracing.splitrun import SplitResult


def test_no_tracing_command_returns_none():
    assert cli.run_command([], False) is None
    assert cli.run_command(["--tracing"], False) is None
    assert cli.run_command(["--unknown", "x"], True) is None


def test_tracing_wanted_from_source_needs_flag():
    assert cli.tracing_wanted([], False) is False
    assert cli.tracing_wanted(["--tracing"], False) is True
    assert cli.tracing_wanted([], True) is True


def test_traces_dir_override():
    assert cli.traces_dir_override(["--tracing", "--traces-dir", "X"]) == "X"
    assert cli.traces_dir_override(["--traces-dir=Y"]) == "Y"
    assert cli.traces_dir_override(["--tracing"]) is None


def test_split_command_splits_named_and_leftover_runs(tmp_path, monkeypatch, capsys):
    calls = []

    def fake_split(run, traces_dir, read_capture):
        calls.append((run.path, traces_dir))
        if run.path.name.endswith("-bad"):
            return SplitResult([], ["boom"])
        return SplitResult([tmp_path / "out"], [])

    monkeypatch.setattr(cli, "split_run_dir", fake_split)
    named = tmp_path / "named"
    assert cli.run_command(["--trace-split", str(named), "--traces-dir", str(tmp_path)], False) == 0
    assert calls == [(named, tmp_path)]

    run = new_run(tmp_path, 133_000_000_000_000_000, 5)
    run.capture_path.write_bytes(b"pml")
    calls.clear()
    assert cli.run_command(["--trace-split", "--traces-dir", str(tmp_path)], False) == 0
    assert calls == [(run.path, tmp_path)]
    assert str(tmp_path / "out") in capsys.readouterr().out

    monkeypatch.setattr(cli, "leftover_runs", lambda d, alive: [RunDir(tmp_path / "x-bad")])
    assert cli.run_command(["--trace-split", "--traces-dir", str(tmp_path)], False) == 1
    assert "boom" in capsys.readouterr().out


def test_probe_passes_procmon_and_default_workload(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(cli, "_run_probe", lambda out, procmon, workload: seen.update(a=(out, procmon, workload)) or 0)
    assert cli.run_command(["--trace-probe", str(tmp_path)], False) == 0
    out, procmon, workload = seen["a"]
    assert out == Path(tmp_path) and procmon.name == "Procmon64.exe" and workload.name == "trace-test.bat"
    cli.run_command(["--trace-probe", str(tmp_path), "--workload", "w.bat"], False)
    assert seen["a"][2] == Path("w.bat")


def test_cli_import_is_light():
    code = ("import sys, tracing.cli; bad = [m for m in ('procmon_parser', 'wx', 'win32api') if m in sys.modules]; "
            "sys.exit(1 if bad else 0)")
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


def _failed_status(run):
    status = run.read_status()
    assert status["state"] == "failed"
    assert status["error"].startswith("The recorder hit an unexpected error")
    assert run.log_path.exists()


def test_recorder_bad_app_pid_records_failure(tmp_path):
    run = new_run(tmp_path, 133_000_000_000_000_000, 5)
    code = cli.run_command(["--trace-recorder", str(run.path), "--app-pid", "abc", "--procmon", "p"], False)
    assert code not in (0, None)
    _failed_status(run)


def test_recorder_build_failure_records_failure(tmp_path, monkeypatch):
    run = new_run(tmp_path, 133_000_000_000_000_000, 5)

    def boom(exe):
        raise RuntimeError("no parser")

    monkeypatch.setattr("tracing.procmon.WindowsProcmon", boom)
    code = cli.run_command(["--trace-recorder", str(run.path), "--app-pid", "1", "--procmon", "p"], False)
    assert code not in (0, None)
    _failed_status(run)


def test_recorder_missing_args_records_failure(tmp_path):
    run = new_run(tmp_path, 133_000_000_000_000_000, 5)
    assert cli.run_command(["--trace-recorder", str(run.path)], False) == 2
    _failed_status(run)


def test_split_named_run_already_split_does_nothing(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "split_run_dir", lambda run, d, r: calls.append(run.path) or SplitResult([], []))
    run = new_run(tmp_path, 133_000_000_000_000_000, 5)
    run.split_path.write_text("{}", encoding="utf-8")
    assert cli.run_command(["--trace-split", str(run.path), "--traces-dir", str(tmp_path)], False) == 0
    assert calls == []
    assert f"{run.path}: already split, nothing to do" in capsys.readouterr().out


def test_split_named_run_with_a_kept_capture_splits_again(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "split_run_dir", lambda run, d, r: calls.append(run.path) or SplitResult([], []))
    run = new_run(tmp_path, 133_000_000_000_000_000, 5)
    run.split_path.write_text("{}", encoding="utf-8")
    run.capture_path.write_bytes(b"pml")
    assert cli.run_command(["--trace-split", str(run.path), "--traces-dir", str(tmp_path)], False) == 0
    assert calls == [run.path]
    assert cli.run_command(["--trace-split", "--traces-dir", str(tmp_path)], False) == 0
    assert calls == [run.path]  # leftover runs still skip a run that has split.json


def test_trace_check_passes_a_good_session(tmp_path, capsys):
    from tests.tracing.test_check_trace_test import good_lines, session
    folder = session(tmp_path, good_lines())
    assert cli.run_command(["--trace-check", str(folder)], True) == 0
    assert capsys.readouterr().out.strip() == "PASS"


def test_trace_check_reports_problems(tmp_path, capsys):
    from tests.tracing.test_check_trace_test import good_lines, session
    folder = session(tmp_path, [line for line in good_lines() if line["op"] != "delete"])
    assert cli.run_command(["--trace-check", str(folder)], True) == 1
    assert "junk.txt" in capsys.readouterr().out


def test_trace_check_missing_or_non_session_folder(tmp_path, capsys):
    assert cli.run_command(["--trace-check", str(tmp_path / "nope")], True) == 2
    assert "not a session folder" in capsys.readouterr().out
    assert cli.run_command(["--trace-check", str(tmp_path)], True) == 2


def test_stop_is_a_tracing_command(tmp_path, capsys):
    assert cli.run_command(["--trace-stop", "--traces-dir", str(tmp_path)], False) == 0
    assert capsys.readouterr().out.strip() == "No tracing run is in progress."


def test_stop_passes_the_traces_folder_the_run_and_the_close_flag(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "_run_stop", lambda traces_dir, run, close: seen.append((traces_dir, run, close)) or 7)
    assert cli.run_command(["--trace-stop", "--traces-dir", str(tmp_path)], False) == 7
    assert cli.run_command(["--trace-stop", "--close-games", "--traces-dir", str(tmp_path)], False) == 7
    assert cli.run_command(["--trace-stop", str(tmp_path / "r"), "--close-games"], True) == 7
    assert cli.run_command(["--close-games", "--traces-dir", str(tmp_path), "--trace-stop"], False) == 7
    assert seen[0] == (tmp_path, None, False) and seen[1] == (tmp_path, None, True)
    assert seen[2][1:] == (tmp_path / "r", True) and seen[3] == (tmp_path, None, True)


def test_close_games_alone_is_not_a_command():
    assert cli.run_command(["--close-games"], True) is None


def test_stop_asks_whether_the_recorder_is_alive(tmp_path, monkeypatch, capsys):
    run = new_run(tmp_path, 133_000_000_000_000_000, 5)
    run.write_status("capturing", recorder_pid=4321)
    run.capture_path.write_bytes(b"pml")
    asked = []
    monkeypatch.setattr(cli, "_is_alive", lambda pid: asked.append(pid) or False)
    assert cli.run_command(["--trace-stop", "--traces-dir", str(tmp_path)], False) == 1
    assert 4321 in asked and "the recorder is no longer running" in capsys.readouterr().out


def test_help_with_a_command_prints_the_help_and_runs_nothing(monkeypatch, capsys):
    def never(*args):
        raise AssertionError("--help must not run the command")

    for name in ("_run_stop", "_split", "_recorder", "_trace_check", "_run_probe"):
        monkeypatch.setattr(cli, name, never)
    for argv in (["--trace-stop", "--close-games", "--help"], ["-h", "--trace-stop"], ["--trace-split", "--help"]):
        assert cli.run_command(argv, False) == 0
        text = " ".join(capsys.readouterr().out.split())
        for name in cli.COMMANDS + ("--close-games", "--traces-dir"):
            assert name in text
        assert "Unsaved game progress is lost" in text and "15 seconds" in text
        assert "a game started through the administrator prompt is not closed" in text
        assert "runs as Game Chooser's user with no more rights than Game Chooser" in text


def test_help_alone_is_not_a_tracing_command():
    assert cli.run_command(["--help"], True) is None


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_the_recorder_is_built_to_close_only_what_the_app_could_and_never_the_app(tmp_path):
    import argparse
    import os
    from tracing.closing import WindowsCloser, process_identity
    run = new_run(tmp_path, 133_000_000_000_000_000, 5)
    recorder = cli._build_recorder(argparse.Namespace(trace_recorder=str(run.path), app_pid=os.getpid(),
                                                      procmon="p.exe"))
    assert isinstance(recorder.closer, WindowsCloser)
    # the limit is who the process named by --app-pid runs as, read from that process itself
    assert recorder.closer.limit == process_identity(os.getpid()) and recorder.closer.limit is not None
    assert os.getpid() in recorder._protected_pids


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_a_recorder_whose_app_has_gone_closes_nothing(tmp_path):
    import argparse
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait(30)
    run = new_run(tmp_path, 133_000_000_000_000_000, 5)
    recorder = cli._build_recorder(argparse.Namespace(trace_recorder=str(run.path), app_pid=gone.pid,
                                                      procmon="p.exe"))
    assert recorder.closer.limit is None
