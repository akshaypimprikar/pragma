-------------------------- MODULE PipelineLanes --------------------------
(***************************************************************************)
(* Pure part of the model: how a pull request is sorted into a lane, and   *)
(* which evidence items the lane requires.                                 *)
(*                                                                         *)
(* Mirrors scripts/check_pr_lane.py with the SCAFFOLD lane config that     *)
(* /pragma:init copies into a project (scaffold/pipeline_lanes.json).      *)
(* pragma's own scripts/pipeline_lanes.json differs in three ways: it has  *)
(* no "app" lane, it also has a "sync" block (scaffold has none), and none *)
(* of its lanes ask for the gate summary. See formal/README.md.            *)
(*                                                                         *)
(* A changed path is abstracted to a KIND, the equivalence class of paths  *)
(* that the glob lists treat the same way. formal/conformance_lanes.py     *)
(* checks this abstraction against the real script for every PR shape.     *)
(***************************************************************************)
EXTENDS Naturals, FiniteSets

(* Path kinds. The comment on each names the globs it stands for.          *)
Kinds == { "app_src",        \* YOUR_PROJECT/**, Tests, UITests, *.xctestplan
                             \* (scaffold/pipeline_lanes.json:20-24)
           "pbxproj",        \* *.xcodeproj/project.pbxproj: a release path AND an app path
                             \* (scaffold/pipeline_lanes.json:9-10 and :23)
           "changelog",      \* CHANGELOG.md, .claude/context/feature-log.md (release paths only)
                             \* (scaffold/pipeline_lanes.json:11-12)
           "skill_plain",    \* .claude/skills/**, scaffold/**: pipeline AND sync paths
                             \* (scripts/pipeline_lanes.json:9-12 and :26-27)
           "skill_guarded",  \* the same, but also a small_pr exclude path (gates, review skills,
                             \* scaffold hooks and workflows)
           "pipe_plain",     \* AGENTS.md, CONSTRAINTS.md, docs/superpowers/**: pipeline only
                             \* (scaffold/pipeline_lanes.json:39-42)
           "pipe_guarded",   \* hooks, scripts/check_*, .github/**, lane configs: pipeline only,
                             \* and a small_pr exclude path (scaffold/pipeline_lanes.json:47)
           "docs" }          \* everything else; falls to the default lane

AppK  == { "app_src", "pbxproj" }
RelK  == { "pbxproj", "changelog" }
SyncK == { "skill_plain", "skill_guarded" }
PipeK == { "skill_plain", "skill_guarded", "pipe_plain", "pipe_guarded" }
ExclK == { "skill_guarded", "pipe_guarded" }

(* Head/base branch combinations that lane_for() tells apart                *)
(* (scripts/check_pr_lane.py:133-150, scripts/check_review_evidence.py:77-82). *)
Branches == { "work",       \* feature/*, fix/*, chore/*, spec/*, hotfix/* ...
              "release",    \* head release/*, base main
              "backmerge",  \* head main, base develop
              "sync",       \* head sync/*, same repo
              "fork" }      \* any branch from a fork: "fork:" prefix, never release/sync

Lanes == { "release", "sync", "app", "pipeline", "docs" }

(* Evidence items, as in EVIDENCE_ITEMS (scripts/check_pr_lane.py:29).      *)
Items == { "gate", "verdict", "codereview", "incident", "synced" }

(* Evidence per lane (scaffold/pipeline_lanes.json:14,26,44,48,54, and the  *)
(* sync block in scripts/pipeline_lanes.json:13-15).                        *)
EvRelease == {}
EvSync    == { "synced" }
EvApp     == { "gate", "verdict", "codereview" }
EvPipe    == { "gate", "verdict", "incident" }
EvSmall   == { "gate", "incident" }
EvDocs    == { "gate" }

(* A PR is its branch kind, the set of path kinds it changes, and whether  *)
(* it changes fewer than small_pr.max_changed_lines (30) lines.             *)
PRs == [ branch : Branches, ch : (SUBSET Kinds) \ {{}}, small : BOOLEAN ]

(* scripts/check_pr_lane.py:133-150. The first rule that applies wins.      *)
LaneFor(p) ==
  IF p.branch = "release" /\ p.ch \subseteq RelK THEN "release"
  ELSE IF p.branch = "backmerge" /\ p.ch \subseteq RelK THEN "release"
  ELSE IF p.branch = "sync" /\ p.ch \subseteq SyncK THEN "sync"
  ELSE IF p.ch \cap AppK # {} THEN "app"
  ELSE IF p.ch \cap PipeK # {} THEN "pipeline"
  ELSE "docs"

BaseEv(l) ==
  CASE l = "release"  -> EvRelease
    [] l = "sync"     -> EvSync
    [] l = "app"      -> EvApp
    [] l = "pipeline" -> EvPipe
    [] l = "docs"     -> EvDocs

(* A small pipeline PR swaps in the small_pr evidence (no model review).    *)
(* Only a PR laned "pipeline" can qualify, and never one that touches an    *)
(* exclude path (scripts/check_pr_lane.py:164-193).                         *)
SmallActive(p) ==
  /\ LaneFor(p) = "pipeline"
  /\ p.small
  /\ p.ch \cap ExclK = {}

(* scripts/check_pr_lane.py:164-193. The lane name decides the label only;  *)
(* a PR also owes the evidence of every other configured lane it touches.   *)
Need(p) ==
  LET l == LaneFor(p)
  IN IF l \in { "release", "sync" }
     THEN BaseEv(l)
     ELSE (IF SmallActive(p) THEN BaseEv(l) \cap EvSmall ELSE BaseEv(l))
          \cup (IF p.ch \cap AppK # {} THEN EvApp ELSE {})
          \cup (IF p.ch \cap PipeK # {}
                THEN (IF SmallActive(p) THEN EvSmall ELSE EvPipe)
                ELSE {})

(***************************************************************************)
(* Lane properties. TLC checks the ASSUMEs below for every one of the 2550 *)
(* PR shapes when it starts. LaneProps(p) is also reused as a state        *)
(* invariant by PragmaPipeline.                                            *)
(***************************************************************************)
LaneProps(p) ==
  LET l == LaneFor(p)
      n == Need(p)
  IN /\ l \in Lanes
     \* L1: code under the app glob never skips gate summary, review or code-review,
     \*     except through the release lane (a release path is all that PR changes).
     /\ (l \notin { "release", "sync" } /\ p.ch \cap AppK # {})
          => { "gate", "verdict", "codereview" } \subseteq n
     \* L2: a PR laned app or pipeline that touches a pipeline path owes the
     \*     motivating incident, even when its lane label is "app".
     /\ (l \in { "app", "pipeline" } /\ p.ch \cap PipeK # {}) => "incident" \in n
     \* L3: a PR touching a file that enforces the rules always owes a review verdict
     \*     (small_pr never applies), unless it is laned release or sync.
     /\ (l \in { "app", "pipeline" } /\ p.ch \cap ExclK # {}) => "verdict" \in n
     \* L4: a fork's branch is never laned release or sync.
     /\ (p.branch = "fork") => l \notin { "release", "sync" }
     \* L5: the docs lane carries no app or pipeline path.
     /\ (l = "docs") => p.ch \cap (AppK \cup PipeK) = {}
     \* L6, L7: the release and sync lanes are decided by paths alone.
     /\ (l = "release") => p.ch \subseteq RelK
     /\ (l = "sync") => p.ch \subseteq SyncK

ASSUME \A p \in PRs : LaneProps(p)

(***************************************************************************)
(* Properties that do NOT hold, by design. Each is checked by a Gap_*.cfg  *)
(* run in PragmaPipeline and is expected to fail. They document which      *)
(* paths a lane exempts from review.                                       *)
(***************************************************************************)

\* G1: the sync lane asks only for "Synced from:", even for files that
\*     enforce the rules (gates and review skills, scaffold hooks and workflows).
\*     By design: .claude/skills/sync-workflow/SKILL.md:11 says the line stands in for
\*     a second review of a change already reviewed in the source project.
SyncGuardedReviewed(p) ==
  (LaneFor(p) = "sync" /\ p.ch \cap ExclK # {}) => "verdict" \in Need(p)

\* G2: the release lane never asks for a review, and "pbxproj" is a release path.
\*     A release PR may therefore change any build setting in project.pbxproj with
\*     no gate summary and no review. /release step 5 checks paths only
\*     (.claude/skills/release/SKILL.md:87-96); the gates job skips release PRs
\*     (scaffold/.github/workflows/gates.yml:83).
ReleaseBuildInputsReviewed(p) ==
  (LaneFor(p) = "release" /\ "pbxproj" \in p.ch) => "verdict" \in Need(p)

=============================================================================
