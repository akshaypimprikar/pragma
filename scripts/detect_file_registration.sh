#!/usr/bin/env bash
# Prints how new .swift files get into the Xcode project: "synchronized" (Xcode 16+
# folder-backed group, nothing to register), "classic" (register with the xcodeproj
# gem via scripts/register_files.rb), "xcproj" (Xcode 27.2 project.xcproj format
# with classic groups; register_files.rb edits the file as text), or "stop" (ask the
# human first; the reason goes to stderr; includes mixed synchronized + classic
# projects). Exits 0 with two arguments.
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
    # a synchronized folder is {"kind": "folder"} in .xcproj
    if grep -Eq '"kind":[[:space:]]*"folder"' "$BUNDLE/project.xcproj"; then
        if grep -q '/compile-sources"' "$BUNDLE/project.xcproj"; then
            echo "stop"
            echo "mixed project: synchronized folders and files listed in compile-sources. Register by hand or convert the rest to folders." >&2
        else
            echo "synchronized"
        fi
    else
        echo "xcproj"
    fi
elif [[ ! -f "$BUNDLE/project.pbxproj" ]]; then
    echo "stop"
    echo "${APP_NAME}.xcodeproj/project.pbxproj not found in $PROJECT_DIR." >&2
elif grep -q "PBXFileSystemSynchronizedRootGroup" "$BUNDLE/project.pbxproj"; then
    # synchronized for every target and nothing listed in a Sources phase, or it is mixed
    targets=$(grep -c "isa = PBXNativeTarget;" "$BUNDLE/project.pbxproj" || true)
    synced=$(grep -c "fileSystemSynchronizedGroups = (" "$BUNDLE/project.pbxproj" || true)
    listed=$(awk '/Begin PBXSourcesBuildPhase section/{s=1} /End PBXSourcesBuildPhase section/{s=0}
        s && /files = \(/ {getline nx; if (nx !~ /\);/) n++} END{print n+0}' "$BUNDLE/project.pbxproj")
    if [[ "$listed" -gt 0 || ( "$synced" -gt 0 && "$synced" -lt "$targets" ) ]]; then
        echo "stop"
        echo "mixed project: synchronized groups plus classic files or targets. Register by hand or convert the rest to folders." >&2
    else
        echo "synchronized"
    fi
else
    echo "classic"
fi
