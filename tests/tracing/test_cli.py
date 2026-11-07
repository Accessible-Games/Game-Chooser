import subprocess
import sys
from pathlib import Path

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
