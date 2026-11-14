# Third-party tools

Game Chooser ships this program inside the app (in `Contents/MacOS/bin/`) and runs it as a separate process to
unpack 7z and cab downloads. It is not linked into Game Chooser. `scripts/fetch_tools.py` downloads a pinned
version and checks its SHA256. Its license text is in `Contents/MacOS/bin/licenses/`.

## 7-Zip 26.06

- File: `7zz`
- License: GNU LGPL, with the unRAR license restriction on some code, and BSD 2-clause and 3-clause parts. See `bin/licenses/7-Zip-License.txt`.
- Source and downloads: https://www.7-zip.org/ (fetched from https://github.com/ip7z/7zip/releases/download/26.06/7z2606-mac.tar.xz)
- Copyright Igor Pavlov.

## ditto

Part of macOS. Used to unpack zip downloads. Not shipped.

# Bundled in the app

The macOS build compiles these into `Game Chooser.app`. `build_macos.sh` copies their license texts into
`Contents/Resources/licenses/` at build time (`scripts/collect_licenses.py`).

- **Python** (the standard library and its extension modules): Python Software Foundation License.
  `licenses/Python.txt`, which also holds the bzip2 license.
- **OpenSSL**: Apache License 2.0. `licenses/Python-openssl-3.txt`.
- **libffi**: MIT. `licenses/Python-libffi.txt`.
- **Expat**: MIT. `licenses/Python-expat.txt`.
- **mpdecimal**: BSD 2-clause. `licenses/Python-mpdecimal.txt`.
- **liblzma**: 0BSD. `licenses/Python-liblzma.txt`.
- **wxPython and wxWidgets** (`wx/`, `libwx_*.dylib`): wxWindows Library Licence. `licenses/wxPython-LICENSE.txt`.
- **frozendict**: GNU LGPL v3. `licenses/frozendict-LICENSE.txt` (the GPL v3 it adds to is `LICENSE.txt`).
- **six**: MIT. `licenses/six-LICENSE.txt`.
- **platform_utils**: MIT. `licenses/platform-utils-LICENSE.txt`.
- **platformdirs**: MIT. `licenses/platformdirs-LICENSE.txt`.
- **smart_list** and **resource_finder**: MIT, as their package metadata declares. Neither project publishes a
  license text, so none is shipped.
