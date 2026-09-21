---
description: A user asks whether a finished branch is ready for a PR. The skill should steer the answer toward scripted, checkable gates.
tags: [smoke, skill-trigger]
max_turns: 8
allowed_tools: [Read, Glob, Grep, Skill]
---

I just finished a feature on my Swift app, on a branch called feature/csv-export. Is it ready for a pull request? What checks should I run first, and in what order? The repo isn't available in this session, so just tell me the checks and their order.
