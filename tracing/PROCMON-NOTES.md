# Process Monitor notes (from the VM probe)

Source: probe runs on the VM. Run 1 was inside an MSIX container (idle VM); run 4 was outside any container.

- **Version:** Process Monitor 4.13.0.0. procmon-parser 0.4.0 reads its PML files without error.
- **Filter:** the destructive filter works; the capture holds only `CAPTURE_OPERATIONS`.
- **Rates, before the capture filter was tightened:** run 1 25 MB/min; run 4 341 MB/min while Windows Update was
  busy (365,557 events, 687 bytes per event; CreateFile 312,571, WriteFile 21,650, SetDispositionInformationFile
  20,954, RegCreateKey 9,872). In the trace-test tree, 795 of 820 CreateFile events were read-only opens. The filter
  was then tightened (read-only opens, opened-existing keys, Delete: False, system processes); the new rate is not
  measured yet.
- **Root start:** the PML's process start minus `GetProcessTimes` start was 54,269 ticks (run 1) and 66,645 ticks
  (run 4), about 5-7 ms. Exact matching never hits; the launch-window fallback finds the root.
- **Readiness race:** `/WaitForIdle` checked once raced Process Monitor's startup (it failed 80 ms after start in
  runs 2 and 3). Fixed by a real bounded wait (932bf43); run 4 passed.
- **`del` in cmd** is a CreateFile (Desired Access: Delete, Disposition: Open, Options: Non-Directory File,
  Delete On Close), already classified as `delete`. It is not a SetDisposition event.
- **Process Monitor's own export** (`renames.csv`) shows the target: `SetRenameInformationFile ... "ReplaceIfExists:
  True, FileName: C:\Games\TraceTest\saves\slot1.sav"`, and `SetDispositionInformationEx ... "Flags:
  FILE_DISPOSITION_DELETE, FILE_DISPOSITION_POSIX_SEMANTICS"` (MoUsoCoreWorker.exe, outside the game tree).
- **Decision:** the rename target is decoded from the bytes (`tracing.pml.install_detail_handlers`).

## Rename record (real bytes, run 4)

It is in the event's `io` stream, not `extra_detail_io`:

| offset | size | field |
|---|---|---|
| 0 | u8 | ReplaceIfExists (bytes 1-7 are padding, ignore) |
| 8 | u64 | RootDirectory (0) |
| 16 | u32 | FileNameLength, in bytes |
| 20 | UTF-16LE | FileName, an NT path such as `\??\C:\Games\...` |

The decoder strips `\??\` and turns `\??\UNC\` into `\`. A non-zero RootDirectory is relative to a handle we cannot
resolve, so the name is kept as given.

`SetRenameInformationEx` and `SetRenameInformationExBypassAccessCheck` use a u32 Flags at offset 0 (ReplaceIfExists
is bit 0x1, in byte 0, so the decoder reads byte 0 for every form) with the same offsets after it. Confirmed by VM
probe run 5 (build 2) against Process Monitor's own decoding: a real `SetRenameInformationEx` had Flags u32 = 5
(REPLACE_IF_EXISTS | SUPPRESS_PIN_STATE_INHERITANCE), so `ReplaceIfExists` is "True", and its FileName decoded to the
Edge `Preferences` path. The bytes are in `tests/tracing/test_pml_decoders.py` (`REAL_RENAME_EX_IO`).

## SetDispositionInformationEx (confirmed by VM probe run 5, build 2)

A u32 Flags at the start of `io`. `FILE_DISPOSITION_DELETE` (0x1) set means `Delete` is "True", otherwise "False".
Confirmed by VM probe run 5 (build 2) against Process Monitor's own decoding: Flags 3 (DELETE | POSIX_SEMANTICS)
gives `Delete` "True". The bytes are in `tests/tracing/test_pml_decoders.py` (`REAL_DISPOSITION_EX_IO`).
