import copy
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import check_review_contract as crc  # noqa: E402

SHA = "a" * 40


def finding(**kw):
    base = dict(id="F1", severity="LOW", path="App/Foo.swift", line=3, evidence="let x = y!",
                fix="unwrap safely", in_diff=True, depends_on_unchanged=False, guard_bypass=False,
                advisory=False, dismissed=False)
    base.update(kw)
    return base


def output(**kw):
    base = dict(
        reviewed_sha=SHA, round="1", lane="app",
        runtime=dict(cli="codex", cli_version="0.166.0", model="gpt-x", sandbox="read-only", network=False),
        gate_verification=[dict(check="Gate 3 grep", result="PASS", reason="")],
        isolation=dict(status="NOT isolated", evidence="subagent unavailable"),
        findings=[finding()], verdict="APPROVED", coverage=["App/Foo.swift"],
        limitations=[], posted=dict(url=None, reason="network off"))
    base.update(kw)
    return base


class ContractTests(unittest.TestCase):
    def check(self, out, head=None):
        return crc.check(out, crc.load_schema(), head)

    def test_valid_output_passes(self):
        self.assertEqual(self.check(output()), [])

    def test_missing_field_fails(self):
        out = output()
        del out["limitations"]
        self.assertTrue(any("limitations" in e for e in self.check(out)))

    def test_extra_field_fails(self):
        self.assertTrue(any("extra" in e for e in self.check(output(notes="hi"))))

    def test_bad_enum_fails(self):
        self.assertTrue(any("round" in e for e in self.check(output(round="3"))))

    def test_short_sha_fails(self):
        self.assertTrue(any("reviewed_sha" in e for e in self.check(output(reviewed_sha="abc123"))))

    def test_head_mismatch_fails(self):
        self.assertTrue(any("head" in e for e in self.check(output(), head="b" * 40)))

    def test_head_match_passes(self):
        self.assertEqual(self.check(output(), head=SHA), [])

    def test_approved_with_blocking_finding_fails(self):
        out = output(findings=[finding(severity="HIGH")])
        self.assertTrue(any("verdict" in e for e in self.check(out)))

    def test_changes_requested_with_blocking_finding_passes(self):
        out = output(findings=[finding(severity="HIGH")], verdict="CHANGES REQUESTED")
        self.assertEqual(self.check(out), [])

    def test_failed_gate_forces_changes_requested(self):
        out = output(gate_verification=[dict(check="Gate 3 grep", result="FAIL", reason="TODO found")])
        self.assertTrue(any("verdict" in e for e in self.check(out)))

    def test_isolated_without_evidence_fails(self):
        out = output(isolation=dict(status="isolated", evidence=""))
        self.assertTrue(any("isolation" in e for e in self.check(out)))

    def test_not_isolated_without_reason_fails(self):
        out = output(isolation=dict(status="NOT isolated", evidence=" "))
        self.assertTrue(any("isolation" in e for e in self.check(out)))

    def test_not_run_without_reason_fails(self):
        out = output(gate_verification=[dict(check="gates CI job", result="NOT RUN", reason="")])
        self.assertTrue(any("gates CI job" in e for e in self.check(out)))

    def test_unposted_without_reason_fails(self):
        self.assertTrue(any("posted" in e for e in self.check(output(posted=dict(url=None, reason="")))))

    def test_empty_url_without_reason_fails(self):
        self.assertTrue(any("posted" in e for e in self.check(output(posted=dict(url="", reason="")))))

    def test_finding_without_evidence_fails(self):
        self.assertTrue(any("F1" in e for e in self.check(output(findings=[finding(evidence="")]))))

    def test_empty_runtime_field_fails(self):
        out = output()
        out["runtime"]["model"] = ""
        self.assertTrue(any("runtime.model" in e for e in self.check(out)))

    def test_unreadable_cli_version_is_written_as_unknown(self):
        # Codex 0.166.0 left cli_version empty on 3 of 12 runs (2026-11-02) when it could not read it.
        with open(crc.SCHEMA_PATH, encoding="utf8") as f:
            desc = json.load(f)["properties"]["runtime"]["properties"]["cli_version"].get("description", "")
        self.assertIn('"unknown"', desc)
        out = output()
        out["runtime"]["cli_version"] = "unknown"
        self.assertEqual(self.check(out), [])

    def test_duplicate_finding_id_fails(self):
        self.assertTrue(any("F1" in e for e in self.check(output(findings=[finding(), finding()]))))

    def test_wrong_type_fails(self):
        self.assertTrue(any("line" in e for e in self.check(output(findings=[finding(line="3")]))))

    def test_schema_is_strict(self):
        # Every object in the schema lists all its properties as required and allows no extras,
        # so the same file works as a strict structured-output schema (codex exec --output-schema).
        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertEqual(set(node["required"]), set(node["properties"]))
                    self.assertIs(node["additionalProperties"], False)
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(crc.load_schema())


class MainTests(unittest.TestCase):
    def run_main(self, text, argv=()):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(text)), mock.patch("sys.stdout", out), \
                mock.patch("sys.stderr", err):
            code = crc.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_valid_exits_zero(self):
        self.assertEqual(self.run_main(json.dumps(output()))[0], 0)

    def test_violation_exits_one(self):
        code, _, err = self.run_main(json.dumps(output(verdict="CHANGES REQUESTED")))
        self.assertEqual(code, 1)
        self.assertIn("verdict", err)

    def test_bad_json_exits_two(self):
        self.assertEqual(self.run_main("not json")[0], 2)

    def test_head_flag(self):
        self.assertEqual(self.run_main(json.dumps(output()), ["--head", "b" * 40])[0], 1)

    def test_input_not_mutated(self):
        out = output()
        before = copy.deepcopy(out)
        crc.check(out, crc.load_schema(), None)
        self.assertEqual(out, before)


if __name__ == "__main__":
    unittest.main()
