---
paths:
  - "/build_windows.sh"
  - "/pyproject.toml"
  - "scripts/**/*.py"
  - "/.github/workflows/*.yml"
---

# Build and Distribution

```bash
./build_windows.sh
```
Needs Nuitka (pinned, run as `python -m nuitka` through `uv`), a C compiler and uv's own Python. It fetches the
extraction tools into `bin/` when missing, builds a standalone folder `build/game-chooser.dist/`
(`--include-package=engine`), then ships `bin/` (with `bin/licenses/`), `THIRD_PARTY.md` and `LICENSE.txt` next to
the exe, and `licenses/` (`scripts/collect_licenses.py`: the license texts of what is built into the app, from
Python's `LICENSE.txt`, the installed packages' dist-info and pywin32's `win32/License.txt`; the texts for OpenSSL,
libffi, expat, mpdecimal and liblzma come from the python-build-standalone release named in uv's Python's `BUILD`
file, since that Python carries none). It fails when the exe, a tool, a license text, `THIRD_PARTY.md` or
`LICENSE.txt` is missing. `THIRD_PARTY.md` lists each bundled component and where its text is in the shipped folder;
a new component needs an entry there and in `collect_licenses.py`. `./build_windows.sh --ship-only DIST` only ships
the tools and license texts into an existing build folder. Distribute the whole `game-chooser.dist` folder.

The normal build leaves out `tracing/` and `procmon_parser` (`--nofollow-import-to`), and afterwards runs
`scripts/check_no_tracing.py build/compilation-report.xml build/game-chooser.dist`, which fails the build if any
tracing module or `tracing`/`procmon`/`traces` file got in, or if the report lists no modules at all (an old report
is deleted before Nuitka runs; a build that fails the check has its dist folder removed).
`./build_windows.sh --tracing` builds the tracing variant into `build/tracing/` instead (with Process Monitor in
`procmon/` and a `TRACING-BUILD.txt` warning); it is for the catalog admin's test machines only and is never
distributed.

## macOS build
`./build_macos.sh` (on a Mac only) builds `build/Game Chooser.app` for the Mac's own processor with the same pinned
Nuitka (`--mode=app`), leaves tracing out and runs `check_no_tracing.py` on the bundle. It has no tracing variant.
It fetches `bin/7zz` when missing, stops when `7zz` doesn't run on this processor (7-Zip's Mac download is arm64
only), and ships it with its license in `Contents/MacOS/bin/` (where `default_tools_dir` looks). It copies `LICENSE.txt`, `THIRD_PARTY-macos.md` (as
`THIRD_PARTY.md`) and `licenses/` into `Contents/Resources/`, then signs the bundle again ad hoc, since adding files
breaks Nuitka's signature. `collect_licenses.py` takes Python's license from `lib/python3.X/` off Windows and skips
pywin32 there. A new component built into the Mac app needs an entry in `THIRD_PARTY-macos.md`.

## Release workflow
`.github/workflows/release.yml` (Windows x64 only, on `windows-latest`) runs when a tag starting with `v` is pushed:
`uv sync --locked`, `scripts/fetch_tools.py` (before the tests, or the `tools` tests skip), `pytest`, the normal
build (which checks the shipped files itself), and a smoke start: a copy of the build, with `APPDATA` pointed at an
empty folder, must still be running after 15 seconds with a main window titled `Game Chooser (`... (a missing DLL
leaves Windows' error box, titled after `game-chooser.exe`, which keeps the process alive). It then zips the folder
as `GameChooser-<tag>-windows-x64.zip` (one top-level `GameChooser` folder) with a `SHA256SUMS.txt`, and publishes
both as a GitHub pre-release with generated notes. Running it by hand (`workflow_dispatch`) is a dry run: no release,
the zip (named with the short commit hash) is kept as a workflow artifact.

## Scripts, tools and dependencies
- **build_macos.sh** - the macOS build (above). **scripts/** - `check_no_tracing.py`, `collect_licenses.py`, `fetch_procmon.py`, `fetch_tools.py`,
  `serve_test_catalog.py`; **bin/** - bundled tools (git-ignored; `fetch_tools.py` takes each from an address with
  its version in it, since a fixed SHA256 breaks when an unversioned file is replaced). `smart_list` (pinned in
  `[tool.uv.sources]`) and its dependency `resource_finder` are uv git dependencies, not on PyPI

## License
GNU GPLv3 - see LICENSE.txt
