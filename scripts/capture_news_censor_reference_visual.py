#!/usr/bin/env python3
"""Capture deterministic exact-reference dashboard screenshots and pixel diffs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageChops

import verify_news_censor_reference_parity as parity

ROOT = Path(__file__).resolve().parents[1]
CHROME_CANDIDATES = (
    Path("/home/founder_ys/.cache-codex-work/ms-playwright/chromium-1228/chrome-linux64/chrome"),
    Path("/home/founder_ys/.cache-codex-work/ms-playwright/chromium-1223/chrome-linux64/chrome"),
    Path("/home/founder_ys/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome"),
)
VIEWPORTS = {"desktop": (1440, 1200), "mobile": (390, 844)}
DESKTOP_STATES = ("all", "biz", "peers", "hdec", "safety", "global", "ai")
MOBILE_STATES = ("all", "peers")

FIXTURE_SCRIPT = r"""
<script>
document.addEventListener('DOMContentLoaded', () => {
  document.querySelector('.when').textContent = '2026.08.03 (월) · 발행 07:00 · 생성 2026-08-03 07:00 KST';
  const cards = [...document.querySelectorAll('.nitem')];
  cards.slice(6).forEach(card => card.remove());
  document.querySelectorAll('.lead, .nitem').forEach((card, i) => {
    const title = card.querySelector('h2, h3');
    const summary = card.querySelector('.lead-sum');
    const why = card.querySelector('.why');
    const source = card.querySelector('.src');
    const thumb = card.querySelector('.thumb');
    const verdict = card.querySelector('.verdict');
    if (title) title.textContent = `검증 기사 제목 ${i + 1} — 동일 시각 기준 데이터`;
    if (summary) summary.textContent = '검증된 발행사 원문을 바탕으로 작성한 동일 길이의 요약 문장입니다.';
    if (why) why.textContent = '현대건설 경영진 관점에서 동일한 길이로 표시하는 중요성 설명입니다.';
    if (source) source.textContent = '검증 발행사 · 08-03 07:00';
    if (thumb) { thumb.setAttribute('style', '--tint:#2E5E8A'); thumb.classList.add('ph'); thumb.innerHTML = '<b>AI</b>'; }
    if (verdict) { verdict.textContent = '관찰'; verdict.setAttribute('style', 'color:#68716A;border-color:#68716A'); }
  });
  document.querySelectorAll('.memo-mk li').forEach((row, i) => {
    row.innerHTML = `<span class="ml">검증 지표 ${i + 1}</span><span class="mv">100.0<small>단위</small></span><em class="delta up">▲ +1.0%</em>`;
  });
  document.querySelectorAll('.memo-mk li').forEach((row, i) => { if (i >= 5) row.remove(); });
  document.querySelector('.memo-wx h4 small').textContent = '명일 정오 기준';
  document.querySelectorAll('.krmap path').forEach(path => { path.setAttribute('fill', '#9DB8A0'); const title = path.querySelector('title'); if (title) title.textContent = '동일 기상값'; });
  document.querySelector('.wximpact').textContent = '전 권역 공개 예보 기준 특이 위험 신호 없음';
  document.querySelector('.wxnotes').innerHTML = '<li class="warn"><b>전 권역</b> <span>낮음</span></li>';
  document.querySelector('.memo-hz .num').innerHTML = '일일 통항 <b>10척</b> <em class="delta down">▼ 9.1%</em><small>vs 7일 평균</small>';
  document.querySelector('.memo-hz .src').textContent = '검증 공개 데이터 · 2026-08-03 관측';
  const safety = document.querySelector('.memo-hz .art');
  if (safety) safety.innerHTML = '<a href="https://publisher.example/fixture">검증된 안전·지정학 기사 제목</a><small>검증 발행사 · 08-03 07:00</small>';
  const allTokens = 'all biz peers hdec safety global ai magazine lens:plant lens:civil_infrastructure lens:building_housing lens:developers lens:development_business lens:competitor_contractors sub:4e7d40c0 sub:fd3376dd sub:e8a249a3 lens:hyundai_group sub:d914e406 lens:domestic_site lens:safety_quality lens:global_business lens:ai lens:new_energy';
  document.querySelectorAll('.lead, .nitem').forEach(card => { card.dataset.t = allTokens; card.classList.remove('hide'); });
  const target = [...document.querySelectorAll('.cat')].find(cat => cat.dataset.cat === '__CATEGORY__');
  if (target) target.click();
  window.scrollTo(0, 0);
});
</script>
"""


def chrome_path() -> Path:
    configured = shutil.which("chromium") or shutil.which("google-chrome")
    if configured:
        return Path(configured)
    for candidate in CHROME_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise RuntimeError("deterministic Chromium binary not found")


def fixture_html(html: str, category: str) -> str:
    html = re.sub(
        r'<link\s+[^>]*href="https://cdn\.jsdelivr\.net[^>]*>\s*',
        "",
        html,
        flags=re.IGNORECASE,
    )
    if "</body>" not in html:
        raise RuntimeError("dashboard closing body missing")
    script = FIXTURE_SCRIPT.replace("__CATEGORY__", category)
    return html.replace("</body>", f"{script}</body>", 1)


def capture(chrome: Path, source: Path, output: Path, width: int, height: int) -> None:
    command = [
        str(chrome),
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--disable-background-networking",
        "--disable-default-apps",
        "--disable-extensions",
        "--disable-features=Translate,MediaRouter",
        "--hide-scrollbars",
        "--force-device-scale-factor=1",
        f"--window-size={width},{height}",
        "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=1500",
        f"--screenshot={output}",
        source.resolve().as_uri(),
    ]
    result = subprocess.run(command, text=True, capture_output=True, timeout=30, check=False)
    if result.returncode or not output.is_file():
        raise RuntimeError(f"Chromium screenshot failed ({result.returncode}): {result.stderr[-1000:]}")
    if Image.open(output).size != (width, height):
        raise RuntimeError(f"unexpected screenshot dimensions: {Image.open(output).size}")


def compare(reference: Path, candidate: Path, difference: Path) -> dict:
    ref = Image.open(reference).convert("RGBA")
    cand = Image.open(candidate).convert("RGBA")
    diff = ImageChops.difference(ref, cand)
    diff.save(difference)
    extrema = diff.getextrema()
    differing = sum(1 for pixel in diff.getdata() if pixel != (0, 0, 0, 0))
    total = ref.width * ref.height
    return {
        "dimensions": [ref.width, ref.height],
        "different_pixels": differing,
        "different_pixel_ratio": round(differing / total, 8),
        "difference_bbox": diff.getbbox(),
        "channel_extrema": extrema,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, default=parity.REFERENCE)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    reference = args.reference.read_bytes()
    if hashlib.sha256(reference).hexdigest() != parity.REFERENCE_SHA256:
        raise SystemExit("VISUAL_REFERENCE_PARITY=FAIL: reference SHA-256 mismatch")
    candidate = args.candidate.read_text(encoding="utf-8")
    if parity.digest(parity.normalized_shell(candidate)) != parity.REFERENCE_SHELL_SHA256:
        raise SystemExit("VISUAL_REFERENCE_PARITY=FAIL: candidate shell mismatch")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    chrome = chrome_path()
    report = {
        "contract": "D7_AK_6E_R4_R5_NEWS_CENSOR_VISUAL_PARITY_V2",
        "states": {},
    }
    with tempfile.TemporaryDirectory(prefix="hdec-news-censor-visual-") as temporary:
        stage = Path(temporary)
        reference_fixture = stage / "reference.html"
        candidate_fixture = stage / "candidate.html"
        state_sets = {"desktop": DESKTOP_STATES, "mobile": MOBILE_STATES}
        for viewport, categories in state_sets.items():
            width, height = VIEWPORTS[viewport]
            for category in categories:
                reference_fixture.write_text(
                    fixture_html(reference.decode("utf-8"), category),
                    encoding="utf-8",
                )
                candidate_fixture.write_text(
                    fixture_html(candidate, category),
                    encoding="utf-8",
                )
                name = f"{viewport}-{category}"
                ref_png = args.output_dir / f"reference-{name}.png"
                candidate_png = args.output_dir / f"candidate-{name}.png"
                diff_png = args.output_dir / f"difference-{name}.png"
                capture(chrome, reference_fixture, ref_png, width, height)
                capture(chrome, candidate_fixture, candidate_png, width, height)
                metrics = compare(ref_png, candidate_png, diff_png)
                metrics.update({
                    "reference": str(ref_png),
                    "candidate": str(candidate_png),
                    "difference": str(diff_png),
                })
                report["states"][name] = metrics
    report["meaningful_structural_difference"] = any(
        item["different_pixel_ratio"] > 0.001
        for item in report["states"].values()
    )
    report["status"] = "PASS" if not report["meaningful_structural_difference"] else "FAIL"
    report_path = args.output_dir / "visual-parity.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    print(f"VISUAL_REFERENCE_PARITY={report['status']}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
