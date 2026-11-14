"""Where the tracing variant finds Process Monitor, writes its traces and how it starts itself again.

A compiled build keeps everything beside its exe; from source, the repo root (the folder of game-chooser.py)
stands in for that folder, and Process Monitor comes from tracing-tools/ (scripts/fetch_procmon.py).
"""
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent


def is_compiled(main_globals: dict) -> bool:
    return "__compiled__" in main_globals


def app_dir(compiled: bool) -> Path:
    if compiled:
        return Path(sys.argv[0]).resolve().parent
    return REPO_DIR


def procmon_exe(compiled: bool) -> Path:
    if compiled:
        return app_dir(True) / "procmon" / "Procmon64.exe"
    return REPO_DIR / "tracing-tools" / "procmon" / "Procmon64.exe"


def traces_dir(compiled: bool, override: str | None) -> Path:
    if override:
        return Path(override)
    return app_dir(compiled) / "traces"


def self_command(compiled: bool) -> tuple[str, list[str]]:
    if compiled:
        return str(Path(sys.argv[0]).resolve()), []
    return sys.executable, [str(REPO_DIR / "game-chooser.py")]
