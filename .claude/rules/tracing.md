---
paths:
  - "tracing/**/*"
  - "tests/tracing/**/*.py"
  - "/scripts/check_no_tracing.py"
  - "/scripts/fetch_procmon.py"
  - "/tests/test_check_no_tracing.py"
  - "/game-chooser.py"
---

# Tracing build (`tracing/`)

A build-time variant for the catalog admin: it records every file and registry write the launched games make, with
Process Monitor, and splits the capture into one folder per play session. Normal builds contain none of it.
Log format: `tracing/LOG-FORMAT.md`. Process Monitor notes from the VM probe: `tracing/PROCMON-NOTES.md`.
Build: see `.claude/rules/build.md`.

- **game-chooser.py** - entry point
- **Rule:** who may import `tracing/` is in CLAUDE.md (Rules that hold everywhere, Tracing). `procmon_parser` is
  imported only inside `tracing/` and its tests; inside `tracing/` always lazily, inside functions. Only
  `app_glue.py` imports wx. The game launch is
  unchanged: `MainFrame` calls `frame.launch_observer` around its `engine.launch.start_game` for a program launch
  only (passing on `Started.pid`, the process ID `start_program` returns, or `start_hta` for an `.hta` game, whose
  process is Windows' `mshta.exe`; a web page launch is never observed),
  and shows `frame.title_note` in the title. The normal build is checked by `scripts/check_no_tracing.py` (fails
  the build on any tracing module or `tracing`/`procmon`/`traces` file, or a report with no modules).
- **Commands** (`game-chooser.py`, through `tracing/cli.py`): `--tracing` (turn tracing on from source; a compiled
  tracing build is always on), `--trace-recorder RUN --app-pid N --procmon EXE` (the elevated recorder, started by
  the app), `--trace-split [RUN...]` (split leftover runs after a crash; prints "No runs to split." when there are none;
  a named RUN that has `split.json` and no capture left prints "already split, nothing to do"; a named RUN whose
  capture was kept is split again; without RUN only runs with no `split.json` are split),
  `--trace-probe OUT [--workload BAT]` (one short capture to learn what the real capture holds),
  `--trace-check FOLDER` (the trace-test acceptance check on a session folder, for a machine with only the compiled
  build: prints `PASS` or one problem per line; exit 0 pass, 1 problems, 2 not a session folder), `--traces-dir PATH`
  (use another traces folder). Exit codes: 2 for a bad command line. `--trace-split`: 0 done, 1 when a run
  couldn't be split or its split had a problem. `--trace-recorder`: 0, 1 when the recorder recorded a failure, 2
  when it could not start. `--trace-probe`: 0, 1 on a missing file, a declined administrator prompt or an error
  (started unelevated, it passes on the elevated probe's code).
- **Where logs go:** `traces/` beside the exe (from source: beside `game-chooser.py`; git-ignored). One folder per
  play session, `<catalog id or title>-<YYYYMMDD-HHMMSS>`, holding `session.json` and `events.jsonl`. The recorder's
  working files live in `traces/_runs/<YYYYMMDD-HHMMSS>-<pid>/`: `status.json`, `sessions/<id>.json`, `filter.pmc`,
  `capture.pml` (and `capture-N.pml`), `recorder.log`, `split.json` (`folders`, `problems`, `capture_kept`). The
  capture and `filter.pmc` are removed after a split with no problems, and kept (`capture_kept: true`) when any
  session has a problem or a session file can't be read. Atomic writes retry `os.replace` while Windows says the
  file is held (`runfiles.replace_with_retry`).
- **Attribution:** a session is the launched process plus its descendants by parent links in the capture. The root
  is matched by pid and the start time read after the launch; failing an exact match, by pid within 2 s of the launch
  call, closest to that start time (`attribution.find_root`). A launch that gave no pid (the administrator prompt's
  shell handed back no process) is recorded with `root_pid` 0 and no start time; its root is then the process whose
  image is the game's exe that started from 2 s before the launch call to 2 s after it returned, never one another
  session holds (pid-known sessions are matched first), preferring the earliest that started at or after the call,
  else the closest to it (`attribution.find_root_by_exe`; the live tree uses the same rule). `process_exit` lines
  are written for every process whose exit is in the capture. `session.json` env carries `app_build` (short git
  commit from source, else null).
- **Capture filter** (`procmon.filter_config`; destructive, so dropped events never reach the file): drops every
  event of `EXCLUDED_PROCESSES` (`APP_PROCESSES`: `Procmon.exe`, `Procmon64.exe`, `System`, `game-chooser.exe`,
  `7z.exe`, `innoextract.exe`;
  `SYSTEM_PROCESSES`: Windows background services and tools), and with them the processes they start, so a game
  whose own tree runs such a name loses that part; read-only opens (`READ_ONLY_ACCESS` Detail prefixes, no write or
  delete right, so a delete-on-close open is kept), `REG_OPENED_EXISTING_KEY` and `Delete: False` events. The
  recorder logs the active filter to `recorder.log` (`describe_filter`). Reads were never recorded.
- **Title:** `Game Chooser (tracing starting)`, `(tracing)` or `(tracing off)`. When off, a dialog says why and
  games launch untraced after the player confirms.
- **Mark key:** Ctrl+Alt+Shift+M adds a numbered mark to every running traced game. Windows OK beep = mark recorded;
  error beep = no game being traced.
- **Modules:**
  - `cli.py` - commands, `--traces-dir`, `tracing_wanted`, `attach_app`
  - `app_glue.py` - the only wx module: starts the recorder (one UAC prompt), title, launch observer, mark key
  - `messages.py` - the UI and recorder messages (the commands in `cli.py`, `probe.py` and `check_trace_test.py`
    print their own text)
  - `client.py` - app side: starts the recorder, writes session files, records launches and marks
  - `recorder.py` - elevated side: drives Process Monitor for the whole run, then splits
  - `procmon.py` - Process Monitor's filter file, start, ready check, stop
  - `pml.py` - reads a capture into `ProcInfo` and `RawEvent` (lazy `procmon_parser`). `read_capture` swaps
    in `event_offsets` for procmon-parser's event offsets reader, which drops bits 32 to 39 of each offset and so
    cannot read a capture over 4 GiB
  - `live.py` - live process snapshot (Windows) used for roots and the running tree
  - `runfiles.py` - `RunDir`, `new_run`, `leftover_runs`; `splitrun.py` - split a run folder
  - `split.py` - attribute events to sessions and merge writes; `attribution.py` - the process tree
  - `kinds.py` - event to write kind; `virtualstore.py` - the VirtualStore path
  - `output.py` - writes `session.json` and `events.jsonl`; `model.py`, `clock.py`, `paths.py`
  - `probe.py` - the `--trace-probe` command
  - `check_trace_test.py` - `python -m tracing.check_trace_test <session folder>` checks a session of the
    trace test game (`tests/catalog_fixtures/games/trace-test`)
