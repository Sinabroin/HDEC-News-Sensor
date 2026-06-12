# docs/daily — 정적 Executive Daily Brief (P0-B5)

`latest.html`은 `scripts/build_static_report.py`가 mock 파이프라인 결과로 생성한
**정적 데모 스냅샷**이다. Telegram 다이제스트의 **"오늘 브리프 보기"** 버튼이
(REPORT_URL이 설정된 경우) 게시된 이 페이지로 연결된다.

## 재생성

```bash
python3 scripts/build_static_report.py --output docs/daily/latest.html
```

- 외부 API/네트워크 호출 0건, 비밀값 불필요, 저장소 `radar.db` 미접촉(임시 DB 사용).
- 페이지는 standalone HTML이다 — 외부 CDN/스크립트/폰트를 일절 참조하지 않는다.
- CI(Telegram Notify 워크플로)는 발송 전에 같은 명령으로 **생성이 깨지지 않는지 확인만**
  하고 자동 commit/publish는 하지 않는다. 게시 내용을 갱신하려면
  로컬에서 재생성한 뒤 commit한다 (mock 데이터라 내용은 사실상 결정적이다).

## GitHub Pages 수동 설정 (선택 — 자동화하지 않음)

1. GitHub → **Settings → Pages**
2. Source: **Deploy from a branch**
3. Branch: **main** / Folder: **/docs** → Save
4. 활성화 후 리포트 주소:
   `https://<owner>.github.io/<repo>/daily/latest.html`
5. GitHub → Settings → Secrets and variables → Actions → **Variables**에
   `REPORT_URL` = 위 주소 추가 → 이후 Telegram 발송에 링크 버튼이 붙는다.

`REPORT_URL`이 없으면 워크플로는 기존처럼 **텍스트 전용** 다이제스트를 발송한다
(실패하지 않는다).

## 보안 주의

- 무료 플랜의 GitHub Pages는 **공개**다. 이 폴더에는 **mock/데모 데이터만** 게시한다.
- 실제 내부 뉴스·민감 신호 데이터는 공개 Pages에 올리지 않는다 —
  실데이터 단계에서는 사내/비공개 호스팅을 사용한다.
- `latest.html`에는 비밀값·토큰·chat id가 절대 포함되지 않는다
  (`scripts/verify_static_report.py`가 검사한다).
