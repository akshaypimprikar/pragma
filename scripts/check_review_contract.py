#!/usr/bin/env python3
"""Check that one /review run's JSON output meets the review stage contract.
The contract is the same in every agent CLI (Claude Code, Codex, ...), so a run in
any of them can be checked the same way. The shape lives in scripts/review.schema.json;
this script validates against that file (a small JSON Schema subset: type, enum,
required, properties, additionalProperties, items) and then applies the rules a
schema cannot express:
  1. reviewed_sha is a full 40-character SHA, and equals --head when given
  2. verdict equals what scripts/review_verdict.py decides from the findings, and is
     CHANGES REQUESTED when any gate_verification check is FAIL
  3. isolation status "isolated" carries evidence of the subagent; "NOT isolated"
     and "N/A" carry a reason
  4. every NOT VERIFIED or NOT RUN check has a reason, an unposted review has a
     reason, and every finding has quoted evidence and a unique id
  5. runtime fields are non-empty (which CLI, version, model and sandbox ran it)
It does not check that the output is true, only that it is complete and consistent;
the run log is the evidence for isolation and for no silent fallback to another CLI.
Usage: check_review_contract.py [--head <sha>] [output.json]   (reads stdin without a file)
Exit codes: 0 contract met, 1 violations listed on stderr, 2 unreadable input.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import review_verdict  # noqa: E402

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "review.schema.json")
TYPES = {"string": str, "boolean": bool, "array": list, "object": dict, "null": type(None)}


def load_schema(path=SCHEMA_PATH):
    with open(path) as fh:
        return json.load(fh)


def type_ok(value, name):
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, TYPES[name])


def validate(value, schema, where, errors):
    types = schema.get("type")
    if types is not None:
        types = types if isinstance(types, list) else [types]
        if not any(type_ok(value, t) for t in types):
            errors.append(f"{where}: expected {' or '.join(types)}, got {type(value).__name__}")
            return
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{where}.{key}: missing")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    errors.append(f"{where}.{key}: extra field not in the schema")
        for key, sub in props.items():
            if key in value:
                validate(value[key], sub, f"{where}.{key}", errors)
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            validate(item, schema["items"], f"{where}[{i}]", errors)


def blank(s):
    return not isinstance(s, str) or not s.strip()


def check(out, schema, head=None):
    errors = []
    validate(out, schema, "output", errors)
    if errors:
        return errors

    if not re.fullmatch(r"[0-9a-f]{40}", out["reviewed_sha"]):
        errors.append("output.reviewed_sha: not a full 40-character SHA")
    elif head and out["reviewed_sha"] != head:
        errors.append(f"output.reviewed_sha: {out['reviewed_sha']} is not the PR head {head}")

    for key, val in out["runtime"].items():
        if isinstance(val, str) and blank(val):
            errors.append(f"output.runtime.{key}: empty")

    seen = set()
    for f in out["findings"]:
        if f["id"] in seen or blank(f["id"]):
            errors.append(f"finding {f['id']!r}: id must be non-empty and unique")
        seen.add(f["id"])
        if blank(f["evidence"]):
            errors.append(f"finding {f['id']!r}: no quoted evidence")

    for g in out["gate_verification"]:
        if g["result"] in ("NOT VERIFIED", "NOT RUN") and blank(g["reason"]):
            errors.append(f"gate check {g['check']!r}: {g['result']} with no reason")

    if blank(out["isolation"]["evidence"]):
        what = "evidence of the subagent" if out["isolation"]["status"] == "isolated" else "a reason"
        errors.append(f"output.isolation: {out['isolation']['status']} with no {what}")

    if blank(out["posted"]["url"]) and blank(out["posted"]["reason"]):
        errors.append("output.posted: not posted and no reason given")

    expected = review_verdict.decide(out["findings"])["verdict"]
    if any(g["result"] == "FAIL" for g in out["gate_verification"]):
        expected = "CHANGES REQUESTED"
    if out["verdict"] != expected:
        errors.append(f"output.verdict: {out['verdict']} but the findings and gate checks give {expected}")
    return errors


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check a /review run's JSON output against the stage contract.")
    ap.add_argument("--head", help="PR HEAD SHA the review must have read")
    ap.add_argument("file", nargs="?", help="output JSON file (default: stdin)")
    args = ap.parse_args(argv)
    try:
        if args.file:
            with open(args.file) as fh:
                out = json.load(fh)
        else:
            out = json.load(sys.stdin)
        schema = load_schema()
    except (OSError, ValueError) as e:
        print(f"check_review_contract: {e}", file=sys.stderr)
        return 2
    errors = check(out, schema, args.head)
    for e in errors:
        print(f"check_review_contract: {e}", file=sys.stderr)
    if errors:
        return 1
    print("Review contract met")
    return 0


if __name__ == "__main__":
    sys.exit(main())
