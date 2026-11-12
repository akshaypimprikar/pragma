#!/usr/bin/env bash
# Runs /gates' deterministic checks (pre-step, Gates 0-11) and prints the Gate
# summary block, so no model transcribes results. The model keeps only the
# judgment parts: filling an empty CHANGELOG (Gate 5), running the
# security-review skill when Gate 7 asks for it, weighing Gate 8's advisory
# candidates, and writing candidate invariants. See .claude/skills/gates/SKILL.md.
#
# Usage: scripts/check_gates.sh [--base REF] [--branch NAME] [--only N,N,...] [--config PATH]
#   --base    ref to diff against (default: main for release/* and hotfix/*, else develop)
#   --branch  branch name to check (default: the current branch; pass it on a detached HEAD)
#   --only    run only these gates, e.g. 3,4,5,10 (what /review re-runs)
#   --config  pipeline config (default: scripts/pipeline_lanes.json), read from its "gates" key
#
# Exit: 0 every blocking gate passed, 1 a blocking gate failed, 2 usage or setup
# error, 3 the working tree is not clean, 4 every blocking gate passed but Gate 7
# needs the security-review skill.
# Details print first; the summary block to paste into the PR body prints last.

set -u

BASE=""; BRANCH=""; ONLY=""; CONFIG=""
while [ $# -gt 0 ]; do
    case "$1" in
        --base|--branch|--only|--config)
            [ $# -ge 2 ] && [ -n "$2" ] || { echo "check_gates.sh: $1 needs a value" >&2; exit 2; }
            case "$1" in --base) BASE=$2 ;; --branch) BRANCH=$2 ;; --only) ONLY=$2 ;; --config) CONFIG=$2 ;; esac
            shift 2 ;;
        -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
        *) echo "check_gates.sh: unknown argument: $1" >&2; exit 2 ;;
    esac
done

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "check_gates.sh: not inside a git repository" >&2; exit 2; }
cd "$ROOT" || exit 2
[ -n "$CONFIG" ] || CONFIG="scripts/pipeline_lanes.json"
[ -n "$BRANCH" ] || BRANCH=$(git branch --show-current)
if [ -z "$BASE" ]; then
    case "$BRANCH" in release/*|hotfix/*) BASE=main ;; *) BASE=develop ;; esac
fi
git rev-parse --verify -q "$BASE^{commit}" >/dev/null || { echo "check_gates.sh: base ref '$BASE' not found (git fetch first?)" >&2; exit 2; }
BASE_NAME=${BASE#origin/}

wants() {  # wants N: true when gate N is selected
    [ -z "$ONLY" ] && return 0
    case ",$ONLY," in *",$1,"*) return 0 ;; esac
    return 1
}

# ── Pre-step — clean tree and pinned SHA ─────────────────────────────────────
# A partial run (--only, as /review uses it) ignores /review's own uncommitted log, as /review step 1 does.
if [ -n "$ONLY" ]; then DIRTY=$(git status --porcelain -- . ':!.claude/context/rejections.md')
else DIRTY=$(git status --porcelain); fi
if [ -n "$DIRTY" ]; then
    echo "Pre-step FAIL: the working tree is not clean. Commit or stash first:"
    echo "$DIRTY"
    exit 3
fi
SHA=$(git rev-parse HEAD)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/check_gates.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

SUMMARY=""; FAILED=0; SECURITY_PENDING=0
row() { SUMMARY="${SUMMARY}$1"$'\n'; }
fail_row() { row "[✗] $1"; FAILED=1; }
section() { printf '\n--- %s ---\n' "$1"; }

cfg() {  # cfg KEY: prints gates.KEY from the config (lists one per line), empty when unset
    python3 - "$CONFIG" "$1" <<'PY'
import json, sys
try:
    with open(sys.argv[1]) as f:
        value = json.load(f).get("gates", {}).get(sys.argv[2], "")
except (OSError, ValueError):
    value = ""
if isinstance(value, list):
    print("\n".join(str(v) for v in value))
elif value not in ("", None):
    print(value)
PY
}

changed_swift() {  # existing .swift files changed on the branch; --diff-filter passes through
    git -c core.quotepath=false diff "$BASE"...HEAD -z --name-only "$@" -- '*.swift' |
        while IFS= read -r -d '' f; do [ -f "$f" ] && printf '%s\n' "$f"; done
}

# ── Gate 0 — build-relevant change check (decides whether Gates 1-2 run) ─────
# This is the only copy of the build-relevance list (scripts/tests/test_gate0_pathspec.py checks it).
GATE0_FILES=$(
    git -c core.quotepath=false diff "$BASE"...HEAD --name-only -- '*.swift' '*.xcodeproj/**' '*.xcworkspace/**' '*.xcconfig' '*Info.plist' '*.entitlements' '*Package.resolved' '*Package.swift' '*.xctestplan'
)
NEW_SWIFT=$(changed_swift --diff-filter=A)
# The gates pass paths one per line, so a name with a newline can't be checked: fail it in every run.
NL_NAMES=$(git diff "$BASE"...HEAD -z --name-only | LC_ALL=C tr '\n\0' '\001\n' | LC_ALL=C grep -c $'\001')
[ "$NL_NAMES" -gt 0 ] && fail_row "File names — $NL_NAMES changed path(s) have a newline in the name; rename them"
if wants 1 || wants 2; then
    section "Gate 0 — build-relevant changes"
    if [ -n "$GATE0_FILES" ]; then echo "$GATE0_FILES"; else echo "none — Gates 1 and 2 are skipped"; fi
fi

# ── Gates 1-2 — build and full test suite ────────────────────────────────────
BUILD_OK=0
DEST=""; TARGET=""
xcode_setup() {  # sets TARGET and DEST; prints why and returns 1 when it can't
    local scheme
    scheme=$(cfg scheme)
    case "$scheme" in ""|YOUR_*) echo "gates.scheme is not set in $CONFIG"; return 1 ;; esac
    SCHEME=$scheme
    TARGET=$(cfg build_target)
    if [ -z "$TARGET" ]; then
        if [ -x scripts/detect_build_target.sh ]; then TARGET=$(scripts/detect_build_target.sh . "$SCHEME")
        else echo "gates.build_target is not set and scripts/detect_build_target.sh is missing"; return 1; fi
    fi
    DEST=$(cfg destination)
    if [ -z "$DEST" ]; then
        local udid
        udid=$(xcrun simctl list devices available -j 2>/dev/null | python3 scripts/select_simulator.py 2>/dev/null)
        [ -n "$udid" ] || { echo "gates.destination is not set and no simulator was found"; return 1; }
        DEST="platform=iOS Simulator,id=$udid"
    fi
}
show_log() { if command -v xcsift >/dev/null 2>&1; then xcsift < "$1"; else tail -n 40 "$1"; fi; }

if wants 1 || wants 2; then
    if [ -z "$GATE0_FILES" ]; then
        wants 1 && row "[–] Build — skipped (no build-relevant changes)"
        wants 2 && row "[–] Tests — skipped (no build-relevant changes)"
    elif ! WHY=$(xcode_setup); then
        section "Gates 1-2 — not configured"; echo "$WHY"
        wants 1 && fail_row "Build — not configured ($WHY)"
        wants 2 && fail_row "Tests — not configured ($WHY)"
    else
        xcode_setup >/dev/null
        if wants 1; then
            section "Gate 1 — build"
            LOG="$TMP/gate1.log"
            # shellcheck disable=SC2086  # TARGET is "-project X" or "-workspace X"
            xcodebuild build $TARGET -scheme "$SCHEME" -configuration Debug -destination "$DEST" > "$LOG" 2>&1; RC=$?
            show_log "$LOG"
            if [ -s "$LOG" ] && [ "$RC" -eq 0 ] && grep -q "BUILD SUCCEEDED" "$LOG"; then
                row "[✓] Build"; BUILD_OK=1
            else
                fail_row "Build — xcodebuild exit $RC, log bytes $(wc -c < "$LOG" | tr -d ' ')"
            fi
        else
            BUILD_OK=1  # --only without 1: trust the caller's build
        fi
        if wants 2; then
            if [ "$BUILD_OK" -ne 1 ]; then
                row "[–] Tests — skipped (build failed)"
            else
                section "Gate 2 — full test suite"
                LOG="$TMP/gate2.log"
                COV_ARGS=""
                if wants 6 && [ -n "$NEW_SWIFT" ]; then COV_ARGS="-enableCodeCoverage YES -resultBundlePath $TMP/gate2.xcresult"; fi
                # shellcheck disable=SC2086
                xcodebuild test $TARGET -scheme "$SCHEME" -destination "$DEST" $COV_ARGS > "$LOG" 2>&1; RC=$?
                show_log "$LOG"
                PASSED=$(grep -E "^Test [Cc]ase '.*' passed|^[✔✓] Test .*passed" "$LOG" | grep -vc "Test run with"); FAILED_TESTS=$(grep -cE "^Test [Cc]ase '.*' failed|^[✘✗] Test .*failed" "$LOG")
                if [ -s "$LOG" ] && [ "$RC" -eq 0 ] && grep -q "TEST SUCCEEDED" "$LOG" && [ "$FAILED_TESTS" -eq 0 ] && [ "$PASSED" -gt 0 ]; then
                    row "[✓] Tests — $PASSED tests executed"
                else
                    fail_row "Tests — xcodebuild exit $RC, passed=$PASSED, failed=$FAILED_TESTS"
                fi
            fi
        fi
    fi
fi

# ── Gate 3 — no TODO/FIXME/HACK in changed Swift files ───────────────────────
if wants 3; then
    HITS=$(changed_swift | while IFS= read -r f; do grep -Hn 'TODO\|FIXME\|HACK' "$f"; done)
    if [ -z "$HITS" ]; then row "[✓] No TODO/FIXME/HACK"
    else section "Gate 3 — TODO/FIXME/HACK"; echo "$HITS"; fail_row "No TODO/FIXME/HACK — $(echo "$HITS" | wc -l | tr -d ' ') hit(s)"; fi
fi

# ── Gate 4 — branch naming ───────────────────────────────────────────────────
if wants 4; then
    case "$BRANCH" in
        feature/*|fix/*|hotfix/*|release/*|spec/*|design/*|ci/*|chore/*) row "[✓] Branch naming" ;;
        *) fail_row "Branch naming — '${BRANCH:-detached HEAD}' is not feature/, fix/, hotfix/, release/, spec/, design/, ci/ or chore/" ;;
    esac
fi

# ── Gate 5 — CHANGELOG.md has Unreleased entries ─────────────────────────────
if wants 5; then
    # Only the [Unreleased] section itself: stop at the next "## " heading, so an empty section
    # never borrows the previous release's bullets. "###" subheadings, CRLF blank lines and
    # Keep-a-Changelog link definitions ("[1.0.0]: https://...") are not entries.
    ENTRIES=$(awk '{sub(/\r$/, "")} /^## \[Unreleased\]/{f=1; next} /^## /{f=0} f && NF && !/^##/ && !/^\[[^]]+\]:/' CHANGELOG.md 2>/dev/null)
    ALL_CHANGED=$(git -c core.quotepath=false diff "$BASE"...HEAD --name-only)
    if [ -n "$ENTRIES" ]; then row "[✓] CHANGELOG"
    elif [ "$ALL_CHANGED" = ".claude/context/feature-log.md" ]; then row "[–] CHANGELOG — N/A (feature-log only)"
    else fail_row "CHANGELOG — Unreleased section missing or empty"; fi
fi

# ── Gate 6 — coverage of new Swift files (≥80% each) ─────────────────────────
if wants 6; then
    if [ -z "$NEW_SWIFT" ]; then row "[–] Coverage — skipped (no new files)"
    elif [ ! -d "$TMP/gate2.xcresult" ]; then
        if wants 2; then fail_row "Coverage — no coverage data (Gate 2 did not run with coverage)"
        else fail_row "Coverage — needs Gate 2 in the same run"; fi
    else
        section "Gate 6 — coverage of new files"
        xcrun xccov view --report --json "$TMP/gate2.xcresult" > "$TMP/cov.json" 2>/dev/null
        printf '%s\n' "$NEW_SWIFT" > "$TMP/new_swift.txt"
        if COV=$(python3 - "$TMP/cov.json" "$TMP/new_swift.txt" scripts/check_coverage.py <<'PY'
import ast, json, sys
COVERAGE_THRESHOLD = 0.80  # per new file; check_coverage.py's WARN_THRESHOLD
# Exclusions follow scripts/check_coverage.py: test files, UI-only files (covered by UI tests),
# main.swift, and its HARDWARE_DEPENDENT_EXCEPTIONS (read from that file, so the two never drift).
hardware = set()
try:
    for node in ast.parse(open(sys.argv[3]).read()).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "HARDWARE_DEPENDENT_EXCEPTIONS" for t in node.targets):
            v = node.value
            if isinstance(v, ast.Call):
                hardware = set(ast.literal_eval(v.args[0])) if v.args else set()
            else:
                hardware = set(ast.literal_eval(v))
except (OSError, ValueError, SyntaxError) as e:
    print(f"could not read HARDWARE_DEPENDENT_EXCEPTIONS from {sys.argv[3]}: {e}"); sys.exit(1)
try:
    report = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    print("could not read the xccov report"); sys.exit(1)
files = [f for t in report.get("targets", []) if "Tests" not in t.get("name", "") for f in t.get("files", [])]
def is_test(rel):  # anchored: a *Tests/ folder or a *Test(s).swift file, not any path containing "Tests"
    parts = rel.split("/")
    return any(p.endswith("Tests") for p in parts[:-1]) or parts[-1].endswith(("Tests.swift", "Test.swift"))
bad = 0
for rel in filter(None, (l.strip() for l in open(sys.argv[2]))):
    name = rel.rsplit("/", 1)[-1]
    if is_test(rel) or name == "main.swift" or name.endswith(("View.swift", "Sheet.swift", "Row.swift")) or name.startswith("Color+") or any(rel.endswith(h) for h in hardware):
        print(f"  [skip] {rel} (test, UI-only or hardware-dependent file)"); continue
    match = [f for f in files if f.get("path", "").endswith("/" + rel)]
    if not match or "lineCoverage" not in match[0]:
        print(f"  [FAIL] {rel} not in the coverage report"); bad += 1; continue
    cov = match[0]["lineCoverage"]
    bad += cov < COVERAGE_THRESHOLD
    print(f"  [{'  OK' if cov >= COVERAGE_THRESHOLD else 'FAIL'}] {rel} {cov * 100:.1f}%")
sys.exit(1 if bad else 0)
PY
        ); then echo "$COV"; row "[✓] Coverage — new files ≥80%"
        else echo "$COV"; fail_row "Coverage — new file(s) under 80% or missing (see report)"; fi
    fi
fi

# ── Gate 7 — security trigger ────────────────────────────────────────────────
if wants 7; then
    PATTERN=$(cfg sensitive_paths)
    if [ -z "$PATTERN" ]; then row "[–] Security — skipped (gates.sensitive_paths not configured)"
    else
        SENS=$(changed_swift | grep -E -- "$PATTERN" 2>"$TMP/g7.err"); GREP_RC=$?
        if [ "$GREP_RC" -gt 1 ]; then section "Gate 7 — invalid pattern"; cat "$TMP/g7.err"; fail_row "Security — gates.sensitive_paths is not a valid regex"
        elif [ -z "$SENS" ]; then row "[–] Security — skipped (no sensitive files)"
        else
            section "Gate 7 — sensitive files changed"; echo "$SENS"
            row "[!] Security — security-review required: $(echo "$SENS" | tr '\n' ' ' | sed 's/ $//')"; SECURITY_PENDING=1
        fi
    fi
fi

# ── Gate 8 — abstraction bloat / duplication (advisory) ──────────────────────
if wants 8; then
    G8=$(python3 - "$BASE" <<'PY'
import re, subprocess, sys
base = sys.argv[1]
def git(*a):
    return subprocess.run(["git", "-c", "core.quotepath=false", *a], capture_output=True, text=True).stdout
out = []
# New protocols with at most one conforming type.
proto_decl = re.compile(r"^((public|internal|package|private|fileprivate|open|nonisolated|@[A-Za-z]+) )*protocol (\w+)")
added = [f for f in git("diff", f"{base}...HEAD", "-z", "--name-only", "--diff-filter=A", "--", "*.swift").split("\0") if f]
for path in added:
    try:
        lines = open(path).read().splitlines()
    except OSError:
        continue
    for line in lines:
        m = proto_decl.match(line.strip())
        if not m:
            continue
        name = m.group(3)
        conf = re.compile(r"\b(class|struct|enum|actor|extension)\b[^{]*[:,]\s*([\w.<>]+\s*,\s*)*" + re.escape(name) + r"\b")
        users = [l for l in git("grep", "-hwF", name, "--", "*.swift").split("\n") if conf.search(l) and not proto_decl.match(l.strip())]
        if len(users) <= 1:
            out.append(f"protocol {name} ({path}) — {len(users)} conforming type(s): inline candidate unless it is a repository-style seam")
# Runs of 3+ consecutive added lines that each appear 2+ times across the diff.
diff = git("diff", f"{base}...HEAD", "-U0", "--", "*.swift")
added_lines, counts, cur, lineno = [], {}, None, 0
for raw in diff.split("\n"):
    if raw.startswith("+++ "):
        cur = raw[6:] if raw.startswith("+++ b/") else None
    elif raw.startswith("@@"):
        lineno = int(re.search(r"\+(\d+)", raw).group(1))
        added_lines.append(None)  # hunk break
    elif raw.startswith("+") and cur:
        text = raw[1:]
        if text.strip():
            counts[text] = counts.get(text, 0) + 1
            added_lines.append((cur, lineno, text))
        lineno += 1
run = []
for item in added_lines + [None]:
    if item and counts[item[2]] >= 2 and (not run or (run[-1][0] == item[0])):
        run.append(item); continue
    if len(run) >= 3:
        out.append(f"duplicated block {run[0][0]}:{run[0][1]}-{run[-1][1]} ({len(run)} lines): extraction candidate")
    run = [item] if item and counts[item[2]] >= 2 else []
print("\n".join(out))
PY
    )
    if [ -z "$G8" ]; then row "[i] Abstraction bloat — no candidates found"
    else
        section "Gate 8 — advisory candidates"; echo "$G8"
        N=$(echo "$G8" | wc -l | tr -d ' ')
        row "[i] Abstraction bloat — $N candidate(s) found (see report)"
    fi
fi

# ── Gate 9 — RED-before-GREEN commit order ───────────────────────────────────
if wants 9; then
    if [ -z "$NEW_SWIFT" ]; then row "[–] RED-before-GREEN commit order — skipped (no new Swift files)"
    elif [ ! -f scripts/check_tdd_commit_order.py ]; then fail_row "RED-before-GREEN commit order — script missing"
    else
        OUT=$(python3 scripts/check_tdd_commit_order.py "$BASE" 2>&1); RC=$?
        section "Gate 9 — RED-before-GREEN commit order"; echo "$OUT"
        case "$RC" in
            0) if echo "$OUT" | grep -qE "skipping|nothing to check"; then row "[–] RED-before-GREEN commit order — skipped (no new files in scoped layers)"
               else row "[✓] RED-before-GREEN commit order"; fi ;;
            2) fail_row "RED-before-GREEN commit order — unconfigured (layer names match nothing in this repo)" ;;
            *) fail_row "RED-before-GREEN commit order — violations (see report)" ;;
        esac
    fi
fi

# ── Gate 10 — architecture & layer rules ─────────────────────────────────────
if wants 10; then
    if G10=$(python3 - "$BASE" "$CONFIG" 2>&1 <<'PY'
import json, re, subprocess, sys
base, config = sys.argv[1], sys.argv[2]
def git(*a):
    return subprocess.run(["git", "-c", "core.quotepath=false", *a], capture_output=True, text=True).stdout
try:
    gates = json.load(open(config)).get("gates", {})
except (OSError, ValueError) as e:
    print(f"cannot read {config}: {e}"); sys.exit(2)
def as_list(v):
    return [v] if isinstance(v, str) else list(v or [])
def swift(filter_=None):
    args = ["diff", f"{base}...HEAD", "-z", "--name-only"] + ([f"--diff-filter={filter_}"] if filter_ else []) + ["--", "*.swift"]
    return [f for f in git(*args).split("\0") if f]
changed, added = swift("d"), swift("A")
rules = [{"name": "No try!/as! in production code", "pattern": r"\btry!|as!", "exclude": "Tests/"}] + list(gates.get("rules", []))
violations, accepted = [], []
for rule in rules:
    flags = re.I if rule.get("ignore_case") else 0
    pats = [re.compile(p, flags) for p in as_list(rule["pattern"])]
    drops = [re.compile(p) for p in as_list(rule.get("drop"))]
    allows = [re.compile(p) for p in as_list(rule.get("allow"))]
    scope = added if rule.get("added_only") else changed
    scope = [f for f in scope if re.search(rule.get("paths", ""), f) and not (rule.get("exclude") and re.search(rule["exclude"], f))]
    for path in scope:
        try:
            lines = open(path, errors="replace").read().splitlines()
        except OSError:
            continue
        if rule.get("mode") == "missing":
            if not any(p.search(l) for l in lines for p in pats):
                violations.append(f"{rule['name']}: {path} does not match {as_list(rule['pattern'])}")
            continue
        for n, line in enumerate(lines, 1):
            if any(p.search(line) for p in pats) and not any(d.search(line) for d in drops):
                hit = f"{path}:{n}: {line.strip()}"
                (accepted if any(a.search(hit) for a in allows) else violations).append(f"{rule['name']}: {hit}")
# UI-test selectors must name something the app defines, or text the test itself types.
app_dir, ui_dir = gates.get("app_dir", ""), gates.get("ui_tests_dir", "")
if app_dir and ui_dir and not app_dir.startswith("YOUR_"):
    sel = re.compile(r'app\.(?:buttons|textFields|staticTexts)\["([^"]*)"\]')
    ui_files = [f for f in git("ls-files", "-z", "--", f"{ui_dir}/*.swift").split("\0") if f]
    app_files = [f for f in git("ls-files", "-z", "--", f"{app_dir}/*.swift").split("\0") if f]
    ui_text = "".join(open(f, errors="replace").read() for f in ui_files)
    app_text = "".join(open(f, errors="replace").read() for f in app_files)
    typed = set(re.findall(r'typeText\("([^"]*)"\)', ui_text))
    allow = set(gates.get("ui_selector_allow", []))
    for literal in sorted(set(sel.findall(ui_text))):
        if f'"{literal}"' not in app_text and literal not in typed and literal not in allow:
            violations.append(f"UI selector: \"{literal}\" is not an identifier or string in {app_dir}/ and not typed by the tests")
for a in accepted:
    print(f"  [accepted] {a}")
for v in violations:
    print(f"  [FAIL] {v}")
if not gates.get("rules"):
    print("  note: no gates.rules configured; only the generic checks ran")
sys.exit(1 if violations else 0)
PY
    ); then
        [ -n "$G10" ] && { section "Gate 10 — architecture & layer rules"; echo "$G10"; }
        if echo "$G10" | grep -q "no gates.rules configured"; then row "[✓] Architecture & layer-rule compliance — generic checks only (no gates.rules)"
        elif echo "$G10" | grep -q "\[accepted\]"; then row "[✓] Architecture & layer-rule compliance — $(echo "$G10" | grep -c '\[accepted\]') accepted exception(s) (see report)"
        else row "[✓] Architecture & layer-rule compliance"; fi
    else
        section "Gate 10 — architecture & layer rules"; echo "$G10"
        N=$(echo "$G10" | grep -c '\[FAIL\]')
        if [ "$N" -gt 0 ]; then fail_row "Architecture & layer-rule compliance — $N violation(s) (see report)"
        else fail_row "Architecture & layer-rule compliance — check did not run (see report)"; fi
    fi
fi

# ── Gate 11 — gate integrity ─────────────────────────────────────────────────
if wants 11; then
    if [ ! -f scripts/check_gate_integrity.py ]; then fail_row "Gate integrity — script missing"
    else
        OUT=$(python3 scripts/check_gate_integrity.py "$BASE" "$BRANCH" 2>&1); RC=$?
        if [ "$RC" -eq 0 ]; then row "[✓] Gate integrity"
        else section "Gate 11 — gate integrity"; echo "$OUT"; fail_row "Gate integrity — violations (see report)"; fi
    fi
fi

# ── Summary ──────────────────────────────────────────────────────────────────
LANE_REF=$BASE
case "$BASE" in origin/*) ;; *) git rev-parse --verify -q "origin/$BASE^{commit}" >/dev/null && LANE_REF="origin/$BASE" ;; esac
LANE=$(python3 scripts/check_pr_lane.py --git "$LANE_REF" --head-branch "$BRANCH" --base-branch "$BASE_NAME" --config "$CONFIG" 2>"$TMP/lane.err") ||
    LANE="unknown ($(tail -n 1 "$TMP/lane.err"))"

printf '\n=== Gate summary (paste into the PR body) ===\n'
echo "Gates run at $SHA"
echo "Lane: $LANE"
if [ -n "$ONLY" ]; then echo "Gates (only $ONLY):"; else echo "Gates:"; fi
printf '%s' "$SUMMARY"
[ "$FAILED" -eq 0 ] && [ "$SECURITY_PENDING" -eq 1 ] && exit 4
exit "$FAILED"
