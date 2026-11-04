"""Reading and writing the registry values a game's setup steps ask for.

HKCU is changed directly. HKLM is read through the 32-bit view (the games are 32-bit) and
written by an elevated reg.exe, because Game Chooser itself never runs as administrator.
"""

import re

from . import messages
from .elevation import Elevator
from .errors import StepError

HIVES = ("HKCU", "HKLM")
# Types `set` can write; also the types a previous value must have for undo to put it back.
WRITABLE_TYPES = ("REG_SZ", "REG_EXPAND_SZ", "REG_DWORD", "REG_QWORD")
_TEXT_TYPES = ("REG_SZ", "REG_EXPAND_SZ")
_INT_LIMITS = {"REG_DWORD": 2 ** 32, "REG_QWORD": 2 ** 64}
_TYPE_NAMES = ("REG_NONE", "REG_SZ", "REG_EXPAND_SZ", "REG_BINARY", "REG_DWORD", "REG_DWORD_BIG_ENDIAN",
               "REG_LINK", "REG_MULTI_SZ", "REG_RESOURCE_LIST", "REG_FULL_RESOURCE_DESCRIPTOR",
               "REG_RESOURCE_REQUIREMENTS_LIST", "REG_QWORD")
_UNSAFE_TEXT = re.compile(r'["\r\n\0]')


def _coerce(type_: str, value: object) -> str | int:
    if type_ not in WRITABLE_TYPES:
        raise StepError(messages.REGISTRY_BAD_SETTING)
    if type_ in _TEXT_TYPES:
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            raise StepError(messages.REGISTRY_BAD_SETTING)
        return str(value)
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise StepError(messages.REGISTRY_BAD_SETTING)
    try:
        number = int(value)
    except ValueError:
        raise StepError(messages.REGISTRY_BAD_SETTING) from None
    if not 0 <= number < _INT_LIMITS[type_]:
        raise StepError(messages.REGISTRY_BAD_SETTING)
    return number


def _reg_quote(text: str) -> str:
    """Quote one reg.exe argument. Callers have already refused quotes and newlines.

    Backslashes just before the closing quote are doubled, or the last one would escape it.
    """
    trailing = len(text) - len(text.rstrip("\\"))
    return f'"{text}{"\\" * trailing}"'


class RegistryAccess:
    def __init__(self, elevator: Elevator, hkcu=None):
        if hkcu is None:
            import winreg as hkcu
        self._winreg = hkcu  # Used for every HKCU call and for HKLM reads.
        self._elevator = elevator

    def _type_name(self, type_: int) -> str:
        for name in _TYPE_NAMES:
            if getattr(self._winreg, name, None) == type_:
                return name
        return f"REG_TYPE_{type_}"

    def get(self, hive: str, key: str, name: str) -> tuple[bool, object, str | None]:
        """(exists, value, type name) of one value; (False, None, None) when it or its key is missing."""
        w = self._winreg
        if hive == "HKCU":
            root, access = w.HKEY_CURRENT_USER, w.KEY_READ
        elif hive == "HKLM":
            root, access = w.HKEY_LOCAL_MACHINE, w.KEY_READ | w.KEY_WOW64_32KEY
        else:
            raise StepError(messages.REGISTRY_BAD_SETTING)
        try:
            with w.OpenKey(root, key, 0, access) as handle:
                value, type_ = w.QueryValueEx(handle, name)
        except FileNotFoundError:
            return False, None, None
        return True, value, self._type_name(type_)

    def can_restore(self, hive: str, type_: str | None, value: object) -> bool:
        """True if `set` could later write this value back exactly as it is now."""
        if hive not in HIVES:
            return False
        try:
            coerced = _coerce(type_, value)
        except StepError:
            return False
        # HKLM goes through reg.exe, which can't be given quotes or newlines.
        return hive != "HKLM" or not _UNSAFE_TEXT.search(str(coerced))

    def set(self, hive: str, key: str, name: str, type_: str, value: object) -> None:
        coerced = _coerce(type_, value)
        if hive == "HKCU":
            w = self._winreg
            with w.CreateKeyEx(w.HKEY_CURRENT_USER, key, 0, w.KEY_SET_VALUE) as handle:
                w.SetValueEx(handle, name, 0, getattr(w, type_), coerced)
        elif hive == "HKLM":
            self._reg_exe("add", key, name, f" /t {type_} /d {_reg_quote(self._safe(str(coerced)))}")
        else:
            raise StepError(messages.REGISTRY_BAD_SETTING)

    def delete(self, hive: str, key: str, name: str) -> None:
        """Remove one value. A value that is already gone is not an error."""
        if hive == "HKCU":
            w = self._winreg
            try:
                with w.OpenKey(w.HKEY_CURRENT_USER, key, 0, w.KEY_SET_VALUE) as handle:
                    w.DeleteValue(handle, name)
            except FileNotFoundError:
                pass
        elif hive == "HKLM":
            if self.get(hive, key, name)[0]:  # reg.exe fails on a missing value; also spares a UAC prompt.
                self._reg_exe("delete", key, name, "")
        else:
            raise StepError(messages.REGISTRY_BAD_SETTING)

    @staticmethod
    def _safe(text: str) -> str:
        if _UNSAFE_TEXT.search(text):
            raise StepError(messages.REGISTRY_UNSAFE_TEXT)
        return text

    def _reg_exe(self, verb: str, key: str, name: str, rest: str) -> None:
        """Run elevated reg.exe on HKLM's 32-bit view. ElevationDeclined passes through untouched."""
        full_key = f"HKLM\\{self._safe(key).strip('\\')}"
        value_arg = "/ve" if name == "" else f"/v {_reg_quote(self._safe(name))}"
        args = f"{verb} {_reg_quote(full_key)} {value_arg}{rest} /f /reg:32"
        if self._elevator.run("reg.exe", args, True) != 0:
            raise StepError(messages.REGISTRY_WRITE_FAILED.format(key=full_key))
