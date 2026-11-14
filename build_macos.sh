#!/bin/bash
# Build script for the Game Chooser macOS app bundle using Nuitka
# Run this on a Mac with: bash build_macos.sh
#
# Builds build/Game Chooser.app for the Mac's own processor (arm64 or x86_64). The bundle carries LICENSE.txt
# (GPLv3), THIRD_PARTY.md (from THIRD_PARTY-macos.md) and licenses/ (scripts/collect_licenses.py) in
# Contents/Resources/. Adding them breaks the signature Nuitka made, so the bundle is signed again afterwards
# (ad hoc, which is enough to run on the Mac that built it; another Mac needs a Developer ID signature and
# notarization, or the player has to allow it in System Settings).
#
# The bundled extraction tools in bin/ are Windows programs, so no bin/ is shipped. There is no tracing build on
# macOS (it needs Process Monitor); the build leaves tracing out and then runs scripts/check_no_tracing.py.

set -u

if [ "$(uname)" != "Darwin" ]; then
    echo "build_macos.sh runs only on macOS."
    exit 1
fi

OUT=build
APP_NAME="Game Chooser"
BUILT_APP="$OUT/game-chooser.app"
APP="$OUT/$APP_NAME.app"
REPORT="$OUT/compilation-report.xml"

echo "Building Game Chooser for macOS ($(uname -m))..."

# Clean the previous build (only Nuitka's own folders and the app: build/ may hold other things)
rm -rf "$BUILT_APP" "$APP" "$OUT/game-chooser.build" "$OUT/game-chooser.dist"

# A report left from an earlier build must never be what the check reads.
rm -f "$REPORT"

# nuitka isn't a project dependency; uv supplies it for this run, pinned like the Windows build's
uv run --with nuitka==4.3.5 python -m nuitka \
    --mode=app \
    --macos-app-name="$APP_NAME" \
    --macos-app-mode=gui \
    --follow-imports \
    --include-package=engine \
    --nofollow-import-to=tracing \
    --nofollow-import-to=procmon_parser \
    --include-module=resource_finder \
    --report="$REPORT" \
    --assume-yes-for-downloads \
    --output-dir="$OUT" \
    game-chooser.py || {
    echo ""
    echo "Build failed! Check the output above for errors."
    exit 1
}

if [ ! -x "$BUILT_APP/Contents/MacOS/game-chooser" ]; then
    echo "$BUILT_APP/Contents/MacOS/game-chooser not found."
    exit 1
fi
mv "$BUILT_APP" "$APP" || exit 1

uv run python scripts/check_no_tracing.py "$REPORT" "$APP" || {
    echo "The build failed the tracing check (see above); it must not be distributed."
    rm -rf "$APP"
    echo "Removed $APP so it can't be distributed by mistake."
    exit 1
}

RESOURCES="$APP/Contents/Resources"
mkdir -p "$RESOURCES"
cp LICENSE.txt "$RESOURCES/" || exit 1
cp THIRD_PARTY-macos.md "$RESOURCES/THIRD_PARTY.md" || exit 1
uv run python scripts/collect_licenses.py "$RESOURCES" || exit 1

codesign --force --deep --sign - "$APP" || exit 1
codesign --verify --deep --strict "$APP" || {
    echo "$APP failed signature verification."
    exit 1
}

echo ""
echo "Build completed successfully!"
echo "App location: $APP"
echo ""
echo "To distribute, zip it with: ditto -c -k --keepParent \"$APP\" GameChooser-macos-$(uname -m).zip"
