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
