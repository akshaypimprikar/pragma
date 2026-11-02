"""install_guard_hook.py registers the guard for Claude Code and Codex CLI.

Claude Code reads .claude/settings.json; Codex reads .codex/hooks.json (same
hooks/PreToolUse shape, regex matcher, no $CLAUDE_PROJECT_DIR). Both entries
run the one copy in .claude/hooks/. The end-to-end test runs the registered
Codex command the way Codex does (sh, session cwd, hook JSON on stdin).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
INSTALL = os.path.join(ROOT, "scripts", "install_guard_hook.py")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import install_guard_hook as ig  # noqa: E402


def read(path):
    with open(path, encoding="utf8") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf8") as f:
        f.write(text)


class InstallGuardHookTests(unittest.TestCase):
    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", self.d]))
        self.claude = os.path.join(self.d, ".claude", "settings.json")
        self.codex = os.path.join(self.d, ".codex", "hooks.json")

    def install(self, check=True):
        return subprocess.run([sys.executable, INSTALL, ROOT, self.d], capture_output=True, text=True, check=check)

    def entries(self, path):
        return json.loads(read(path))["hooks"]["PreToolUse"]

    def test_fresh_install_registers_both(self):
        self.install()
        self.assertEqual(self.entries(self.claude), [{"matcher": ig.MATCHER, "hooks": [{"type": "command", "command": ig.COMMAND}]}])
        self.assertEqual(self.entries(self.codex), [{"matcher": ig.CODEX_MATCHER, "hooks": [{"type": "command", "command": ig.CODEX_COMMAND}]}])

    def test_rerun_is_a_no_op(self):
        self.install()
        before = (read(self.claude), read(self.codex))
        out = self.install().stdout
        self.assertEqual((read(self.claude), read(self.codex)), before)
        self.assertIn(".codex/hooks.json already registers", out)
        self.assertFalse([f for f in os.listdir(os.path.join(self.d, ".codex")) if ".bak-" in f])

    def test_existing_claude_install_gains_codex_entry(self):
        os.makedirs(os.path.dirname(self.claude))
        write(self.claude, json.dumps(
            {"hooks": {"PreToolUse": [{"matcher": ig.MATCHER, "hooks": [{"type": "command", "command": ig.COMMAND}]}]}}))
        out = self.install().stdout
        self.assertIn("settings.json already registers", out)
        self.assertEqual(self.entries(self.codex)[0]["matcher"], ig.CODEX_MATCHER)

    def test_existing_codex_hooks_are_kept_and_backed_up(self):
        os.makedirs(os.path.dirname(self.codex))
        other = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo other"}]}
        write(self.codex, json.dumps({"hooks": {"PreToolUse": [other], "Stop": [{"hooks": []}]}}))
        self.install()
        data = json.loads(read(self.codex))
        self.assertEqual(data["hooks"]["PreToolUse"][0], other)
        self.assertEqual(data["hooks"]["Stop"], [{"hooks": []}])
        self.assertTrue([f for f in os.listdir(os.path.join(self.d, ".codex")) if f.startswith("hooks.json.bak-")])

    def test_invalid_codex_file_writes_nothing(self):
        os.makedirs(os.path.dirname(self.codex))
        write(self.codex, "{not json")
        proc = self.install(check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("hooks.json is not valid JSON", proc.stderr)
        self.assertFalse(os.path.exists(self.claude))
        self.assertFalse(os.path.exists(os.path.join(self.d, ".claude", "hooks")))
        self.assertEqual(read(self.codex), "{not json")

    def test_codex_command_blocks_apply_patch_on_feature_branch(self):
        git = ["git", "-C", self.d]
        subprocess.run(git + ["init", "-q"], check=True)
        write(os.path.join(self.d, "CLAUDE.md"), "# Demo\n")
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"], check=True)
        subprocess.run(git + ["checkout", "-q", "-b", "feature/demo"], check=True)
        self.install()
        sub = os.path.join(self.d, "App")
        os.makedirs(sub)

        def run(rel):
            payload = {"tool_name": "apply_patch", "cwd": sub, "hook_event_name": "PreToolUse",
                       "tool_input": {"command": f"*** Begin Patch\n*** Update File: {rel}\n@@\n+x\n*** End Patch\n"}}
            env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
            return subprocess.run(["sh", "-c", ig.CODEX_COMMAND], cwd=sub, input=json.dumps(payload),
                                  capture_output=True, text=True, env=env)

        blocked = run("../CLAUDE.md")
        self.assertEqual(blocked.returncode, 2, blocked.stderr)
        self.assertIn("BLOCKED", blocked.stderr)
        self.assertEqual(run("A.swift").returncode, 0)

    def test_project_below_git_root_points_codex_at_its_hook(self):
        git = ["git", "-C", self.d]
        subprocess.run(git + ["init", "-q"], check=True)
        proj = os.path.join(self.d, "ios")
        os.makedirs(proj)
        write(os.path.join(proj, "CLAUDE.md"), "# Demo\n")
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"], check=True)
        subprocess.run(git + ["checkout", "-q", "-b", "feature/demo"], check=True)
        subprocess.run([sys.executable, INSTALL, ROOT, proj], capture_output=True, text=True, check=True)
        entry = json.loads(read(os.path.join(proj, ".codex", "hooks.json")))["hooks"]["PreToolUse"][0]
        command = entry["hooks"][0]["command"]
        self.assertEqual(command, ig.codex_command("ios"))
        payload = {"tool_name": "apply_patch", "cwd": proj, "hook_event_name": "PreToolUse",
                   "tool_input": {"command": "*** Begin Patch\n*** Update File: CLAUDE.md\n@@\n+x\n*** End Patch\n"}}
        proc = subprocess.run(["sh", "-c", command], cwd=proj, input=json.dumps(payload), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2, proc.stderr)


if __name__ == "__main__":
    unittest.main()
