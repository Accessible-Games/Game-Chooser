"""Download the bundled extraction tools (7-Zip, innoextract) into bin/.

Run: uv run python scripts/fetch_tools.py
Each download is checked against a pinned SHA256. The binaries are not committed (bin/ is ignored).
"""

import hashlib
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin"

SEVEN_ZR_URL = "https://www.7-zip.org/a/7zr.exe"
SEVEN_ZR_SHA256 = "256feca8e274e5da655e2a284fabafd9f554365eb164862089dacd4e8276d282"
SEVEN_ZIP_URL = "https://www.7-zip.org/a/7z2409-x64.exe"
SEVEN_ZIP_SHA256 = "bdd1a33de78618d16ee4ce148b849932c05d0015491c34887846d431d29f308e"
INNOEXTRACT_URL = "https://constexpr.org/innoextract/files/innoextract-1.9-windows.zip"
INNOEXTRACT_SHA256 = "6989342c9b026a00a72a38f23b62a8e6a22cc5de69805cf47d68ac2fec993065"


def download(url: str, dest: Path, expected: str) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "GameChooser-fetch-tools/0.1"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out)
    actual = hashlib.sha256(dest.read_bytes()).hexdigest()
    print(f"{url}\n  SHA256 {actual}")
    if actual != expected:
        raise SystemExit(f"SHA256 mismatch for {url}: expected {expected or '(none recorded)'}, got {actual}")


def extract(zf: zipfile.ZipFile, name: str, dest: Path, url: str) -> None:
    if name not in zf.namelist():
        raise SystemExit(f"{name} not found in {url}")
    with zf.open(name) as src, open(dest, "wb") as dst:
        shutil.copyfileobj(src, dst)


def main() -> None:
    BIN.mkdir(exist_ok=True)
    licenses = BIN / "licenses"
    licenses.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        seven_zr = tmp / "7zr.exe"
        installer = tmp / "7z-x64.exe"
        inno_zip = tmp / "innoextract.zip"
        download(SEVEN_ZR_URL, seven_zr, SEVEN_ZR_SHA256)
        download(SEVEN_ZIP_URL, installer, SEVEN_ZIP_SHA256)
        download(INNOEXTRACT_URL, inno_zip, INNOEXTRACT_SHA256)

        out = tmp / "7z"
        subprocess.run([str(seven_zr), "x", "-y", f"-o{out}", str(installer), "7z.exe", "7z.dll", "License.txt"],
                       check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        shutil.copy2(out / "7z.exe", BIN / "7z.exe")
        shutil.copy2(out / "7z.dll", BIN / "7z.dll")
        shutil.copy2(out / "License.txt", licenses / "7-Zip-License.txt")

        with zipfile.ZipFile(inno_zip) as zf:
            extract(zf, "innoextract.exe", BIN / "innoextract.exe", INNOEXTRACT_URL)
            # The zip ships its own license/ folder (innoextract's zlib license plus its bundled libraries').
            for name in zf.namelist():
                if name.startswith("license/") and not name.endswith("/"):
                    extract(zf, name, licenses / Path(name).name, INNOEXTRACT_URL)
    print("Tools are in", BIN)


if __name__ == "__main__":
    sys.exit(main())
