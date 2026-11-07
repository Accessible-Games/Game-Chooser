"""Download Sysinternals Process Monitor into tracing-tools/procmon/ for the save-tracing variant.

Run: uv run python scripts/fetch_procmon.py
The zip is checked against a pinned SHA256. Process Monitor is never committed or redistributed: tracing-tools/
is git-ignored, and only the tracing build copies it into its own dist folder.
"""

import hashlib
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

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
        req = urllib.request.Request(PROCMON_URL, headers={"User-Agent": "GameChooser-fetch-procmon/0.1"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(archive, "wb") as out:
            shutil.copyfileobj(resp, out)
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        print(f"{PROCMON_URL}\n  SHA256 {actual}")
        if actual != PROCMON_SHA256:
            raise SystemExit("SHA256 mismatch for ProcessMonitor.zip: Sysinternals may have released a new version. "
                             "Check its signature, then update PROCMON_SHA256.")
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            for name in FILES:
                if name not in names:
                    raise SystemExit(f"{name} not found in {PROCMON_URL}")
                with zf.open(name) as src, open(PROCMON_DIR / name, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    print("Process Monitor is in", PROCMON_DIR)


if __name__ == "__main__":
    sys.exit(main())
