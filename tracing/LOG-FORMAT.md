# Tracing log format

One folder per play session under `traces/` beside the exe: `<catalog id or title>-<YYYYMMDD-HHMMSS>` (local
time of the launch; the id or title is made safe for a folder name: anything but letters, digits, `.`, `_` and `-`
becomes `-`). If the name exists, `-2`, `-3` and so on are added. Each folder holds `session.json` and
`events.jsonl`. Both are UTF-8. `session.json` has `"format": 1`.

A play session is one launch of a game: the launched process and every process it starts, followed through parent
links. Only events from those processes are kept.

Process Monitor drops these events while capturing (the filter is destructive: they never reach the file), and the
recorder writes the active filter to `recorder.log`. What a trace therefore cannot hold:

- Any event of these processes, and every process started under them (their process events are gone, so a game's tree
  cannot pass through one): `Procmon.exe`, `Procmon64.exe`, `System`, `game-chooser.exe` (also the recorder), `7z.exe`,
  `innoextract.exe`, `svchost.exe`, `MsMpEng.exe`, `MpDefenderCoreService.exe`, `NisSrv.exe`, `MoUsoCoreWorker.exe`,
  `TiWorker.exe`, `TrustedInstaller.exe`, `SearchIndexer.exe`, `SearchProtocolHost.exe`, `SearchFilterHost.exe`,
  `CompatTelRunner.exe`, `wuauclt.exe`, `UsoClient.exe`, `sppsvc.exe`, `lsass.exe`, `services.exe`, `csrss.exe`,
  `smss.exe`, `wininit.exe`, `winlogon.exe`, `Registry`, `MemCompression`, `SgrmBroker.exe`,
  `SecurityHealthService.exe`, `WmiPrvSE.exe`, `dllhost.exe`, `taskhostw.exe`, `claude.exe`, `nvda.exe`. A game whose
  own tree runs a program of one of those names loses that part. `explorer.exe`, `conhost.exe`, `cmd.exe`,
  `powershell.exe`, `python.exe` and `rundll32.exe` are kept because games and launchers run them.
- A CreateFile that opens an existing file with only one of these Desired Access values and Disposition Open (no
  write, append, delete, Generic All or Maximum Allowed right, so no write kind is lost): `Read Attributes`,
  `Generic Read`, `Read Control`, `Read Data/List Directory, Synchronize`,
  `Read Data/List Directory, Execute/Traverse, Synchronize`, `Execute/Traverse, Synchronize`,
  `Read Data/List Directory, Execute/Traverse, Read Attributes, Synchronize`, `Synchronize`,
  `Read Attributes, Synchronize`, `Generic Read/Execute`, `Generic Execute`, `Read EA`,
  `Read Attributes, Read Control, Synchronize`, `Read Control, Synchronize`,
  `Read Data/List Directory, Read Attributes, Synchronize`, `Read Data/List Directory`. An access string not listed
  is kept, so a delete-on-close open (Desired Access `Delete`) is still recorded.
- RegCreateKey events whose Detail has `Disposition: REG_OPENED_EXISTING_KEY`, and disposition events with Detail
  `Delete: False` (neither is a write kind).
- Reads were never recorded.

## Times

Every time comes from Process Monitor's clock: FILETIME, 100 ns ticks since 1601-01-01 UTC. In the files:

- `t`, `start_t`, `end_t`: seconds (float, 6 places) since the session's time zero.
- `start`, `end`, `time`: UTC text, `YYYY-MM-DDTHH:MM:SS.ffffffZ`.

Time zero is the start of the game's first process (`time_zero_source` is `"process_start"`). When that process isn't
in the capture, `time_zero_source` is `"launch_call"` (the moment Game Chooser called launch) and `problem` says so.

The game's first process is found in the capture by its pid and the start time Game Chooser read right after
launching it. If no process has exactly that start time, it is the process with that pid that started within 2 s
of the launch call, the one whose start is closest to the start time read (or to the launch call when none was
read).

When the launch gave Game Chooser no pid (a game marked "run as administrator" is started through Windows' prompt,
and the shell can hand back no process), the first process is found by its executable instead: the process whose
image path is the game's `exe` (compared case-insensitively, after normalizing the path) that started from 2 s
before the launch call to 2 s after the call returned, and that is not already part of another session (sessions
whose pid is known are matched first). Of those, the earliest that started at or after the launch call is taken
(the real process starts after the call, once the prompt is accepted); only when none did, the one whose start is
closest to the call. A process of the same exe that started outside that window is never taken.

## session.json

- `format`: 1.
- `session_id`: id of this launch.
- `game`: `title`, `catalog_id` (null for a game added by hand), `library_id`, `launch_path`, `exe` (full path
  launched), `game_dir` (its working folder).
- `env`: `windows` (platform text), `local_appdata`, `user_profile`, `app_exe`, `app_exe_mtime`, `app_build`,
  `compiled`. `app_build` is the short git commit of the source Game Chooser ran from (`git rev-parse --short
  HEAD`), or null when git couldn't say; a compiled build has null there, and `app_exe_mtime` identifies it.
- `start`: time zero as UTC text. `end`: when the last process exited, or null.
- `still_running_at_capture_end`: true when some process was still running when the capture ended (then `end` is
  null; the processes that did exit still have their `process_exit` lines).
- `ended_by`: how the session ended.
  - `"exit"`: every process of the session exited by itself (`end` is the last exit).
  - `"stop"`: the run was ended with `--trace-stop` while this session was still going: the recorder counted it as
    running when it took the request, or the capture shows a process of it still running at the end. `stop` says
    more.
  - `"capture_end"`: a process was still running when the capture ended and the run was not stopped with
    `--trace-stop` (the recorder failed or was killed, or the capture is being split after a crash).
  - null: the game's first process isn't in the capture (see `problem`) and no stop named the session.
- `stop`: null unless `ended_by` is `"stop"`. Then an object:
  - `time` (UTC text) and `t`: when the recorder took the stop request. Events after it are still recorded, up to
    the moment Process Monitor stopped.
  - `close_games`: true when the stop was asked to close the traced games (`--close-games`). When false the three
    lists below are empty, no process was touched, and the processes without an `end` in `processes` are the ones
    left running.
  - `asked_to_close`: pids that had a visible window and were asked to close it (WM_CLOSE).
  - `ended`: pids that were still running after the wait and were ended by force (TerminateProcess). A program
    ended this way had no chance to save, so its last writes may be missing from the trace because they were never
    made.
  - `not_closed`: one `{"pid", "reason"}` per process of the session still running after all that. `reason` is a
    short sentence: it runs as another user, it runs with more rights than Game Chooser (a game started through
    the administrator prompt), who it runs as couldn't be read, a stop never closes it, it could not be ended,
    Process Monitor stopped first, or this recorder can't close games. See "Ending a run with --trace-stop".
  - A session the recorder did not count as running (a game launched after the request was taken, or one whose
    processes its live view missed) has three empty lists even with `close_games` true: it was not touched. The
    lists are also short when the recorder died while closing (see "Ending a run with --trace-stop").
  - With `close_games` true and everything closed, `end` is set and `still_running_at_capture_end` is false, as for
    a game that exited by itself; `ended_by` is what tells the two apart.
- `time_zero_source`: see Times. `root_pid`: pid of the game's first process (as found in the
  capture; when it wasn't found, the pid recorded at launch, which is 0 when the launch gave no pid).
- `processes`: one object per process in the session: `pid`, `parent_pid`, `image_path`, `command_line`, `start`,
  `end` (null when still running), `start_t`, `end_t`, `virtualized` (UAC virtualization on for this process),
  `is_64bit`.
- `marks`: `{"n", "t", "time"}` for each mark made with Ctrl+Alt+Shift+M, numbered from 1.
- `event_count`: number of lines in `events.jsonl` that are writes (not `process_start`, `process_exit` or `mark`).
- `problem`: null, or a sentence saying why the session could not be attributed.
- `known_limits`: the list under Known limits, so the file carries it.

## events.jsonl

One JSON object per line, sorted by `t`. At the same `t`, order is `process_start`, writes, `mark`, `process_exit`.
Every line has `t` (number) and `op` (string).

### Lines that are not writes

- `process_start`: `pid`, `parent_pid`, `process` (file name), `image_path`, `command_line`.
- `process_exit`: `pid`, `process`. Written for every process whose exit is in the capture, even while other
  processes of the session are still running.
- `mark`: `n` (the mark's number, as in `session.json`).

### Write lines

Every write line has `process` (file name of the program), `pid`, `path` and `result` (Process Monitor's result
text, such as `SUCCESS` or `ACCESS DENIED`). Failed writes are kept. `path` is a file path or a registry path.
Extra fields by kind:

- `file_write`: `bytes` (total length written), `count` (how many writes were merged), and `t_end` when more than
  one was merged.
- `rename`: `new_path` (see the note below).
- `reg_set_value`: `value_type` (such as `REG_SZ`) and `value_data`: text, or hex for binary; a list is joined
  with newlines; at most 256 characters.
- Any write on a virtualized process that Windows redirects: `virtual_store_path` (see below). A rename can also have
  `new_virtual_store_path`.

### Write kinds and the Process Monitor operations that make them

Exactly these nine kinds:

- `file_create`: `CreateFile` that created a file (open result `Created`); or a failed `CreateFile` whose
  disposition was Create, Overwrite, OverwriteIf or Supersede (or OpenIf with write or append access).
- `folder_create`: `CreateFile` that created a folder (open result `Created`, option `Directory File`).
- `file_write`: `WriteFile`; or `CreateFile` that overwrote or superseded a file (open result `Overwritten` or
  `Superseded`).
- `rename`: `SetRenameInformationFile`, `SetRenameInformationEx`, `SetRenameInformationExBypassAccessCheck`.
  `path` is the old name.
- `delete`: `SetDispositionInformationFile` or `SetDispositionInformationEx` with Delete set; or a successful
  `CreateFile` with `Delete On Close`.
- `reg_create_key`: `RegCreateKey` that made a new key (`REG_CREATED_NEW_KEY`), or that failed.
- `reg_set_value`: `RegSetValue`.
- `reg_delete_value`: `RegDeleteValue`.
- `reg_delete_key`: `RegDeleteKey`.

Events with result `FAST IO DISALLOWED` or `REPARSE` are Windows retrying the same call, so they are dropped.
A `CreateFile` that only opened something existing is not a write and is not kept.

### The merge rule

Writes are merged only when two `file_write` lines are next to each other in the sorted lines, and have the same
`pid`, `path` and `result`. The first line stays, its `count` and `bytes` are added up, and its `t_end` is the
time of the last one merged. Nothing else is merged: a different process, file or result, or any other line in
between, starts a new line.

### virtual_store_path

A non-elevated, 32-bit style (legacy) process can be virtualized: Windows quietly sends its writes under
`C:\Program Files`, `C:\Program Files (x86)`, `C:\Windows`, `C:\ProgramData` or `HKLM\SOFTWARE` to the player's
VirtualStore instead. `path` stays what the game asked for. `virtual_store_path` says where the data really is:

- a file: `<LOCALAPPDATA>\VirtualStore\<rest of the path after the drive letter>`.
- a registry key: `HKCU\Software\Classes\VirtualStore\MACHINE\SOFTWARE\<rest>`. Keys under `Classes\`,
  `Microsoft\Windows\` and `Microsoft\Windows NT\` are not virtualized.

The field is absent when the process is not virtualized, the path is not in those places, or it is already inside
a VirtualStore.

### Rename target

`new_path` comes from the rename event's details, decoded from the capture's bytes (see `PROCMON-NOTES.md`).
It is a Win32 path. It is `null` only if the record was unreadable, and then there is no `new_virtual_store_path`
either. The old name in `path` is always there.

## Known limits

These are `KNOWN_LIMITS` in `tracing/output.py`, word for word:

- Work a game hands to a process outside its own process tree (a COM server started by Windows, a service, the browser) is not attributed to the session.
- Writes made through memory-mapped files can be flushed by the System process and then are not attributed to the session.

## Orphans the recorder waits for

A note on games that leave a process behind (not one of the known limits above): after Game Chooser quits, the recorder keeps capturing while the
session's processes run. That includes an orphan: a process whose parent is gone, started after the launch, whose
program is inside the game's folder. Waiting for it does not make its writes part of the session: a process is
attributed only when its chain of parents, as the capture records them, reaches the game's first process.

A run that waits on a process nobody is going to close (a helper program, a Windows prompt) is ended with
`--trace-stop`.

## Ending a run with --trace-stop

`game-chooser.exe --trace-stop [RUN] [--close-games]` ends the run in progress while traced programs are still open.
The command runs without administrator rights and touches no process itself. It and the recorder talk through two
files in the run folder (both written whole, then moved into place):

- `stop-request.json`, written by the command: `requested` (FILETIME), `close_games` (true or false) and
  `requester_pid`. The recorder looks for it every half second while it is capturing, and takes it by renaming it
  to `stop-request.taken.json`. If the recorder has not answered after 30 seconds of capturing, the command removes
  the request and says it was withdrawn; the run goes on. The rename and the removal can't both succeed, so a
  request is either taken or withdrawn, never both: when the command finds the request already gone it waits for
  the result instead, and when it can't remove the file it keeps waiting and tries again.
- `stop.json`, written by the recorder when it takes the request: `requested` (copied from the request), `time`
  (FILETIME, when it was taken), `close_games`, and `sessions`: one entry per session the recorder counted as
  running at that moment, each with the lists `asked_to_close`, `ended` and `not_closed` (see `stop` under
  session.json). With `--close-games` it is written again after every process asked, ended or refused, so it
  says what has been done even if the recorder dies in the middle; the full `not_closed` lists are written when the
  closing is over. So in a run whose recorder died while closing (`status.json` never reached `done`), a pid in
  none of the lists was not touched yet, and may or may not still be running. A request the recorder never took
  (no `stop.json`) is not a stop.

From the moment the request is taken, `status.json` says `stopping` (Game Chooser's title turns to `(tracing off)`
without a dialog; the next launch says why and asks whether to launch untraced), no new launch joins the run, and
the recorder goes on as at a normal end: Process Monitor is stopped, the capture is split, `status.json` says `done`.
A stop is not a problem: it does not keep the capture.

"Running" is the recorder's live view (the launched process, its descendants, and orphans inside the game's folder:
see the section above), which can hold a process the capture does not attribute to the session. So a session can
have `ended_by` `"stop"` while every process the capture gave it has an `end`.

With `--close-games`, before Process Monitor is stopped (so the writes games make while closing are recorded):

1. Every running process of those sessions is asked to close: WM_CLOSE to each of its visible top-level windows. A
   process with no visible window is not asked.
2. The recorder waits up to 15 seconds, checking every half second; it goes on as soon as all of them are gone.
3. What is still running is ended (TerminateProcess), each process only if its pid still has the same start time.
   Then up to 5 seconds for them to be gone.

A process is asked or ended only if it runs as Game Chooser's user with no more rights than Game Chooser (the
same user in its token, and an integrity level no higher). The recorder reads both from Game Chooser's own process
when it starts, and checks each process right before touching it. This is what keeps the recorder, which has
administrator rights and takes its list of sessions from files any program of the user can write, from closing
anything those programs couldn't close themselves. **The cost: a game started through the administrator prompt is
not closed by `--close-games`.** It is left running and listed in `not_closed` with the reason, and the command
exits with 1. Processes of another user and Windows' own services are refused the same way. If Game Chooser had
already gone when the recorder started, nothing is closed.

Also never asked or ended, even if a session's tree holds them: the recorder itself, Game Chooser, pids 0 and 4, any
program on the list of processes the capture drops (first list at the top of this file), and a process whose
program path can't be read. They show up in `not_closed` too. Nothing in `not_closed` is waited for. A game
launched after the request was taken is not part of the stop. If Process Monitor dies during the wait, nothing is
ended.

What the live view holds, and so what `--close-games` closes, is everything a traced game started that is still
running: its helpers, the console host (`conhost.exe`) Windows gives a console program, a browser or an editor the
game opened as its own child process, and orphans inside the game's folder.

After a stop the mark key records nothing (the error beep), even for a game that is still open.

## The run folder's split.json

The recorder's working files are in `traces/_runs/<YYYYMMDD-HHMMSS>-<pid>/`. After the capture is split, that
folder holds `split.json`: `folders` (the session folders written), `problems` (one sentence per session that
couldn't be recovered: a session with a `problem`, or a session file under `sessions/` that couldn't be read),
`capture_kept` and `stop`. `stop` is null for a run that ended by itself; for a run ended with `--trace-stop` it is
`requested` and `time` (UTC text; `requested` is null when the request didn't carry one), `close_games` and
`sessions` (the ids of the sessions that were running, sorted).

When there are no problems, `capture_kept` is false and the capture files (`capture.pml`, `capture-N.pml`) and
`filter.pmc` are removed. When there is any problem, `capture_kept` is true and they stay, so
`--trace-split <run folder>` can split them again (the new session folders get the usual `-2` suffixes). A run with
`split.json` and no capture files left is already done: `--trace-split <run folder>` says so and does nothing.
`--trace-split` without a folder splits only runs that have no `split.json` yet.
