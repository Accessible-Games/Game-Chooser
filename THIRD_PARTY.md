# Third-party tools

Game Chooser ships these programs next to the app (in `bin/`) and runs them as separate processes to unpack downloads.
They are not linked into Game Chooser. `scripts/fetch_tools.py` downloads pinned versions and checks their SHA256.
Their license texts are kept in `bin/licenses/`.

## 7-Zip 26.06

- Files: `7z.exe`, `7z.dll`
- License: GNU LGPL, with the unRAR license restriction on some code, and BSD 2-clause and 3-clause parts. See `bin/licenses/7-Zip-License.txt`.
- Source and downloads: https://www.7-zip.org/ (fetched from https://github.com/ip7z/7zip/releases/download/26.06/7z2606-x64.exe, the link on its download page)
- Copyright Igor Pavlov.

## innoextract 1.9

- File: `innoextract.exe`
- License: zlib (`bin/licenses/innoextract.LICENSE.txt`). The Windows zip also bundles liblzma (public domain), Boost (Boost Software License 1.0) and
  libstdc++ (GPLv3 with the GCC Runtime Library Exception); their texts are in `bin/licenses/`.
- Source and downloads: https://constexpr.org/innoextract/ (fetched from https://constexpr.org/innoextract/files/innoextract-1.9-windows.zip)
- Copyright Daniel Scharrer.

## Windows Installer (msiexec)

Part of Windows. Used for MSI admin extraction. Not shipped.

# Bundled in the app

The build compiles these into `game-chooser.exe` or ships them beside it. `build_windows.sh` copies their license
texts into `licenses/` at build time (`scripts/collect_licenses.py`).

- **Python** (`python313.dll`, the standard library, `.pyd` modules): Python Software Foundation License.
  `licenses/Python.txt`, which also holds the bzip2 license (`_bz2.pyd`).
- **OpenSSL** (`libcrypto-3-x64.dll`, `libssl-3-x64.dll`): Apache License 2.0. `licenses/Python-openssl-3.txt`.
- **libffi** (`libffi-8.dll`): MIT. `licenses/Python-libffi.txt`.
- **Expat** (`pyexpat.pyd`): MIT. `licenses/Python-expat.txt`.
- **mpdecimal** (`_decimal.pyd`): BSD 2-clause. `licenses/Python-mpdecimal.txt`.
- **liblzma** (`_lzma.pyd`): 0BSD. `licenses/Python-liblzma.txt`.
- **Microsoft Visual C++ runtime** (`vcruntime140.dll`, `vcruntime140_1.dll`, `msvcp140.dll`): Microsoft's
  redistributable code; the conditions are under "Additional Conditions for this Windows binary build" in
  `licenses/Python.txt`.
- **wxPython and wxWidgets** (`wx/`, `wx*.dll`): wxWindows Library Licence. `licenses/wxPython-LICENSE.txt`.
- **pywin32** (`win32*.pyd`, `win32com/`, `pythoncom313.dll`, `pywintypes313.dll`): Python Software Foundation
  License by its package metadata; its own notice is `licenses/pywin32.txt`.
- **frozendict**: GNU LGPL v3. `licenses/frozendict-LICENSE.txt` (the GPL v3 it adds to is `LICENSE.txt`).
- **six**: MIT. `licenses/six-LICENSE.txt`.
- **platform_utils**: MIT. `licenses/platform-utils-LICENSE.txt`.
- **platformdirs**: MIT. `licenses/platformdirs-LICENSE.txt`.
- **smart_list** (with `iat_hook32.dll`, `iat_hook64.dll`) and **resource_finder**: MIT, as their package metadata
  declares. Neither project publishes a license text, so none is shipped.
