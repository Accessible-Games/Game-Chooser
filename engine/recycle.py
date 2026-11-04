"""Windows Recycle Bin access. Kept apart so the rest of the engine stays platform-neutral."""

from pathlib import Path

from . import messages
from .errors import EngineError


def send_to_recycle_bin(paths: list[Path]) -> None:
    if not paths:
        return
    from win32com.shell import shell, shellcon  # imported here so the module loads on any platform

    flags = (shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION | shellcon.FOF_SILENT
             | shellcon.FOF_NOERRORUI)
    names = "\0".join(str(Path(p).resolve()) for p in paths)
    result, _aborted = shell.SHFileOperation((0, shellcon.FO_DELETE, names, None, flags))
    if result != 0:
        raise EngineError(messages.RECYCLE_FAILED)
