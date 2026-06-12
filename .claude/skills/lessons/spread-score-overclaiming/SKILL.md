---
name: spread-score-overclaiming
description: Apply when adding "n개 매체 보도" / spread / coverage indicators to briefing or digest output. Dedup drops duplicate articles before insert, so true multi-outlet coverage cannot be derived from stored rows — any spread number must be labeled as a heuristic estimate.
---

## 발견 (P0-B2)

brief에 "이슈 확산도(여러 매체 보도)" 지표를 넣으려 했으나, collector가
중복 기사를 **insert 전에 dedup으로 제거**하기 때문에 (mock_019는 url_hash,
mock_020은 normalized_title 중복으로 소멸) 저장된 데이터에는 "같은 사건을
몇 개 매체가 보도했는가"가 남아 있지 않다. DB만 보고 spread를 계산하면
실제 확산도를 조용히 과소/과대 평가하게 된다.

## 원인

dedup은 저장 비용·중복 알림 방지를 위한 설계이고, 확산도는 그 dedup이
버리는 바로 그 정보다. 두 요구가 충돌한다는 사실이 어디에도 기록되어
있지 않았다.

## 재발 방지 규칙

- 저장된 행만으로 만든 spread 지표는 반드시 **추정치로 라벨링**한다
  ("관련 신호 n건 · 출처 m곳" = topic 후보 겹침 휴리스틱). "n개 매체가
  보도" 같은 확정 표현 금지.
- brief 구조체에 `spread_method`를 명시하고, operator_note에 한계를
  적는다 — `verify_executive_brief.py`가 spread 필드/라벨 존재를 검사한다.
- 진짜 확산도가 필요해지면(P0-B 실데이터 단계) dedup 시점에 중복 카운트를
  별도 집계 필드로 남기는 설계 변경을 먼저 검토한다. 저장 후 역산은 불가능.
