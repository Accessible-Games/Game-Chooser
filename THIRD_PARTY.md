# Third-party tools

Game Chooser ships these programs next to the app (in `bin/`) and runs them as separate processes to unpack downloads.
They are not linked into Game Chooser. `scripts/fetch_tools.py` downloads pinned versions and checks their SHA256.
Their license texts are kept in `bin/licenses/`.

## 7-Zip 26.06

- Files: `7z.exe`, `7z.dll`
- License: GNU LGPL, with the unRAR license restriction on some code, and BSD 2-clause and 3-clause parts. See `License.txt`.
- Source and downloads: https://www.7-zip.org/ (fetched from https://github.com/ip7z/7zip/releases/download/26.06/7z2606-x64.exe, the link on its download page)
- Copyright Igor Pavlov.

## innoextract 1.9

- File: `innoextract.exe`
- License: zlib. The Windows zip also bundles liblzma (public domain), Boost (Boost Software License 1.0) and
  libstdc++ (GPLv3 with the GCC Runtime Library Exception); their texts are in `bin/licenses/`.
- Source and downloads: https://constexpr.org/innoextract/ (fetched from https://constexpr.org/innoextract/files/innoextract-1.9-windows.zip)
- Copyright Daniel Scharrer.

## Windows Installer (msiexec)

Part of Windows. Used for MSI admin extraction. Not shipped.
