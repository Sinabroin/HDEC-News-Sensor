#!/usr/bin/env python3
"""D7-AD-X — 뉴스 신선도 정책 검증(오프라인 · 네트워크 불필요).

· app.news_recency 단일 정책 소스
· build_static_dashboard._build_now_bank live stale guard
· mock/demo badge 계약(템플릿·빌더)
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
NEWS_RECENCY = ROOT / "app" / "news_recency.py"

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    tag = "PASS" if ok else "FAIL"
    line = f"[{tag}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def check_news_recency_module() -> None:
    src = NEWS_RECENCY.read_text(encoding="utf-8")
    check("1a: IMMEDIATE_MAX_AGE_HOURS 정의", "IMMEDIATE_MAX_AGE_HOURS" in src)
    check("1b: passes_immediate_recency 정의", "def passes_immediate_recency" in src)
    from app import news_recency  # noqa: E402

    now = datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)
    fresh = (now - timedelta(hours=24)).isoformat()
    stale = (now - timedelta(hours=100)).isoformat()
    check("1c: mock은 stale도 통과", news_recency.passes_immediate_recency(stale, "mock", ref_dt=now))
    check("1d: live stale(>72h) 차단",
          not news_recency.passes_immediate_recency(stale, "live", ref_dt=now))
    check("1e: live fresh(24h) 통과",
          news_recency.passes_immediate_recency(fresh, "live", ref_dt=now))


def check_builder_wiring() -> None:
    src = BUILDER.read_text(encoding="utf-8")
    check("2a: build_static_dashboard가 news_recency import",
          "from app import news_recency" in src)
    check("2b: _build_now_bank에 passes_immediate_recency 호출",
          "passes_immediate_recency" in src and "stale_filtered_count" in src)
    check("2c: immediate_status에 immediate_max_age_hours",
          "immediate_max_age_hours" in src)


def check_template_demo_badge() -> None:
    tpl = TEMPLATE.read_text(encoding="utf-8")
    check("3a: previewflag 데모 배지 존재", 'class="previewflag"' in tpl and "데모 데이터" in tpl)
    check("3b: meta.news_data_mode 데모 모델", '"news_data_mode": "mock"' in tpl)
    check("3c: live 판별 JS(news_data_mode === \"live\")",
          'news_data_mode === "live"' in tpl)


def check_mock_build_status() -> None:
    import subprocess  # noqa: PLC0415

    with tempfile.TemporaryDirectory(prefix="hdec_recency_") as tmp:
        out = Path(tmp) / "dash.html"
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "build_static_dashboard.py"),
             "--output", str(out)],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120,
        )
        if not check("4a: mock dashboard 빌드", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "")[-180:]):
            return
        html = out.read_text(encoding="utf-8")
        check("4b: mock HTML news-data-mode:mock",
              "<!--news-data-mode:mock-->" in html or "news-data-mode:mock" in html)
        m = re.search(r'<script type="application/json" id="preview-model">\s*(.*?)\s*</script>',
                      html, re.S)
        if m:
            model = json.loads(m.group(1))
            meta = model.get("meta") or {}
            check("4c: meta.demo=true(mock)", meta.get("demo") is True)
            st = model.get("immediate_status") or {}
            check("4d: immediate_status.stale_filtered_count 키",
                  "stale_filtered_count" in st)
        check("4e: public mock도 raw '데모 데이터' 제거 + 내부 샘플 정직 표기",
              "데모 데이터" not in html and "내부 고정 샘플" in html)


def main() -> int:
    print(f"== verify_news_recency_policy (D7-AD-X) @ {ROOT} ==")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    check_news_recency_module()
    check_builder_wiring()
    check_template_demo_badge()
    check_mock_build_status()
    if _failures:
        print("\nFAILURES:")
        for f in _failures:
            print(" -", f)
        return 1
    print("\nOK: news recency policy verifier passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
