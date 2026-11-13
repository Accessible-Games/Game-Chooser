"""scripts/check_no_tracing.py: the normal build must hold no tracing code."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_no_tracing", ROOT / "scripts" / "check_no_tracing.py")
check_no_tracing = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_no_tracing)

CLEAN_FILES = ["game-chooser.exe", "bin/7z.exe"]


def report(*names):
    modules = "".join(f'    <module name="{n}" kind="PythonSourceModule" />\n' for n in names)
    return f'<?xml version="1.0"?>\n<nuitka-compilation-report>\n{modules}</nuitka-compilation-report>\n'


def test_clean_report_passes():
    assert check_no_tracing.find_tracing(report("engine.jobs", "main_window"), CLEAN_FILES) == []


# file names are matched in any case, and a name that starts with tracing or procmon counts
@pytest.mark.parametrize("module, files, named", [
    ("tracing.recorder", CLEAN_FILES, "tracing.recorder"), ("tracing", CLEAN_FILES, "tracing"),
    ("procmon_parser", CLEAN_FILES, "procmon_parser"), ("procmon_parser.logs", CLEAN_FILES, "procmon_parser.logs"),
    ("a", ["Tracing/x.pyd"], "Tracing/x.pyd"), ("a", ["bin/Procmon64.exe"], "bin/Procmon64.exe"),
])
def test_a_tracing_module_or_file_fails(module, files, named):
    problems = check_no_tracing.find_tracing(report("main_window", module), files)
    assert len(problems) == 1 and named in problems[0]


def test_procmon_or_traces_files_fail():
    problems = check_no_tracing.find_tracing(
        report("main_window"), CLEAN_FILES + ["procmon/Procmon64.exe", "traces/x/session.json"]
    )
    assert any("procmon/Procmon64.exe" in p for p in problems)
    assert any("traces/x/session.json" in p for p in problems)


def test_similar_names_pass():
    problems = check_no_tracing.find_tracing(
        report("engine.tracing_helpers"), CLEAN_FILES + ["bin/licenses/tracing-notes.txt"]
    )
    assert problems == []


def test_real_report_passes():
    path = ROOT / "build" / "compilation-report.xml"
    if not path.exists():
        pytest.skip("no normal build report")
    dist = ROOT / "build" / "game-chooser.dist"
    files = [p.relative_to(dist).as_posix() for p in dist.rglob("*")] if dist.is_dir() else []
    assert check_no_tracing.find_tracing(path.read_text(encoding="utf-8"), files) == []


NO_MODULES = "The compilation report lists no modules; its format may have changed, so the build can't be checked."


def test_empty_report_fails():
    assert check_no_tracing.find_tracing("", CLEAN_FILES) == [NO_MODULES]
    assert check_no_tracing.find_tracing(report(), CLEAN_FILES) == [NO_MODULES]


def test_garbled_report_fails():
    garbled = '<nuitka-compilation-report><mod name="engine.jobs"/>\x00\x01 garbage'
    assert check_no_tracing.find_tracing(garbled, CLEAN_FILES) == [NO_MODULES]


def test_main_fails_on_a_report_without_modules(tmp_path, capsys):
    xml = tmp_path / "report.xml"
    xml.write_text("<nuitka-compilation-report/>", encoding="utf-8")
    dist = tmp_path / "dist"
    dist.mkdir()
    assert check_no_tracing.main(["check", str(xml), str(dist)]) == 1
    assert NO_MODULES in capsys.readouterr().out
