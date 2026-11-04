"""Windows Recycle Bin access. Kept apart so the rest of the engine stays platform-neutral.

Files only ever go to the Recycle Bin. FOF_ALLOWUNDO alone recycles "if possible" and otherwise deletes
for good, so two guards are added: every path's volume must be a fixed drive with a working Bin, and
FOF_WANTNUKEWARNING makes the shell abort (instead of silently deleting) for anything it cannot recycle,
such as a file bigger than the Bin allows.
"""

import os
from pathlib import Path

from . import messages
from .errors import EngineError


def _volume_root(path: Path) -> str:
    return os.path.splitdrive(str(path))[0] + "\\"


def send_to_recycle_bin(paths: list[Path]) -> None:
    if not paths:
        return
    import win32file  # imported here so the module loads on any platform
    from win32com.shell import shell, shellcon

    absolute = [Path(p).absolute() for p in paths]  # absolute(), not resolve(): a link is recycled, not its target
    for root in {_volume_root(p) for p in absolute}:
        if root == "\\" or win32file.GetDriveType(root) != win32file.DRIVE_FIXED:
            raise EngineError(messages.RECYCLE_FAILED)
        try:
            shell.SHQueryRecycleBin(root)
        except Exception as exc:
            raise EngineError(messages.RECYCLE_FAILED) from exc

    flags = (shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION | shellcon.FOF_WANTNUKEWARNING
             | shellcon.FOF_SILENT | shellcon.FOF_NOERRORUI)
    names = "\0".join(str(p) for p in absolute)
    result, aborted = shell.SHFileOperation((0, shellcon.FO_DELETE, names, None, flags))
    if result != 0 or aborted:
        raise EngineError(messages.RECYCLE_FAILED)
