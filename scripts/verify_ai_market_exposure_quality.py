#!/usr/bin/env python3
"""D7-U verifier — AI hyperscaler exposure + market indicator completeness.

Runs fully offline (no network, DB writes, secrets, or send) for the PASS/FAIL
gate. It guards two executive-surface quality regressions reported from the
dashboard screenshot:

  A. Market monitor let unlinked / chartless indicators (—, 미연동, 보고-only)
     dominate the visible surface. The fix splits indicators into linked
     (current value + trend/source) vs. unlinked watchlist rows, shows linked
     first, moves unlinked into a de-emphasized "미연동 관찰 후보" area, and
     exposes linked/unlinked/chartless counts — without fabricating any value.

  B. The AI signal surface showed almost no US big-tech / hyperscaler value-chain
     content. The fix (a) keeps the AI value-chain policy externalized, (b) adds a
     broad display-only ai_value_chain_pool to the brief, and (c) reserves AI-bank
     slots for hyperscaler / AI-chip stories so domestic construction signals do
     not crowd them out. When no hyperscaler item is collected, the dashboard
     exposes diagnostic counts (ai_hyper_count / ai_value_chain_pool_count) rather
     than silently looking empty.

Check groups (offline):
  1. AI value-chain policy still externalized (engine/policy split intact).
  2. Synthetic AI exposure — the engine classifies the canonical hyperscaler /
     value-chain titles into the right layer/tier/lens and excludes generic AI.
  3. AI dashboard model — committed live dashboard AI bank has valid lenses, and
     any hyperscaler value-chain title present in the model also appears on the AI
     surface (bank or ai_rows); rows carry HDEC relevance tier.
  4. Market model — linked rows are prioritized above unlinked, unlinked are
     clearly separated as watchlist candidates, counts are exposed, and no fake
     numeric values are generated for unavailable indicators.
  5. Public safety — no token-like strings, no Telegram/GitHub API calls in public
     HTML, no private site-watchlist content.

Optional live diagnostic (informational only, never fails):
  python3 scripts/verify_ai_market_exposure_quality.py --live-diagnostic
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app import ai_value_chain  # noqa: E402  (offline import — no network at load)

DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
ENGINE = ROOT / "app" / "ai_value_chain.py"
POLICY = ROOT / "data" / "ai_value_chain_policy.json"

# Valid dashboard nav/filter lens keys (verifier-local copy so drift is a failure).
VALID_LENS = {
    "now", "new", "ai", "civil_infrastructure", "building_housing", "plant",
    "new_energy", "development_business", "global_business", "safety_quality",
    "hyundai_group", "competitor_contractors", "trust_companies", "developers",
    "oil_energy", "hormuz", "domestic_site", "overseas_site", "overseas_branch",
    "overseas_subsidiary",
}

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


# ── Group 1: AI value-chain policy still externalized ─────────────────────────
def _string_collection_literals(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            strs = [e.value for e in node.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if strs:
                yield getattr(node, "lineno", "?"), strs


def verify_policy_externalized() -> None:
    print("\n== 1. AI value-chain policy externalized ==")
    ok_exists = check("1a: data/ai_value_chain_policy.json 존재", POLICY.exists())
    if ok_exists:
        try:
            policy = json.loads(POLICY.read_text(encoding="utf-8"))
        except ValueError as exc:
            policy = {}
            check("1b: policy JSON 파싱", False, str(exc))
        else:
            check("1b: policy term_groups 존재", isinstance(policy.get("term_groups"), dict))
            check("1c: policy chip_vendor_terms 존재(정밀 벤더 그룹)",
                  bool(policy.get("term_groups", {}).get("chip_vendor_terms")))
    if not check("1d: 엔진 소스 존재", ENGINE.exists()):
        return
    tree = ast.parse(ENGINE.read_text(encoding="utf-8"))
    flagged = []
    for lineno, strs in _string_collection_literals(tree):
        has_hangul = any(any("가" <= ch <= "힣" for ch in s) for s in strs)
        if (len(strs) >= 3 and has_hangul) or (len(strs) >= 6):
            flagged.append((lineno, len(strs), strs[:4]))
    check("1e: 엔진에 대형 하드코딩 도메인 term 튜플 없음(정책 분리 유지)",
          not flagged, f"flagged={flagged[:4]}" if flagged else "엔진은 정책 기반(키워드 없음)")


# ── Group 2: Synthetic AI exposure ────────────────────────────────────────────
def _lenses(title: str) -> set[str]:
    return ai_value_chain.recommended_lenses(title, "", "")


def verify_synthetic_exposure() -> None:
    print("\n== 2. Synthetic AI value-chain exposure ==")
    # (title, expected_tier, lens predicate, expect_executive_candidate, expect_hyper_vc)
    cases = [
        ("OpenAI Broadcom AI chip", 3,
         lambda L: "ai" in L, True, True),
        ("OpenAI AI data center power", 2,
         lambda L: "ai" in L and ("new_energy" in L or "global_business" in L), True, True),
        ("Anthropic Claude data center lease", 2,
         lambda L: "ai" in L and ("global_business" in L or "development_business" in L), True, True),
        ("Microsoft AI data center power", 2,
         lambda L: "ai" in L and "new_energy" in L, True, True),
        ("AWS AI data center power", 2,
         lambda L: "ai" in L and "new_energy" in L, True, True),
        ("Meta AI data center power contract", 2,
         lambda L: "ai" in L and "new_energy" in L, True, True),
        ("Oracle AI data center capex funding", 2,
         lambda L: "ai" in L and ("global_business" in L or "development_business" in L), True, True),
        ("generic OpenAI chatbot app update", 5,
         lambda L: "ai" not in L, False, False),
    ]
    for title, exp_tier, lens_pred, exp_cand, exp_hyper in cases:
        vc = ai_value_chain.classify_ai_value_chain(title, "", "")
        lenses = _lenses(title)
        tier_ok = int(vc["hdec_relevance_tier"]) == exp_tier
        lens_ok = lens_pred(lenses)
        cand_ok = ai_value_chain.is_executive_ai_candidate(vc) == exp_cand
        hyper_ok = ai_value_chain.is_hyperscaler_value_chain(title, "", "") == exp_hyper
        ok = tier_ok and lens_ok and cand_ok and hyper_ok
        detail = (f"tier={vc['hdec_relevance_tier']}(exp {exp_tier}) layer={vc['ai_value_chain_layer']} "
                  f"lens={sorted(lenses)} cand={ai_value_chain.is_executive_ai_candidate(vc)} "
                  f"hyper={ai_value_chain.is_hyperscaler_value_chain(title, '', '')}")
        check(f"2: {title[:46]}", ok, detail)
    # Generic hyperscaler model news must be excluded from the dashboard top.
    gen = ai_value_chain.classify_ai_value_chain("generic OpenAI chatbot app update", "", "")
    check("2x: generic AI 앱 업데이트는 임원 AI 후보에서 제외(tier5)",
          gen["ai_value_chain_layer"] == ai_value_chain.LAYER_GENERIC_AI
          and not ai_value_chain.is_executive_ai_candidate(gen))


# ── shared model loading ──────────────────────────────────────────────────────
def _load_model(path: Path):
    if not check(f"model: {path.name} 존재", path.exists()):
        return None
    html = path.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'<script type="application/json" id="preview-model">\s*(.*?)\s*</script>',
                  html, re.S)
    if not check("model: preview-model JSON 추출", bool(m)):
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        check("model: preview-model JSON 파싱", False, str(exc))
        return None


def _row_lens(row: dict) -> list:
    lenses = row.get("lens") or row.get("lenses") or []
    if isinstance(lenses, str):
        return [lenses] if lenses.strip() else []
    return list(lenses or [])


def _all_model_rows(model: dict):
    for bucket in ("news_rows", "ai_rows"):
        for i, row in enumerate(model.get(bucket) or []):
            yield bucket, i, row
    for lens, rows in (model.get("lens_banks") or {}).items():
        for i, row in enumerate(rows or []):
            yield f"bank:{lens}", i, row


def _is_hyper(row: dict) -> bool:
    return ai_value_chain.is_hyperscaler_value_chain(
        row.get("title") or "", row.get("source") or "", row.get("snippet") or "")


# ── Group 3: AI dashboard model ───────────────────────────────────────────────
def verify_ai_dashboard_model(model: dict) -> None:
    print("\n== 3. AI dashboard model (committed live) ==")
    banks = model.get("lens_banks") or {}
    ai_bank = banks.get("ai") or []
    ai_rows = model.get("ai_rows") or []
    ai_surface = list(ai_bank) + list(ai_rows)
    check("3a: AI 표면(ai bank ∪ ai_rows) 비어있지 않음", bool(ai_surface),
          f"bank={len(ai_bank)} ai_rows={len(ai_rows)}")

    empty = [f"{r.get('title')!r}" for r in ai_surface if not _row_lens(r)]
    check("3b: AI 표면 모든 행에 lens 보유", not empty, "; ".join(empty[:4]))

    emitted = set()
    for _, _, row in _all_model_rows(model):
        emitted.update(_row_lens(row))
    invalid = sorted(emitted - VALID_LENS)
    check("3c: 모든 emitted lens 키가 유효", not invalid, f"미정의: {invalid}")

    # Tier metadata present on AI bank rows.
    missing_tier = []
    for r in ai_bank:
        tier = r.get("hdecRelevanceTier")
        if tier is None:
            tier = (r.get("provenance") or {}).get("hdec_relevance_tier")
        if tier is None:
            missing_tier.append(r.get("title"))
    check("3d: AI 뱅크 행에 HDEC 관련성 tier 메타 부착",
          not missing_tier, f"누락 {len(missing_tier)}건" if missing_tier else "")

    # Core invariant: any hyperscaler value-chain title in the model also appears on
    # the AI surface (reserved-slot guarantee). Vacuously true when none collected.
    model_hyper = {(r.get("title") or "")
                   for _, _, r in _all_model_rows(model) if _is_hyper(r) and r.get("title")}
    ai_titles = {(r.get("title") or "") for r in ai_surface}
    if model_hyper:
        surfaced = model_hyper & ai_titles
        check("3e: 모델 내 하이퍼스케일러 신호가 AI 표면에도 노출(예약 슬롯 보장)",
              bool(surfaced),
              f"model={len(model_hyper)} surfaced={len(surfaced)} "
              f"missing={sorted(model_hyper - ai_titles)[:3]}")
    else:
        check("3e: 모델 내 하이퍼스케일러 신호 0건 — 진단 카운트로 정직 노출(빈 표면 위장 아님)",
              "ai_hyper_count" in (model.get("meta") or {}),
              f"ai_hyper_count={model.get('meta', {}).get('ai_hyper_count')} "
              f"pool={model.get('meta', {}).get('ai_value_chain_pool_count')}")

    meta = model.get("meta") or {}
    check("3f: AI 노출 진단 카운트 메타 노출(ai_hyper_count/ai_value_chain_pool_count)",
          "ai_hyper_count" in meta and "ai_value_chain_pool_count" in meta,
          f"hyper={meta.get('ai_hyper_count')} pool={meta.get('ai_value_chain_pool_count')}")


# ── Group 4: Market model ─────────────────────────────────────────────────────
_MARKET_AUTO_LIVE_MODES = {"live_market", "delayed_market", "live_macro", "delayed_macro"}


def _market_linked(it: dict) -> bool:
    """D7-AE-RC1: "값이 있다"만으로는 부족하다 — data_mode(설계상 분류)가 허용 목록이고,
    history_data_mode(있다면, 오늘 실제로 어디서 왔는지)도 허용 목록이어야 linked다.
    scripts/build_static_dashboard.py의 _market_is_auto_live · 템플릿 JS의 isAutoLive와
    동일 규칙(단일 계약, 세 곳 모두 갱신)."""
    v = it.get("value")
    has_value = v is not None and str(v).strip() not in ("", "—")
    if not has_value:
        return False
    if it.get("data_mode") not in _MARKET_AUTO_LIVE_MODES:
        return False
    hist_mode = it.get("history_data_mode")
    if hist_mode and hist_mode not in _MARKET_AUTO_LIVE_MODES:
        return False
    return True


def _market_chartable(it: dict) -> bool:
    hist = it.get("history") if isinstance(it.get("history"), dict) else {}
    arr = (hist or {}).get("1m") or []
    return len([x for x in arr if isinstance(x, (int, float))]) >= 2


def verify_market_model(model: dict, html: str) -> None:
    print("\n== 4. Market model ==")
    items = model.get("market_items") or []
    check("4a: 시장 유니버스(market_items) 존재", bool(items), f"{len(items)}종")
    if not items:
        return

    cats = {it.get("category") for it in items}
    check("4b: 시장 카테고리 섹션 존재", len(cats) >= 3, f"{len(cats)} categories")

    linked = sum(1 for it in items if _market_linked(it))
    unlinked = sum(1 for it in items if not _market_linked(it))
    chartless = sum(1 for it in items if _market_linked(it) and not _market_chartable(it))
    meta = model.get("meta") or {}
    # Counts exposed in metadata (single source for verifier + render).
    counts_ok = (meta.get("market_linked_count") == linked
                 and meta.get("market_unlinked_count") == unlinked
                 and meta.get("market_chartless_count") == chartless
                 and "market_deprioritized_count" in meta)
    check("4c: 연동/미연동/차트없음 카운트가 meta에 정확히 노출",
          counts_ok,
          f"meta(linked={meta.get('market_linked_count')},unlinked={meta.get('market_unlinked_count')},"
          f"chartless={meta.get('market_chartless_count')}) vs computed("
          f"{linked},{unlinked},{chartless})")

    # No section should be dominated by unlinked rows in the prominent area: the
    # render separates linked-first and labels unlinked as 관찰 후보 candidates. We
    # verify the separation logic + honesty copy are present (rendering is client JS).
    # Render anchors live in the template (committed docs/daily may lag behind builder).
    tpl = TEMPLATE.read_text(encoding="utf-8", errors="ignore")
    check("4d: 렌더가 연동 우선·미연동 분리 로직 보유(marketLinked + mcat-unlinked + mcat-backlog)",
          "function marketLinked" in tpl and "mcat-unlinked" in tpl
          and "mcat-backlog" in tpl)
    check("4e: 미연동 분리 정직성 안내문 노출",
          "미연동 지표는 하단 관찰 후보로 분리했습니다" in html)
    # D7-AC — 우측 요약 레일은 제거되었다(보조 컨텍스트 섹션 제거). 미연동 누출 방지는 이제
    # rail UI(if (it && marketLinked(it))) 존재가 아니라 model(market_items/meta) 기준으로
    # 검증한다: 연동 지표가 충분하고 chartless 상한(D7-Z3=14, 아래 4i)을 지킨다. 레일 재유입
    # 회귀 가드는 verify_dashboard_lens_filters 1e가 담당한다.
    check("4f: 시장 요약 품질 — 연동 지표 다수 + chartless 상한(model 기준·레일 UI 비의존)",
          linked >= 7 and chartless <= 14,
          f"linked={linked} chartless={chartless} (D7-Z3 ceiling=14)")

    # No fabricated values: unavailable indicators must have null value + empty spark.
    faked = []
    for it in items:
        if it.get("data_mode") == "unavailable":
            if it.get("value") not in (None, "", "—"):
                faked.append(f"{it.get('id')}={it.get('value')!r}")
            if it.get("spark"):
                faked.append(f"{it.get('id')}.spark")
    check("4g: 미연동(unavailable) 지표에 가짜 값/스파크 없음(값 null)",
          not faked, "; ".join(faked[:5]))

    # D7-AE-RC1: "linked > unlinked"는 linked가 "값이 있으면 전부"(proxy/manual 포함)이던
    # 시절 기준이다. 이제 linked는 진짜 자동 live/delayed만이라(사용자 지시 — proxy/manual/
    # unavailable/데모대체는 메인 뷰 자격 없음), 정직하게 세면 소수여도 정상이다(실측:
    # 49종 중 19종). "죽은 행에 지배되지 않음"의 실제 의도는 "연동 지표가 무의미하게
    # 적지 않음"이므로, 과반 대신 절대 하한(운영자가 실사용 가능한 수준)으로 검사한다.
    check("4h: 연동 지표가 유의미한 절대 하한 이상(섹션이 죽은 행에 지배되지 않음)",
          linked >= 15, f"linked={linked} unlinked={unlinked}")

    check("4i: D7-Z2 상세 차트 미연동 회귀 방지(chartless <= 14)",
          chartless <= 14, f"chartless={chartless} ceiling=14")

    required_charted = ("lumber", "rebar", "audkrw", "cadkrw", "dxy", "eurusd", "usdjpy")
    by_id = {it.get("id"): it for it in items}
    missing_charted = [item_id for item_id in required_charted
                       if item_id not in by_id or not _market_chartable(by_id[item_id])]
    check("4j: D7-Z2 신규 상세 차트 핵심 항목 유지",
          not missing_charted, "missing/not-chartable=" + ",".join(missing_charted))


# ── Group 5: Public safety ────────────────────────────────────────────────────
TOKEN_SHAPE = re.compile(r"[0-9]{8,}:[A-Za-z0-9_-]{20,}")          # bot-token shape
PAT_SHAPE = re.compile(r"\b(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
API_HOSTS = ("api.telegram.org", "api.github.com")


def verify_public_safety(model: dict, html: str) -> None:
    print("\n== 5. Public safety ==")
    low = html.lower()
    check("5a: 봇 토큰 형태 문자열 없음", not TOKEN_SHAPE.search(html))
    check("5b: GitHub PAT 형태 문자열 없음", not PAT_SHAPE.search(html))
    hosts = [h for h in API_HOSTS if h in low]
    check("5c: Telegram/GitHub API 호스트 호출 없음", not hosts, f"발견: {hosts}")
    check("5d: sendMessage 등 직접 발송 호출 없음", "sendmessage" not in low)
    # Site-watchlist tree in the public model must contain only the tracked PUBLIC
    # list (D7-AD-Z/D7-AE contract renewal). Absent tree is still fine; a present
    # tree must be is_private=False and every node label must exist in
    # data/site_watchlist.public.json (no private-name leakage).
    tree = model.get("site_watch_tree")
    if tree in (None, [], {}):
        check("5e: 현장 워치리스트 트리 비노출(공개 빌드 · 허용)", True)
    else:
        pub_names: set = set()
        try:
            pub_raw = json.loads((ROOT / "data" / "site_watchlist.public.json")
                                 .read_text(encoding="utf-8"))
            for it in (pub_raw.get("items") if isinstance(pub_raw, dict) else pub_raw) or []:
                if isinstance(it, dict) and it.get("name"):
                    pub_names.add(str(it["name"]))
        except (OSError, ValueError):
            pass
        labels = {str(n.get("label") or "")
                  for sc in (tree.get("by_scope") or {}).values()
                  for g in (sc.get("groups") or [])
                  for n in (g.get("nodes") or [])}
        extra = {l for l in labels if l and l not in pub_names}
        check("5e: 공개 모델 현장 트리는 추적 공개 목록 이름만(비공개 누출 0 · D7-AE 갱신)",
              not tree.get("is_private") and not extra,
              f"extra={sorted(extra)[:3]}" if extra else "ok")


# ── live diagnostic (informational, never fails) ──────────────────────────────
_DIAG_QUERIES = (
    "OpenAI AI chip", "오픈AI 자체 AI 칩", "OpenAI AI data center power",
    "Anthropic Claude AI data center", "Microsoft AI data center power",
    "AWS AI data center power", "Google AI data center power",
    "Meta AI data center power", "Oracle AI data center",
    "AI 데이터센터 전력 인프라",
)


def run_live_diagnostic() -> int:
    """Informational only: probe targeted Google News RSS for hyperscaler/value-chain
    items and show how each would classify. Never fails if news is absent."""
    import urllib.request
    import xml.etree.ElementTree as ET
    from app import live_collector, surface_contracts

    print("== LIVE DIAGNOSTIC (informational · 발송/DB/비밀값 없음) ==")
    cfg = {}
    try:
        cfg = live_collector._load_sources()
    except Exception:
        cfg = {}
    total = 0
    for q in _DIAG_QUERIES:
        print(f"\n-- query: {q}")
        try:
            url = live_collector._build_google_news_url(q, cfg)
            xml_text = live_collector._fetch(url, 10)
            root = ET.fromstring(xml_text)
        except Exception as exc:  # network/parse — informational, keep going
            print(f"   (unavailable: {type(exc).__name__})")
            continue
        items = root.findall(".//item")
        if not items:
            print("   (no results)")
            continue
        for it in items[:5]:
            title = (it.findtext("title") or "").strip()
            src_el = it.find("{*}source")
            if src_el is None:
                src_el = it.find("source")
            source = (src_el.text if src_el is not None else "") or ""
            vc = ai_value_chain.classify_ai_value_chain(title, source, "")
            try:
                eligible = surface_contracts.decide_ai_tab(
                    {"title": title, "source": source, "snippet": "", "url": "http://x/y"}).eligible
            except Exception:
                eligible = False
            would_enter = bool(eligible and ai_value_chain.is_executive_ai_candidate(vc))
            total += 1
            print(f"   · {title[:64]!r}")
            print(f"     source={source!r} layer={vc['ai_value_chain_layer']} "
                  f"tier={vc['hdec_relevance_tier']} "
                  f"hyper_vc={ai_value_chain.is_hyperscaler_value_chain(title, source, '')} "
                  f"would_enter_ai_bank={would_enter}")
    print(f"\n(diagnostic only — {total} items inspected; 뉴스 부재 시 실패하지 않음)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D7-U AI/market exposure quality verifier")
    parser.add_argument("--live-diagnostic", action="store_true",
                        help="targeted live RSS probes (informational only, never fails)")
    args = parser.parse_args(argv)
    if args.live_diagnostic:
        return run_live_diagnostic()

    print(f"== verify_ai_market_exposure_quality @ {ROOT} ==")
    verify_policy_externalized()
    verify_synthetic_exposure()
    model = _load_model(DASHBOARD)
    if model is not None:
        html = DASHBOARD.read_text(encoding="utf-8", errors="ignore")
        verify_ai_dashboard_model(model)
        verify_market_model(model, html)
        verify_public_safety(model, html)

    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — AI 하이퍼스케일러 노출 + 시장 지표 완전성 확인 (D7-U)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
