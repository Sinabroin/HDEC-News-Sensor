# 다음 운영자 핸드오프 체크리스트 (Next Operator Handoff)

HDEC Executive Radar를 인수하는 다음 운영자/개발자를 위한 인계 문서다.
함께 읽을 것: `EXECUTIVE_RADAR_RUNBOOK.md`(매일 운영), `EXECUTIVE_DEMO_SCRIPT.md`(임원 시연),
저장소 루트 `CLAUDE.md`·`rules.md`(도메인 경계·금지 규칙 — 충돌 시 `rules.md`가 우선).

---

## 1. 기준 커밋 (Baseline)

- **`5d5a543 fix: restore live article quality gate`** (main = origin/main, 동기화 상태).
- 작업 트리는 `.claude/settings.local.json`(로컬 전용)을 제외하면 clean이어야 한다.
- mock 데모 기준 숫자(불변): **수집·분석 28 / 신호 21 / 즉시 3 / 검토 4 / 추적 14 / 참고·제외 7**.

## 2. 프로젝트 상태 (Status)

- **D3A–D3G 완료.** 임원 의사결정 레이더로서의 분류·라우팅·중복제어·근거/감사·품질 게이트가 정착.
- **공개 live 스냅샷 검증됨** — 공개 Pages 주소가 `news-data-mode:live`로 게시·검증된 상태.
- **전체 게이트 10/10 PASS**(`5d5a543` 기준 실측), `git diff --check` clean.
- 게시 자동화: `.github/workflows/telegram-notify.yml`(KST 08:00 예약 → live 리포트 생성 →
  성공 시 `github-actions[bot]` auto-commit → Telegram 다이제스트 발송).

## 3. D3A–D3G로 완료한 것 (각 능력 + 잠금 검증기)

| 슬라이스 | 능력 | 잠금 검증기 / 주요 커밋 |
|---|---|---|
| **D3A** 토픽 분류 가드 | 주입 토픽이 AI로 오분류되는 false positive 차단, 재무 AI→OTHER, 분양 PR/스포츠 강등 | `verify_executive_topic_classification.py` · `b74cb05` |
| **D3B** 사유 텍스트 특화 | generic "수주 경쟁력·시장 포지션 영향권" 제거, 유형별 구체 사유 | `verify_executive_reason_text_specificity.py` · `2d9d743`/`f47aebd` |
| **D3C** Top 노출 품질 | 임원 Top 노출 품질 게이트 강화 | `verify_executive_top_exposure_quality.py` · `25a6d0f` |
| **D3D** 노출 클러스터 캡 | 같은 사안 near-duplicate를 클러스터로 묶어 대표 1건만 상단(`ai_datacenter_power` 등) | `verify_executive_cluster_exposure.py` · `d64275c` |
| **D3F** Surface dedup + 감사 | 한 기사/클러스터가 여러 Top 카드를 도배하지 않게 cross-surface dedup + 운영자 노출 감사 섹션 | `verify_executive_surface_dedup_audit.py` · `8edd4bd` |
| **D3G** Live 품질 게이트 복구 | D3D 클러스터 캡 이후 stale해진 집계 호스트(f_agg) 기대를 검증기 전용으로 좁게 교정 | `verify_live_article_quality_gate.py` · `5d5a543` |

> D3G 상세 근본원인: D3D가 `ai_datacenter_power` 클러스터 키를 도입하면서, 집계 호스트
> near-dup(`f_agg`, v.daum.net)이 신뢰 매체 동일 사안(`f_ai_dc`)과 한 클러스터로 묶여 AI
> 테마 섹션에서 dedup됐다. C1.11 검증기는 "f_agg가 AI 섹션 고정"을 기대했으나 이는 D3D
> 이후 **stale**했다. 제품은 정상(하드 제외 아님 — `top_new_issues`에 'Daum 경유'로 노출).
> 수정은 **검증기 전용**이었고 제품/리포트는 불변. (메모리 `hdec-radar-p0d3g-live-quality-gate`)

## 4. 현재 게이트 팩 (Gate Pack)

- **전체 게이트**: 런북 §B(10개 verifier + `py_compile` + `git diff --check`). 릴리스/핸드오프 시.
- **빠른 게이트**: 런북 §C(static_report / live_article_quality_gate / final_live_routing /
  data_source_honesty + `git diff --check`). 평상 운영 시.
- 전부 네트워크 없이 결정적으로 돈다(fixture + temp DB 시뮬, 저장소 `radar.db` 미접촉, 비밀값 0건).

## 5. 공개 리포트가 stale해 보일 때 복구 (Resume)

1. 자동 게시가 끝났는지 확인: `git fetch origin main && git log --oneline -3 origin/main`
   (`chore: update live daily report` 또는 최신 커밋이 올라왔는지).
2. 공개 캐시 버스트로 재요청(런북 §D): `?v=<COMMIT>-$(date +%s)`.
3. 여전히 옛 내용/`news-data-mode:live`가 0이면 수동 갱신(런북 §A 3–6): live 빌드 →
   `news_data_mode=live` 확인 → 변경분만 `docs/daily/latest.html` 커밋 → push.
4. live 빌드가 mock으로 fallback하면 **가짜 live를 게시하지 않는다** — 복원 후 §E 트리아지.

## 6. 과거 실수 반복 금지 (Do-Not-Repeat)

- **stale 검증기 vs 제품 버그를 먼저 구분한다.** verifier만 FAIL이고 제품 출력이 정상이면,
  제품을 약화하기 전에 검증기 기대가 옛 동작에 묶였는지 본다(D3G가 정확히 이 경우였다).
  stale이면 검증기를 **좁게** 고치고 **이유 주석**을 남긴다. 테스트를 통째로 무력화하지 않는다.
- **`git add .` 금지.** 항상 파일 단위로 stage한다.
- **`.claude/settings.local.json`을 stage하지 않는다.** (로컬 전용 설정)
- **force push 금지.** main에 강제 푸시하지 않는다.
- **랭킹/정밀도를 고치면서 provider/수집을 넓히지 않는다.** 커버리지 변경과 랭킹 변경은
  별도 스프린트다(런북 §F 원칙 1). 한 번에 섞으면 회귀 원인 분리가 불가능해진다.
- **테마 멀티렌즈 탭을 일괄 dedup으로 접지 않는다.** 같은 사안을 여러 관점으로 보여주는
  설계다. dedup은 항상 보이는 digest/top_new가 테마 카드를 반복하지 않게 하는 데만 쓴다.
- **집계 호스트라는 이유만으로 정상 기사를 하드 제외하지 않는다**(특히 Daum 경유 현대건설
  직접 기사 — D3G 회귀). 표시 라벨만 정규화하고 원문 URL은 보존한다.
- **mock 데모 숫자(28/21/3/4/14/7)를 깨지 않는다.** 라우팅/표현 변경은 섹션 멤버십만 바꾸고
  등급을 재계산하지 않아야 한다. 깨지면 scoring/collector 경계를 침범한 것이다.

## 7. 다음 권장 작업 (Next Recommended Work)

- **D3I — 사람 검토 큐 / 수동 Send 게이트 하드닝**(Human Review Queue / Manual Send Gate hardening),
  **또는 D3I — 알림 후보 워크플로 폴리시**(Alert candidate workflow polish).
- 방향: 현재 "예약 게시 + 다이제스트 자동" 위에, **임원에게 즉시 알림을 띄우기 전 운영자
  검토/승인 단계**를 더 명시적으로 만든다(임의 자동 발송 금지 원칙 유지 — 런북 §F 6).
- 착수 전 점검: 이 작업은 **알림/발송 경계**(notification 도메인 + 워크플로)이지 랭킹/분류가
  아니다. provider/수집/scoring을 건드리지 않고 끝내는 것이 성공 기준이다.

---

### 인계 시 즉시 실행해 볼 것 (Smoke)

```bash
git fetch origin main && git status --short --branch     # 5d5a543, 동기화 확인
python3 -m py_compile scripts/*.py app/*.py               # 컴파일
# 런북 §C 빠른 게이트 4종 + git diff --check
python3 scripts/verify_static_report.py
python3 scripts/verify_live_article_quality_gate.py
python3 scripts/verify_executive_final_live_routing.py
python3 scripts/verify_data_source_honesty.py
git diff --check
```
