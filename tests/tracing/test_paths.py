import sys
from pathlib import Path

from tracing import paths

REPO = Path(__file__).resolve().parent.parent.parent


def test_is_compiled():
    assert paths.is_compiled({"__compiled__": object()})
    assert not paths.is_compiled({"__name__": "__main__"})


def test_procmon_and_traces_paths(tmp_path, monkeypatch):
    exe = tmp_path / "dist" / "game-chooser.exe"
    monkeypatch.setattr(sys, "argv", [str(exe), "--tracing"])
    dist = exe.parent.resolve()

    assert paths.app_dir(True) == dist
    assert paths.procmon_exe(True) == dist / "procmon" / "Procmon64.exe"
    assert paths.traces_dir(True, None) == dist / "traces"
    assert paths.self_command(True) == (str(exe.resolve()), [])

    assert paths.app_dir(False) == REPO
    assert paths.procmon_exe(False) == REPO / "tracing-tools" / "procmon" / "Procmon64.exe"
    assert paths.traces_dir(False, None) == REPO / "traces"
    assert paths.self_command(False) == (sys.executable, [str(REPO / "game-chooser.py")])

    override = str(tmp_path / "elsewhere")
    assert paths.traces_dir(True, override) == Path(override)
    assert paths.traces_dir(False, override) == Path(override)
