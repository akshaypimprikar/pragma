#!/usr/bin/env bash
# Prints the xcodebuild container flag for a project: "-workspace <App>.xcworkspace"
# when it uses CocoaPods (a Podfile) or has a root-level workspace, otherwise
# "-project <App>.xcodeproj". Shared by scripts/setup.sh and scripts/sync_skills.sh
# so the skills, workflows and AGENTS.md all get the same answer.
#
# Usage: detect_build_target.sh PROJECT_DIR APP_NAME
set -euo pipefail

PROJECT_DIR="$1"
APP_NAME="$2"

if [[ -f "$PROJECT_DIR/Podfile" || -d "$PROJECT_DIR/${APP_NAME}.xcworkspace" ]]; then
    echo "-workspace ${APP_NAME}.xcworkspace"
else
    echo "-project ${APP_NAME}.xcodeproj"
fi
