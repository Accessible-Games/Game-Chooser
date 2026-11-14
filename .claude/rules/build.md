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
Needs Nuitka (pinned, run as `python -m nuitka` through `uv`) and a C compiler. It fetches the extraction tools into
`bin/` when missing, builds a standalone folder `build/game-chooser.dist/` (`--include-package=engine`), then ships
`bin/` (with `bin/licenses/`), `THIRD_PARTY.md` and `LICENSE.txt` next to the exe. `./build_windows.sh --ship-only DIST` only ships the
tools into an existing build folder. Distribute the whole `game-chooser.dist` folder.

The normal build leaves out `tracing/` and `procmon_parser` (`--nofollow-import-to`), and afterwards runs
`scripts/check_no_tracing.py build/compilation-report.xml build/game-chooser.dist`, which fails the build if any
tracing module or `tracing`/`procmon`/`traces` file got in, or if the report lists no modules at all (an old report
is deleted before Nuitka runs; a build that fails the check has its dist folder removed).
`./build_windows.sh --tracing` builds the tracing variant into `build/tracing/` instead (with Process Monitor in
`procmon/` and a `TRACING-BUILD.txt` warning); it is for the catalog admin's test machines only and is never
distributed.

## Release workflow
`.github/workflows/release.yml` (Windows x64 only, on `windows-latest`) runs when a tag starting with `v` is pushed:
`uv sync --locked`, `scripts/fetch_tools.py` (before the tests, or the `tools` tests skip), `pytest`, the normal
build, a check that the exe, the tools, `bin/licenses/`, `THIRD_PARTY.md` and `LICENSE.txt` are in the built folder,
and a smoke start (a copy of the build, with `APPDATA` pointed at an empty folder, must still be running after 15
seconds). It then zips the folder as `GameChooser-<tag>-windows-x64.zip` (one top-level `GameChooser` folder) with a
`SHA256SUMS.txt`, and publishes both as a GitHub release with generated notes. Running it by hand
(`workflow_dispatch`) is a dry run: no release, the zip (named with the short commit hash) is kept as a workflow
artifact.

## Scripts, tools and dependencies
- **scripts/** - `check_no_tracing.py`, `fetch_procmon.py`, `fetch_tools.py`, `serve_test_catalog.py`;
  **bin/** - bundled tools (git-ignored). `smart_list` (pinned in `[tool.uv.sources]`)
  and its dependency `resource_finder` are uv git dependencies, not on PyPI

## License
GNU GPLv3 - see LICENSE.txt
