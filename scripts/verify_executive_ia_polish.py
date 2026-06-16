"""P0-C1.9 검증기 — Executive IA Simplification + AI/Risk Radar Focus 회귀 검사.

임원용 리포트가 'AI-first, 노이즈 최소' IA로 재구성됐는지 결정적으로 검사한다
(네트워크/비밀값 없음, 임시 DB 격리):

정적 리포트
- 상단 목차(AI 레이더 / 리스크·규제 / 수주·해외 / 거시경제 / 전체 근거)가 있다.
- Executive Signal 직후 첫 신호 섹션이 AI 레이더다 (ai-radar < risk-radar < macro).
- 거시경제는 <details ...macro-section>로 기본 접힘(open 없음)이고, 전체 리포트에
  열린 <details open>가 0건이다 (점수 구성요소·전체 근거도 기본 접힘).
- 점수 구성요소는 '중요도' summary 아코디언 뒤에 접혀 있다.
- 노이즈 용어가 없다: '상대 강도', '유사 주제 기사', '참고 묶음 추정', '권장 워치 액션'.
- 헤더(masthead)에 '공개 RSS 수집 · 시장지표 미연동' 노이즈가 없다 (footer로 이동).
- 신호 카드 라벨 줄에 '관찰/기회' 노이즈 라벨이 없다.

brief JSON
- ai_radar_signals / risk_regulation_signals / business_signals / macro_economy_signals 존재.
- AI 레이더에 macro_economy 기사가 섞이지 않는다 (AI 인프라 관련만).
- 중대재해/안전/규제 기사가 리스크·규제 레이더에 surface된다 (종합 중요도가 낮아도
  risk_priority로 상단 노출 — '버려지지 않음').

Telegram digest
- AI 레이더가 거시경제/시장지표보다 먼저 나온다. 노이즈 용어가 없다.

대시보드
- 레이더 탭(AI/리스크·규제/수주·해외/거시경제/전체)이 있고 기본 선택이 AI다.

사용법:
    python3 scripts/verify_executive_ia_polish.py
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_BUILDER = ROOT / "scripts" / "build_static_report.py"
BRIEF_BUILDER = ROOT / "scripts" / "build_executive_brief.py"
DIGEST_BUILDER = ROOT / "scripts" / "build_telegram_digest.py"
TEMPLATE = ROOT / "templates" / "index.html"
RADAR_DB = ROOT / "radar.db"

# 메인 화면에서 추방돼야 하는 노이즈 용어 (리포트 전체 기준).
NOISE_TERMS = ["상대 강도", "유사 주제 기사", "참고 묶음", "권장 워치 액션"]
# 리스크 키워드 (리스크·규제 레이더 surface 검사용).
RISK_KEYWORDS = ["중대재해", "안전", "사망", "산재", "처벌", "규제", "감독",
                 "영업정지", "입찰제한", "시행령", "국토부", "고용"]
NAV_LABELS = ["AI 레이더", "리스크·규제", "수주·해외", "거시경제", "전체 근거"]

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


def _clean_env(**extra: str) -> dict:
    env = {**os.environ, "APP_MODE": "mock", "NEWS_MODE": "mock"}
    for key in ("MESSAGE", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS", "DB_PATH"):
        env.pop(key, None)
    env.update(extra)
    return env


def _db_state():
    if not RADAR_DB.exists():
        return None
    stat = RADAR_DB.stat()
    return (stat.st_mtime_ns, stat.st_size)


def run_script(path: Path, *flags: str, timeout: int = 300):
    return subprocess.run(
        [sys.executable, str(path), *flags],
        capture_output=True, text=True, env=_clean_env(), cwd=ROOT, timeout=timeout)


# ---------- 정적 리포트 IA ----------

def check_report_ia() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_ia_") as tmp:
        out = Path(tmp) / "latest.html"
        proc = run_script(REPORT_BUILDER, "--output", str(out))
        if not check("리포트 생성 동작", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "").strip()[-200:]):
            return
        html = out.read_text(encoding="utf-8")

    body = html[html.index("<body>"):] if "<body>" in html else html

    # 상단 목차
    nav_match = re.search(r'<nav class="topnav".*?</nav>', html, re.S)
    nav = nav_match.group(0) if nav_match else ""
    check("상단 목차(topnav) 존재", bool(nav))
    for label in NAV_LABELS:
        check(f"목차 버튼 '{label}' 존재", label in nav)

    # 섹션 순서: AI → 리스크 → 수주·해외 → 거시경제 → 전체 근거
    def idx(marker):
        return html.index(marker) if marker in html else -1
    ai_i = idx('id="ai-radar"')
    risk_i = idx('id="risk-radar"')
    biz_i = idx('id="biz-radar"')
    macro_i = idx('id="macro"')
    ev_i = idx('id="evidence"')
    check("AI 레이더 섹션(id=ai-radar) 존재", ai_i >= 0)
    check("리스크·규제 레이더 섹션(id=risk-radar) 존재", risk_i >= 0)
    check("수주·해외 섹션(id=biz-radar) 존재", biz_i >= 0)
    check("거시경제 섹션(id=macro) 존재", macro_i >= 0)
    check("전체 근거 섹션(id=evidence) 존재", ev_i >= 0)
    check("AI 레이더가 Executive Signal 직후 첫 신호 섹션 (ai < risk)",
          0 <= ai_i < risk_i)
    check("AI 레이더가 거시경제보다 앞 (ai < macro)", 0 <= ai_i < macro_i)
    check("섹션 순서 ai < risk < biz < macro < evidence",
          0 <= ai_i < risk_i < biz_i < macro_i < ev_i)
    check("본문에서 'AI 레이더'가 '거시경제'보다 먼저",
          "AI 레이더" in body and body.index("AI 레이더") < body.index("거시경제"))

    # 거시경제 = 기본 접힘 <details ...macro-section>, open 없음
    macro_det = re.search(r'<details id="macro"[^>]*>', html)
    check("거시경제가 <details>로 렌더", bool(macro_det))
    check("거시경제 details에 'macro-section' 클래스",
          bool(macro_det) and "macro-section" in macro_det.group(0))
    check("거시경제 details에 open 속성 없음 (기본 접힘)",
          bool(macro_det) and " open" not in macro_det.group(0))
    check("거시경제 summary 라벨 '거시경제'",
          bool(re.search(r'<details id="macro"[^>]*>\s*<summary>.*?거시경제', html, re.S)))

    # 전체 근거 = 기본 접힘 <details>
    ev_det = re.search(r'<details id="evidence"[^>]*>', html)
    check("전체 근거가 <details>로 렌더 (기본 접힘)",
          bool(ev_det) and " open" not in ev_det.group(0))

    # 리포트 전체에 열린 <details open> 0건 (점수/거시/전체 근거 모두 기본 접힘)
    check("리포트 전체에 열린 <details ... open> 0건 (자동 펼침 없음)",
          not re.search(r"<details[^>]*\sopen[\s>]", html))

    # 점수 아코디언: 구성요소가 '중요도' summary 뒤에 접혀 있다
    check("점수 아코디언(score-acc details) 존재", 'class="score-acc"' in html)
    check("중요도 summary 존재 (구성요소 펼치기)",
          bool(re.search(r"<summary>.*?중요도.*?</summary>", html, re.S)))
    check("점수 구성요소(comps)가 마크업에 존재", 'class="comps"' in html)

    # 노이즈 용어 추방
    for term in NOISE_TERMS:
        check(f"리포트에 노이즈 용어 '{term}' 없음", term not in html)
    check("리포트에 '유사 주제 기사는 참고 묶음 추정' 문구 없음",
          "참고 묶음 추정" not in html and "유사 주제 기사는" not in html)

    # 헤더(masthead)에 출처/시장지표 노이즈 없음 (footer로 이동)
    mast = re.search(r'<header class="masthead">.*?</header>', html, re.S)
    masthead = mast.group(0) if mast else ""
    check("헤더(masthead) 추출", bool(masthead))
    check("헤더에 '공개 RSS 수집' 노이즈 없음", "공개 RSS 수집" not in masthead)
    check("헤더에 '시장지표 미연동' 노이즈 없음", "미연동" not in masthead)

    # 신호 카드 라벨 줄에 '관찰/기회' 노이즈 라벨 없음
    label_rows = re.findall(r'<p class="labels">(.*?)</p>', html, re.S)
    bad_labels = [r for r in label_rows if "관찰" in r or "기회" in r]
    check("신호 카드 라벨에 '관찰/기회' 노이즈 라벨 없음", not bad_labels,
          "; ".join(b[:40] for b in bad_labels[:2]))

    # 옛 일반 '주요 신호 Top 3' 헤딩이 섹션별 레이더로 대체됨
    check("옛 '주요 관찰 신호' 헤딩 없음", "주요 관찰 신호" not in html)
    check("섹션별 레이더 헤딩으로 대체 (AI 레이더 주요 신호)",
          "AI 레이더 주요 신호" in html)

    # 거시경제 honesty 유지 (Macro Snapshot 섹션 + 미연동)
    check("거시경제 안에 시장지표 미연동 표기 유지 (정직성)", "미연동" in html)


# ---------- brief JSON 레이더 분류 ----------

def check_brief_radar() -> None:
    proc = run_script(BRIEF_BUILDER, "--json")
    if not check("brief --json 동작", proc.returncode == 0,
                 (proc.stderr or "").strip()[-200:]):
        return
    try:
        brief = json.loads(proc.stdout)
    except ValueError as exc:
        check("brief --json 파싱", False, str(exc))
        return

    ai = brief.get("ai_radar_signals")
    risk = brief.get("risk_regulation_signals")
    biz = brief.get("business_signals")
    macro = brief.get("macro_economy_signals")
    for key, val in [("ai_radar_signals", ai), ("risk_regulation_signals", risk),
                     ("business_signals", biz), ("macro_economy_signals", macro)]:
        check(f"brief에 {key} 존재 (리스트)", isinstance(val, list), str(type(val)))

    check("AI 레이더 신호 1건 이상 (mock)", bool(ai), f"{len(ai or [])}건")
    # AI 레이더에 macro_economy 기사가 섞이지 않는다
    bad_ai = [s.get("title") for s in (ai or [])
              if s.get("radar_section") != "ai"]
    check("AI 레이더는 전부 radar_section=ai (거시경제 미혼입)", not bad_ai,
          "; ".join(str(b)[:30] for b in bad_ai[:2]))
    bad_macro = [s.get("title") for s in (macro or [])
                 if s.get("radar_section") != "macro_economy"]
    check("거시경제 신호는 전부 radar_section=macro_economy", not bad_macro,
          "; ".join(str(b)[:30] for b in bad_macro[:2]))

    # 리스크·규제 레이더: 중대재해/안전/규제 기사가 surface되고, 종합 중요도가 낮아도
    # risk_priority로 상단 노출된다 ('버려지지 않음').
    check("리스크·규제 레이더 신호 1건 이상 (mock)", bool(risk), f"{len(risk or [])}건")
    if risk:
        top = risk[0]
        title = top.get("title") or ""
        check("리스크 신호가 리스크 키워드와 매칭",
              any(k in title for k in RISK_KEYWORDS), title[:40])
        check("리스크 신호에 risk_priority_score 존재",
              isinstance(top.get("risk_priority_score"), (int, float)),
              str(top.get("risk_priority_score")))
        check("리스크 신호에 risk_radar_label 존재", bool(top.get("risk_radar_label")),
              str(top.get("risk_radar_label")))
        check("리스크 신호 regulatory_relevance == True",
              top.get("regulatory_relevance") is True)
        # 핵심: 종합 중요도가 낮아도(가중합 희석) risk_priority로 상단 노출 ('버려지지 않음')
        fs = top.get("final_score") or 0
        rp = top.get("risk_priority_score") or 0
        check("리스크 우선도가 surface를 보장 (risk_priority >= 3.0)", rp >= 3.0,
              f"final_score={fs} risk_priority={rp}")
        check("안전/규제 기사가 저관련 항목으로만 묻히지 않음 (risk_priority >= final_score)",
              rp >= fs, f"final_score={fs} risk_priority={rp}")


# ---------- Telegram digest AI-first ----------

def check_digest_ai_first() -> None:
    proc = run_script(DIGEST_BUILDER, "--dry-run")
    if not check("digest --dry-run 동작", proc.returncode == 0,
                 (proc.stderr or "").strip()[-200:]):
        return
    msg = proc.stdout or ""
    check("digest에 'AI 레이더' 섹션 존재", "AI 레이더" in msg)
    macro_pos = msg.find("[Macro Snapshot")
    si_pos = msg.find("시장지표 미연동")
    ai_pos = msg.find("AI 레이더")
    check("digest: AI 레이더가 Macro Snapshot보다 먼저",
          ai_pos >= 0 and (macro_pos < 0 or ai_pos < macro_pos))
    check("digest: AI 레이더가 시장지표 미연동보다 먼저",
          ai_pos >= 0 and (si_pos < 0 or ai_pos < si_pos))
    for term in ["상대 강도", "유사 주제", "권장 워치 액션", "주요 관찰 신호"]:
        check(f"digest에 노이즈 용어 '{term}' 없음", term not in msg)
    # 거시경제는 리포트로 위임만 (digest 본문이 거시로 시작하지 않는다)
    check("digest 거시경제 안내가 리포트 위임 표현", "리포트에서 확인" in msg)


# ---------- 대시보드 레이더 탭 ----------

def check_dashboard_tabs() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")
    check("대시보드에 레이더 탭 영역(radar-tabs)", 'id="radar-tabs"' in html)
    check("대시보드에 RADAR_TABS 정의", "RADAR_TABS" in html)
    check("대시보드 기본 선택 탭 == AI", 'activeRadar: "ai"' in html)
    check("대시보드 레이더 렌더 함수(renderRadarTabs)", "renderRadarTabs" in html)
    for label in ["리스크·규제", "수주·해외", "거시경제"]:
        check(f"대시보드 레이더 탭 '{label}'", label in html)
    check("대시보드에 옛 '상대 강도' 표현 없음", "상대 강도" not in html)


def main() -> int:
    print(f"== verify_executive_ia_polish @ {ROOT} ==")
    db_before = _db_state()

    check_report_ia()
    check_brief_radar()
    check_digest_ai_first()
    check_dashboard_tabs()

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
