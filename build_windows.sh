#!/bin/bash
# Build script for Game Chooser Windows executable using Nuitka
# Run this from WSL with: bash build_windows.sh
#
# The built app extracts downloads with the bundled tools in bin/ (7-Zip and innoextract, fetched by
# scripts/fetch_tools.py; bin/ is not committed). engine/extract.py looks for them in the bin/ folder beside
# the executable, so this script ships bin/ (with the tools' licenses) and THIRD_PARTY.md next to it.
# Without them every zip, 7z, cab, Inno and NSIS install would fail.

set -u

TOOLS=(bin/7z.exe bin/7z.dll bin/innoextract.exe)

fetch_tools_if_missing() {
    local missing=0
    for tool in "${TOOLS[@]}"; do
        if [ ! -f "$tool" ]; then
            missing=1
        fi
    done
    if [ ! -d bin/licenses ]; then
        missing=1
    fi
    if [ "$missing" -eq 1 ]; then
        echo "Extraction tools missing from bin/; fetching them..."
        uv run python scripts/fetch_tools.py || return 1
    fi
    for tool in "${TOOLS[@]}"; do
        if [ ! -f "$tool" ]; then
            echo "Missing $tool after fetch_tools.py; cannot ship a build without it."
            return 1
        fi
    done
    if [ ! -d bin/licenses ] || [ -z "$(ls -A bin/licenses)" ]; then
        echo "bin/licenses is missing or empty; the tools can't ship without their licenses."
        return 1
    fi
}

# ship_tools DIST: copy bin/ (tools and licenses) and THIRD_PARTY.md into the built app's folder.
ship_tools() {
    local dist="$1"
    if [ ! -d "$dist" ]; then
        echo "Build folder $dist not found."
        return 1
    fi
    rm -rf "$dist/bin"
    mkdir -p "$dist/bin/licenses"
    for tool in "${TOOLS[@]}"; do
        cp "$tool" "$dist/bin/" || return 1
    done
    cp bin/licenses/* "$dist/bin/licenses/" || return 1
    cp THIRD_PARTY.md "$dist/" || return 1
    for tool in "${TOOLS[@]}"; do
        if [ ! -f "$dist/$tool" ]; then
            echo "$dist/$tool was not shipped."
            return 1
        fi
    done
    echo "Shipped bin/ ($(ls "$dist/bin" | grep -c -v '^licenses$') tools, $(ls "$dist/bin/licenses" | wc -l) license files) and THIRD_PARTY.md"
}

# `bash build_windows.sh --ship-only DIST` only copies the tools into an existing build folder (a dry run).
if [ "${1:-}" = "--ship-only" ]; then
    fetch_tools_if_missing || exit 1
    ship_tools "${2:?usage: build_windows.sh --ship-only DIST}" || exit 1
    exit 0
fi

echo "Building Game Chooser for Windows..."

fetch_tools_if_missing || exit 1

# Clean the previous build (only Nuitka's own folders: build/ may hold other things)
if [ -d "build/game-chooser.dist" ] || [ -d "build/game-chooser.build" ]; then
    echo "Cleaning previous build..."
    rm -rf build/game-chooser.dist build/game-chooser.build
fi

# smart_list and resource_finder are submodules whose packages sit one folder down (smart_list/smart_list/,
# resource_finder/resource_finder.py). game_list.py and dialogs.py put those folders on sys.path at run time, which
# Nuitka never sees, so it would compile the outer folder as an empty namespace package and the exe would fail to
# start ("cannot import name 'VirtualSmartList'"). Put them on the path for the build, and include them by name.
# WSLENV passes PYTHONPATH through to uv.exe when this runs from WSL.
export PYTHONPATH="smart_list;resource_finder"
export WSLENV="${WSLENV:+$WSLENV:}PYTHONPATH"

# Run Nuitka to build the executable (nuitka isn't a project dependency; uv supplies it for this run)
uv run --with nuitka python -m nuitka \
    --standalone \
    --windows-console-mode=disable \
    --follow-imports \
    --include-package=engine \
    --include-module=resource_finder \
    --report=build/compilation-report.xml \
    --include-data-files=smart_list/smart_list/iat_hook32.dll=iat_hook32.dll \
    --include-data-files=smart_list/smart_list/iat_hook64.dll=iat_hook64.dll \
    --assume-yes-for-downloads \
    --output-dir=build \
    game-chooser.py

if [ $? -eq 0 ]; then
    ship_tools build/game-chooser.dist || exit 1
    echo ""
    echo "Build completed successfully!"
    echo "Executable location: build/game-chooser.dist/game-chooser.exe"
    echo ""
    echo "To distribute, copy the entire 'build/game-chooser.dist' folder (it includes bin/ and THIRD_PARTY.md)"
else
    echo ""
    echo "Build failed! Check the output above for errors."
    exit 1
fi
