# Contributing

## How this project works

ios-agent-workflow is a scaffold — not a library. You copy the command files, CI workflows, and support scripts into your own iOS project and adapt them to your architecture. Everything in this repo is kept in sync with [FinanceTracker](https://github.com/akshaypimprikar/personal-finance-tracker), a production iOS app where every change is battle-tested before it lands here.

That means the bar for contributions is: **would this hold up on a real production codebase?** Not just in theory.

---

## Ways to contribute

**Bug reports** — something in a command behaves differently than documented, or the agent consistently does the wrong thing. Use the bug report issue template.

**Command improvements** — a rule, check, or instruction that would make an agent more reliable. Use the improvement issue template. Include the failure mode you're solving and the project it occurred on.

**New commands** — if you've built a stage that fits the pipeline (e.g. a localisation agent, a performance audit agent) and run it on a real project, open an issue to discuss before sending a PR.

---

## Adapting for your project

Run the setup script from the cloned repo:

```bash
./scripts/setup.sh MyApp /path/to/your-ios-project [MyScheme]
```

This copies commands, context files, CI workflows, and scripts into your project and substitutes all placeholders. Then:

1. Fill in `CLAUDE.md` with your architecture rules and build commands
2. Populate `.claude/context/invariants.md` with your non-negotiable rules before running `/feature`

The agents read `CLAUDE.md` and `.claude/context/` before every task. That's where project-specific context lives — not inside the command files themselves.

## Contributing to the CI scaffold

The three workflows (`pr-checks.yml`, `ui-tests.yml`, `release.yml`) and the two scripts (`select_simulator.py`, `check_coverage.py`) follow the same rules as commands: changes must be tested on a real iOS project first. The scripts are intentionally project-agnostic — PRs that introduce project-specific assumptions into them won't be merged.

---

## Submitting a PR

- Target `develop`, not `main`
- One command change per PR — keep diffs reviewable
- Include in the PR description: what broke or was missing, what project you tested on, and what the agent did differently after the change
- PRs that haven't been tested on a real project won't be merged

---

## Questions

Open a GitHub Discussion or reach out on [LinkedIn](https://www.linkedin.com/in/akshaypimprikar).
