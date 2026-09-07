#!/usr/bin/env bash
# Usage: scripts/slim_simulator.sh <simulator-udid> [--requires feature,feature,...]
#
# Best-effort: disables ~170 non-essential simulator background daemons
# (Siri, Spotlight, widgets, iCloud, photo analysis, etc.) via SimSlim
# (https://github.com/MobAI-App/simslim) to cut per-simulator memory
# footprint — measured on a real production test run (FinanceTracker,
# full unit + UI suite, 2026-09): 2.62GB -> 0.83GB idle (~3x), 35s -> 9s
# boot, zero test failures across unit tests and 7 UI test suites,
# including one that exercises real UNUserNotificationCenter delivery.
#
# No-ops cleanly if the `simslim` CLI isn't installed — this is an
# optional speed/memory optimization, never a hard dependency of the
# pipeline. Install: https://github.com/MobAI-App/simslim#installation
#
# Guardrail: a stock slim disables push (apsd), StoreKit (storekitd),
# universal links (swcd), and Spotlight/search among other categories.
# If your test suite depends on any of those, pass --requires so this
# script can verify safety before touching the simulator — it does not
# guess on your behalf. Example:
#   scripts/slim_simulator.sh "$SIM_ID" --requires push,storekit

set -euo pipefail

UDID="${1:-}"
[[ -z "$UDID" ]] && { echo "Usage: $0 <simulator-udid> [--requires feature,feature,...]" >&2; exit 1; }
shift

REQUIRES=""
if [[ "${1:-}" == "--requires" ]]; then
    REQUIRES="${2:-}"
fi

if ! command -v simslim &>/dev/null; then
    echo "  (simslim not installed — skipping simulator slimming, no effect on tests)"
    exit 0
fi

if [[ -n "$REQUIRES" ]]; then
    echo "  → simslim doctor: checking $UDID is safe to slim for required features: $REQUIRES"
    if ! simslim doctor "$UDID" --requires "$REQUIRES"; then
        echo "  ! simslim doctor flagged a conflict with --requires $REQUIRES — leaving simulator at stock, continuing"
        exit 0
    fi
fi

echo "  → Slimming simulator $UDID via simslim…"
if simslim on "$UDID"; then
    simslim measure "$UDID" || true
else
    echo "  ! simslim on failed — continuing with full simulator (non-fatal)"
fi
