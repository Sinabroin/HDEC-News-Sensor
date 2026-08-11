# AI PROJECT EXECUTION STANDARD
## User-Outcome-First Engineering & Production Acceptance Rules

**Version:** 1.0  
**Status:** Canonical cross-project engineering rule  
**Applies to:** All AI-assisted software/product projects, regardless of whether the implementation agent is Codex, Claude Code, or another coding agent.

---

# 0. PURPOSE

This standard exists to prevent a recurring failure mode:

> The agent completes code changes, passes many tests, and reports success, while the user's actual expected product behavior is still broken, incomplete, too strict, too loose, not deployed, not scheduled correctly, or not proven in production.

The primary rule is:

> **Tests are evidence. User-visible outcomes are acceptance.**

A project is not complete because code exists, unit tests pass, a PR is green, or a workflow runs successfully.

A project is complete only when the agreed user-facing outcomes have been demonstrated at the appropriate acceptance level.

---

# 1. ORDER OF AUTHORITY

Every task must resolve requirements using this order unless the project explicitly defines a different higher-order Source of Truth:

1. Explicit current user requirement
2. Project-specific acceptance contract
3. Project Source of Truth / sealed ADR / product requirements
4. This common execution standard
5. Existing implementation
6. Existing tests
7. Agent assumptions

If implementation or tests conflict with the user's current accepted product requirement, the agent must not blindly preserve the implementation or tests.

Existing tests are not product authority.

---

# 2. START WITH USER-OUTCOME ACCEPTANCE

Before modifying code, the implementation agent must state the exact user-visible acceptance matrix.

Every material task must answer:

- What must the user actually see or experience?
- What must never happen?
- What real examples distinguish PASS from FAIL?
- What constitutes code-complete?
- What constitutes production-complete?
- Which actions require explicit operator approval?

Do not start with files to edit, classes to refactor, number of tests, or implementation preference. Start with product behavior.

---

# 3. CODE_COMPLETE AND PRODUCTION_COMPLETE ARE DIFFERENT

## CODE_COMPLETE

`CODE_COMPLETE=true` means:

- required code changes are implemented;
- deterministic tests pass;
- regression fixtures pass;
- historical defects are reproduced and fixed;
- no known acceptance requirement is missing from the implementation;
- work is committed and remotely recoverable.

It does **not** mean the production system has been proven.

## PRODUCTION_COMPLETE

`PRODUCTION_COMPLETE=true` requires the real product path to be observed successfully where production proof is relevant.

Examples:

- real scheduled job executed;
- real notification arrived;
- real public page loaded;
- real button/link opened the correct target;
- real persisted state reconciled correctly;
- real deployment contains the intended version;
- real integration consumed and produced the expected result.

Never convert `CODE_COMPLETE=true` into `SYSTEM_LAUNCHED=true` without the required production evidence.

---

# 4. USER-OUTCOME ACCEPTANCE OUTRANKS TEST COUNT

The following is invalid reasoning:

> "324 tests passed, therefore the feature is complete."

A large test count does not prove product correctness.

Required reasoning:

> "Every user-defined acceptance case passed, including historical failures and adversarial neighbors. The relevant regression suite also passed."

The final report must emphasize, in this order:

1. acceptance cases;
2. historical real defects;
3. adversarial edge cases;
4. real integration evidence;
5. regression suite.

Test totals are secondary.

---

# 5. EVERY REAL DEFECT CREATES A PERMANENT REGRESSION

When a real-world defect is found, the fix is incomplete until all of the following exist:

1. exact defect reproduction;
2. root-cause identification;
3. deterministic regression fixture;
4. corrected expected result;
5. neighboring-class audit;
6. proof the fix does not break the opposite side of the policy.

Example: if a publisher identity bug is found, do not test only the exact bad domain. Also test intended parent, intended child, sibling properties, unrelated properties, aliases, exact-domain behavior, and subdomain behavior.

This is the **Neighbor-Class Rule**.

---

# 6. DO NOT PATCH ONLY THE OBSERVED STRING

Never solve a structural defect with a one-off exception if the defect represents a class of failures.

Bad:

- special-case one URL;
- special-case one title;
- special-case one customer;
- special-case one date.

Preferred:

- fix the identity rule;
- fix the classification contract;
- fix the state machine;
- fix the scheduling contract;
- fix the schema;
- add adversarial coverage.

One-off exceptions are allowed only when the product rule itself is explicitly one-off.

---

# 7. PRECISION AND RECALL MUST BOTH BE MEASURED

For systems that select, classify, rank, alert, recommend, filter, moderate, retrieve, or detect, **never optimize only for false positives.**

Every hardening task must evaluate both:

- Precision: bad items that incorrectly passed
- Recall: good items that incorrectly disappeared

The final report must state:

- `OVER_FILTERING_DETECTED=`
- `UNDER_FILTERING_DETECTED=`

A system that sends no bad results because it sends nothing is not automatically good. A system that produces high volume by lowering standards is also not automatically good.

---

# 8. NO FILLER TO SATISFY A QUOTA

If the product has a desired throughput, cadence, or content volume:

- treat it as an operating target unless the user explicitly defines a hard quota;
- never weaken semantic quality just to fill the quota;
- preserve qualified backlog when pacing delays delivery;
- allow 0 when no qualified supply exists;
- explicitly distinguish "no qualified result" from "system failure."

The system must make truthful emptiness observable.

---

# 9. REAL-CORPUS REPLAY IS REQUIRED FOR CONTENT SYSTEMS

For news, search, recommendations, rankings, trading signals, commerce discovery, moderation, alerts, or similar systems, synthetic unit fixtures alone are insufficient.

Maintain a deterministic real/historical replay set containing:

- known good cases;
- known bad cases;
- borderline cases;
- previously leaked false positives;
- previously lost true positives.

For each case, record:

- human expected decision;
- system actual decision;
- decisive evidence;
- rejection/acceptance stage;
- final reason.

The replay set becomes a product acceptance asset.

---

# 10. IDENTITY AND AUTHORITY MATCHING MUST BE EXACT-BY-DEFAULT

When identity affects authorization, source quality, policy tier, routing, permissions, or trust:

- exact matching is the default;
- parent-domain inheritance must never be assumed;
- family inheritance must be explicitly enumerated;
- aliases must be bounded;
- sibling identities must be adversarially tested.

The same principle applies to domains, organizations, accounts, tenants, package names, model IDs, repositories, environments, and deployment targets.

Broad substring/prefix/suffix matching is prohibited when it can elevate authority.

---

# 11. ONE IMPLEMENTER, ONE INDEPENDENT AUDITOR

For high-value or production-critical closure work:

## Implementation agent

Exactly one primary implementation agent owns the patch set.

It may inspect, modify, test, commit, push the task branch, and update the Draft PR.

## Independent auditor

A different agent/model should audit after implementation.

The auditor must not begin by assuming the implementation is correct. Its job is to falsify:

- claimed root cause;
- acceptance matrix;
- regression coverage;
- production assumptions;
- hidden policy drift;
- scope omissions.

Avoid alternating two agents as co-implementers on the same closure task unless explicitly necessary.

---

# 12. THE AUDITOR MUST ATTACK CLAIMS, NOT REPEAT TESTS

An independent audit must include:

- diff inspection;
- adversarial counterexamples;
- historical real defect replay;
- neighboring-class tests;
- acceptance contract comparison;
- check for unrelated scope changes;
- check for unproven production claims.

Merely rerunning the implementer's test command is not an independent audit.

---

# 13. FAIL CLOSED ON UNKNOWN OR AMBIGUOUS STATE

When the agent cannot establish branch ownership, dirty-file ownership, environment identity, production authorization, source authority, exact deployment target, secret handling, or state reconciliation, it must stop the unsafe action and report the ambiguity.

Never reset, clean, overwrite, force-push, or silently reconstruct ambiguous user work.

---

# 14. PRODUCTION ACTIONS ARE EXPLICIT BOUNDARIES

The following must be treated separately from ordinary implementation unless the user has explicitly authorized them:

- production send;
- real financial transaction;
- workflow dispatch with side effects;
- production database mutation;
- secret or variable change;
- deployment;
- merge to protected/main branch;
- deletion;
- irreversible migration.

The final report must distinguish code changes performed, production actions performed, and production actions still requiring operator approval.

---

# 15. STATEFUL AUTOMATION MUST BE IDEMPOTENT

Scheduled/retry/automation workflows must use machine-readable state for duplicate prevention.

Do not rely only on current clock time, UI visibility, "probably already ran", or manual memory.

Required properties where applicable:

- at-most-once or explicitly defined retry semantics;
- claim before/after send contract;
- partial-failure reconciliation;
- duplicate suppression;
- safe restart behavior;
- truthful success state.

---

# 16. SCHEDULED SYSTEMS MUST PROVE THE NATURAL SCHEDULE

A manual run is not always equivalent to scheduled production.

For schedule-dependent behavior, final production acceptance should verify a natural scheduled execution unless the project explicitly accepts a manual equivalent.

Record intended schedule, scheduler timezone, best-effort nature if relevant, actual observed execution, and actual resulting product behavior.

Do not promise exact timing from a best-effort scheduler. Use a target window when that is the actual platform behavior.

---

# 17. EMPTY SUCCESS MUST BE DISTINGUISHABLE FROM FAILURE

If a valid result can contain zero items, the user-facing product must distinguish:

- successful run with zero qualified results;
- collection failure;
- processing failure;
- transport failure;
- stale output.

Silence must not be the only signal unless silence is explicitly the product requirement.

---

# 18. NO MISLEADING UI

A visible control must be one of:

- functional;
- explicitly disabled;
- clearly labelled unavailable/preview-only.

Do not ship controls that appear active but deterministically fail because the backend is absent.

A backend-dependent feature may be deferred without blocking unrelated core acceptance, but the UI must tell the truth.

---

# 19. CROSS-MACHINE / CROSS-SESSION CONTINUITY

Git remote state is the authoritative handoff medium unless the project explicitly defines another durable authority.

Before ending a working session:

1. run relevant acceptance checks;
2. update handoff;
3. commit a coherent checkpoint;
4. push;
5. verify remote head;
6. record next action.

Never leave important completed work only on one machine.

Required final flags:

- `REMOTE_CHECKPOINT_AVAILABLE=`
- `CROSS_MACHINE_HANDOFF_READY=`

If push failed, handoff is not complete.

---

# 20. DIRTY REPOSITORY SAFETY

Never perform unrelated work inside a dirty checkout without first establishing ownership of every dirty path.

Preferred solution: create a dedicated clean worktree and resume the remote task branch there.

Never casually run `git reset --hard`, `git clean -fd`, overwrite user files, or stash unknown work as if it belongs to the task.

---

# 21. MAIN MAY MOVE DURING AUTOMATION

If production bots or other developers may advance the main branch:

- always `fetch` before major checkpoints;
- compare against current remote truth;
- merge remote main into the task branch only when needed;
- preserve history;
- avoid force push;
- avoid shared-history rebase unless the project explicitly permits it.

A remembered main SHA is informational, not permanent authority.

---

# 22. NO SILENT REQUIREMENT REDUCTION

Agents must not reinterpret:

- "must work" as "prototype exists";
- "automatic" as "manual command works";
- "production ready" as "unit tests pass";
- "daily" as "when there are results";
- "major media" as "any trusted source";
- "real-time" as "eventually";
- "continue on another machine" as "there are uncommitted files locally."

If the requested end state cannot be achieved within the allowed safety boundary, report it as a blocker. Do not redefine success downward.

---

# 23. DO NOT OVERBUILD BEFORE CLOSING THE USER PATH

Once the required architecture is sufficient, prioritize completing the user path over adding more frameworks, abstractions, diagnostics, or hardening layers.

Before adding a new layer ask:

> Does this directly close a failed acceptance case?

If not, defer it unless it is required for safety.

Avoid endless hardening loops.

---

# 24. CHANGE BUDGET / SCOPE LOCK

Every closure task must define the allowed change surfaces.

Changes outside the scope require justification.

The agent must report intended files/surfaces, actual files changed, and unexpected files changed.

Do not use a production-closure task for unrelated refactoring.

---

# 25. ACCEPTANCE EVIDENCE LADDER

Use the strongest feasible evidence.

From weakest to strongest:

1. static inspection
2. unit test
3. deterministic integration test
4. historical/adversarial replay
5. local end-to-end
6. deployed non-production end-to-end
7. controlled production canary
8. natural production observation

A lower level must not be presented as if it proves a higher level.

---

# 26. FINAL REPORT FORMAT

Every substantial implementation task must finish with at least:

```text
TASK=
BRANCH=
HEAD=
REMOTE_HEAD=

USER_OUTCOME_ACCEPTANCE=
CODE_COMPLETE=
PRODUCTION_COMPLETE=
SYSTEM_LAUNCHED=

HISTORICAL_DEFECTS_REPLAYED=
ADVERSARIAL_CASES_PASS=

OVER_FILTERING_DETECTED=
UNDER_FILTERING_DETECTED=

VERIFIERS_PASS=
VERIFIERS_FAIL=

PRODUCTION_ACTIONS_PERFORMED=
PRODUCTION_ACTIONS_NOT_PERFORMED=

KNOWN_BLOCKERS=
REQUIRES_OPERATOR_APPROVAL=

FILES_CHANGED=
COMMITS_CREATED=
PUSH_EXECUTED=

REMOTE_CHECKPOINT_AVAILABLE=
CROSS_MACHINE_HANDOFF_READY=

NEXT_SINGLE_ACTION=
```

Do not omit failures. Do not replace an incomplete field with vague prose.

---

# 27. DEFINITION OF DONE

A project task may be declared DONE only when all required layers are true:

## Product
- user-visible acceptance matrix satisfied;

## Correctness
- known defects fixed;
- historical replay passes;
- adversarial neighbors pass;

## Regression
- relevant existing suites pass;

## Safety
- no unauthorized production action occurred;

## Durability
- checkpoint committed and pushed;

## Production
- production proof completed if required for the requested completion state.

Otherwise use a narrower status:

- `IMPLEMENTATION_IN_PROGRESS`
- `CODE_COMPLETE_PRODUCTION_UNPROVEN`
- `BLOCKED_OPERATOR_ACTION_REQUIRED`
- `PRODUCTION_CANARY_REQUIRED`
- `PRODUCTION_OBSERVATION_REQUIRED`

Never use a cosmetic PASS.

---

# 28. PROJECT-SPECIFIC OVERLAY

Every repository should define a short project-specific acceptance overlay.

Recommended path:

`docs/acceptance/PROJECT_ACCEPTANCE.md`

It should contain:

1. Product North Star
2. User-visible must-pass outcomes
3. Must-never-happen outcomes
4. Historical real defects
5. Real-corpus replay cases
6. Production side-effect boundaries
7. Code-complete conditions
8. Production-complete conditions
9. Current blockers
10. Next single action

The common standard governs **how work is performed**.

The project acceptance overlay governs **what success means for that product**.

---

# 29. AGENT STARTUP CONTRACT

At the start of any important coding task, instruct the implementation agent:

```text
Read and obey AI_PROJECT_EXECUTION_STANDARD.md first.

Then read the project-specific acceptance contract.

Do not begin implementation until you can state the user-visible acceptance matrix.

Existing tests are evidence, not product authority.

Do not claim completion from verifier counts alone.

Every real defect must become a regression plus neighboring-class coverage.

Separate CODE_COMPLETE from PRODUCTION_COMPLETE.

Before stopping, commit, push, verify the remote checkpoint, and provide NEXT_SINGLE_ACTION.
```

---

# 30. NON-NEGOTIABLE SUMMARY

These rules are non-negotiable across projects:

1. **User outcome > test count.**
2. **Real defect -> permanent regression.**
3. **Test the neighboring failure class, not only the exact bug.**
4. **Measure recall as well as precision.**
5. **No filler.**
6. **Identity/authority matching is exact-by-default.**
7. **One implementer; independent auditor afterward.**
8. **Code complete != production complete.**
9. **No production-complete claim without required real E2E evidence.**
10. **No misleading controls.**
11. **Do not damage unknown dirty work.**
12. **Checkpoint and push before handoff.**
13. **Never silently lower the user's definition of success.**
14. **Stop adding hardening layers once the actual user path should be closed.**
15. **No cosmetic PASS.**

---

# 31. REQUIRED PROJECT ADOPTION

For every active project:

- add this standard to the repository root as `AI_PROJECT_EXECUTION_STANDARD.md`;
- add/update `docs/acceptance/PROJECT_ACCEPTANCE.md`;
- instruct the active implementation agent to read both before coding;
- include both in the repository so the rules travel across machines and sessions;
- update the project acceptance overlay whenever a real production defect or product requirement changes.

This file should evolve only when a cross-project lesson is genuinely reusable.

Project-specific behavior belongs in the project acceptance overlay, not in this common standard.
