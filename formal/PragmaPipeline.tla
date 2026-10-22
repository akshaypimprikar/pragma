------------------------- MODULE PragmaPipeline -------------------------
(***************************************************************************)
(* Stage transitions of pragma's pipeline for ONE change, from /spec to a  *)
(* merged PR, plus one /release cycle, as the skills and CI scripts        *)
(* implement them. Citations are file:line in the repo at the commit this  *)
(* file was written against.                                               *)
(*                                                                         *)
(* The agent is the adversary. With Compliant = TRUE it follows the skill  *)
(* text. With Compliant = FALSE it may skip or fake any step the skills    *)
(* only ask for, so the invariants then test what the scripts and CI       *)
(* actually enforce. A property that holds only when Compliant = TRUE is   *)
(* a rule that rests on the agent behaving.                                *)
(***************************************************************************)
EXTENDS Naturals, FiniteSets, PipelineLanes

CONSTANTS
  MaxTasks,    \* plan tasks in the change
  MaxHead,     \* bound on commits on the PR branch (model bound only)
  MaxPosts,    \* bound on verdict reviews posted (model bound only)
  Compliant,   \* TRUE: agent follows the skills; FALSE: agent may skip or fake steps
  PRShapes,    \* the PR shapes the feature flow may open, a subset of PRs
  RelShapes    \* the shapes a release/* PR may have, a subset of PRs

VARIABLES
  stage,       \* feature-flow stage, see StageSet
  tasks,       \* per plan task: "todo" | "red" | "green" | "bundled"
  head,        \* number of commits on the branch; the head SHA is its index
  cl,          \* cl[i] = class of commit i: "code" | "log" | "docs"
  pr,          \* the PR shape, fixed at gh pr create
  gateSha,     \* the commit the last /gates run covered (0 = never)
  gatePass,    \* result of that run
  bodyGate,    \* SHA in the PR body's "Gates run at <sha>" line (0 = none)
  ciState,     \* the required `gates` check on the current head
  ccSha,       \* SHA in the body's "code-review: ... at <sha>" line (0 = none)
  inc,         \* body has a non-empty "Motivating incident:" line
  syn,         \* body has a non-empty "Synced from:" line
  ovr,         \* body has a non-empty "Round cap override:" line
  rounds,      \* full-round verdict reviews posted (what the cap counts)
  confirms,    \* "Round confirm" verdict reviews posted
  lastV,       \* verdict of the latest verdict review: "none" | "APPROVED" | "CHANGES"
  lastSha,     \* its "Reviewed at <sha>"
  evSnap,      \* result of the latest review-evidence run (what branch protection reads)
  audited,     \* /test ran before /gates
  effRounds,   \* reviews that are full rounds in substance, whatever their label
  relStage,    \* release-flow stage
  relPre,      \* /release pre-flight checks ran
  relShape,    \* shape of the release PR
  relEv,       \* a non-release-lane release PR got its lane's evidence
  tagTests,    \* result of the tag-triggered release.yml test jobs
  ghRel        \* who created the GitHub release: "none" | "workflow" | "manual"

vars == << stage, tasks, head, cl, pr, gateSha, gatePass, bodyGate, ciState,
           ccSha, inc, syn, ovr, rounds, confirms, lastV, lastSha, evSnap,
           audited, effRounds, relStage, relPre, relShape, relEv, tagTests, ghRel >>

StageSet == { "idea", "spec_ok", "plan_ok", "impl", "tested", "gated",
              "pr_open", "merged", "escalated" }
RelStages == { "none", "branch", "pr", "merged_main", "tagged", "backmerge", "released" }
Classes == { "code", "log", "docs" }

(***************************************************************************)
(* Evidence at a head SHA, as scripts/check_review_evidence.py evaluates   *)
(* it (:48-58 _sha_ok, :85-131 evaluate).                                  *)
(*                                                                         *)
(* An SHA-tied item is valid at the head when its SHA IS the head, or when *)
(* every later commit changed only carryover paths:                        *)
(*   gate_summary, code_review: carryover_paths = rejections.md and        *)
(*     incidents.md, class "log" (scaffold/pipeline_lanes.json:62-65)      *)
(*   review_verdict: also review_carryover_paths = docs/*.md, CHANGELOG,   *)
(*     README, class "docs" (scaffold/pipeline_lanes.json:56-61)           *)
(***************************************************************************)
ShaOk(s, hd, c, allowed) ==
  /\ s \in 1..hd
  /\ (s = hd \/ \A i \in (s + 1)..hd : c[i] \in allowed)

\* Items hold for the given variable values (used on current and primed state).
EvOK(p, hd, c, bg, cc, lv, ls, rd, ov, ic, sy) ==
  \A it \in Need(p) :
    CASE it = "gate"       -> ShaOk(bg, hd, c, { "log" })
      [] it = "codereview" -> ShaOk(cc, hd, c, { "log" })
      [] it = "incident"   -> ic
      [] it = "synced"     -> sy
      [] it = "verdict"    ->
           \* the latest verdict is APPROVED and reviewed at a still-valid SHA, and the
           \* two-round cap holds (check_review_evidence.py:102-117)
           /\ lv = "APPROVED"
           /\ ShaOk(ls, hd, c, { "log", "docs" })
           /\ (rd <= 2 \/ ov)

EvidenceNow == EvOK(pr, head, cl, bodyGate, ccSha, lastV, lastSha, rounds, ovr, inc, syn)

\* review-evidence runs on opened, synchronize, reopened and edited, NOT on a posted
\* review (.github/workflows/review-evidence.yml:18-19, .claude/skills/review/SKILL.md:329-331).
\* Actions that fire it recompute evSnap from the NEW state.
Refresh ==
  evSnap' = EvOK(pr', head', cl', bodyGate', ccSha', lastV', lastSha', rounds', ovr', inc', syn')

AnyBundled == \E t \in 1..MaxTasks : tasks[t] = "bundled"
AllDone    == \A t \in 1..MaxTasks : tasks[t] \in { "green", "bundled" }
LaneNow    == LaneFor(pr)

DefaultPR == [ branch |-> "work", ch |-> { "docs" }, small |-> FALSE ]

Init ==
  /\ stage = "idea"
  /\ tasks = [t \in 1..MaxTasks |-> "todo"]
  /\ head = 0
  /\ cl = [i \in 1..MaxHead |-> "code"]
  /\ pr = DefaultPR
  /\ gateSha = 0
  /\ gatePass = FALSE
  /\ bodyGate = 0
  /\ ciState = "none"
  /\ ccSha = 0
  /\ inc = FALSE
  /\ syn = FALSE
  /\ ovr = FALSE
  /\ rounds = 0
  /\ confirms = 0
  /\ lastV = "none"
  /\ lastSha = 0
  /\ evSnap = FALSE
  /\ audited = FALSE
  /\ effRounds = 0
  /\ relStage = "none"
  /\ relPre = FALSE
  /\ relShape = CHOOSE s \in RelShapes : TRUE
  /\ relEv = FALSE
  /\ tagTests = "none"
  /\ ghRel = "none"

(***************************************************************************)
(* Feature flow: /spec, /plan, /feature, /test, /gates                     *)
(***************************************************************************)

\* The user approves the spec; /spec appends decisions.md and hands off
\* (.claude/skills/spec/SKILL.md:110-122).
ApproveSpec ==
  /\ stage = "idea"
  /\ stage' = "spec_ok"
  /\ UNCHANGED << tasks, head, cl, pr, gateSha, gatePass, bodyGate, ciState, ccSha, inc, syn, ovr,
                  rounds, confirms, lastV, lastSha, evSnap, audited, effRounds,
                  relStage, relPre, relShape, relEv, tagTests, ghRel >>

\* /plan runs after the user approves a spec and ends when the user approves the plan
\* (.claude/skills/plan/SKILL.md:11-13, :64-65).
ApprovePlan ==
  /\ stage = "spec_ok"
  /\ stage' = "plan_ok"
  /\ UNCHANGED << tasks, head, cl, pr, gateSha, gatePass, bodyGate, ciState, ccSha, inc, syn, ovr,
                  rounds, confirms, lastV, lastSha, evSnap, audited, effRounds,
                  relStage, relPre, relShape, relEv, tagTests, ghRel >>

\* /feature starts on a feature/* branch off develop once the plan is approved
\* (.claude/skills/feature/SKILL.md:11-19).
StartImpl ==
  /\ stage = "plan_ok"
  /\ stage' = "impl"
  /\ UNCHANGED << tasks, head, cl, pr, gateSha, gatePass, bodyGate, ciState, ccSha, inc, syn, ovr,
                  rounds, confirms, lastV, lastSha, evSnap, audited, effRounds,
                  relStage, relPre, relShape, relEv, tagTests, ghRel >>

\* Tasks run in order; each is two commits, RED (test only) then GREEN, and the full
\* suite must pass before the next task (.claude/skills/feature/SKILL.md:24-32).
TasksBefore(t) == \A u \in 1..(t - 1) : tasks[u] \in { "green", "bundled" }

RedCommit(t) ==
  /\ stage = "impl" /\ head < MaxHead
  /\ tasks[t] = "todo" /\ TasksBefore(t)
  /\ tasks' = [tasks EXCEPT ![t] = "red"]
  /\ head' = head + 1
  /\ cl' = [cl EXCEPT ![head + 1] = "code"]
  /\ UNCHANGED << stage, pr, gateSha, gatePass, bodyGate, ciState, ccSha, inc, syn, ovr,
                  rounds, confirms, lastV, lastSha, evSnap, audited, effRounds,
                  relStage, relPre, relShape, relEv, tagTests, ghRel >>

GreenCommit(t) ==
  /\ stage = "impl" /\ head < MaxHead
  /\ tasks[t] = "red"
  /\ tasks' = [tasks EXCEPT ![t] = "green"]
  /\ head' = head + 1
  /\ cl' = [cl EXCEPT ![head + 1] = "code"]
  /\ UNCHANGED << stage, pr, gateSha, gatePass, bodyGate, ciState, ccSha, inc, syn, ovr,
                  rounds, confirms, lastV, lastSha, evSnap, audited, effRounds,
                  relStage, relPre, relShape, relEv, tagTests, ghRel >>

\* A non-compliant agent bundles test and implementation in one commit. /feature forbids it
\* (SKILL.md:29-31); Gate 9 (scripts/check_tdd_commit_order.py:1-12) is what catches it.
BundledCommit(t) ==
  /\ ~Compliant
  /\ stage = "impl" /\ head < MaxHead
  /\ tasks[t] = "todo" /\ TasksBefore(t)
  /\ tasks' = [tasks EXCEPT ![t] = "bundled"]
  /\ head' = head + 1
  /\ cl' = [cl EXCEPT ![head + 1] = "code"]
  /\ UNCHANGED << stage, pr, gateSha, gatePass, bodyGate, ciState, ccSha, inc, syn, ovr,
                  rounds, confirms, lastV, lastSha, evSnap, audited, effRounds,
                  relStage, relPre, relShape, relEv, tagTests, ghRel >>

\* /test audits coverage and commits tests before /gates (.claude/skills/test/SKILL.md:11-13).
\* Nothing in CI or review-evidence checks that it ran.
AuditTests ==
  /\ stage = "impl" /\ AllDone /\ head < MaxHead
  /\ stage' = "tested"
  /\ audited' = TRUE
  /\ head' = head + 1
  /\ cl' = [cl EXCEPT ![head + 1] = "code"]
  /\ UNCHANGED << tasks, pr, gateSha, gatePass, bodyGate, ciState, ccSha, inc, syn, ovr,
                  rounds, confirms, lastV, lastSha, evSnap, effRounds,
                  relStage, relPre, relShape, relEv, tagTests, ghRel >>

\* /gates pins the head SHA (.claude/skills/gates/SKILL.md:23-32). Gate 9 fails when any task
\* bundled its test and code (SKILL.md:130-153). It can run again after the PR is open, when a
\* fix changes HEAD (SKILL.md:334-335). A compliant agent never skips /test.
RunGates ==
  /\ \/ stage \in { "tested", "gated", "pr_open" }
     \/ (stage = "impl" /\ AllDone /\ ~Compliant)
  /\ gateSha' = head
  /\ gatePass' \in (IF AnyBundled THEN { FALSE } ELSE BOOLEAN)
  /\ stage' = IF stage = "pr_open" THEN "pr_open" ELSE "gated"
  /\ UNCHANGED << tasks, head, cl, pr, bodyGate, ciState, ccSha, inc, syn, ovr,
                  rounds, confirms, lastV, lastSha, evSnap, audited, effRounds,
                  relStage, relPre, relShape, relEv, tagTests, ghRel >>

\* gh pr create follows a gate summary at the pinned SHA; a failed gate means "do NOT open
\* the PR" (.claude/skills/gates/SKILL.md:21, :247-252, :291-337). The summary goes into the PR
\* body, and opening the PR fires both required checks.
OpenPR ==
  /\ stage = "gated" /\ gateSha = head
  /\ (Compliant => gatePass)
  /\ pr' \in PRShapes
  /\ stage' = "pr_open"
  /\ bodyGate' = gateSha
  /\ ciState' = "pending"
  /\ UNCHANGED << tasks, head, cl, gateSha, gatePass, ccSha, inc, syn, ovr,
                  rounds, confirms, lastV, lastSha, audited, effRounds,
                  relStage, relPre, relShape, relEv, tagTests, ghRel >>
  /\ Refresh

(***************************************************************************)
(* PR flow: pushes, CI, /pr-followup, /review, merge                       *)
(***************************************************************************)

\* /gates re-run after the PR is open: update the body's SHA (gates/SKILL.md:334-335). A
\* non-compliant agent may write any SHA; check_review_evidence.py reads the line only
\* (:90-96) and cannot tell whether the gates really ran there.
EditBodyGate ==
  /\ stage = "pr_open"
  /\ \E c \in (IF Compliant THEN { gateSha } ELSE 1..head) :
       /\ c \in 1..head /\ c # bodyGate
       /\ bodyGate' = c
  /\ UNCHANGED << stage, tasks, head, cl, pr, gateSha, gatePass, ciState, ccSha, inc, syn, ovr,
                  rounds, confirms, lastV, lastSha, audited, effRounds,
                  relStage, relPre, relShape, relEv, tagTests, ghRel >>
  /\ Refresh

\* A new commit on the PR branch (a fix, a code-review fix, a docs touch-up). The class says
\* which paths it changed. It restarts CI and fires review-evidence (synchronize).
PushCommit(c) ==
  /\ stage = "pr_open" /\ head < MaxHead
  /\ head' = head + 1
  /\ cl' = [cl EXCEPT ![head + 1] = c]
  /\ ciState' = "pending"
  /\ UNCHANGED << stage, tasks, pr, gateSha, gatePass, bodyGate, ccSha, inc, syn, ovr,
                  rounds, confirms, lastV, lastSha, audited, effRounds,
                  relStage, relPre, relShape, relEv, tagTests, ghRel >>
  /\ Refresh

\* The required `gates` job: Gate 9, Gate 11 and citations run from the base branch, and are
\* skipped on the release lane (scaffold/.github/workflows/gates.yml:83-108). A task whose
\* test and code share a commit fails Gate 9 for good. Any other failure is a nondeterministic
\* check result.
CIResolve ==
  /\ stage = "pr_open" /\ ciState = "pending"
  /\ ciState' \in (IF AnyBundled /\ LaneNow # "release" THEN { "fail" } ELSE { "pass", "fail" })
  /\ UNCHANGED << stage, tasks, head, cl, pr, gateSha, gatePass, bodyGate, ccSha, inc, syn, ovr,
                  rounds, confirms, lastV, lastSha, evSnap, audited, effRounds,
                  relStage, relPre, relShape, relEv, tagTests, ghRel >>

\* app lane: built-in code-review at medium effort, then a "code-review: <url> at <sha>"
\* body line (.claude/skills/pr-followup/SKILL.md:52-55). The compliant agent runs it only
\* when the lane asks for it, and writes the head SHA.
WriteCodeReview ==
  /\ stage = "pr_open"
  /\ (Compliant => "codereview" \in Need(pr))
  /\ \E c \in (IF Compliant THEN { head } ELSE 1..head) :
       ccSha' = c
  /\ UNCHANGED << stage, tasks, head, cl, pr, gateSha, gatePass, bodyGate, ciState, inc, syn, ovr,
                  rounds, confirms, lastV, lastSha, audited, effRounds,
                  relStage, relPre, relShape, relEv, tagTests, ghRel >>
  /\ Refresh

\* "Motivating incident:" body line (pr-followup/SKILL.md:38-40) and "Synced from:" line.
AddIncident ==
  /\ stage = "pr_open"
  /\ (Compliant => "incident" \in Need(pr))
  /\ inc' = TRUE
  /\ UNCHANGED << stage, tasks, head, cl, pr, gateSha, gatePass, bodyGate, ciState, ccSha, syn, ovr,
                  rounds, confirms, lastV, lastSha, audited, effRounds,
                  relStage, relPre, relShape, relEv, tagTests, ghRel >>
  /\ Refresh

AddSynced ==
  /\ stage = "pr_open"
  /\ (Compliant => "synced" \in Need(pr))
  /\ syn' = TRUE
  /\ UNCHANGED << stage, tasks, head, cl, pr, gateSha, gatePass, bodyGate, ciState, ccSha, inc, ovr,
                  rounds, confirms, lastV, lastSha, audited, effRounds,
                  relStage, relPre, relShape, relEv, tagTests, ghRel >>
  /\ Refresh

\* Any edit of the PR body fires review-evidence again. /review ends with one such edit,
\* the "Review: <url> at <sha>" line (review/SKILL.md:329-331). A compliant /review does it
\* inside its own step, so only a non-compliant agent needs this action.
EditBody ==
  /\ ~Compliant /\ stage = "pr_open"
  /\ UNCHANGED << stage, tasks, head, cl, pr, gateSha, gatePass, bodyGate, ciState, ccSha, inc, syn, ovr,
                  rounds, confirms, lastV, lastSha, audited, effRounds,
                  relStage, relPre, relShape, relEv, tagTests, ghRel >>
  /\ Refresh

\* A full-round verdict review. Round 1 reviews the whole PR; round 2 runs only after a
\* CHANGES REQUESTED round 1 and can only approve, because after round 2 the remaining
\* non-HIGH findings become issues and a remaining HIGH goes to the user
\* (.claude/skills/review/SKILL.md:33-40). Gate verification: APPROVED needs the gate line
\* at the head SHA and a passing `gates` job (review/SKILL.md:70-74, :110-117). The lane
\* decides whether /review runs at all (review/SKILL.md:28-31, pr-followup/SKILL.md:34-47).
\* Posted as a separate review object; a compliant agent then edits the body (SKILL.md:329-331).
PostRound(v) ==
  /\ stage = "pr_open" /\ rounds + confirms < MaxPosts
  /\ Compliant =>
       /\ "verdict" \in Need(pr)
       /\ \/ rounds = 0
          \/ (rounds = 1 /\ lastV = "CHANGES" /\ v = "APPROVED")
       /\ ("codereview" \in Need(pr)) => ccSha # 0
       /\ (v = "APPROVED") => (bodyGate = head /\ ciState = "pass")
  /\ \E s \in (IF Compliant THEN { head } ELSE 1..head) :
       /\ lastSha' = s
  /\ rounds' = rounds + 1
  /\ effRounds' = effRounds + 1
  /\ lastV' = v
  /\ UNCHANGED << stage, tasks, head, cl, pr, gateSha, gatePass, bodyGate, ciState, ccSha, inc, syn, ovr,
                  confirms, audited, relStage, relPre, relShape, relEv, tagTests, ghRel >>
  /\ IF Compliant THEN Refresh ELSE UNCHANGED evSnap

\* `/review --confirm`: after an APPROVED verdict, when the head moved by more than log-only
\* commits, review only the new diff and post "Round confirm". It is not a round
\* (review/SKILL.md:47-49). The cap ignores any review whose body has a "Round confirm" line
\* (check_review_evidence.py:65-67). Nothing checks that a confirm follows an APPROVED
\* verdict at an earlier SHA, so a non-compliant agent can label a full review "confirm";
\* effRounds counts it as the full round it is.
MovedByCode == \E i \in (lastSha + 1)..head : cl[i] = "code"

PostConfirm(v) ==
  /\ stage = "pr_open" /\ rounds + confirms < MaxPosts
  /\ Compliant => (lastV = "APPROVED" /\ MovedByCode)
  /\ \E s \in (IF Compliant THEN { head } ELSE 1..head) :
       /\ lastSha' = s
  /\ confirms' = confirms + 1
  /\ effRounds' = effRounds + (IF lastV = "APPROVED" /\ MovedByCode THEN 0 ELSE 1)
  /\ lastV' = v
  /\ UNCHANGED << stage, tasks, head, cl, pr, gateSha, gatePass, bodyGate, ciState, ccSha, inc, syn, ovr,
                  rounds, audited, relStage, relPre, relShape, relEv, tagTests, ghRel >>
  /\ IF Compliant THEN Refresh ELSE UNCHANGED evSnap

\* After a CHANGES REQUESTED round 1 and a still-blocking HIGH after round 2, the
\* decision goes to the user (review/SKILL.md:37-38). The flow stops here.
Escalate ==
  /\ Compliant /\ stage = "pr_open"
  /\ rounds = 1 /\ lastV = "CHANGES"
  /\ stage' = "escalated"
  /\ UNCHANGED << tasks, head, cl, pr, gateSha, gatePass, bodyGate, ciState, ccSha, inc, syn, ovr,
                  rounds, confirms, lastV, lastSha, evSnap, audited, effRounds,
                  relStage, relPre, relShape, relEv, tagTests, ghRel >>

\* The user adds "Round cap override: <reason>" to the PR body (review/SKILL.md:43-44).
UserOverride ==
  /\ stage = "pr_open" /\ rounds >= 2 /\ ~ovr
  /\ ovr' = TRUE
  /\ UNCHANGED << stage, tasks, head, cl, pr, gateSha, gatePass, bodyGate, ciState, ccSha, inc, syn,
                  rounds, confirms, lastV, lastSha, audited, effRounds,
                  relStage, relPre, relShape, relEv, tagTests, ghRel >>
  /\ Refresh

\* No command merges. The user merges when the required checks `gates` and `review-evidence`
\* are green (scripts/setup.sh:269-271). What branch protection reads is the LAST run of each
\* check, so the guard is evSnap, not the live evidence.
Merge ==
  /\ stage = "pr_open" /\ ciState = "pass" /\ evSnap
  /\ stage' = "merged"
  /\ UNCHANGED << tasks, head, cl, pr, gateSha, gatePass, bodyGate, ciState, ccSha, inc, syn, ovr,
                  rounds, confirms, lastV, lastSha, evSnap, audited, effRounds,
                  relStage, relPre, relShape, relEv, tagTests, ghRel >>

(***************************************************************************)
(* Release flow: /release (.claude/skills/release/SKILL.md)                *)
(***************************************************************************)
RelUnchangedFeature ==
  UNCHANGED << stage, tasks, head, cl, pr, gateSha, gatePass, bodyGate, ciState, ccSha, inc, syn, ovr,
               rounds, confirms, lastV, lastSha, evSnap, audited, effRounds >>

\* Pre-flight: tests pass on develop, no TODO, no force-unwrap (release/SKILL.md:15-39).
RelPreflight ==
  /\ relStage = "none" /\ ~relPre
  /\ relPre' = TRUE
  /\ RelUnchangedFeature
  /\ UNCHANGED << relStage, relShape, relEv, tagTests, ghRel >>

\* Branch release/<version> off develop (release/SKILL.md:41-46). Nothing but the skill text
\* ties this to the pre-flight run.
RelBranch ==
  /\ relStage = "none" /\ (Compliant => relPre)
  /\ relStage' = "branch"
  /\ RelUnchangedFeature
  /\ UNCHANGED << relPre, relShape, relEv, tagTests, ghRel >>

\* Open the PR to main (release/SKILL.md:93-105). The lane comes from the paths changed on
\* the release branch (scripts/check_pr_lane.py:136-139).
RelOpenPR ==
  /\ relStage = "branch"
  /\ relStage' = "pr"
  /\ relShape' \in RelShapes
  /\ RelUnchangedFeature
  /\ UNCHANGED << relPre, relEv, tagTests, ghRel >>

\* A release PR that is not laned release must gather its lane's full evidence first
\* (release/SKILL.md:96, check_pr_lane.py:138-139). Abstracted to one flag.
RelEvidence ==
  /\ relStage = "pr" /\ LaneFor(relShape) # "release" /\ ~relEv
  /\ relEv' = TRUE
  /\ RelUnchangedFeature
  /\ UNCHANGED << relStage, relPre, relShape, tagTests, ghRel >>

\* Merge with a merge commit (release/SKILL.md:105). A release-lane PR needs no evidence
\* (EvRelease = {}) and the gates job skips it (gates.yml:83).
RelMerge ==
  /\ relStage = "pr" /\ (LaneFor(relShape) = "release" \/ relEv)
  /\ relStage' = "merged_main"
  /\ RelUnchangedFeature
  /\ UNCHANGED << relPre, relShape, relEv, tagTests, ghRel >>

\* Tag and push the tag (release/SKILL.md:107-114). The tag push starts release.yml, whose
\* jobs run the tests and only then create the GitHub release
\* (scaffold/.github/workflows/release.yml:10-12, :130-150).
RelTag ==
  /\ relStage = "merged_main"
  /\ relStage' = "tagged"
  /\ tagTests' = "pending"
  /\ RelUnchangedFeature
  /\ UNCHANGED << relPre, relShape, relEv, ghRel >>

RelTests ==
  /\ tagTests = "pending"
  /\ tagTests' \in { "pass", "fail" }
  /\ RelUnchangedFeature
  /\ UNCHANGED << relStage, relPre, relShape, relEv, ghRel >>

\* Back-merge PR from main to develop (release/SKILL.md:116-122).
RelBackmerge ==
  /\ relStage = "tagged"
  /\ relStage' = "backmerge"
  /\ RelUnchangedFeature
  /\ UNCHANGED << relPre, relShape, relEv, tagTests, ghRel >>

\* The workflow creates the release only after its test jobs pass (release.yml:132).
GhReleaseWorkflow ==
  /\ relStage \in { "tagged", "backmerge" } /\ tagTests = "pass" /\ ghRel = "none"
  /\ ghRel' = "workflow"
  /\ RelUnchangedFeature
  /\ UNCHANGED << relStage, relPre, relShape, relEv, tagTests >>

\* /release step 8 runs `gh release create` itself, without waiting for those jobs
\* (release/SKILL.md:124-130).
GhReleaseManual ==
  /\ relStage \in { "tagged", "backmerge" } /\ ghRel = "none"
  /\ ghRel' = "manual"
  /\ RelUnchangedFeature
  /\ UNCHANGED << relStage, relPre, relShape, relEv, tagTests >>

RelDone ==
  /\ relStage = "backmerge" /\ ghRel # "none"
  /\ relStage' = "released"
  /\ RelUnchangedFeature
  /\ UNCHANGED << relPre, relShape, relEv, tagTests, ghRel >>

Next ==
  \/ ApproveSpec \/ ApprovePlan \/ StartImpl
  \/ \E t \in 1..MaxTasks : RedCommit(t) \/ GreenCommit(t) \/ BundledCommit(t)
  \/ AuditTests \/ RunGates \/ OpenPR
  \/ EditBodyGate \/ CIResolve \/ WriteCodeReview \/ AddIncident \/ AddSynced \/ EditBody
  \/ \E c \in Classes : PushCommit(c)
  \/ \E v \in { "APPROVED", "CHANGES" } : PostRound(v) \/ PostConfirm(v)
  \/ Escalate \/ UserOverride \/ Merge
  \/ RelPreflight \/ RelBranch \/ RelOpenPR \/ RelEvidence \/ RelMerge \/ RelTag
  \/ RelTests \/ RelBackmerge \/ GhReleaseWorkflow \/ GhReleaseManual \/ RelDone

Spec == Init /\ [][Next]_vars

(***************************************************************************)
(* Invariants expected to hold                                             *)
(***************************************************************************)

TypeOK ==
  /\ stage \in StageSet
  /\ tasks \in [1..MaxTasks -> { "todo", "red", "green", "bundled" }]
  /\ head \in 0..MaxHead
  /\ cl \in [1..MaxHead -> Classes]
  /\ pr \in PRs
  /\ gateSha \in 0..MaxHead /\ bodyGate \in 0..MaxHead /\ ccSha \in 0..MaxHead
  /\ lastSha \in 0..MaxHead
  /\ gatePass \in BOOLEAN /\ inc \in BOOLEAN /\ syn \in BOOLEAN /\ ovr \in BOOLEAN
  /\ evSnap \in BOOLEAN /\ audited \in BOOLEAN
  /\ ciState \in { "none", "pending", "pass", "fail" }
  /\ lastV \in { "none", "APPROVED", "CHANGES" }
  /\ rounds \in Nat /\ confirms \in Nat /\ effRounds \in Nat
  /\ relStage \in RelStages
  /\ relPre \in BOOLEAN /\ relEv \in BOOLEAN
  /\ relShape \in PRs
  /\ tagTests \in { "none", "pending", "pass", "fail" }
  /\ ghRel \in { "none", "workflow", "manual" }

\* The lane rules hold for the PR that is open (see PipelineLanes.LaneProps).
LaneProperties == LaneProps(pr)

\* Stages are reached in order: no PR before a /gates run at its head, no merge without a PR.
StageOrder ==
  /\ stage \in { "pr_open", "merged", "escalated" } => gateSha \in 1..MaxHead
  /\ stage \in { "tested", "gated", "pr_open", "merged", "escalated" } => AllDone
  /\ stage = "merged" => ciState = "pass"

\* No merge without the evidence the lane requires, valid at the head SHA (or a SHA that
\* only carryover commits follow). The user-facing statement of the Merge rule.
MergeNeedsEvidence == stage = "merged" => EvidenceNow

\* A task whose test and code share a commit never merges: Gate 9 in CI catches it.
NoBundledMerged == stage = "merged" => ~AnyBundled

\* The two-round cap: a merged PR that needs a review has at most two full rounds, or an
\* override line (check_review_evidence.py:44, :113-117).
RoundCap == (stage = "merged" /\ "verdict" \in Need(pr)) => (rounds <= 2 \/ ovr)

\* With a compliant agent the review loop stays inside the cap without any override:
\* the review loop terminates after at most 2 rounds (review/SKILL.md:33-40).
RoundsBounded == Compliant => (rounds <= 2 \/ ovr)

\* /review's verdict binds to the head: an APPROVED verdict at the head of a merged PR that
\* needs a review (the user-facing form of check_review_evidence.py:102-117).
ApprovedAtHead ==
  (stage = "merged" /\ "verdict" \in Need(pr)) =>
    (lastV = "APPROVED" /\ ShaOk(lastSha, head, cl, { "log", "docs" }))

\* Release: nothing is tagged until the release PR merged, and a release PR that is not
\* laned release only merges with its lane's evidence.
ReleaseOrder ==
  /\ relStage \in { "merged_main", "tagged", "backmerge", "released" }
       => (LaneFor(relShape) = "release" \/ relEv)
  /\ ghRel # "none" => relStage \in { "tagged", "backmerge", "released" }
  /\ relStage = "released" => ghRel # "none"

(***************************************************************************)
(* Properties that do NOT hold. Each Gap_*.cfg checks one and is expected  *)
(* to fail; the counterexample is the finding.                             *)
(***************************************************************************)

\* GAP 1 (Compliant = FALSE). review-evidence is not rerun by a posted review, so a review
\* posted after the last run can leave a green check on a PR whose latest verdict is no
\* longer APPROVED, or that went past the cap.
\* Gap_StaleCheck.cfg: INVARIANT MergeNeedsEvidence

\* GAP 2 (Compliant = FALSE). The cap counts verdict reviews that lack a "Round confirm"
\* line; nothing checks a confirm follows an APPROVED verdict. A third full review labelled
\* "Round confirm" passes the cap.
ConfirmLabelHonest == (stage = "merged") => (effRounds <= 2 \/ ovr)

\* GAP 3 (Compliant = FALSE). The gate_summary check reads the SHA in the body line and never
\* the gate results, and CI does not rerun build, tests or coverage as a required check.
\* A merged PR whose gate evidence is satisfied by a line the agent wrote.
GateLineBacksARun ==
  (stage = "merged" /\ "gate" \in Need(pr)) => (gatePass /\ gateSha = bodyGate)

\* GAP 4 (Compliant = FALSE). Nothing checks that /test ran.
TestRanBeforeMerge == stage = "merged" => audited

\* GAP 5 (any Compliant). The sync lane needs only "Synced from:" even for guard files.
\* GAP 6 (any Compliant). The release lane needs nothing even when project.pbxproj changes.
SyncLaneReviewsGuards == stage \in { "pr_open", "merged" } => SyncGuardedReviewed(pr)
ReleaseLaneReviewsBuildInputs ==
  relStage \in { "pr", "merged_main", "tagged", "backmerge", "released" }
    => ReleaseBuildInputsReviewed(relShape)

\* GAP 7 (Compliant = FALSE). Nothing ties branch and tag to the pre-flight run.
PreflightBeforeBranch == relStage # "none" => relPre

\* GAP 8 (any Compliant). The skill's step 8 creates the GitHub release without waiting for
\* the tag-triggered test jobs that gate release.yml's own release step.
GhReleaseAfterTests == ghRel # "none" => tagTests = "pass"

(***************************************************************************)
(* Model inputs: the PR shapes TLC explores. One shape per lane outcome.   *)
(* PipelineLanes checks all 2550 shapes with ASSUME; these keep the        *)
(* state space small.                                                      *)
(***************************************************************************)
Shape(b, c, s) == [ branch |-> b, ch |-> c, small |-> s ]

ShapesMain ==
  { Shape("work", { "app_src" }, FALSE),                  \* app
    Shape("work", { "app_src", "pipe_plain" }, FALSE),    \* app, owes the incident too
    Shape("work", { "pipe_plain" }, TRUE),                \* small pipeline PR: no review
    Shape("work", { "pipe_guarded" }, TRUE),              \* small but touches a guard file
    Shape("work", { "pipe_plain" }, FALSE),               \* pipeline
    Shape("sync", { "skill_guarded" }, FALSE),            \* sync lane
    Shape("work", { "docs" }, FALSE),                     \* docs lane
    Shape("fork", { "skill_plain" }, FALSE) }             \* fork: pipeline, never sync

RelShapesMain ==
  { Shape("release", { "pbxproj", "changelog" }, FALSE),  \* release lane
    Shape("release", { "pbxproj" }, FALSE),               \* release lane, build settings only
    Shape("release", { "app_src", "pbxproj" }, FALSE) }   \* not release: laned app

=============================================================================
