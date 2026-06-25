#!/usr/bin/env python3
"""D7-C verifier — summary dashboard article quality + clarified Telegram buttons.

Fully offline (no network, DB writes, secrets, or send). It proves the D7-C asks:

Part C — article quality (build_static_dashboard.py selection):
  · No exact or near-duplicate dashboard titles (re-broadcast copies collapsed).
  · Source label is not mostly the generic '원문 경유' — domain-derived publisher used.
  · Rows are drawn from the shared brief's VISIBLE signal sections (same data latest.html
    renders); ≥N freshly-built rows match brief signal titles.
  · Weak HDEC-adjacent false positives ('HD현대·현대차·현대건설기계') are not featured as
    Hyundai E&C direct relevance.
  · Every dashboard article URL is http(s); rows carry title/source/url/lens.
  · The new selection helpers behave correctly on synthetic live-like inputs.

Part A/B — clarified buttons:
  · build_payload orders '대시보드 보기' → dashboard-latest.html then '상세 리포트 보기' →
    latest.html; the old labels are gone from the sender.

Separation: latest.html stays the full Executive Daily Brief; dashboard-latest.html stays
the summary dashboard.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
BRIEF_BUILDER = ROOT / "scripts" / "build_executive_brief.py"
SENDER = ROOT / "scripts" / "send_telegram.py"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"
LATEST = ROOT / "docs" / "daily" / "latest.html"

SUMMARY_LABEL = "대시보드 보기"
FULL_REPORT_LABEL = "상세 리포트 보기"
OLD_SUMMARY_LABEL = "요약 대시보드 보기"
OLD_FULL_LABEL = "전체 리포트 보기"
BASE = "https://guides.playground-aidesignlab.co.kr/HDEC-News-Sensor/daily"
DASH_URL = f"{BASE}/dashboard-latest.html"
REPORT_URL = f"{BASE}/latest.html"
VIA_GENERIC = "원문 경유"
N_MATCH_MIN = 5
NEAR_DUP_THRESHOLD = 0.7
TOKEN_SHAPE = re.compile(r"[0-9]{8,}:[A-Za-z0-9_-]{20,}")

VALID_LENS = {
    "now", "new", "ai", "civil_infrastructure", "building_housing", "plant",
    "new_energy", "development_business", "global_business", "safety_quality",
    "hyundai_group", "competitor_contractors", "trust_companies", "developers",
    "oil_energy", "hormuz", "domestic_site", "overseas_site", "overseas_branch",
    "overseas_subsidiary",
}

_failures = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    tag = "PASS" if ok else "FAIL"
    line = f"[{tag}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _model(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="preview-model">(.*?)</script>',
                  html, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except ValueError:
        return {}


def _clean_env(**extra: str) -> dict:
    env = {**os.environ, "APP_MODE": "mock"}
    for key in ("NEWS_MODE", "MACRO_MODE", "REPORT_URL", "DASHBOARD_URL",
                "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS", "TELEGRAM_SEND_MODE",
                "REVIEW_APPROVED", "CONFIRM_SEND", "MESSAGE", "TELEGRAM_BOT_USERNAME"):
        env.pop(key, None)
    env.update(extra)
    return env


def _run(args, timeout=300) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                          env=_clean_env(), timeout=timeout)


def _tokens(t: str) -> set:
    return set(re.findall(r"[가-힣A-Za-z0-9]+", str(t or "").lower()))


def _overlap(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _near_dup_pairs(titles: list) -> list:
    pairs = []
    for i in range(len(titles)):
        for j in range(i + 1, len(titles)):
            if _overlap(titles[i], titles[j]) >= NEAR_DUP_THRESHOLD:
                pairs.append((titles[i][:24], titles[j][:24]))
    return pairs


def _brief_titles() -> set:
    proc = _run([sys.executable, str(BRIEF_BUILDER), "--json"])
    if proc.returncode != 0:
        return set()
    brief = json.loads(proc.stdout)
    titles = set()
    for key in ("top_immediate_signals", "top_new_issues", "hdec_direct_signals",
                "ai_radar_signals", "business_signals", "risk_regulation_signals",
                "competitor_supply_signals", "macro_economy_signals"):
        for s in brief.get(key) or []:
            if s.get("title"):
                titles.add(s["title"])
    return titles


def _import_builder():
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_static_dashboard as b  # noqa: E402
    return b


# ---------------------------------------------------------------------------
# 1 · 빌더가 만드는 행의 품질 (mock 신선 빌드 + 공유 brief 대조)
# ---------------------------------------------------------------------------

def check_builder_quality() -> None:
    b = _import_builder()
    brief_titles = _brief_titles()
    if not check("1a: 공유 brief 신호 제목 수집(>0)", bool(brief_titles), f"{len(brief_titles)}건"):
        return
    with tempfile.TemporaryDirectory(prefix="hdec_q_") as tmp:
        out = Path(tmp) / "dash.html"
        proc = _run([sys.executable, str(BUILDER), "--output", str(out)])
        if not check("1b: builder --output 동작", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "")[-200:]):
            return
        html = out.read_text(encoding="utf-8")
        model = _model(html)
        rows = model.get("news_rows") or []
        ai = model.get("ai_rows") or []
        allrows = rows + ai
        titles = [r.get("title", "") for r in allrows]

        check("1c: 신선 빌드 news 행 다수(>=6)", len(rows) >= 6, f"{len(rows)}행")
        check("1d: 모든 행에 title/source/lens 존재",
              bool(allrows) and all(r.get("title") and r.get("source") and r.get("lens")
                                    for r in allrows))
        check("1e: 모든 news 행이 http(s) URL 보유",
              bool(rows) and all(str(r.get("url", "")).startswith("http") for r in rows))
        # 근접 중복 제거 — 신선 빌드 행에 임계 이상 겹치는 제목 쌍이 없어야 한다
        dups = _near_dup_pairs(titles)
        check("1f: 근접 중복 제목 없음(임계 0.7)", not dups, f"{dups[:2]}")
        # 일반 '원문 경유'가 출처의 다수를 차지하지 않아야 한다 (도메인 유래 매체명 사용)
        via = sum(1 for r in allrows if str(r.get("source", "")).strip() == VIA_GENERIC)
        check("1g: 출처가 일반 '원문 경유'로 도배되지 않음(<50%)",
              not allrows or via <= len(allrows) // 2, f"{via}/{len(allrows)}")
        # latest.html 가시 섹션과 동일 출처 — 다수 행 제목이 공유 brief 신호에 존재
        matched = [t for t in titles if t in brief_titles]
        check(f"1h: 가시 brief 신호와 일치하는 행 >= {N_MATCH_MIN}",
              len(matched) >= N_MATCH_MIN, f"{len(matched)}/{len(titles)} 일치")
        # featured hero가 약한 HDEC 오탐이 아님
        fm = re.search(r'<article class="card featured"[^>]*>.*?<h2>(.*?)</h2>', html, re.S)
        feat = fm.group(1) if fm else ""
        check("1i: featured hero가 약한 HDEC 오탐(HD현대·현대건설기계 등) 아님",
              bool(feat) and not b._is_weak_hdec_fp({"title": feat}), feat[:42])
        # 약한 HDEC 오탐이 행에 과대표집되지 않음
        weak = [t for t in titles if b._is_weak_hdec_fp({"title": t})]
        check("1j: 약한 HDEC 오탐이 행에 과대표집되지 않음(<=1)", len(weak) <= 1, f"{weak[:2]}")


# ---------------------------------------------------------------------------
# 2 · 커밋된 대시보드의 품질 (live/mock 무관 구조 검사) + 빌드 모드 정직성
# ---------------------------------------------------------------------------

def check_committed_quality() -> None:
    if not check("2a: docs/daily/dashboard-latest.html 존재", DASHBOARD.exists()):
        return
    html = _read(DASHBOARD)
    mk = re.search(r"news-data-mode:(live|mock)", html)
    check("2b: 빌드 모드 마커(live|mock) 존재", bool(mk), mk.group(1) if mk else "없음")
    model = _model(html)
    rows = model.get("news_rows") or []
    ai = model.get("ai_rows") or []
    allrows = rows + ai
    titles = [r.get("title", "") for r in allrows]
    check("2c: 커밋 대시보드 news 행 다수(>=6)", len(rows) >= 6, f"{len(rows)}행")
    check("2d: 모든 행에 title/source/url/lens 존재",
          bool(allrows) and all(r.get("title") and r.get("source")
                                and str(r.get("url", "")).startswith("http")
                                and r.get("lens") for r in rows))
    keys = {l for r in allrows for l in r.get("lens", [])}
    check("2e: 모든 lens 키가 유효", keys <= VALID_LENS, f"미정의: {sorted(keys - VALID_LENS)}")
    exact = [t for t in titles if titles.count(t) > 1]
    check("2f: 정확 중복 제목 없음", not exact, f"{set(exact)}")
    check("2g: 근접 중복 제목 없음(임계 0.7)", not _near_dup_pairs(titles),
          f"{_near_dup_pairs(titles)[:2]}")
    via = sum(1 for r in allrows if str(r.get("source", "")).strip() == VIA_GENERIC)
    check("2h: 출처가 일반 '원문 경유'로 도배되지 않음(<50%)",
          not allrows or via <= len(allrows) // 2, f"{via}/{len(allrows)}")
    check("2i: 모든 대시보드 URL이 http(s)",
          bool(rows) and all(str(r.get("url", "")).startswith(("http://", "https://"))
                             for r in rows))
    check("2j: 발송 토큰/시크릿 미혼입",
          not TOKEN_SHAPE.search(html) and "TELEGRAM_BOT_TOKEN" not in html)


def check_build_mode_honesty() -> None:
    """mock 빌드는 news-data-mode:mock으로 정직 표기됨(빌드 모드에 따른 마커)."""
    with tempfile.TemporaryDirectory(prefix="hdec_mode_") as tmp:
        out = Path(tmp) / "dash.html"
        proc = _run([sys.executable, str(BUILDER), "--output", str(out)])
        if not check("3a: mock 빌드 동작", proc.returncode == 0 and out.exists()):
            return
        html = out.read_text(encoding="utf-8")
        mk = re.search(r"news-data-mode:(live|mock)", html)
        check("3b: mock 빌드 마커 == mock(빌드 모드 정직)",
              bool(mk) and mk.group(1) == "mock", mk.group(1) if mk else "없음")
        check("3c: live 표기 라벨이 mock 빌드에 잘못 들어가지 않음",
              "자동 수집 기사" not in html)


# ---------------------------------------------------------------------------
# 4 · 분리: latest.html=전체 리포트 / dashboard-latest.html=요약 대시보드
# ---------------------------------------------------------------------------

def check_separation() -> None:
    if not check("4a: docs/daily/latest.html 존재", LATEST.exists()):
        return
    latest = _read(LATEST)
    dashboard = _read(DASHBOARD)
    check("4b: latest.html은 전체 Executive Daily Brief", "Executive Daily Brief" in latest)
    check("4c: dashboard-latest.html은 요약 대시보드(export 마커)",
          "dashboard-export:summary" in dashboard and 'id="preview-model"' in dashboard)
    check("4d: latest.html != dashboard-latest.html", latest != dashboard)
    check("4e: 전체 리포트에 대시보드 전용 토큰 미혼입",
          'id="preview-model"' not in latest and "dashboard-export:summary" not in latest)


# ---------------------------------------------------------------------------
# 5 · 명확화된 Telegram 버튼 라벨/경로 (NEW 라벨, OLD 라벨 제거)
# ---------------------------------------------------------------------------

def check_button_labels() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import send_telegram as st
    except Exception as exc:  # noqa: BLE001
        check("5: send_telegram import", False, str(exc))
        return
    payload = st.build_payload("DRY", "m", REPORT_URL, "", DASH_URL)
    btns = json.loads(payload["reply_markup"])["inline_keyboard"][0]
    labels = [b["text"] for b in btns]
    urls = [b["url"] for b in btns]
    check("5a: 버튼 순서 = 대시보드 보기 → 상세 리포트 보기",
          labels[:2] == [SUMMARY_LABEL, FULL_REPORT_LABEL], " / ".join(labels[:2]))
    check("5b: '대시보드 보기' → dashboard-latest, '상세 리포트 보기' → latest 매핑",
          urls[:2] == [DASH_URL, REPORT_URL])
    sender_src = _read(SENDER)
    check("5c: 새 라벨 상수 사용",
          f'SUMMARY_BUTTON_TEXT = "{SUMMARY_LABEL}"' in sender_src
          and f'FULL_REPORT_BUTTON_TEXT = "{FULL_REPORT_LABEL}"' in sender_src)
    check("5d: 옛 라벨이 sender에서 제거됨",
          OLD_SUMMARY_LABEL not in sender_src and OLD_FULL_LABEL not in sender_src)


# ---------------------------------------------------------------------------
# 6 · 새 선택 헬퍼의 동작 (합성 live-like 입력) — live 동작 증명
# ---------------------------------------------------------------------------

def check_helpers_unit() -> None:
    b = _import_builder()
    # _better_source: 일반 '원문 경유'를 도메인 유래 매체명/도메인으로 보강
    check("6a: '원문 경유'+yna URL → 연합뉴스",
          b._better_source({"display_source": VIA_GENERIC,
                            "url": "https://www.yna.co.kr/view/x"}) == "연합뉴스")
    check("6b: '원문 경유'+미식별 도메인 → 도메인 노출(일반 라벨 회피)",
          b._better_source({"display_source": VIA_GENERIC,
                            "url": "https://local-paper.example/a"}) == "local-paper.example")
    check("6c: 식별된 매체명/집계 라벨은 보존",
          b._better_source({"display_source": "연합뉴스"}) == "연합뉴스"
          and b._better_source({"display_source": "Daum 경유"}) == "Daum 경유")
    # _drop_near_dups: 재전송 근접 중복 1건만 남김
    dd = b._drop_near_dups([
        {"title": "AI 데이터센터 전력수요 급증 송배전 투자 확대"},
        {"title": "AI 데이터센터 전력수요 급증 송배전 투자 재개"},
        {"title": "체코 원전 본계약 팀코리아 유럽 수주"}])
    check("6d: 근접 중복 재전송본 제거(3→2)", len(dd) == 2, [x["title"][:16] for x in dd])
    # _is_weak_hdec_fp: 현대건설기계/HD현대/현대차는 오탐, 현대건설(E&C)은 진짜
    fp_true = ["HD현대건설기계, 굴착기 수출", "현대건설기계 영업익 급증", "현대차 美 공장 착공"]
    fp_false = ["현대건설, 네옴 수주", "현대엔지니어링 정유플랜트", "현대건설·HD현대 컨소시엄"]
    check("6e: 현대건설기계/HD현대/현대차 = 약한 오탐(True)",
          all(b._is_weak_hdec_fp({"title": t}) for t in fp_true))
    check("6f: 현대건설(E&C) 직접 언급 = 오탐 아님(False)",
          not any(b._is_weak_hdec_fp({"title": t}) for t in fp_false))
    # featured 가드: hdec_direct에 약한 오탐이 먼저 와도 진짜 현대건설을 featured로
    brief = {"hdec_direct_signals": [
                {"title": "HD현대건설기계 굴착기 수출", "final_score": 4.0,
                 "article_id": "fp", "url": "https://news.example.com/fp"},
                {"title": "현대건설, 네옴 AI 데이터센터 EPC 수주", "final_score": 3.5,
                 "article_id": "real", "url": "https://news.example.com/real"}],
             "top_immediate_signals": [], "top_new_issues": [], "ai_radar_signals": []}
    feat = (b._derive(brief).get("featured_sig") or {}).get("title", "")
    check("6g: featured 선택이 약한 오탐을 건너뛰고 현대건설 신호를 고름",
          feat.startswith("현대건설"), feat[:40])


def main() -> int:
    print(f"== verify_dashboard_article_quality @ {ROOT} ==")
    check_builder_quality()
    check_committed_quality()
    check_build_mode_honesty()
    check_separation()
    check_button_labels()
    check_helpers_unit()

    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 요약 대시보드 기사 품질 + 명확화된 Telegram 버튼 확인 (D7-C)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
