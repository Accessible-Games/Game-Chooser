#!/bin/bash
# Build script for Game Chooser Windows executable using Nuitka
# Run this from WSL with: bash build_windows.sh

echo "Building Game Chooser for Windows..."

# Clean previous build if it exists
if [ -d "build" ]; then
    echo "Cleaning previous build..."
    rm -rf build
fi

# Run Nuitka to build the executable
python.exe -m nuitka \
    --standalone \
    --windows-console-mode=disable \
    --enable-plugin=implicit-imports \
    --follow-imports \
    --output-dir=build \
    game-chooser.py

if [ $? -eq 0 ]; then
    echo ""
    echo "Build completed successfully!"
    echo "Executable location: build/game-chooser.dist/game-chooser.exe"
    echo ""
    echo "To distribute, copy the entire 'build/game-chooser.dist' folder"
else
    echo ""
    echo "Build failed! Check the output above for errors."
    exit 1
fi
