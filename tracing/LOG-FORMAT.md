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

### Rename target (current state)

`new_path` comes from the rename event's details. Its decoder (Task 8) is waiting for the probe on the VM
(`--trace-probe`) to show what the real capture holds. Until then, rename lines may have `"new_path": null`, and
then there is no `new_virtual_store_path` either. The old name in `path` is always there.

## Known limits

These are `KNOWN_LIMITS` in `tracing/output.py`, word for word:

- Work a game hands to a process outside its own process tree (a COM server started by Windows, a service, the browser) is not attributed to the session.
- Writes made through memory-mapped files can be flushed by the System process and then are not attributed to the session.

## Orphans the recorder waits for

A note on games that leave a process behind (not one of the known limits above): after Game Chooser quits, the recorder keeps capturing while the
session's processes run. That includes an orphan: a process whose parent is gone, started after the launch, whose
program is inside the game's folder. Waiting for it does not make its writes part of the session: a process is
attributed only when its chain of parents, as the capture records them, reaches the game's first process.

## The run folder's split.json

The recorder's working files are in `traces/_runs/<YYYYMMDD-HHMMSS>-<pid>/`. After the capture is split, that
folder holds `split.json`: `folders` (the session folders written), `problems` (one sentence per session that
couldn't be recovered: a session with a `problem`, or a session file under `sessions/` that couldn't be read) and
`capture_kept`. When there are no problems, `capture_kept` is false and the capture files (`capture.pml`,
`capture-N.pml`) and `filter.pmc` are removed. When there is any problem, `capture_kept` is true and they stay, so
`--trace-split <run folder>` can split them again (the new session folders get the usual `-2` suffixes). A run with
`split.json` and no capture files left is already done: `--trace-split <run folder>` says so and does nothing.
`--trace-split` without a folder splits only runs that have no `split.json` yet.
