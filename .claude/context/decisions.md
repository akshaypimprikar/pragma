# Agent Decision Log

<!-- Append one entry per spec run. Never edit past entries. -->
<!-- Format:
## YYYY-MM-DD — <Feature Name>
**Approaches considered:** <brief list>
**Chosen:** <approach name>
**Reason:** <one sentence>
-->

## 2026-11-01 — Provider-swapping: run pragma stages without Claude Code
**Approaches considered:** transport (Claude Code + base-URL proxy to a non-Claude model); stage (`provider:` key + dispatcher inside Claude Code); harness (pragma's skills run inside another agent CLI, Codex first)
**Chosen:** harness, starting with the `review` stage, judged by a JSON stage contract (`review.schema.json` + `scripts/check_review_contract.py`)
**Reason:** only the harness layer lets a team without Claude Code run a pragma stage; Anthropic doesn't support the transport route, and a dispatcher still needs Claude Code (`codex-plugin-cc` already covers the second-opinion case).
