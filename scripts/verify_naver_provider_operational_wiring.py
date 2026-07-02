#!/usr/bin/env python3
"""D7-AD-X — Naver News API 1급 provider 운영 배선(operational wiring) 검증 (완전 오프라인).

'있기만 한 보조 코드'가 아니라 live 수집의 명시적 1급 provider로 기록·표시되는지 계약으로
검증한다. 네트워크 0건·비밀값 접근 0건으로 실행되며(자격증명 있는 경로는 monkeypatch로
시뮬레이션), 저장소 radar.db를 건드리지 않는다(DB가 필요한 검사는 temp DB 서브프로세스).

핵심 검사 (task F/B/D/E/G):
- 자격증명 부재 시 실패가 아니라 disabled / skipped_missing_credentials로 정직 보고.
- 자격증명 있으면(시뮬레이션) provider=naver_news_api가 source_metadata까지 보존.
- Google+Naver 중복은 provider=google_news_rss+naver_news_api로 병합 + 원문(originallink) 우선,
  Google News 경유 URL은 aggregator provenance(source_url)로 보존, Naver-only 보존.
- collector.run(live)의 provider_status가 status/raw/dedup 카운트를 담는 nested 구조.
- 대시보드 모델 row에 provider/collectionProvider가 존재(providers=(none) 회귀 방지) +
  news_provider_summary가 모델에 노출.
- 외부 href는 퍼블리셔 직링크 우선, news.google.com은 fallback, warning URL은 절대 href 아님.
- 자격증명은 유무(bool)만 노출하고 값/토큰은 어디에도 출력하지 않음.
- 워크플로가 이미 존재하는 repo secrets로 NAVER_NEWS_ENABLED=1을 live 경로에 안전 주입.

사용법:
    python3 scripts/verify_naver_provider_operational_wiring.py
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("APP_MODE", "mock")

CONFIG = ROOT / "app" / "config.py"
NAVER_PROVIDER = ROOT / "app" / "naver_news_provider.py"
NEWS_ACCESS = ROOT / "app" / "news_access.py"
BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
AUDIT = ROOT / "scripts" / "audit_major_source_coverage.py"
RADAR_DB = ROOT / "radar.db"
LIVE_WORKFLOWS = (
    ROOT / ".github" / "workflows" / "telegram-notify.yml",
    ROOT / ".github" / "workflows" / "scheduled-live-refresh.yml",
)

NAVER_OFFICIAL_ENDPOINT = "https://openapi.naver.com/v1/search/news.json"
# 자격증명 토큰 모양(숫자:영숫자 등) 하드코딩 금지 스캔.
TOKEN_SHAPE = re.compile(r"[0-9]{8,}:[A-Za-z0-9_-]{20,}")
SECRET_KV = re.compile(r"(?i)(api[_-]?key|client_secret|bearer|password)\s*[:=]\s*"
                       r"[\"'][A-Za-z0-9_-]{16,}")
GOOGLE_AGG = "https://news.google.com/rss/articles/CBMiEXAMPLE"

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def _db_state():
    if not RADAR_DB.exists():
        return None
    stat = RADAR_DB.stat()
    return (stat.st_mtime_ns, stat.st_size)


# ---------- config / workflow 정적 배선 ----------

def check_config_env_names() -> None:
    src = CONFIG.read_text(encoding="utf-8")
    for name in ("NAVER_NEWS_ENABLED", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"):
        check(f"config.py에 {name} 정의", name in src)
    check("config.py에 token 모양 하드코딩 없음 (자격증명은 env에서만)",
          not TOKEN_SHAPE.search(src))


def check_workflow_wiring() -> None:
    for wf in LIVE_WORKFLOWS:
        if not check(f"{wf.name} 존재", wf.exists()):
            continue
        text = wf.read_text(encoding="utf-8")
        check(f"{wf.name}: NAVER_NEWS_ENABLED live 경로 활성화",
              'NAVER_NEWS_ENABLED: "1"' in text or "NAVER_NEWS_ENABLED: '1'" in text
              or "NAVER_NEWS_ENABLED: 1" in text)
        check(f"{wf.name}: secrets.NAVER_CLIENT_ID 참조 (하드코딩 아님)",
              "${{ secrets.NAVER_CLIENT_ID }}" in text)
        check(f"{wf.name}: secrets.NAVER_CLIENT_SECRET 참조 (하드코딩 아님)",
              "${{ secrets.NAVER_CLIENT_SECRET }}" in text)
        unsafe = [ln.strip() for ln in text.splitlines()
                  if re.search(r"NAVER_CLIENT_(ID|SECRET)\s*:", ln)
                  and "secrets." not in ln]
        check(f"{wf.name}: NAVER_CLIENT_* env가 secrets로만 주입", not unsafe,
              "; ".join(unsafe))
        leak = [ln.strip() for ln in text.splitlines()
                if ("echo" in ln.lower() or "printf" in ln.lower())
                and ("NAVER_CLIENT_ID" in ln or "NAVER_CLIENT_SECRET" in ln)]
        check(f"{wf.name}: NAVER 자격증명을 echo/print하지 않음", not leak, "; ".join(leak))
        check(f"{wf.name}: token 모양 하드코딩 없음", not TOKEN_SHAPE.search(text))


# ---------- provider leaf: 상태/파서/자격증명 유무 ----------

def check_naver_leaf_states() -> None:
    from app import config as cfg
    from app import naver_news_provider as nv

    src = NAVER_PROVIDER.read_text(encoding="utf-8")
    check("naver_provider가 공식 엔드포인트만 사용", NAVER_OFFICIAL_ENDPOINT in src)
    check("naver_provider가 os.environ을 직접 읽지 않음 (config만)", "os.environ" not in src)
    check("naver_provider에 token 모양 하드코딩 없음", not TOKEN_SHAPE.search(src))

    saved = (cfg.NAVER_NEWS_ENABLED, cfg.NAVER_CLIENT_ID, cfg.NAVER_CLIENT_SECRET)
    net = {"calls": 0}
    orig_req = nv._request_json

    def _boom(*_a, **_k):
        net["calls"] += 1
        raise AssertionError("disabled/skip 경로에서 네트워크 호출 금지")

    nv._request_json = _boom
    try:
        cfg.NAVER_NEWS_ENABLED = False
        r = nv.fetch()
        check("disabled → status disabled + 네트워크 0건",
              r["status"] == nv.STATUS_DISABLED and net["calls"] == 0)
        check("disabled 상태에 credentials_present(bool) 노출",
              isinstance(r.get("credentials_present"), bool))

        cfg.NAVER_NEWS_ENABLED = True
        cfg.NAVER_CLIENT_ID = ""
        cfg.NAVER_CLIENT_SECRET = ""
        r = nv.fetch()
        check("enabled+자격증명 부재 → skipped_missing_credentials + 네트워크 0건",
              r["status"] == nv.STATUS_SKIPPED_MISSING_CREDENTIALS and net["calls"] == 0)
        check("skip 상태 credentials_present=False (정직)",
              r.get("credentials_present") is False and r["articles"] == [])

        # 자격증명 present 시뮬레이션 (값은 가짜 · 네트워크는 stub) → active + provider 보존.
        cfg.NAVER_CLIENT_ID = "fake-id"
        cfg.NAVER_CLIENT_SECRET = "fake-secret"
        payload = {"items": [
            {"title": "<b>현대건설</b> 사우디 원전 EPC 수주",
             "originallink": "https://www.yna.co.kr/view/AKR1",
             "link": "https://n.news.naver.com/redirect",
             "description": "원전 &amp; SMR <b>수주</b>",
             "pubDate": "Mon, 16 Jun 2025 09:00:00 +0900"}]}
        nv._request_json = lambda *_a, **_k: payload
        r = nv.fetch()
        rows = r.get("articles") or []
        check("자격증명 present(시뮬) → status active + raw_count>0",
              r["status"] == nv.STATUS_ACTIVE and r.get("raw_count", 0) >= 1, str(r["status"]))
        check("active 상태 credentials_present=True", r.get("credentials_present") is True)
        check("naver active row: provider=naver_news_api가 source_metadata까지 보존",
              bool(rows) and rows[0]["source_metadata"]["provider"] == nv.PROVIDER)
        # 반환 dict 어디에도 자격증명 값/토큰 모양이 없어야 한다.
        blob = json.dumps(r, ensure_ascii=False)
        check("naver fetch 반환에 자격증명 값/토큰 노출 없음",
              "fake-secret" not in blob and "fake-id" not in blob
              and not TOKEN_SHAPE.search(blob))
    finally:
        nv._request_json = orig_req
        (cfg.NAVER_NEWS_ENABLED, cfg.NAVER_CLIENT_ID, cfg.NAVER_CLIENT_SECRET) = saved


def check_naver_parser_originallink() -> None:
    from app import naver_news_provider as nv
    payload = {"items": [
        {"title": "원문 있는 기사", "originallink": "https://www.mk.co.kr/a/1",
         "link": "https://n.news.naver.com/redirect1", "description": "d",
         "pubDate": "Tue, 17 Jun 2025 10:00:00 +0900"},
        {"title": "원문 없는 기사", "originallink": "",
         "link": "https://n.news.naver.com/only", "description": "d", "pubDate": ""},
    ]}
    rows = nv.parse_response(payload, "q", "2026-07-01T00:00:00+09:00",
                            {"mk.co.kr": "매일경제"}, 10)
    check("naver 파서: originallink 우선 (url=originallink)",
          len(rows) == 2 and rows[0]["url"] == "https://www.mk.co.kr/a/1")
    check("naver 파서: originallink 없으면 link 사용",
          len(rows) == 2 and rows[1]["url"] == "https://n.news.naver.com/only")


# ---------- 교차 dedup: provider 병합 + 원문 우선 + aggregator 보존 ----------

def _raw(provider, title, url, source="출처 미상"):
    return {"id": provider[:4], "title": title, "source": source,
            "published_at": "2026-06-17T09:00:00+09:00", "url": url, "snippet": "s",
            "source_metadata": {"provider": provider, "query": "q", "source_url": url,
                                "collected_at": "t", "provider_response_id": "r"}}


def check_cross_provider_merge() -> None:
    from app import collector
    google = [_raw("google_news_rss", "현대건설 사우디 원전 EPC 수주",
                   "https://news.google.com/rss/articles/REDIRECT1"),
              _raw("google_news_rss", "삼성물산 데이터센터 EPC 단독",
                   "https://news.google.com/rss/articles/G2")]
    naver = [_raw("naver_news_api", "현대건설, 사우디 원전 EPC 수주",
                  "https://www.yna.co.kr/view/AKR1", "연합뉴스"),
             _raw("naver_news_api", "현대건설 도시정비 단독 수주",
                  "https://www.mk.co.kr/only", "매일경제")]
    merged = collector.merge_provider_articles(google + naver)
    check("교차 dedup: 중복 1쌍 → 3건 (both 병합 + google-only + naver-only 보존)",
          len(merged) == 3, f"{len(merged)}건")
    dup = next((x for x in merged if "원전" in x["title"]), None)
    only = next((x for x in merged if "도시정비" in x["title"]), None)
    check("교차 dedup: dup provider=google_news_rss+naver_news_api",
          dup and dup["source_metadata"]["provider"] == "google_news_rss+naver_news_api",
          dup and dup["source_metadata"]["provider"])
    check("교차 dedup: dup url=퍼블리셔 원문(originallink) 우선",
          dup and dup["url"] == "https://www.yna.co.kr/view/AKR1", dup and dup["url"])
    check("교차 dedup: Google News 경유 URL을 source_url(provenance)로 보존",
          dup and "news.google.com" in (dup["source_metadata"].get("source_url") or ""),
          dup and dup["source_metadata"].get("source_url"))
    check("교차 dedup: Naver-only 보존 (provider=naver_news_api 단독)",
          only and only["source_metadata"]["provider"] == "naver_news_api")
    g_only, n_only, both = collector._provider_dedup_counts(merged)
    check("provider_dedup_counts: (google_only=1, naver_only=1, both=1)",
          (g_only, n_only, both) == (1, 1, 1), str((g_only, n_only, both)))


# ---------- 외부 URL 정책 (퍼블리셔 직링크 우선, google fallback, warning 배제) ----------

def check_external_url_policy() -> None:
    from app import news_access as na
    choose = na.choose_external_article_url
    check("originallink(퍼블리셔)이 news.google url 필드보다 우선",
          choose({"url": GOOGLE_AGG, "original_url": "https://www.mk.co.kr/x"})
          == "https://www.mk.co.kr/x")
    check("source_metadata.source_url(퍼블리셔)이 news.google url보다 우선",
          choose({"url": GOOGLE_AGG,
                  "source_metadata": {"source_url": "https://www.yna.co.kr/v/1"}})
          == "https://www.yna.co.kr/v/1")
    check("news.google url은 퍼블리셔 직링크 없을 때만 fallback (제거 아님)",
          choose({"url": GOOGLE_AGG}) == GOOGLE_AGG)
    check("네이버 포털 링크도 fallback (직링크 없을 때)",
          choose({"url": "https://n.news.naver.com/x"}) == "https://n.news.naver.com/x")
    check("warning URL은 절대 href 아님",
          choose({"url": "https://www.hdec.kr/warning/WARNING.jpg"}) == "")
    check("final_url은 기본 href로 승격하지 않음",
          choose({"url": "https://pub.example/u", "final_url": "https://pub.example/f"})
          == "https://pub.example/u")
    check("is_aggregator_url: google/포털=True, 퍼블리셔=False",
          na.is_aggregator_url(GOOGLE_AGG) and na.is_aggregator_url("https://v.daum.net/x")
          and not na.is_aggregator_url("https://www.yna.co.kr/x"))
    # news_access가 leaf: 외부 네트워크 강제 없음.
    access_src = NEWS_ACCESS.read_text(encoding="utf-8")
    check("news_access에 외부 네트워크 강제 없음(leaf)",
          not any(t in access_src for t in
                  ("urlopen(", "requests.get(", "httpx.get(", "socket.socket")))


# ---------- 대시보드 모델 provider provenance ----------

def _sig(provider, url, title, src_url=None):
    meta = {"provider": provider, "query": "q", "source_url": src_url or url,
            "collected_at": "t", "provider_response_id": "r"}
    # article_id는 url 기준으로 고유하게 만든다 (provider[:4]로 만들면 결합 토큰과 충돌).
    return {"article_id": "a_" + str(abs(hash(url)) % 1_000_000), "title": title, "url": url,
            "source": "연합뉴스", "published_at": "2026-07-01T09:00:00+09:00",
            "source_metadata_json": json.dumps(meta, ensure_ascii=False),
            "opportunity_or_risk": "기회", "final_score": 4.2}


def check_dashboard_model_provider_fields() -> None:
    import build_static_dashboard as B
    g = B._row_from_signal(_sig("google_news_rss", GOOGLE_AGG, "AI 데이터센터 EPC"))
    n = B._row_from_signal(_sig("naver_news_api", "https://www.yna.co.kr/v/1", "현대건설 원전"))
    both = B._row_from_signal(_sig("google_news_rss+naver_news_api",
                                   "https://www.mk.co.kr/x", "현대건설 SMR",
                                   src_url=GOOGLE_AGG))
    for label, row, token, plabel in (
            ("google", g, "google_news_rss", "Google News RSS"),
            ("naver", n, "naver_news_api", "Naver News API"),
            ("both", both, "google_news_rss+naver_news_api", "Google+Naver")):
        check(f"_row_from_signal({label}): provider/collectionProvider 존재",
              row.get("provider") == token and row.get("collectionProvider") == plabel,
              f"{row.get('provider')} / {row.get('collectionProvider')}")
        for key in ("source_metadata", "source_metadata_json", "aggregator_url",
                    "original_url", "canonical_url"):
            check(f"_row_from_signal({label}): '{key}' 필드 존재", key in row)
    check("both row: aggregator_url=news.google 보존 + original_url=퍼블리셔 직링크",
          "news.google" in both["aggregator_url"]
          and both["original_url"] == "https://www.mk.co.kr/x",
          f"{both['aggregator_url']} / {both['original_url']}")

    # provider provenance 없는 row(카테고리 근거 등)도 부착된다.
    cat = {"article_id": "c1", "title": "t", "url": GOOGLE_AGG,
           "source_metadata_json": json.dumps({"provider": "google_news_rss",
                                               "source_url": GOOGLE_AGG})}
    B._attach_provider_fields(cat)
    check("_attach_provider_fields: source_metadata_json에서 provider 부착",
          cat.get("provider") == "google_news_rss"
          and cat.get("collectionProvider") == "Google News RSS")

    summ = B._news_provider_summary([g, n, both], {
        "google_news_rss": {"status": "active", "raw_count": 40, "after_dedup_count": 33},
        "naver_news_api": {"status": "active", "raw_count": 22, "after_dedup_count": 18,
                           "credentials_present": True},
        "both_count": 1})
    check("news_provider_summary: visible 카운트 분포 (google=1,naver=1,both=1)",
          summ["google_news_rss"]["visible_count"] == 1
          and summ["naver_news_api"]["visible_count"] == 1
          and summ["both"]["visible_count"] == 1, str(summ))
    check("news_provider_summary: provider_status raw/status 병합",
          summ["google_news_rss"].get("raw_count") == 40
          and summ["naver_news_api"].get("credentials_present") is True)


# ---------- collector.run(live) provider_status nested (temp DB 서브프로세스) ----------

_RUN_PAYLOAD = r'''
import os, sys, json, tempfile
d = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(d, "t.db")
os.environ["APP_MODE"] = "mock"
ROOT = %r
sys.path.insert(0, ROOT)
from app import db, config, collector, live_collector, naver_news_provider as nv
config.NEWS_MODE = "live"
db.init_db()

def _raw(provider, title, url, source="출처 미상"):
    return {"id": provider[:4] + title[:2], "title": title, "source": source,
            "published_at": "2026-06-17T09:00:00+09:00", "url": url, "snippet": "s",
            "source_metadata": {"provider": provider, "query": "q", "source_url": url,
                                "collected_at": "t", "provider_response_id": "r"}}

live_collector.fetch_all = lambda *a, **k: [
    _raw("google_news_rss", "현대건설 원전 수주", "https://news.google.com/rss/articles/R1"),
    _raw("google_news_rss", "삼성물산 데이터센터", "https://news.google.com/rss/articles/R2")]
nv.fetch = lambda *a, **k: {"provider": "naver_news_api", "status": "active",
    "credentials_present": True, "raw_count": 2, "articles": [
    _raw("naver_news_api", "현대건설, 원전 수주", "https://www.yna.co.kr/v/1", "연합뉴스"),
    _raw("naver_news_api", "현대건설 도시정비 단독", "https://www.mk.co.kr/only", "매일경제")]}

r = collector.run()
ps = r.get("provider_status") or {}
g = ps.get("google_news_rss") or {}
n = ps.get("naver_news_api") or {}
print("RESULT_JSON:" + json.dumps({
    "google_nested": isinstance(g, dict),
    "naver_nested": isinstance(n, dict),
    "google_status": g.get("status"),
    "naver_status": n.get("status"),
    "naver_raw": n.get("raw_count"),
    "naver_only": n.get("naver_only_count"),
    "dedup_merged": n.get("dedup_merged_count"),
    "creds": n.get("credentials_present"),
    "both_count": ps.get("both_count"),
    "news_mode": r.get("news_data_mode"),
}, ensure_ascii=False))
'''


def check_collector_run_provider_status() -> None:
    payload = _RUN_PAYLOAD % str(ROOT)
    env = {k: v for k, v in os.environ.items() if k != "DB_PATH"}
    env["APP_MODE"] = "mock"
    try:
        proc = subprocess.run([sys.executable, "-c", payload], capture_output=True,
                              text=True, cwd=ROOT, timeout=120, env=env)
    except subprocess.TimeoutExpired:
        check("collector.run(live) provider_status 서브프로세스", False, "timeout")
        return
    line = next((ln for ln in proc.stdout.splitlines()
                 if ln.startswith("RESULT_JSON:")), None)
    if line is None:
        check("collector.run(live) provider_status 서브프로세스", False,
              (proc.stderr or proc.stdout)[-300:])
        return
    out = json.loads(line[len("RESULT_JSON:"):])
    check("provider_status: google/naver 둘 다 nested dict",
          out["google_nested"] and out["naver_nested"], str(out))
    check("provider_status: 두 provider active (live 수집)",
          out["google_status"] == "active" and out["naver_status"] == "active")
    check("provider_status: naver raw/only/merged/creds 카운트 노출",
          out["naver_raw"] == 2 and out["naver_only"] == 1
          and out["dedup_merged"] == 1 and out["creds"] is True, str(out))
    check("provider_status: both_count 노출 + news_data_mode=live",
          out["both_count"] == 1 and out["news_mode"] == "live")


# ---------- 빌드된 mock 모델에 provider provenance (temp DB 서브프로세스) ----------

def check_built_model_provider_provenance() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_naver_wiring_") as tmp:
        out = Path(tmp) / "dash.html"
        env = dict(os.environ, APP_MODE="mock")
        env.pop("NEWS_MODE", None)  # mock 빌드 (네트워크 0건)
        proc = subprocess.run(
            [sys.executable, str(BUILDER), "--output", str(out)],
            capture_output=True, text=True, cwd=ROOT, timeout=240, env=env)
        if proc.returncode != 0 or not out.exists():
            check("mock 대시보드 빌드", False, (proc.stderr or "")[-300:])
            return
        html = out.read_text(encoding="utf-8")
    m = re.search(r'<script[^>]*id="preview-model"[^>]*>(.*?)</script>', html, re.S)
    if not check("빌드 모델 preview-model 파싱", bool(m)):
        return
    model = json.loads(m.group(1))
    check("모델에 news_provider_summary 존재", "news_provider_summary" in model)

    def _rows(mo):
        fr = mo.get("featured_row")
        if isinstance(fr, dict) and fr:
            yield fr
        for k in ("news_rows", "ai_rows"):
            for r in mo.get(k) or []:
                if isinstance(r, dict):
                    yield r
        for _, rs in (mo.get("lens_banks") or {}).items():
            for r in rs or []:
                if isinstance(r, dict):
                    yield r
        for s in mo.get("nav_category_sections") or []:
            for r in (s or {}).get("articles") or []:
                if isinstance(r, dict):
                    yield r

    rows = list(_rows(model))
    with_provider = [r for r in rows if r.get("provider")]
    check("빌드 모델: provider 필드가 전부 (none)이 아님 (회귀 방지)",
          len(with_provider) >= 1 and len(rows) >= 1,
          f"{len(with_provider)}/{len(rows)} rows carry provider")
    check("빌드 모델: 모든 표시 row에 provider 필드 존재",
          all(r.get("provider") for r in rows), f"{len(rows)}행")
    # HTML 전체에 자격증명 값/토큰 모양 노출 없음.
    check("빌드 HTML에 secret/token 모양 노출 없음",
          not TOKEN_SHAPE.search(html) and not SECRET_KV.search(html))


# ---------- 감사 스크립트: credentials_present + 카운트 노출 ----------

def check_audit_reports_provider_counts() -> None:
    import audit_major_source_coverage as audit
    # 순수 헬퍼: link/visibility 요약 키 계약.
    scored = [
        {"id": "1", "alert_grade": "즉시 확인", "url": "https://www.yna.co.kr/v/1",
         "source_metadata_json": '{"provider":"naver_news_api","source_url":"https://www.yna.co.kr/v/1"}'},
        {"id": "2", "alert_grade": "중요 신호", "url": GOOGLE_AGG,
         "source_metadata_json": '{"provider":"google_news_rss","source_url":"' + GOOGLE_AGG + '"}'},
    ]
    summary = audit._link_visibility_summary(scored, top_ids=set())
    for key in ("visible_google_count", "visible_naver_count", "visible_both_count",
                "google_news_href_count", "publisher_href_count"):
        check(f"audit 링크 요약 키 '{key}' 존재", key in summary)
    check("audit 링크 요약: 퍼블리셔/aggregator href 분류",
          summary["publisher_href_count"] == 1 and summary["google_news_href_count"] == 1,
          str(summary))
    src = AUDIT.read_text(encoding="utf-8")
    check("audit 소스에 credentials_present 노출", "credentials_present" in src)
    check("audit 소스에 raw_google_count/raw_naver_count 노출",
          "raw_google_count" in src and "raw_naver_count" in src)
    check("audit 소스에 token 모양 하드코딩 없음", not TOKEN_SHAPE.search(src))


def main() -> int:
    print(f"== verify_naver_provider_operational_wiring @ {ROOT} ==")
    db_before = _db_state()

    check_config_env_names()
    check_workflow_wiring()
    check_naver_leaf_states()
    check_naver_parser_originallink()
    check_cross_provider_merge()
    check_external_url_policy()
    check_dashboard_model_provider_fields()
    check_collector_run_provider_status()
    check_built_model_provider_provenance()
    check_audit_reports_provider_counts()

    check("repo의 radar.db가 검증 중 변경/생성되지 않음 (temp DB 격리)",
          _db_state() == db_before)

    if _failures:
        print(f"\nRESULT: FAIL ({len(_failures)} failed)")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
