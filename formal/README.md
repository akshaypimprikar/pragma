# TLA+ model of pragma's pipeline

A model of the stage transitions in pragma's skills and CI scripts: `/spec`, `/plan`, `/feature`, `/test`, `/gates`, `/pr-followup`, `/review`, the merge, and `/release`. Every transition cites the file and line it comes from in a comment.

| File | What it is |
|---|---|
| `PipelineLanes.tla` | Lane sorting and per-lane evidence, mirrors `scripts/check_pr_lane.py`. Its `ASSUME` checks 7 lane rules on all 2550 PR shapes. |
| `PragmaPipeline.tla` | One change from `/spec` to merge, plus one `/release`. The agent may skip or fake steps; CI and the scripts are the guards. |
| `PragmaPipeline.cfg` | Main run. Every invariant must hold. |
| `Gap_*.cfg` | One run per known gap. Each is expected to FAIL; the counterexample is the finding. |
| `conformance_lanes.py` | Needs no Java. Runs all 2550 PR shapes through the real `check_pr_lane.py` and compares with the model's lane and evidence rules. |

## Status

TLC has not been run on these files. The machine that wrote them has no Java and no `tla2tools.jar`, and nothing was installed. Two checks did run:

- `python3 formal/conformance_lanes.py` prints `2550 shapes checked, 0 mismatches`.
- A throwaway Python copy of the state machine (not committed) found no violation of the main invariants over 510,656 states (`MaxHead = 4`), and reproduced every gap below. Run TLC to confirm; a syntax slip in the `.tla` files is possible.

## Run TLC

```bash
brew install openjdk                      # or any JDK 11+
curl -L -o /tmp/tla2tools.jar https://github.com/tlaplus/tlaplus/releases/latest/download/tla2tools.jar
export PATH="$(brew --prefix openjdk)/bin:$PATH"
cd formal
java -cp /tmp/tla2tools.jar tlc2.TLC -deadlock -config PragmaPipeline.cfg PragmaPipeline   # expect: no error
for g in Gap_*.cfg; do echo "== $g"; java -cp /tmp/tla2tools.jar tlc2.TLC -deadlock -config "$g" PragmaPipeline | grep -E "Invariant|No error"; done   # expect: Invariant ... is violated
```

`-deadlock` is needed because the model has end states (merged, escalated, released). Add `-workers auto` to use all cores.

## What the main run checks (`Compliant = TRUE`)

| Invariant | Meaning |
|---|---|
| `MergeNeedsEvidence` | A merged PR holds every evidence item its lane needs, valid at the head SHA or an SHA followed only by carryover commits. |
| `StageOrder` | No PR without a `/gates` run, no merge without a passing `gates` check. |
| `NoBundledMerged` | A task whose test and code share a commit never merges (Gate 9 in CI). |
| `RoundCap`, `RoundsBounded` | At most 2 full review rounds without an override line. The loop stops after round 2. |
| `ApprovedAtHead` | The latest verdict is APPROVED and bound to the head. |
| `LaneProperties` | The 7 lane rules in `PipelineLanes.LaneProps`. |
| `ReleaseOrder` | No tag before the release PR merged, and a non-release-lane release PR needs its evidence. |

## Gaps the model shows (each `Gap_*.cfg`)

None of these is a bug in pipeline code that anyone has reported. Each is a rule that rests on the agent following the skill text, or a path a lane exempts on purpose. Decide per item.

| Config | Compliant | Finding |
|---|---|---|
| `Gap_StaleCheck` | FALSE | `review-evidence` does not rerun on a posted review (`.github/workflows/review-evidence.yml:19`, `.claude/skills/review/SKILL.md:329-331`). A review posted after the last run, such as CHANGES REQUESTED or a third round, leaves a green check. The skill closes this with a body edit after each review, which nothing enforces. |
| `Gap_ConfirmLabel` | FALSE | The cap skips any review with a `Round confirm` line (`scripts/check_review_evidence.py:65-67`). Nothing checks a confirm follows an APPROVED verdict, so a full review labelled confirm is not counted, and confirms have no cap. |
| `Gap_GateLine` | FALSE | `gate_summary` reads only the SHA in `Gates run at <sha>` (`scripts/check_review_evidence.py:90-96`), never the gate results. Build and tests are not a required check in the Merge rule (`scripts/setup.sh:271`). |
| `Gap_TestSkipped` | FALSE | Nothing checks that `/test` ran before `/gates`. |
| `Gap_Preflight` | FALSE | Nothing ties the release branch to the pre-flight run. |
| `Gap_SyncLane` | TRUE | The sync lane asks only for `Synced from:`, also for guard files (gates and review skills, scaffold hooks and workflows). By design: `.claude/skills/sync-workflow/SKILL.md:11`. Exists only in pragma's own lane config. |
| `Gap_ReleaseLane` | TRUE | A release PR that changes only `project.pbxproj` and the changelog needs no gate summary and no review, and the `gates` job skips it (`scaffold/.github/workflows/gates.yml:83`). A build-setting edit can break the build (`.claude/skills/gates/SKILL.md:44-47`). `/release` step 5 checks paths only. |
| `Gap_GhRelease` | TRUE | `/release` step 8 runs `gh release create` without waiting for the tag-triggered test jobs (`.claude/skills/release/SKILL.md:124-130`), while `release.yml` creates the release only after them (`scaffold/.github/workflows/release.yml:132`). Both create the same release. |

## Scope and limits

- Models the scaffold lane config (`scaffold/pipeline_lanes.json`) plus the `sync` block from `scripts/pipeline_lanes.json`. pragma's own config has no `app` lane and asks for no gate summary, so `Gap_GateLine` does not apply there.
- A path is abstracted to its kind (the glob class), and a commit to a class (`code`, `log`, `docs`). Line counts are one bit: under 30 changed lines or not.
- Model bounds: `MaxTasks = 1`, `MaxHead = 5`, `MaxPosts = 3`. Change them in the `.cfg` files.
- Liveness is not checked. In a bounded model, states that run out of commits look like stuck runs. The review loop bound is a safety invariant (`RoundsBounded`) instead.
- `/review` findings, `review_verdict.py` severity rules, Gates 3 to 8 and 10, and the guard hook are not modelled.
