#!/usr/bin/env bash
# Prints "<persistence> <ui>" for a project, guessed from its sources:
# persistence = swiftdata | coredata | realm | none, ui = swiftui | uikit.
# Architecture (mvvm/mvc/viper) is not detectable; setup.sh defaults it to mvvm
# and the human edits project.architecture in scripts/pipeline_lanes.json.
# Used by scripts/setup.sh to fill scripts/pipeline_lanes.json.
#
# Usage: detect_project_settings.sh PROJECT_DIR
set -euo pipefail

PROJECT_DIR="$1"

has_import() {
    grep -rqE --include='*.swift' --exclude-dir=Pods --exclude-dir=Carthage --exclude-dir=.build --exclude-dir=DerivedData --exclude-dir='*.xcframework' "^[[:space:]]*(@preconcurrency[[:space:]]+)?import $1\b" "$PROJECT_DIR" 2>/dev/null
}

if has_import SwiftData; then
    persistence=swiftdata
elif has_import RealmSwift || grep -qiE "^[[:space:]]*pod[[:space:]]+['\"]Realm" "$PROJECT_DIR/Podfile" 2>/dev/null; then
    persistence=realm
elif has_import CoreData || [[ -n "$(find "$PROJECT_DIR" \( -name Pods -o -name Carthage -o -name .build -o -name DerivedData \) -prune -o -name '*.xcdatamodeld' -print -quit 2>/dev/null)" ]]; then
    persistence=coredata
else
    persistence=none
fi

if has_import SwiftUI; then
    ui=swiftui
elif has_import UIKit; then
    ui=uikit
else
    ui=swiftui
fi

echo "$persistence $ui"
