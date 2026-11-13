"""Download Sysinternals Process Monitor into tracing-tools/procmon/ for the save-tracing variant.

Run: uv run python scripts/fetch_procmon.py
The zip is checked against a pinned SHA256. Process Monitor is never committed or redistributed: tracing-tools/
is git-ignored, and only the tracing build copies it into its own dist folder.
"""

import sys
import tempfile
import zipfile
from pathlib import Path

from fetch_tools import download, extract  # scripts/ is on the path when this script runs

ROOT = Path(__file__).resolve().parent.parent
PROCMON_DIR = ROOT / "tracing-tools" / "procmon"

PROCMON_URL = "https://download.sysinternals.com/files/ProcessMonitor.zip"
# Process Monitor 4.13 (2026-11-07); its Procmon64.exe is Authenticode-signed by Microsoft Corporation.
PROCMON_SHA256 = "97330813853e0e514a11ee16a47d4c6fe90148fae834649839afb30487cc8b0b"
FILES = ("Procmon64.exe", "Eula.txt")


def main() -> None:
    PROCMON_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp_name:
        archive = Path(tmp_name) / "ProcessMonitor.zip"
        try:
            download(PROCMON_URL, archive, PROCMON_SHA256)
        except SystemExit as mismatch:
            raise SystemExit(f"{mismatch}. Sysinternals may have released a new version. Check its signature, then "
                             "update PROCMON_SHA256.") from None
        with zipfile.ZipFile(archive) as zf:
            for name in FILES:
                extract(zf, name, PROCMON_DIR / name, PROCMON_URL)
    print("Process Monitor is in", PROCMON_DIR)


if __name__ == "__main__":
    sys.exit(main())
