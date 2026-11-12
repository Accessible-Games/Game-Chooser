---
paths:
  - "/build_windows.sh"
  - "/pyproject.toml"
  - "scripts/**/*.py"
---

# Build and Distribution

```bash
./build_windows.sh
```
Needs Nuitka (run as `python -m nuitka` through `uv`) and a C compiler. It fetches the extraction tools into `bin/`
when missing, builds a standalone folder `build/game-chooser.dist/` (`--include-package=engine`), then ships `bin/`
(with `bin/licenses/`) and `THIRD_PARTY.md` next to the exe. `./build_windows.sh --ship-only DIST` only ships the
tools into an existing build folder. Distribute the whole `game-chooser.dist` folder.

The normal build leaves out `tracing/` and `procmon_parser` (`--nofollow-import-to`), and afterwards runs
`scripts/check_no_tracing.py build/compilation-report.xml build/game-chooser.dist`, which fails the build if any
tracing module or `tracing`/`procmon`/`traces` file got in, or if the report lists no modules at all (an old report
is deleted before Nuitka runs; a build that fails the check has its dist folder removed).
`./build_windows.sh --tracing` builds the tracing variant into `build/tracing/` instead (with Process Monitor in
`procmon/` and a `TRACING-BUILD.txt` warning); it is for the catalog admin's test machines only and is never
distributed.

## Scripts, tools and dependencies
- **scripts/** - `check_no_tracing.py`, `fetch_procmon.py`, `fetch_tools.py`, `serve_test_catalog.py`;
  **bin/** - bundled tools (git-ignored). `smart_list` (pinned in `[tool.uv.sources]`)
  and its dependency `resource_finder` are uv git dependencies, not on PyPI

## License
GNU GPLv3 - see LICENSE.txt
