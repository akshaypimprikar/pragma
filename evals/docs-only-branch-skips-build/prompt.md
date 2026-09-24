---
description: A docs-only branch. The skill's Gate 0 says to skip the build and test gates and to report them as skipped, not passed.
tags: [smoke, gate-0]
max_turns: 8
allowed_tools: [Read, Glob, Grep, Skill]
---

My branch only changes README.md and CHANGELOG.md, no source files. Do I need to run the full xcodebuild build and test suite before I open the PR? If I skip any checks, how should I report that?
