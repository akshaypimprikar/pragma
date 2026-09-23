# Shared by scripts/setup.sh and scripts/sync_skills.sh — source, don't execute.
# BSD `sed -i` (macOS, ships no GNU sed) requires an explicit (even if empty)
# extension argument after -i; GNU `sed -i` treats that same argument as the
# pattern itself and errors out. Detect once, per-invocation, rather than
# hand-copying this check in every script that needs sed -i.
sedi() {
    if sed --version 2>/dev/null | grep -q GNU; then
        sed -i "$@"
    else
        sed -i '' "$@"
    fi
}
