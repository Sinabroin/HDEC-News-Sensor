# HDEC NEWS SENSOR — PROJECT ACCEPTANCE CONTRACT
## Project-specific overlay for AI_PROJECT_EXECUTION_STANDARD.md

**Version:** 1.3
**Project:** 현대건설 임원용 뉴스 센서 에이전트 / HDEC News Sensor  
**Repository:** `Sinabroin/HDEC-News-Sensor`  
**Status:** Production acceptance contract  

---

# 1. PRODUCT NORTH STAR

The product must collect and deliver a small number of genuinely important AI-related news items for Hyundai E&C executives, with a strong preference for major and authoritative media, without padding, stock/theme noise, or weak-source filler.

Primary product requirement:

> 제대로 기사들을 주요 언론사 위주로, 적절한 내용을 가져온다.

This requirement outranks test counts, implementation convenience, and historical policy drift.

---

# 2. USER-VISIBLE MUST-PASS OUTCOMES

## Realtime Teams Watch

The user should receive meaningful realtime AI / data-center / power / construction / industrial / regulation events at a useful operating cadence.

Expected behavior:

- major-media-first;
- article substance must independently qualify;
- the bounded publisher title/subtitle/factual lead must make a material AI
  corporate, industrial, infrastructure, regulation, or risk event the
  dominant subject;
- normal meaningful flow target: roughly one strong article per rolling hour when sufficient qualified supply exists;
- TOP / HDEC-direct / critical material events may send immediately;
- no filler when nothing qualifies;
- duplicate events collapse to one best representative;
- valid backlog must not be silently discarded merely because pacing delayed delivery.

## Daily Brief

Around the morning 08:00 KST operating window:

- a dated Daily edition must exist;
- the user must receive a Teams Daily message once per edition;
- non-empty edition: send the actual Daily digest;
- empty edition: send a truthful status message saying no article met the standard;
- silence must not be used to represent a successful empty run.

## Daily Brief Editor

The morning Daily message must provide:

- `Daily Brief 편집기에서 열기`
- `게시된 Daily Brief 보기`

The Editor action must refer to the exact dated/immutable edition identity.
The reader action must refer to the exact dated Daily publication.
A mutable `latest` URL must not be the authoritative Teams action target.

---

# 3. MUST-NEVER-HAPPEN OUTCOMES

The following are hard failures:

- IT조선 treated as 조선일보 merely because `it.chosun.com` is under `chosun.com`;
- 조선비즈 treated as 조선일보 merely because `biz.chosun.com` is under `chosun.com`;
- sibling publication/domain inheritance elevates source authority without explicit enumeration;
- a display source alias overrides an explicitly configured URL publication identity;
- an unknown foreign host or unenumerated sibling subdomain inherits authority from a display source alias or parent domain;
- `premium.sbs.co.kr` inherits SBS Tier-A realtime authority or sends a standalone realtime Teams card;
- opinion/column/contributed pieces auto-send as realtime Watch news;
- ETF/fund/theme-stock articles auto-send merely because they mention AI;
- weak specialist/trusted sources fill unused Teams capacity;
- publisher reputation alone creates qualification;
- search query text creates qualification;
- investor-audience guidance, stock selection, valuation, or market strategy
  becomes a realtime AI event merely because it mentions AI/HBM/GPU;
- generic sector earnings or business tailwind commentary becomes a realtime
  AI event merely because AI demand is described as the cause;
- a non-AI roundup title becomes a realtime AI event because a secondary
  bundled item or snippet contains AI vocabulary;
- incidental AI/HBM/GPU/semiconductor vocabulary creates qualification without
  a bounded, materially relevant AI event;
- empty Daily produces silence that is indistinguishable from workflow failure;
- retries cause duplicate Daily sends;
- Editor UI exposes a control that looks usable but deterministically fails because its backend is unavailable;
- tests are reported as production proof when no real production E2E exists.
- a deterministic verifier treats mutable production `latest` as a frozen historical fixture;
- a fallback/category visual is counted as a real article photo;
- an unrelated Daily/Editor fixture failure disables Watch or Scheduled Refresh;
- Review Console generation is reported as Editor notification delivery;
- a non-empty Daily or Weekly is delivered with incomplete real-photo coverage.

---

# 4. SOURCE TIER CONTRACT

## Publisher identity authority

The permanent authority invariant is:

> **KNOWN_AUTHORITATIVE_URL_IDENTITY > DISPLAY_SOURCE_ALIAS**

When an exact selected-URL host is configured for a publication, that URL
publication identity is canonical and overrides a contradictory display source
alias. Unknown foreign hosts do not inherit alias authority. Unenumerated
sibling subdomains do not inherit parent-publication authority. Exact configured
hosts and bounded exact aliases are permitted; broad substring or suffix
matching must never elevate authority.

## Tier A — Core Major

Immediate consideration is allowed only after all substantive gates pass.

- 연합뉴스
- MBC
- KBS
- 조선일보
- YTN
- JTBC
- 중앙일보
- 매일경제
- 한국경제
- SBS
- 동아일보
- 한겨레
- 경향신문

Tier A is a quality prior, not a qualification bypass.

## Tier B — Major General / Economic / Industrial

Explicitly allowed for automatic Teams consideration when substantive gates pass:

- 서울경제
- 한국일보
- 뉴스1
- 뉴시스
- 조선비즈
- 머니투데이
- 이데일리
- 아시아경제
- 파이낸셜뉴스
- 헤럴드경제
- 전자신문
- 디지털타임스
- 국민일보
- 세계일보
- 서울신문
- 문화일보

Tier B must remain an explicit allowlist.
Do not auto-promote all `trusted_other` publishers.

## Tier C — Specialist / Niche

Examples:

- IT조선
- ZDNet Korea
- 테크M
- 테크월드
- 더벨
- 인베스트조선
- 대한경제
- 에너지경제
- 전기신문

Tier C may support Editor / evidence use, but must not become a standalone realtime Teams card by default.

---

# 5. SUBSTANTIVE WATCH GATES

A publisher tier never overrides the article-level requirements.

Realtime Teams eligibility must continue to require the relevant combination of:

- publisher-direct/source authority;
- freshness/current-run evidence;
- AI centrality;
- HDEC/executive relevance;
- executive materiality;
- importance;
- hard stock/theme exclusions;
- fund/ETF noise exclusion;
- opinion/contribution exclusion;
- delivery category evidence;
- event dedup;
- already-sent state/ledger checks.

`SEARCH_QUERY_CAUSED_QUALIFICATION=0` is a permanent invariant.

## Realtime opinion contract

The realtime opinion gate may use only explicit publisher-section values and
clear bracketed title-boundary markers. Normalized Korean opinion sections and
explicit English sections such as `Opinion`, `Editorial`, `Column`,
`Commentary`, `Op-Ed`, and `OpEd` must reject. Bracketed markers must reject at
either a clear leading or trailing title boundary, including forms such as
`[기고] 제목`, `제목 [기고]`, `【Opinion】 제목`, and `제목 【Opinion】`.

Incidental prose that merely contains a token must remain allowed. Summary,
query, and body text have zero authority for this realtime gate.

---

# 6. HISTORICAL REAL DEFECTS — PERMANENT REGRESSION CASES

Every case below must remain in deterministic regression/replay coverage.

## HDEC-DEFECT-001 — IT조선 publisher identity leak

Observed production send:

- source: IT조선
- URL: `https://it.chosun.com/news/articleView.html?idxno=2023092167659`
- sent: `2026-08-11T06:03:06+09:00`
- title cluster: `흔들리는구글위기인가새판짜기인가`

Required expected result:

- must not resolve as 조선일보;
- must not resolve as Tier A;
- opinion/contribution evidence must independently prevent realtime auto-send where present;
- final realtime decision: REJECT.

Neighbor-class fixtures must include:

- `chosun.com`
- `www.chosun.com`
- `it.chosun.com`
- `biz.chosun.com`
- `sports.chosun.com`

## HDEC-DEFECT-002 — AI ETF false positive

Observed historical production false positive:

- 연합뉴스
- `배재규 방향과 시간에 투자 한투운용 전략산업 ETF 출시`

Required expected result:

- realtime Teams decision: REJECT;
- bare launch wording, fund scale, or publisher authority must not rescue it.

## HDEC-DEFECT-003 — useful Tier-B event lost by source gate

Known human-valuable event:

- 한국일보
- LS일렉트릭 × GS건설 AI 데이터센터 직류배전 사업 협력

Required expected result:

- may become Teams-eligible under Tier-B policy if substantive gates pass;
- must not be rejected solely because it is `trusted_other` under the old source policy.

## HDEC-DEFECT-004 — empty Daily indistinguishable from failure

Required expected result:

- a valid zero-candidate morning still publishes a truthful dated Daily;
- a single Teams Daily status message is sent;
- exact Editor and exact reader actions remain available;
- no duplicate on retry.

## HDEC-DEFECT-005 — SBS Premium realtime authority leak

Observed production URL:

- `https://premium.sbs.co.kr/article/r26f8YfJ9`

Required expected result:

- `premium.sbs.co.kr` must never inherit SBS Tier-A realtime authority;
- `operator_surface=editorial_analysis`;
- realtime standalone Teams auto-send is `false`;
- TOP, HDEC-direct, high score, and strong query metadata cannot rescue this surface;
- final realtime decision: REJECT;
- Editor/supporting-context use may remain possible.

The replay must keep the observed incident separate from synthetic adversarial
metadata. Unverified historical title, source, timestamp, and article metadata
must remain null/unknown rather than being represented as observed fact.

## HDEC-DEFECT-006 — mutable latest date rollover

Observed production state: the immutable historical Review bundle remained
`2026-08-11` while mutable `docs/editorial/review/latest` correctly advanced to
`2026-08-12`. The deterministic R4 acceptance verifier loaded both as
`2026-08-11` and failed on the correct rollover.

Required expected result:

- deterministic historical fixtures never derive authority from production `latest`;
- exact/latest mirror tests construct an isolated temporary fixture pair;
- a wrong edition in that isolated pair still fails closed with identity mismatch;
- current mutable `latest` may advance without breaking historical verification.

## HDEC-DEFECT-007 — fallback image false green

Observed Weekly W33 rendered deterministic blue/navy fallback graphics while
existing checks treated image-backed cards as success.

Required expected result:

- fallback visuals are explicit and never count as real article photos;
- a non-empty Daily/Weekly requires one byte-validated, locally materialized,
  immutable public raster photo per rendered article;
- `article_count=12`, `real_article_photo_count=0`, and
  `fallback_visual_count=12` fails production authorization even with 12 `<img>` tags;
- fake raster suffixes, SVG/data fallbacks, hotlinks, and unavailable
  Review-relative paths fail closed.

## HDEC-DEFECT-008 — cross-workflow verifier blast radius

Observed one Editorial acceptance fixture failure stopped Daily Brief, Realtime
Watch, and Scheduled Live Refresh at their offline verifier stage.

Required expected result:

- Watch, Refresh, Daily, Weekly, and Editor scheduled gates are scoped to their
  own runtime dependencies plus genuinely shared low-level invariants;
- a Daily-only fixture failure cannot stop Watch or Refresh;
- a Watch-only failure still blocks Watch and cannot silently permit sends;
- the broad R4 acceptance verifier remains an integration/CI suite, not a
  scheduled cross-product kill switch.

## HDEC-DEFECT-009 — Editor generated but not delivered

Observed the Review Console workflow built and published a valid console but
did not independently notify the user; later Daily failure therefore left no
morning Editor link.

Required expected result:

- Editor generation, publication, public verification, claim, send, duplicate
  skip, and failure are separate machine-readable states;
- the authoritative notification target is a content-addressed immutable
  Review snapshot, never `latest` and never a guessed later Daily identity;
- the automatic SMTP path persists delivery success only for exact SMTP DATA
  250; explicit operator reconciliation remains a distinct evidence kind;
- retry sends zero duplicates for the same date/snapshot;
- an armed transport observed by a different run becomes
  `ambiguous_reconciliation_required`, fails visibly, and performs no automatic
  resend or SMTP connection;
- ambiguity can be cleared only by an explicitly authorized operator who
  either supplies evidence of delivery or authorizes a retry; the evidence
  digest and action remain in the delivery-state audit history;
- Editor success and Daily success remain independent truths.

## HDEC-DEFECT-010 — investor-guidance dominant subject leaked to Watch

Observed production send:

- 매일경제
- `https://www.mk.co.kr/news/business/12126910`
- `HBM·ASIC…암호같은 이름 알면 반도체 투자가 보인다 [반도체플러스]`

Required expected result:

- final realtime decision: REJECT;
- investor audience, stock-selection, valuation, portfolio, or market-guidance
  dominance is not a material AI industrial event;
- the bare word `투자` is never globally blocked;
- a confirmed corporate AI infrastructure investment, supply contract,
  capacity expansion, or construction event remains eligible to continue.

## HDEC-DEFECT-011 — generic AI tailwind admitted as a new AI event

Observed production send:

- 서울경제
- `https://www.sedaily.com/article/20079249`
- `철강·석화 ‘AI·반도체 특수’로 불황 뚫었다`

Required expected result:

- final realtime decision: REJECT unless bounded publisher evidence proves a
  distinct newly confirmed material AI action/event;
- earnings, utilization, sector outlook, price, or business-performance
  commentary does not qualify merely because AI demand is the cause or
  tailwind;
- a confirmed material AI infrastructure project, CAPEX, contract, regulation,
  acquisition, security incident, or comparable industrial event remains
  eligible to continue.

## HDEC-DEFECT-012 — non-AI roundup contaminated by a secondary AI item

Observed production send:

- 세계일보
- `https://www.segye.com/newsView/20260813519823`
- `[경제 단신] ‘탱크데이’ 논란…스벅 2분기 적자전환 외`

Required expected result:

- final realtime decision: REJECT when the title's dominant item is non-AI and
  AI appears only in another bundled item/snippet;
- literal `외` is not a blanket ban;
- an AI-dominant title using harmless roundup-like punctuation remains eligible
  to continue when all other gates pass.

## HDEC-DEFECT-013 — incidental AI inference without bounded evidence

Observed production-window class includes articles whose publisher title,
subtitle, and factual lead establish a different industrial or market subject,
while AI/HBM/GPU/semiconductor vocabulary appears only incidentally or through
non-authoritative metadata.

Required expected result:

- final realtime decision: REJECT when bounded publisher evidence contains no
  central, materially relevant AI event;
- query text, generated summary/implication/category, and publisher prestige
  each have zero qualification authority;
- a known corporate actor plus a generic launch/groundbreaking/completion does
  not create qualification when AI appears only as an incidental product or
  facility feature in the factual lead;
- an actor bridge must prove a material AI target or AI-infrastructure nexus in
  the bounded factual publisher lead;
- a material AI event proven by bounded publisher evidence remains eligible to
  continue through all established Watch gates.

## HDEC-DEFECT-014 — AI-infrastructure groundbreaking idiom over-filtered

Observed acceptance fixture:

- `과기정통부, 국가AI컴퓨팅센터 첫 삽`
- factual publisher lead: `국가 AI 컴퓨팅센터 건설을 착공해 GPU 인프라 구축을 시작했다.`

Required expected result:

- `첫 삽`, `첫삽`, `기공`, `기공식`, and `착공식` are recognized as
  construction-start headline cues, never as unconditional qualification
  tokens;
- eligibility requires an AI/AI-infrastructure-aligned title and a bounded
  factual publisher lead that independently confirms a current action such as
  착공, 건설·구축 착수, 기공식 개최, or 공사 시작;
- speculative groundbreaking review, a historical groundbreaking reference
  without a new material event, and a non-AI construction event with only an
  incidental AI feature remain realtime REJECT;
- government/public-institution and HDEC/competitor actors use the same
  semantic requirements, and all established Watch gates still apply.

---

# 7. REAL-CORPUS REPLAY MATRIX

The acceptance replay must contain at least:

### MUST REJECT FROM REALTIME TEAMS

1. IT조선 — 흔들리는 구글, 위기인가 새판짜기인가
2. 연합뉴스 — 전략산업 ETF 출시 false positive
3. representative stock/theme article
4. explicit opinion/column/contributed article
5. weak Tier-C specialist article with no independently sufficient event
6. observed SBS Premium incident URL with only verified provenance
7. explicitly synthetic high-materiality SBS Premium authority stress case
8. observed investor-guidance dominant article with AI/HBM vocabulary
9. observed generic AI-demand earnings/tailwind commentary with no new event
10. observed non-AI roundup with an AI secondary item
11. incidental AI/HBM/GPU vocabulary without a material AI event
12. non-AI apartment/factory/product event with only an incidental AI feature
13. speculative or historical groundbreaking context without a new event

### MUST BE CAPABLE OF TEAMS ELIGIBILITY

1. 한국일보 — LS일렉트릭 × GS건설 AI 데이터센터 직류배전 협력
2. material Tier-B AI data-center / power / industrial contract or investment event
3. qualifying Tier-A major-media event
4. HDEC-direct material event
5. TOP critical event
6. confirmed corporate AI infrastructure investment with a concrete amount
7. AI semiconductor supply/capacity contract
8. material AI regulation or security/risk event
9. AI-dominant title with harmless `…외` formatting
10. AI-computing/data-center `첫 삽` with publisher-lead proof of current 착공
11. AI-infrastructure `기공식` with publisher-lead proof construction started

### EDITOR-ONLY / SUPPORTING

1. useful specialist analysis without a realtime event
2. strategic context article without sufficient urgency/importance
3. held/rejected article whose reason is visible to the operator

For every replay row output:

```text
TITLE=
SOURCE=
URL=
PROVENANCE=
RESOLVED_PUBLISHER_IDENTITY=
SOURCE_TIER=
SEMANTIC_CLASS=
AI_CENTRAL=
EXECUTIVE_RELEVANT=
MATERIAL=
STOCK_MARKET=
FUND_PRODUCT=
ROUNDUP=
INVESTOR_DOMINANT=
IMPORTANCE=
OPINION_GATE=
TEAMS_POLICY_ELIGIBLE=
FINAL_REALTIME_DECISION=
REASON=
HUMAN_EXPECTED=
MATCH=
```

All mandatory rows must have `MATCH=true` before `CODE_COMPLETE=true`.

---

# 8. PRECISION / RECALL BALANCE

Both sides must be explicitly measured.

Required final fields:

```text
WATCH_PRECISION_VERDICT=
WATCH_RECALL_VERDICT=
OVER_FILTERING_DETECTED=
UNDER_FILTERING_DETECTED=
```

A Watch that selects zero from healthy useful supply because the source gate is too narrow is not fully acceptable.
A Watch that sends weak source/opinion/theme content to increase volume is also not acceptable.

---

# 9. WATCH PACING CONTRACT

For normal important events:

- target roughly one best article per rolling 60-minute window when qualified supply exists;
- do not send filler;
- preserve still-current eligible backlog for later consideration;
- do not silently discard qualified rows because the current window is occupied.

For TOP / HDEC-direct / critical events:

- immediate eligibility may bypass the normal pacing window.

If no qualified supply exists:

- send 0 realtime article cards.

This pacing contract applies to realtime Watch only, not the morning Daily status/digest.

---

# 10. DAILY MORNING CONTRACT

The operational target is around 08:00 KST, acknowledging GitHub schedule best-effort behavior.

The ordering should ensure the Editor normally precedes Daily generation.

The workflow must be state-idempotent across retries.

## Non-empty edition

Required:

- exact dated Editor available;
- exact dated Daily publication;
- immutable edition identity/manifest preserved;
- one Teams Daily send;
- exact Editor CTA;
- exact dated reader CTA.

## Empty edition

Required:

- zero filler articles;
- truthful dated Daily publication;
- exact dated Editor preserved;
- exactly one Teams Daily status send;
- message clearly states that no article met the standard;
- exact Editor CTA;
- exact dated reader CTA;
- retry does not duplicate the status send.

---

# 11. EDITOR FUNCTIONAL CONTRACT

Required acceptance:

- exact dated edition loads;
- latest resolves correctly;
- candidate bundle loads;
- truthful empty edition renders;
- non-empty fixture renders candidate cards;
- ordering/edit controls function;
- Daily preview functions;
- exact-edition reconstruction works;
- Daily Teams CTA points to exact Editor;
- reader CTA points to exact dated Daily.

If article import backend remains unavailable:

- the import UI must be explicitly disabled or labelled unavailable;
- it must not look operational and then fail unexpectedly.

Article-import provisioning must not block unrelated core launch acceptance unless the user explicitly promotes it to a launch blocker.

## Editor armed-transport reconciliation

Scheduled workflows must never invoke reconciliation automatically. After
inspecting the exact date, snapshot, GitHub run, SMTP provider evidence, and
tracked state diff, an authorized operator may run one of the following on a
main-branch GitHub runner (then separately review and publish the state diff):

```bash
EDITORIAL_PRODUCTION=1 GITHUB_ACTIONS=true GITHUB_REF=refs/heads/main \
EDITOR_RECONCILIATION_AUTHORIZED=1 \
python3 scripts/run_editor_delivery.py \
  --snapshot-id REVIEW_SNAPSHOT_ID \
  --reconcile-mark-delivered \
  --authorized-by OPERATOR_ID \
  --operator-evidence-file /path/to/nonsecret-evidence.txt

EDITORIAL_PRODUCTION=1 GITHUB_ACTIONS=true GITHUB_REF=refs/heads/main \
EDITOR_RECONCILIATION_AUTHORIZED=1 \
python3 scripts/run_editor_delivery.py \
  --snapshot-id REVIEW_SNAPSHOT_ID \
  --reconcile-release-retry \
  --authorized-by OPERATOR_ID \
  --operator-evidence-file /path/to/nonsecret-evidence.txt
```

`mark-delivered` does not fabricate SMTP 250 evidence; it records a distinct
operator-reconciled evidence kind. `release-retry` sends nothing itself and
only permits a later normal claim/arm/send attempt.

---

# 12. CODE-COMPLETE CONDITIONS

`CODE_COMPLETE=true` requires all of the following:

```text
IT_CHOSUN_NOT_CHOSUN=PASS
CHOSUN_BIZ_NOT_CHOSUN=PASS
TRUE_CHOSUN_IDENTITY=PASS
SIBLING_DOMAIN_INHERITANCE_BLOCKED=PASS
PUBLISHER_URL_AUTHORITY_INVARIANT=PASS
SBS_PREMIUM_OBSERVED_INCIDENT=PASS
SBS_PREMIUM_ADVERSARIAL_STRESS=PASS
SBS_REPLAY_PROVENANCE=PASS

ETF_FALSE_POSITIVE_REJECTED=PASS
OPINION_REALTIME_REJECTED=PASS
OPINION_TRAILING_MARKER=PASS
OPINION_ENGLISH_SECTION=PASS
TIER_C_STANDALONE_AUTO_SEND_ZERO=PASS

TIER_B_MATERIAL_EVENT_ELIGIBLE=PASS
LS_ELECTRIC_GS_EC_REPLAY=PASS
PUBLISHER_ALONE_NEVER_QUALIFIES=PASS

NORMAL_PACING=PASS
TOP_HDEC_BYPASS=PASS
QUALIFIED_BACKLOG_PRESERVED=PASS
FILLER_ZERO=PASS

NONEMPTY_DAILY_PATH=PASS
EMPTY_DAILY_STATUS_PATH=PASS
DAILY_RETRY_IDEMPOTENCE=PASS
DAILY_EXACT_EDITOR_CTA=PASS
DAILY_EXACT_READER_CTA=PASS

EDITOR_EMPTY_RENDER=PASS
EDITOR_NONEMPTY_RENDER=PASS
EDITOR_EXACT_RECONSTRUCTION=PASS
NO_MISLEADING_IMPORT_CONTROL=PASS

SCHEDULED_REFRESH_STALE_VERIFIER_REPAIRED=PASS

DATE_ROLLOVER_REGRESSION=PASS
MUTABLE_LATEST_NOT_TEST_FIXTURE=PASS
WATCH_GATE_FAULT_ISOLATED=PASS
REFRESH_GATE_FAULT_ISOLATED=PASS

REAL_ARTICLE_PHOTO_CONTRACT=PASS
FALLBACK_FALSE_GREEN_BLOCKED=PASS
DAILY_REAL_PHOTO_GATE=PASS
WEEKLY_REAL_PHOTO_GATE=PASS
PUBLIC_IMAGE_ASSET_VERIFICATION=PASS

EDITOR_INDEPENDENT_DELIVERY_PATH=PASS
EDITOR_DELIVERY_IDEMPOTENCE=PASS
EDITOR_EXACT_SNAPSHOT_LINK=PASS
EDITOR_AMBIGUOUS_ARM_FAIL_CLOSED=PASS
EDITOR_RECONCILIATION_PATH=PASS

EXACT_REVIEW_ASSET_FIRST=PASS
EXACT_REVIEW_ASSET_REMOTE_INDEPENDENCE=PASS
REVIEW_ASSET_PATH_ESCAPE_BLOCKED=PASS
REVIEW_ASSET_BYTES_REVALIDATED=PASS

HISTORICAL_REPLAY=PASS
ADVERSARIAL_NEIGHBOR_CASES=PASS
RELEVANT_REGRESSIONS=PASS
REMOTE_CHECKPOINT_AVAILABLE=true
```

---

# 13. PRODUCTION-COMPLETE CONDITIONS

`PRODUCTION_COMPLETE=true` and `SYSTEM_LAUNCHED=true` are forbidden until real-world proof exists for the required production path.

Minimum required production proof:

```text
REAL_WATCH_SEND_PASS=true
REAL_WATCH_BAD_SOURCE_LEAK=false
REAL_DAILY_SEND_PASS=true
REAL_EDITOR_LINK_PASS=true
REAL_PUBLIC_DAILY_LINK_PASS=true
NATURAL_SCHEDULE_OBSERVED=true
```

The actual production evidence must show the deployed code version intended for launch.

Offline verifier PASS is not a substitute.

---

# 14. IMPLEMENTER / AUDITOR OWNERSHIP

For R4-OPS-5 closure:

- Codex = sole implementation agent
- Claude Code = independent audit agent after Codex reaches `CODE_COMPLETE=true`

Claude Code should not casually modify the same closure patch during the first audit.
Its first role is to attempt to falsify Codex's acceptance claims.

If the audit finds a real defect:

- return the defect to Codex for a focused repair;
- add permanent regression coverage;
- rerun independent audit.

Avoid alternating broad implementation ownership.

---

# 15. PRODUCTION SIDE-EFFECT BOUNDARIES DURING IMPLEMENTATION

Without explicit operator authorization, implementation work must not:

- merge to main;
- perform production Teams send;
- perform SMTP production send;
- change repository variables/secrets;
- mutate production ledgers;
- dispatch side-effecting production workflows;
- force push;
- rewrite shared history.

Code/test/docs commits and normal pushes to the WIP branch are allowed.

---

# 16. CROSS-MACHINE CONTINUITY

Before stopping implementation:

- update handoff;
- commit coherent work;
- push task branch;
- verify local head == remote task head;
- update Draft PR;
- record next single action.

Required:

```text
REMOTE_CHECKPOINT_AVAILABLE=true
CROSS_MACHINE_HANDOFF_READY=true
```

No important completed work may exist only on the current company/home machine.

---

# 17. CURRENT CLOSURE SCOPE

Do not expand R4-OPS-5 beyond:

1. publisher identity correctness;
2. A/B/C source tiering;
3. opinion/contribution realtime hard gate;
4. Watch recall/pacing;
5. empty + non-empty Daily morning contract;
6. Editor functional truthfulness;
7. existing PR #40 verifier repairs;
8. acceptance replay and regression coverage.

Do not redesign Weekly, dashboards, unrelated market integrations, or unrelated UI during this closure unless a regression directly requires it.

---

# 18. FINAL STATUS VOCABULARY

Use only truthful statuses:

- `IMPLEMENTATION_IN_PROGRESS`
- `CODE_COMPLETE_PRODUCTION_UNPROVEN`
- `BLOCKED_OPERATOR_ACTION_REQUIRED`
- `PRODUCTION_CANARY_REQUIRED`
- `PRODUCTION_OBSERVATION_REQUIRED`
- `PRODUCTION_COMPLETE`

Until real E2E succeeds:

```text
SYSTEM_LAUNCHED=false
```

No cosmetic PASS.
