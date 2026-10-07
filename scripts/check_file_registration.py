#!/usr/bin/env python3
"""Fail when a new .swift file is not in a classic-group Xcode project.

Runs plutil -lint on a project.pbxproj (a project.xcproj is parsed by register_files.rb
instead), xcodebuild -list on the project, and checks every given .swift file is in a
Sources build phase (via register_files.rb --check). Projects with synchronized groups
pass without checks; stop mode fails.

Usage: check_file_registration.py --project-dir DIR --app-name NAME FILE...
Exit codes: 0 pass, 1 gate failed, 2 stop and ask the human.
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-dir", required=True)
    ap.add_argument("--app-name", required=True)
    ap.add_argument("files", nargs="*")
    args = ap.parse_args()

    swift = [f for f in args.files if f.endswith(".swift")]
    if not swift:
        print("no new .swift files: nothing to check")
        return 0

    detect = subprocess.run(
        [os.path.join(HERE, "detect_file_registration.sh"), args.project_dir, args.app_name],
        capture_output=True, text=True,
    )
    mode = detect.stdout.strip()
    if mode == "synchronized":
        print("synchronized groups: new files compile without registration, gate skipped")
        return 0
    if mode not in ("classic", "xcproj"):
        print(f"mode {mode}: stop and ask the human. {detect.stderr.strip()}")
        return 2

    bundle = os.path.join(args.project_dir, f"{args.app_name}.xcodeproj")
    if mode == "classic":
        lint = subprocess.run(["plutil", "-lint", os.path.join(bundle, "project.pbxproj")], capture_output=True, text=True)
        if lint.returncode != 0:
            print(f"plutil -lint failed: {lint.stdout.strip()} {lint.stderr.strip()}")
            return 1
    listing = subprocess.run(["xcodebuild", "-list", "-project", bundle], capture_output=True, text=True)
    if listing.returncode != 0:
        print(f"xcodebuild -list failed: {listing.stderr.strip()[-300:]}")
        return 1

    chk = subprocess.run(
        ["ruby", os.path.join(HERE, "register_files.rb"), "--check", args.project_dir, args.app_name, *swift],
        capture_output=True, text=True,
    )
    if chk.returncode == 0:
        return 0
    if chk.returncode == 3:
        print(chk.stderr.strip())
        return 2
    if chk.stderr.strip():
        print(chk.stderr.strip())
        return 1
    print("new .swift files not in any Sources build phase (run scripts/register_files.rb):")
    print(chk.stdout.strip())
    return 1


if __name__ == "__main__":
    sys.exit(main())
