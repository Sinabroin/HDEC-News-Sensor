# docs/daily — 정적 Executive Daily Brief (P0-B5 · P0-C1.5)

`latest.html`은 `scripts/build_static_report.py`가 생성하는 **정적 Executive Daily
Brief**다. `dashboard-latest.html`은 `scripts/build_static_dashboard.py`가 생성하는
**정적 요약 대시보드 export**다. Telegram 다이제스트의 **"요약 대시보드 보기"** 버튼은
`dashboard-latest.html`, **"전체 리포트 보기"** 버튼은 `latest.html`로 연결된다.

이 파일은 두 가지 상태일 수 있다:

- **mock 데모 스냅샷** — 로컬/기본값. 모드 배지 `데모 데이터`, 출처 `데모(mock) 데이터`.
- **live 실뉴스 리포트** — Telegram Notify 워크플로(빈 메시지/예약 실행)가
  `NEWS_MODE=live`로 생성해 **auto-commit**한 것. 모드 배지 `LIVE · 공개 RSS`,
  출처 `뉴스: 공개 RSS 수집 · 시장지표: 미연동`. 기사 링크는 실제 공개 RSS 원문 URL.

어느 상태든 **시장지표(macro)는 항상 "미연동"** 이며 가짜 시세 수치를 싣지 않는다
(P0-B6). 본문 전문·비밀값·토큰은 절대 포함하지 않는다
(`scripts/verify_static_report.py`가 mock·live 모드 모두 기계 검사한다).

## 재생성

```bash
python3 scripts/build_static_report.py --output docs/daily/latest.html
python3 scripts/build_static_dashboard.py --output docs/daily/dashboard-latest.html
```

- mock 빌드는 외부 API/네트워크 호출 0건, 비밀값 불필요, 저장소 `radar.db`
  미접촉(임시 DB 사용). `NEWS_MODE=live` 빌드는 공개 RSS만 호출하며 비밀값은 여전히 불필요.
- 페이지는 standalone HTML이다 — 외부 CDN/스크립트/폰트를 일절 참조하지 않는다
  (기사 원문 `href` 링크만 외부를 가리키며 `target="_blank" rel="noopener noreferrer"`).

## 자동 게시 (P0-C1.5)

Telegram Notify 워크플로의 **빈 메시지/예약 실행**은 발송 전 verifier 통과 후
`NEWS_MODE=live`로 `latest.html`을 생성하고, **live 수집이 성공한 경우에만**
`github-actions[bot]`이 main에 `chore: update live daily report`로 auto-commit한다
(변경분이 있을 때만 — 없으면 skip하며 실패하지 않는다).

- live 수집이 실패/0건이면 작업 트리를 복원해 **가짜 live를 게시하지 않고**,
  정직하게 mock(데모) 라벨된 fallback 다이제스트를 발송한다.
- **커스텀 메시지** 실행(workflow_dispatch에 메시지 입력)은 live를 강제하지 않고
  입력 메시지를 그대로 발송한다 (DASHBOARD_URL/REPORT_URL 버튼은 그대로 유지).

수동으로 갱신하려면 로컬에서 재생성한 뒤 commit해도 된다.

## GitHub Pages 설정 (브랜치 게시 — 1회 수동)

리포트 **게시(commit)** 는 워크플로가 자동화하지만, Pages **소스 설정**은 1회 수동이다:

1. GitHub → **Settings → Pages**
2. Source: **Deploy from a branch**
3. Branch: **main** / Folder: **/docs** → Save
4. 활성화 후 리포트 주소:
   `https://<owner>.github.io/<repo>/daily/latest.html`
   (커스텀 도메인 사용 시 그 주소)
5. GitHub → Settings → Secrets and variables → Actions → **Variables**에
   `REPORT_URL` = 위 `latest.html` 주소 추가 → 이후 Telegram 발송에
   "전체 리포트 보기" 링크 버튼이 붙는다.
6. 선택: `DASHBOARD_URL` =
   `https://<owner>.github.io/<repo>/daily/dashboard-latest.html`
   (커스텀 도메인도 동일 경로). 생략하면 sender가 표준 `REPORT_URL`에서 파생한다.

`REPORT_URL`과 `DASHBOARD_URL`이 모두 없으면 워크플로는 기존처럼 **텍스트 전용**
다이제스트를 발송한다(실패하지 않는다).

## 보안 주의

- 무료 플랜의 GitHub Pages는 **공개**다. 이 폴더에는 **공개 가능한 데이터만** 게시한다 —
  mock 데모 데이터 또는 **공개 RSS 뉴스의 제목·요약·원문 URL과 public-safe 점수 언어**.
- 비공개 유료 뉴스 본문·내부 민감 신호·운영자 메모는 공개 Pages에 올리지 않는다.
  그런 데이터 단계에서는 사내/비공개 호스팅으로 전환한다.
- `latest.html`/`dashboard-latest.html`에는 비밀값·토큰·chat id·본문 전문이 절대 포함되지 않는다
  (`scripts/verify_static_report.py`가 mock·live 모드 모두 검사한다).
