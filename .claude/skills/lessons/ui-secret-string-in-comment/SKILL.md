---
name: ui-secret-string-in-comment
description: 프론트 코드 작성/수정 시 적용. 주석에도 webhook/key 계열 문자열을 쓰지 않는다.
---
## 실수
templates/index.html에 "비밀값(API key, webhook URL)을 포함하지 않는다"는 안내 주석을 넣어
D8 금지 문자열 스캔(`grep -iE "webhook|api[_-]?key" templates/`)에 걸림.

## 원인
검수 기준은 "프론트 코드에 webhook·key 문자열 0건"인데, 값이 아닌 단어 자체도 스캔 대상임을
간과하고 가드 주석을 추가함.

## 재발 방지 규칙
- 프론트 파일에는 webhook/key/token/secret 계열 단어를 주석으로도 쓰지 않는다.
- 보안 원칙 설명은 README/rules 문서에만 둔다.
- 프론트 작성 직후 스캔을 돌리고 나서 다른 작업으로 넘어간다.
