#!/usr/bin/env bash
# Prints how new .swift files get into the Xcode project: "synchronized" (Xcode 16+
# folder-backed group, nothing to register), "classic" (register with the xcodeproj
# gem via scripts/register_files.rb), "xcproj" (project.xcproj format, not editable
# yet), or "stop" (ask the human first; the reason goes to stderr). Always exits 0.
#
# Usage: detect_file_registration.sh PROJECT_DIR APP_NAME
set -euo pipefail

PROJECT_DIR="$1"
APP_NAME="$2"
BUNDLE="$PROJECT_DIR/${APP_NAME}.xcodeproj"

for generator in project.yml Project.swift; do
    if [[ -e "$PROJECT_DIR/$generator" ]]; then
        echo "stop"
        echo "$generator exists: the project is regenerated from it, so edits to the .xcodeproj would be lost. Add files there instead." >&2
        exit 0
    fi
done

if [[ -f "$BUNDLE/project.xcproj" ]]; then
    echo "xcproj"
elif [[ ! -f "$BUNDLE/project.pbxproj" ]]; then
    echo "stop"
    echo "${APP_NAME}.xcodeproj/project.pbxproj not found in $PROJECT_DIR." >&2
elif grep -q "PBXFileSystemSynchronizedRootGroup" "$BUNDLE/project.pbxproj"; then
    echo "synchronized"
else
    echo "classic"
fi
