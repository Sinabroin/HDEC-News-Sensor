# D7-AD-Y 임원 대시보드 비주얼 톤 조사 (CSS 변경 전 · 계획 전용)

작성 시점: D7-AD-W Phase 1C. **이번 Phase에서는 CSS 대규모 리스킨을 하지 않는다.**
실제 스타일 변경은 별도 승인 후 D7-AD-Y에서 진행한다.

## 현재 인상

- 배경·카드·강조색에 **하늘색(sky) 계열**(`--sky`, `#F1F7FE`, `#EAF2FC`, `#CFE0F4`)이 많아 전체 톤이 **가벼운 SaaS**처럼 느껴진다.
- 임원 보고용 대시보드로는 **점잖고(sober) 신뢰감 있는 executive radar** 톤이 더 적합하다.

## 목표 톤 (조사 방향)

| 영역 | 현재 | 목표 |
|---|---|---|
| 배경 | sky-tint `#FAFCFE` / `#F5F9FE` | warm gray / off-white — 파란기 축소 |
| 텍스트 | `--ink3` + sky accent | navy / slate — 본문 대비 강화 |
| 강조 | sky 단독 pill·border | navy + muted blue + amber 포인트(과하지 않게) |
| 카드 | sky border hover | 경계(line) 정돈, 그림자 약화 |
| 배지/pill | 밝은 sky·amber | executive 보고서 스타일 — 채도 낮춤 |

## 후보 팔레트 (D7-AD-Y 승인 전 · CSS 미적용)

| 토큰 | 후보 hex | 용도 |
|---|---|---|
| navy | `#1E3A5F` | 제목·핵심 숫자 |
| slate | `#475569` | 본문·보조 |
| warm gray | `#F5F4F2` | 페이지 배경 |
| muted blue | `#5B7FA6` | 링크·활성( sky 대체 ) |
| off-white | `#FAFAF8` | 카드 배경 |

## 금지

- 너무 어두운 dark 모드 / **군사 작전실** 느낌
- 가짜 데이터·차트로 “풍성해 보이게” 만드는 장식
- 기능 IA 변경 없이 색만 바꾸는 것은 OK, 레이아웃 대개편은 별도 승인

## 현대건설 임원 보고용 톤

- **신뢰·절제·가독성** 우선 — 화려한 gradient·과한 pill 색상 자제
- 리스크(amber/red)는 유지하되 배경 sky와 **동시에 쓰이지 않게** 대비 조정
- 실제 CSS 변경은 **D7-AD-Y 승인 후** `templates/dashboard_preview.html` CSS 변수 일괄 교체

## 착수 전 체크리스트 (D7-AD-Y)

- [ ] `templates/dashboard_preview.html` CSS 변수(`--sky`, `--ink`, `--card`) 팔레트 초안
- [ ] 접근성 대비(본문 4.5:1 이상) 스폿 체크
- [ ] P0-A/P0-B verifier 회귀 없이 적용
- [ ] 모바일(900px) 레일·pill 가독성 확인
