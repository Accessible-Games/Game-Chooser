"""Registration-free COM: a private copy of a shared component, found through a manifest beside the exe.

When the player turns off "Register shared components system-wide", a COM runtime (such as
dx8vb.dll) is not registered with Windows. Instead a copy of the DLL sits beside the game's exe,
and `<exe>.manifest` names its classes and type library, so Windows finds them for that program
only. This can't work for an exe that embeds its own manifest (Windows then ignores the file
beside it); PrivateComUnavailable says so, and the game is told it needs the shared component.

pywin32 is imported inside the functions, so `import engine` works without it.
"""

import os
from pathlib import Path
from xml.sax.saxutils import quoteattr

from .errors import EngineError

# Set to False if private copies turn out not to work for the catalog's games: every COM runtime
# then gets the "needs the shared component" note instead.
PRIVATE_COM_SUPPORTED: bool = True

_RT_MANIFEST = 24


class PrivateComUnavailable(EngineError):
    """A private copy can't be made to work for this exe (internal; the player sees a note)."""


def has_embedded_manifest(exe: Path) -> bool:
    """True if `exe` carries its own manifest resource. False if it has none, or can't be read as a program."""
    try:
        import pywintypes
        import win32api
        import win32con
    except ImportError:
        return False
    try:
        handle = win32api.LoadLibraryEx(str(exe), 0, win32con.LOAD_LIBRARY_AS_DATAFILE)
    except pywintypes.error:
        return False
    try:
        return bool(win32api.EnumResourceNames(handle, _RT_MANIFEST))
    except pywintypes.error:
        return False
    finally:
        win32api.FreeLibrary(handle)


def read_com_info(dll: Path) -> tuple[list[dict], dict]:
    """The creatable classes ([{clsid, progid}]) and type library ({tlbid, version}) a COM DLL describes.

    The ProgID is "<library name>.<class name>", the name Visual Basic registers it under.
    Raises pythoncom.com_error if the file has no type library.
    """
    import pythoncom
    tlb = pythoncom.LoadTypeLib(str(dll))
    tlbid, _lcid, _syskind, major, minor, _flags = tlb.GetLibAttr()
    library = tlb.GetDocumentation(-1)[0]
    classes = []
    for i in range(tlb.GetTypeInfoCount()):
        if tlb.GetTypeInfoType(i) != pythoncom.TKIND_COCLASS:
            continue
        attr = tlb.GetTypeInfo(i).GetTypeAttr()
        if attr.wTypeFlags & pythoncom.TYPEFLAG_FCANCREATE:
            classes.append({"clsid": str(attr.iid), "progid": f"{library}.{tlb.GetDocumentation(i)[0]}"})
    return classes, {"tlbid": str(tlbid), "version": f"{major}.{minor}"}


def com_manifest_xml(dlls: list[tuple[str, list[dict], dict]]) -> str:
    """An application manifest naming each DLL's classes and type library: (file name, classes, typelib)."""
    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">',
             '  <assemblyIdentity type="win32" name="GameChooser.PrivateCOM" version="1.0.0.0"/>']
    for name, classes, typelib in dlls:
        tlbid = quoteattr(typelib["tlbid"])
        lines.append(f"  <file name={quoteattr(name)}>")
        for c in classes:
            progid = f" progid={quoteattr(c['progid'])}" if c.get("progid") else ""
            lines.append(f'    <comClass clsid={quoteattr(c["clsid"])}{progid} threadingModel="Apartment" '
                         f"tlbid={tlbid}/>")
        lines.append(f'    <typelib tlbid={tlbid} version={quoteattr(typelib["version"])} helpdir=""/>')
        lines.append("  </file>")
    lines.append("</assembly>")
    return "\n".join(lines) + "\n"


def write_private_com(game_dir: Path, exe: Path, dlls: list[Path], reader=read_com_info) -> None:
    """Write `<exe>.manifest` so `exe` finds the COM classes in `dlls` (which must sit beside it).

    Raises PrivateComUnavailable, with nothing written, if the exe embeds a manifest, already has one
    beside it, or a DLL isn't beside it or describes no creatable class.
    """
    if not PRIVATE_COM_SUPPORTED:
        raise PrivateComUnavailable("private COM is turned off")
    exe = Path(exe)
    if not exe.resolve().is_relative_to(Path(game_dir).resolve()):
        raise PrivateComUnavailable(f"{exe} is outside {game_dir}")
    manifest = exe.with_name(exe.name + ".manifest")
    if os.path.lexists(manifest):
        raise PrivateComUnavailable(f"{manifest.name} already exists")
    if has_embedded_manifest(exe):
        raise PrivateComUnavailable(f"{exe.name} embeds a manifest")
    described = []
    for dll in dlls:
        dll = Path(dll)
        if os.path.normcase(dll.parent.resolve()) != os.path.normcase(exe.parent.resolve()):
            raise PrivateComUnavailable(f"{dll.name} isn't beside {exe.name}")
        try:
            classes, typelib = reader(dll)
        except Exception as e:
            raise PrivateComUnavailable(f"{dll.name} has no readable type library") from e
        if not classes:
            raise PrivateComUnavailable(f"{dll.name} describes no creatable class")
        described.append((dll.name, classes, typelib))
    try:
        manifest.write_text(com_manifest_xml(described), encoding="utf-8")
    except OSError as e:
        try:
            manifest.unlink(missing_ok=True)
        except OSError:
            pass
        raise PrivateComUnavailable(f"{manifest.name} couldn't be written") from e
