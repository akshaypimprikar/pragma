import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import review_verdict as rv  # noqa: E402


def f(sev, **kw):
    return dict(id=kw.pop("id", "x"), severity=sev, **kw)


class DecideTests(unittest.TestCase):
    def test_high_always_blocks(self):
        self.assertEqual(rv.decide([f("HIGH", advisory=True)])["verdict"], "CHANGES REQUESTED")

    def test_medium_in_diff_blocks(self):
        self.assertEqual(rv.decide([f("MEDIUM", in_diff=True)])["blocking"], ["x"])

    def test_medium_outside_diff_gets_issue(self):
        r = rv.decide([f("MEDIUM")])
        self.assertEqual((r["verdict"], r["issues"]), ("APPROVED", ["x"]))

    def test_advisory_medium_never_blocks(self):
        self.assertEqual(rv.decide([f("MEDIUM", in_diff=True, advisory=True)])["verdict"], "APPROVED")

    def test_medium_exceptions_block(self):
        self.assertEqual(rv.decide([f("MEDIUM", depends_on_unchanged=True)])["verdict"], "CHANGES REQUESTED")
        self.assertEqual(rv.decide([f("MEDIUM", guard_bypass=True)])["verdict"], "CHANGES REQUESTED")

    def test_advisory_overrides_exception_flags(self):
        self.assertEqual(rv.decide([f("MEDIUM", advisory=True, guard_bypass=True)])["verdict"], "APPROVED")
        self.assertEqual(rv.decide([f("MEDIUM", advisory=True, depends_on_unchanged=True)])["verdict"], "APPROVED")

    def test_low_goes_to_issues(self):
        self.assertEqual(rv.decide([f("LOW")])["issues"], ["x"])

    def test_low_never_blocks(self):
        self.assertEqual(rv.decide([f("LOW", in_diff=True)])["verdict"], "APPROVED")

    def test_dismissed_ignored(self):
        r = rv.decide([f("HIGH", dismissed=True)])
        self.assertEqual((r["verdict"], r["blocking"], r["issues"]), ("APPROVED", [], []))

    def test_no_findings_approved(self):
        self.assertEqual(rv.decide([])["verdict"], "APPROVED")


class MainTests(unittest.TestCase):
    def run_main(self, text):
        with mock.patch("sys.stdin", io.StringIO(text)):
            return rv.main()

    def run_main_out(self, text):
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(text)), mock.patch("sys.stdout", out):
            return rv.main(), out.getvalue()

    def test_string_flag_exits_2(self):
        self.assertEqual(self.run_main_out('[{"id": "a", "severity": "MEDIUM", "advisory": "false"}]')[0], 2)

    def test_missing_or_empty_or_non_string_id_exits_2(self):
        for bad in ('{"severity": "LOW"}', '{"id": "", "severity": "LOW"}', '{"id": 1, "severity": "LOW"}'):
            self.assertEqual(self.run_main_out(f"[{bad}]")[0], 2)

    def test_unflagged_medium_exits_2(self):
        self.assertEqual(self.run_main_out('[{"id": "a", "severity": "MEDIUM"}]')[0], 2)
        self.assertEqual(self.run_main_out('[{"id": "a", "severity": "MEDIUM", "in_diff": false}]')[0], 0)
        self.assertEqual(self.run_main_out('[{"id": "a", "severity": "MEDIUM", "advisory": true}]')[0], 0)

    def test_duplicate_id_exits_2(self):
        self.assertEqual(self.run_main_out('[{"id": "a", "severity": "LOW"}, {"id": "a", "severity": "HIGH"}]')[0], 2)

    def test_missing_or_lowercase_severity_exits_2(self):
        self.assertEqual(self.run_main_out('[{"id": "a"}]')[0], 2)
        self.assertEqual(self.run_main_out('[{"id": "a", "severity": "high"}]')[0], 2)

    def test_empty_stdin_exits_2(self):
        self.assertEqual(self.run_main_out("")[0], 2)

    def test_printed_json(self):
        rc, out = self.run_main_out('[{"id": "a", "severity": "LOW"}, {"id": "b", "severity": "HIGH"}]')
        self.assertEqual((rc, json.loads(out)), (0, {"verdict": "CHANGES REQUESTED", "blocking": ["b"], "issues": ["a"]}))

    def test_bad_severity_exits_2(self):
        self.assertEqual(self.run_main('[{"id": "a", "severity": "SEVERE"}]'), 2)

    def test_non_list_exits_2(self):
        self.assertEqual(self.run_main("{}"), 2)

    def test_invalid_json_exits_2(self):
        self.assertEqual(self.run_main("{not json"), 2)

    def test_valid_exits_0(self):
        self.assertEqual(self.run_main_out("[]")[0], 0)


if __name__ == "__main__":
    unittest.main()
