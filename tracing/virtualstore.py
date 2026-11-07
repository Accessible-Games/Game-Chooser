"""Where Windows redirects a virtualized (non-elevated, legacy 32-bit) process's writes under UAC VirtualStore."""

import re

_REGISTRY_PREFIX = re.compile(r"^HKLM\\SOFTWARE\\(.+)$", re.I)
_NOT_VIRTUALIZED = ("classes\\", "microsoft\\windows\\", "microsoft\\windows nt\\")
_FILE_PREFIX = re.compile(r"^[A-Za-z]:\\(?:Program Files|Program Files \(x86\)|Windows|ProgramData)\\", re.I)


def virtual_store_path(path: str, virtualized: bool, local_appdata: str) -> str | None:
    if not virtualized or "\\virtualstore\\" in path.lower():
        return None
    match = _REGISTRY_PREFIX.match(path)
    if match:
        rest = match.group(1)
        if rest.lower().startswith(_NOT_VIRTUALIZED):
            return None
        return "HKCU\\Software\\Classes\\VirtualStore\\MACHINE\\SOFTWARE\\" + rest
    if _FILE_PREFIX.match(path):
        return local_appdata.rstrip("\\") + "\\VirtualStore\\" + path[3:]
    return None
