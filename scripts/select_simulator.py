#!/usr/bin/env python3
"""
Reads `xcrun simctl list devices available -j` from stdin and prints the UDID
of the latest available standard iPhone (excludes Pro/Plus/Max/SE/Air, and the
budget line regardless of naming generation — SE through 2025, "17e" onward).
Usage: xcrun simctl list devices available -j | python3 scripts/select_simulator.py
"""
import json
import re
import sys

BUDGET_LINE_SUFFIX = re.compile(r"\d+e\b")  # matches "17e", not "iPhone 17" or "iPhone 17 Pro"

devices = json.load(sys.stdin)["devices"]
for runtime in sorted(devices, reverse=True):
    if "iOS" not in runtime:
        continue
    for device in devices[runtime]:
        name = device.get("name", "")
        if (
            device.get("isAvailable")
            and "iPhone" in name
            and "Pro" not in name
            and "Plus" not in name
            and "Max" not in name
            and "SE" not in name
            and "Air" not in name
            and not BUDGET_LINE_SUFFIX.search(name)
        ):
            print(device["udid"])
            sys.exit(0)

print("No suitable simulator found", file=sys.stderr)
sys.exit(1)
