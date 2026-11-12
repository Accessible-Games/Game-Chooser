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
  `--trace-stop [RUN] [--close-games]` (end the run in progress while traced programs are still open; see Ending a
  run below), `-h`/`--help` beside any command (print the help and run nothing; alone it is not a tracing command),
  `--trace-probe OUT [--workload BAT]` (one short capture to learn what the real capture holds),
  `--trace-check FOLDER` (the trace-test acceptance check on a session folder, for a machine with only the compiled
  build: prints `PASS` or one problem per line; exit 0 pass, 1 problems, 2 not a session folder), `--traces-dir PATH`
  (use another traces folder). Exit codes: 2 for a bad command line. `--trace-split`: 0 done, 1 when a run
  couldn't be split or its split had a problem. `--trace-recorder`: 0, 1 when the recorder recorded a failure, 2
  when it could not start. `--trace-probe`: 0, 1 on a missing file, a declined administrator prompt or an error
  (started unelevated, it passes on the elevated probe's code). `--trace-stop`: 0 when the run ended cleanly or no
  run was in progress ("No tracing run is in progress."), or a named RUN had already ended ("already ended, nothing
  to do"); 1 on a problem (the recorder is gone, or didn't take the request within 30 s, a recorder failure, a
  split problem, a process `--close-games` could not close); 2 when more than one run is in progress and none is
  named, or RUN is not a run folder.
- **Ending a run (`--trace-stop`, `stop.py`):** the command runs unelevated and touches no process. It finds the
  run whose recorder is alive (`status.json`'s `recorder_pid` and `recorder_start`, so a pid given to another
  program doesn't count; `cli._recorder_is_alive`) and that has not ended, writes `stop-request.json` into its
  folder (atomic), then waits and prints what the recorder did (`Wrote <folder>`, `Problem: ...`). A status that
  can't be read for a moment is read again and never taken for the recorder's death. The
  recorder polls for the request in its loop (`Recorder._take_stop`): it writes `stop.json` (the sessions its live
  tree counts as running; rewritten after every process asked, ended or refused), and only then sets the status
  to `stopping` (the app reads the status first, so this order is what keeps the title change quiet; a test pins
  it), takes no more sessions, and goes on as at a normal end
  (Process Monitor stopped, capture split, `done`). The split marks those sessions `ended_by: "stop"`, and any
  other session the capture shows still running (`split.normalize_stop`, `split._ended`; LOG-FORMAT.md). A stop is
  not a failure and not a split problem. Once tracing is off, `TraceClient.mark` records nothing.
  - Recorder already gone: nothing is written; "the recorder is no longer running, so there is nothing to stop",
    plus the advice to run `--trace-split` when its capture is unsplit; exit 1. A dead run beside a live one is
    left to the live recorder, which splits leftovers at its end.
  - The recorder takes a request by renaming it to `stop-request.taken.json` (`RunDir.claim_stop_request`). No
    answer within `ACK_SECONDS` (30 s, counted only while the status says `capturing`): the command removes the
    request (`withdraw_stop_request`) and the run goes on. Rename and removal are atomic and exclusive, so the
    command reports a withdrawal only when its own removal succeeded; a request already gone was taken (it waits
    for the result), and one it can't remove is tried again at each poll. After the request is taken there is no
    time limit while the recorder lives.
  - A run that is already ending is waited for; its request is never overwritten (`write_stop_request` only
    creates: `runfiles._atomic_create`; every atomic write uses a temp file of its own).
  - A launch under way when the stop is taken: `TraceClient.game_launched` polls again before recording, so it
    writes no session. The instant between that poll and the write is not closed: such a session is split like
    any other and, if Process Monitor had already stopped, becomes a split problem (exit 1, capture kept).
  - `--close-games` (opt-in; without it no process is touched): the recorder, before stopping Process Monitor, asks
    each running process of those sessions to close (`closing.WindowsCloser.ask_close`: WM_CLOSE to its visible
    top-level windows), waits `CLOSE_WAIT_SECONDS` (15 s, less when all are gone), ends what is left (`end`:
    TerminateProcess after checking the start time on the opened handle), then waits `END_WAIT_SECONDS` (5 s).
    Candidates come from `LiveTree.running_processes()` for the sessions listed in `stop.json`, by (pid, start),
    minus the recorder's own pid, the app's pid (`protected_pids`), pids 0 and 4, a name in `EXCLUDED_PROCESSES`
    and a process with no readable path. That list is not a safeguard: the live tree is seeded from session files
    (and the request from a file) that any program of the user can write, while the recorder is elevated.
    **The safeguard is `closing.Closer`:** on the process handle it then acts on, after the start time, it
    refuses (`Refused`) any process whose token user is not the app's user, whose integrity level is above the
    app's, or whose token can't be read. The app's user and level (`Identity`) are read once, when the recorder
    is built, from the process named by `--app-pid` (`closing.process_identity`); if that fails, every process is
    refused. What this guarantees: `--close-games` closes nothing the user's own unelevated programs couldn't
    close themselves. What it does not: a session file can still name another unelevated program of the same
    user. The cost: a game started through the administrator prompt is not closed. A refused or protected
    process is left alone, not waited for, and listed in `not_closed` with its reason (exit code 1).
    **Never act on a process outside `Closer`, and never widen the limit.**
- **Where logs go:** `traces/` beside the exe (from source: beside `game-chooser.py`; git-ignored). One folder per
  play session, `<catalog id or title>-<YYYYMMDD-HHMMSS>`, holding `session.json` and `events.jsonl`. The recorder's
  working files live in `traces/_runs/<YYYYMMDD-HHMMSS>-<pid>/`: `status.json`, `sessions/<id>.json`, `filter.pmc`,
  `capture.pml` (and `capture-N.pml`), `recorder.log`, `stop-request.json` (`stop-request.taken.json` once taken)
  and `stop.json` (only for a run ended with `--trace-stop`), `split.json` (`folders`, `problems`, `capture_kept`,
  `stop`). The
  capture and `filter.pmc` are removed after a split with no problems, and kept (`capture_kept: true`) when any
  session has a problem or a session file can't be read. Atomic writes retry `os.replace` while Windows says the
  file is held (`runfiles.replace_with_retry`).
- **Attribution:** a session is the launched process plus its descendants by parent links in the capture. The root
  is matched by pid and the start time read after the launch; failing an exact match, by pid within 2 s of the launch
  call, closest to that start time (`attribution.find_root`). A launch that gave no pid (the administrator prompt's
  shell handed back no process) is recorded with `root_pid` 0 and no start time; its root is then the process whose
  image is the game's exe that started from 2 s before the launch call to 2 s after it returned, never one another
  session holds (pid-known sessions are matched first), preferring the earliest that started at or after the call,
  else the closest to it (`attribution.find_root_by_exe`; the live tree uses the same rule). A launch that gave a
  pid but whose start time couldn't be read: the live tree takes the process with that pid only if it started
  within the same 2 s window, and then keeps to that one process (a later program given the pid is never the
  root). `process_exit` lines
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
  games launch untraced after the player confirms. The one exception is a run ended with `--trace-stop`
  (`TraceState.announce` is False): the title turns to `(tracing off)` and no dialog opens from the timer; the
  next launch question starts with the reason (`messages.STOPPED`).
- **Mark key:** Ctrl+Alt+Shift+M adds a numbered mark to every running traced game. Windows OK beep = mark recorded;
  error beep = no game being traced.
- **Modules:**
  - `cli.py` - commands, `--traces-dir`, `tracing_wanted`, `attach_app`
  - `app_glue.py` - the only wx module: starts the recorder (one UAC prompt), title, launch observer, mark key
  - `messages.py` - the UI and recorder messages (the commands in `cli.py`, `probe.py` and `check_trace_test.py`
    print their own text)
  - `client.py` - app side: starts the recorder, writes session files, records launches and marks
  - `recorder.py` - elevated side: drives Process Monitor for the whole run, then splits; takes a stop request
  - `stop.py` - the `--trace-stop` command (unelevated): finds the run, writes the request, waits, reports
  - `closing.py` - `Closer` (the rules: start time, then who the process runs as) over `ProcessAccess`
    (`WindowsProcesses`: lazy ctypes), `WindowsCloser`, `Identity`, `process_identity`, the `not_closed` reasons
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
- **Tests of the stop:** `test_recorder_stop.py` (the recorder, with a fake machine and closer), `test_stop.py` (the
  command, and command plus recorder through the run folder's files), `test_closing_policy.py` (who the closer
  refuses, on fake processes), `test_closing.py` and
  `test_stop_real_processes.py` (marker `windows`: the real closer and snapshot on Python child processes of the
  test, whose windows are hidden or off-screen and can't be activated), `test_app_glue.py` (the glue's state
  handling without a running wx app; the only tracing test that imports wx, skipped when wxPython is missing).
