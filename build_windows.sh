#!/bin/bash
# Build script for Game Chooser Windows executable using Nuitka
# Run this on Windows from Git Bash with: bash build_windows.sh
#
# The built app extracts downloads with the bundled tools in bin/ (7-Zip and innoextract, fetched by
# scripts/fetch_tools.py; bin/ is not committed). engine/extract.py looks for them in the bin/ folder beside
# the executable, so this script ships bin/ (with the tools' licenses) and THIRD_PARTY.md next to it.
# Without them every zip, 7z, cab, Inno and NSIS install would fail.
#
# `bash build_windows.sh --tracing` builds the tracing variant into build/tracing/ instead: it adds the tracing/
# and procmon_parser packages, ships Process Monitor (fetched by scripts/fetch_procmon.py) in procmon/, and writes
# TRACING-BUILD.txt. It is for the catalog admin's test machines only; never distribute it. The normal build
# leaves tracing out and then runs scripts/check_no_tracing.py, which fails the build if any tracing code got in.

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

TRACING=0
if [ "${1:-}" = "--tracing" ]; then
    TRACING=1
fi

if [ "$TRACING" -eq 1 ]; then
    OUT=build/tracing
    TRACING_FLAGS=(--include-package=tracing --include-package=procmon_parser)
    echo "Building the TRACING build of Game Chooser for Windows..."
else
    OUT=build
    TRACING_FLAGS=(--nofollow-import-to=tracing --nofollow-import-to=procmon_parser)
    echo "Building Game Chooser for Windows..."
fi
DIST="$OUT/game-chooser.dist"
REPORT="$OUT/compilation-report.xml"
TRACING_WARNING="This is the tracing build of Game Chooser. It records every file and registry write made by the games it launches. Never give it to anyone; it is for the catalog admin's test machines only."

fetch_tools_if_missing || exit 1

if [ "$TRACING" -eq 1 ] && [ ! -f tracing-tools/procmon/Procmon64.exe ]; then
    echo "Process Monitor missing from tracing-tools/procmon/; fetching it..."
    uv run python scripts/fetch_procmon.py || exit 1
fi

# Clean the previous build (only Nuitka's own folders: build/ may hold other things)
if [ -d "$DIST" ] || [ -d "$OUT/game-chooser.build" ]; then
    echo "Cleaning previous build..."
    rm -rf "$DIST" "$OUT/game-chooser.build"
fi

# smart_list (and resource_finder, which it imports) are uv git dependencies installed in the project's .venv.
# smart_list loads iat_hook32.dll / iat_hook64.dll through resource_finder, which looks beside the exe, so copy
# them there from the installed package. tr drops the CR that uv.exe prints.
SMART_LIST_DIR="$(uv run python -c "import os, smart_list; print(os.path.dirname(smart_list.__file__))" | tr -d '\r')"
if [ -z "$SMART_LIST_DIR" ]; then
    echo "Could not find the installed smart_list package. Run 'uv sync' first."
    exit 1
fi

# A report left from an earlier build must never be what the check reads.
rm -f "$REPORT"

# Run Nuitka to build the executable (nuitka isn't a project dependency; uv supplies it for this run)
uv run --with nuitka python -m nuitka \
    --standalone \
    --windows-console-mode=disable \
    --follow-imports \
    --include-package=engine \
    "${TRACING_FLAGS[@]}" \
    --include-module=resource_finder \
    --report="$REPORT" \
    --include-data-files="$SMART_LIST_DIR/iat_hook32.dll=iat_hook32.dll" \
    --include-data-files="$SMART_LIST_DIR/iat_hook64.dll=iat_hook64.dll" \
    --assume-yes-for-downloads \
    --output-dir="$OUT" \
    game-chooser.py

if [ $? -eq 0 ]; then
    ship_tools "$DIST" || exit 1
    if [ "$TRACING" -eq 1 ]; then
        mkdir -p "$DIST/procmon"
        cp tracing-tools/procmon/Procmon64.exe tracing-tools/procmon/Eula.txt "$DIST/procmon/" || exit 1
        echo "$TRACING_WARNING" > "$DIST/TRACING-BUILD.txt"
    else
        uv run python scripts/check_no_tracing.py "$REPORT" "$DIST" || {
            echo "The normal build failed the tracing check (see above); it must not be distributed."
            rm -rf "$DIST"
            echo "Removed $DIST so it can't be distributed by mistake."
            exit 1
        }
    fi
    echo ""
    echo "Build completed successfully!"
    echo "Executable location: $DIST/game-chooser.exe"
    echo ""
    if [ "$TRACING" -eq 1 ]; then
        echo "$TRACING_WARNING"
    else
        echo "To distribute, copy the entire '$DIST' folder (it includes bin/ and THIRD_PARTY.md)"
    fi
else
    echo ""
    echo "Build failed! Check the output above for errors."
    exit 1
fi
