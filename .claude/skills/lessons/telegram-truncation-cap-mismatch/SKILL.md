---
name: telegram-truncation-cap-mismatch
description: Apply when editing Telegram send/digest scripts. The sender's truncation cap silently mangled any message longer than its limit; sender cap must always be >= builder budget.
---

## 실수

P0-B1 착수 시점의 `scripts/send_telegram.py`는 메시지를 800자에서 절단했다
(`message[:797] + "..."`). 일일 다이제스트는 통상 600~1500자 이상이므로,
빌더를 붙이는 순간 본문이 중간에서 잘려 나가는 회귀가 예정되어 있었다.

## 원인

발송 스크립트의 절단 상한이 "고정 테스트 메시지" 시절 값(800)으로 남아 있었고,
메시지를 생성하는 쪽(빌더)과 발송하는 쪽(센더)의 길이 계약이 어디에도 명시되지
않아 두 상수가 따로 놀 수 있었다.

## 재발 방지 규칙

- 길이 계약을 상수로 명시한다: 빌더 `MESSAGE_BUDGET`(3000) <= 센더
  `MAX_MESSAGE_LEN`(3500) < Telegram 한도(4096).
- 두 상수 중 하나를 바꿀 때는 반드시 둘 다 확인한다.
- `scripts/verify_telegram_digest.py`의 "발송 상한 >= digest 예산" 검사가
  이 관계를 자동 검증한다 — 이 검사를 지우거나 우회하지 않는다.
