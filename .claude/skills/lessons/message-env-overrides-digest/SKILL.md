---
name: message-env-overrides-digest
description: Apply when editing telegram-notify.yml or send_telegram.py message resolution. A non-empty MESSAGE default/fallback in the workflow makes the digest path unreachable on every run.
---

## 실수

P0-B1 착수 시점의 `telegram-notify.yml`은 schedule 실행에
`MESSAGE: ${{ ... || 'HDEC Executive Radar scheduled test' }}` 라는 비어 있지 않은
fallback을, manual 실행에 `required: true` + 고정 default 메시지를 갖고 있었다.
이 상태에서 "MESSAGE 없으면 digest 발송" 로직을 붙여도 MESSAGE가 항상 채워지므로
다이제스트는 영원히 발송되지 않는다.

## 원인

센더의 분기 조건(MESSAGE 비어 있음)과 워크플로가 실제로 주입하는 값(항상 비어
있지 않음)을 따로 수정하면 서로 모순돼도 어느 쪽도 에러가 나지 않는다 —
조용히 fallback 메시지만 발송된다.

## 재발 방지 규칙

- workflow_dispatch `message` input은 `required: false` + `default: ""` 를 유지한다.
- env 주입은 `MESSAGE: ${{ github.event.inputs.message || '' }}` 처럼 빈 문자열
  fallback만 허용한다 (`|| '아무 텍스트'` 금지).
- `scripts/verify_telegram_digest.py`의 "MESSAGE fallback이 빈 문자열" /
  "workflow input default가 비어 있음" 검사가 이를 자동 검증한다.
- 발송 로그의 `Message source:` 라벨(env-message / mock-digest)로 실제 경로를
  Actions 로그에서 확인할 수 있다.
