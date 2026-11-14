"""Copy the license texts of what Nuitka builds into the app into DIST/licenses/ (run by build_windows.sh and
build_macos.sh).

Run: uv run python scripts/collect_licenses.py DIST
Texts come from the installed packages and the Python uv uses. That Python (python-build-standalone) has no texts
for the libraries built into it (OpenSSL, libffi, expat, mpdecimal, liblzma), so those are fetched from the
python-build-standalone release named in its BUILD file. Any text not found stops the build.
"""

import shutil
import sys
import urllib.request
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

# Packages built into the exe whose dist-info carries a License-File.
PACKAGES = ["wxPython", "frozendict", "six", "platformdirs", "platform-utils"]
# Libraries built into python-build-standalone's Python, by their name in that repo's LICENSE.<name>.txt files.
PYTHON_LIBRARIES = ["openssl-3", "libffi", "expat", "mpdecimal", "liblzma"]
PBS_URL = "https://raw.githubusercontent.com/astral-sh/python-build-standalone/{tag}/LICENSE.{name}.txt"


def fail(message: str):
    raise SystemExit(f"{message}; the build can't ship without it.")


def installed(name: str):
    try:
        return distribution(name)
    except PackageNotFoundError:
        fail(f"{name} is not installed, so its license text was not found")


def file_text(path: Path, what: str) -> bytes:
    if not path.is_file():
        fail(f"{path} ({what}) is missing")
    return path.read_bytes()


def package_texts(name: str) -> list[tuple[str, bytes]]:
    dist = installed(name)
    found = []
    for entry in dist.metadata.get_all("License-File") or []:
        # Newer wheels keep license files under licenses/ in the dist-info folder, older ones at its top. Copied as
        # bytes: not every license file is UTF-8.
        ends = (f".dist-info/licenses/{entry}", f".dist-info/{entry}")
        path = next((p for p in dist.files or [] if p.as_posix().endswith(ends) and Path(p.locate()).is_file()), None)
        if path is not None:
            found.append((f"{name}-{Path(entry).stem}.txt", path.read_binary()))
    if not found:
        fail(f"No license text found in {name}'s dist-info")
    return found


def python_license(base: Path, platform: str) -> Path:
    """Where python-build-standalone keeps Python's license: beside python.exe on Windows, with the standard
    library elsewhere."""
    if platform == "win32":
        return base / "LICENSE.txt"
    return base / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "LICENSE.txt"


def main() -> None:
    out = Path(sys.argv[1]) / "licenses"
    base = Path(sys.base_prefix)
    texts = [("Python.txt", file_text(python_license(base, sys.platform), "Python's license"))]
    for name in PACKAGES:
        texts += package_texts(name)
    if sys.platform == "win32":
        # pywin32's texts sit in its package folders (311 has none in its dist-info).
        texts.append(("pywin32.txt", file_text(Path(installed("pywin32").locate_file("win32/License.txt")),
                                               "pywin32's license")))
    tag = file_text(base / "BUILD", "the python-build-standalone release of uv's Python").decode().strip()
    for name in PYTHON_LIBRARIES:
        url = PBS_URL.format(tag=tag, name=name)
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                texts.append((f"Python-{name}.txt", resp.read()))
        except OSError as exc:
            fail(f"Could not fetch {url} ({exc})")
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    for filename, data in texts:
        (out / filename).write_bytes(data)
    print(f"Wrote {len(texts)} license texts to {out}")


if __name__ == "__main__":
    main()
