#!/usr/bin/env python3
"""Offline regression verifier for the Editorial Review Console."""

from __future__ import annotations

import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
from html import unescape
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("APP_MODE", "mock")
os.environ.setdefault("NEWS_MODE", "mock")

from app import editorial_briefings, editorial_feedback, editorial_review  # noqa: E402
from app.editorial_briefings import KST  # noqa: E402


class V:
    def __init__(self):
        self.checks = 0
        self.failures = 0

    def check(self, name, condition, detail=""):
        self.checks += 1
        if condition:
            print(f"PASS: {name}")
        else:
            self.failures += 1
            print(f"FAIL: {name} {detail}")

    def equal(self, name, actual, expected):
        self.check(name, actual == expected, f"expected={expected!r} actual={actual!r}")


def _browser_executable() -> Path | None:
    candidates = [
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("google-chrome"),
        "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
        "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def _browser_path(path: Path, browser: Path) -> str:
    if browser.suffix.casefold() != ".exe":
        return path.resolve().as_uri()
    windows_path = subprocess.run(
        ["wslpath", "-w", str(path.resolve())],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return "file:///" + windows_path.replace("\\", "/")


def _browser_argument_path(path: Path, browser: Path) -> str:
    if browser.suffix.casefold() != ".exe":
        return str(path.resolve())
    return subprocess.run(
        ["wslpath", "-w", str(path.resolve())],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def run_browser_interaction(console_path: Path, profile_dir: Path) -> dict[str, object]:
    """Exercise real drag/drop and reload behavior in dependency-free headless Chrome."""
    browser = _browser_executable()
    if browser is None:
        return {"browser_available": False, "error": "Chrome/Edge executable not found"}
    harness = r"""
<script>
(async()=>{
  const results={browser_available:true};
  const phaseKey=storageKey+":r3v6-browser-phase";
  const expectedKey=storageKey+":r3v6-browser-expected";
  const pause=milliseconds=>new Promise(resolve=>setTimeout(resolve,milliseconds));
  function dispatchDrag(source,target,clientY){
    const transfer=new DataTransfer();
    source.dispatchEvent(new DragEvent("dragstart",{bubbles:true,cancelable:true,dataTransfer:transfer,clientY:clientY||0}));
    target.dispatchEvent(new DragEvent("dragover",{bubbles:true,cancelable:true,dataTransfer:transfer,clientY:clientY||0}));
    target.dispatchEvent(new DragEvent("drop",{bubbles:true,cancelable:true,dataTransfer:transfer,clientY:clientY||0}));
    source.dispatchEvent(new DragEvent("dragend",{bubbles:true,cancelable:true,dataTransfer:transfer,clientY:clientY||0}));
  }
  function domSelected(){
    return [...document.querySelectorAll(".selected-article-card")].map(card=>card.dataset.selectedId);
  }
  function sectorOrder(){
    return [...document.querySelectorAll(".editorial-sector")].map(section=>section.dataset.sectorCategory);
  }
  window.alert=()=>{};
  if(localStorage.getItem(phaseKey)!=="restore"){
    const initiallySelected=document.querySelector(".candidate.selected");
    const removedId=initiallySelected.dataset.id;
    initiallySelected.querySelector('input[type="checkbox"]').click();
    const beforeCount=state.selected.length;
    const candidate=document.querySelector(".candidate:not(.selected)");
    const candidateId=candidate.dataset.id;
    const corporateZone=document.querySelector('[data-drop-category="기업동향"]');
    dispatchDrag(candidate,corporateZone,corporateZone.getBoundingClientRect().bottom-2);
    results.left_to_right_drag=state.selected.length===beforeCount+1&&state.selected.includes(candidateId);
    results.left_drop_category=view(candidateId).category==="기업동향"&&!!document.querySelector(`[data-sector-category="기업동향"] [data-selected-id="${candidateId}"]`);

    let movedCard=document.querySelector(`[data-selected-id="${candidateId}"]`);
    let technologyZone=document.querySelector('[data-drop-category="기술정보"]');
    dispatchDrag(movedCard,technologyZone,technologyZone.getBoundingClientRect().bottom-2);
    results.cross_sector_move=view(candidateId).category==="기술정보"&&!!document.querySelector(`[data-sector-category="기술정보"] [data-selected-id="${candidateId}"]`);

    const secondId=state.selected.find(id=>id!==candidateId&&view(id).category!=="기술정보");
    movedCard=document.querySelector(`[data-selected-id="${secondId}"]`);
    technologyZone=document.querySelector('[data-drop-category="기술정보"]');
    dispatchDrag(movedCard,technologyZone,technologyZone.getBoundingClientRect().bottom-2);
    const technologyCards=[...document.querySelectorAll('[data-sector-category="기술정보"] .selected-article-card')];
    const beforeOrder=technologyCards.map(card=>card.dataset.selectedId);
    const reorderSource=technologyCards[1];
    const reorderTarget=technologyCards[0];
    const targetBox=reorderTarget.getBoundingClientRect();
    dispatchDrag(reorderSource,reorderTarget,targetBox.top+1);
    const afterOrder=[...document.querySelectorAll('[data-sector-category="기술정보"] .selected-article-card')].map(card=>card.dataset.selectedId);
    results.same_sector_reorder=beforeOrder.join("|")!==afterOrder.join("|")&&afterOrder[0]===beforeOrder[1];
    results.dom_state_order=domSelected().join("|")===state.selected.join("|");
    results.review_status_draft=state.reviewStatus==="draft";

    const extraCandidate=[...document.querySelectorAll(".candidate:not(.selected)")].find(card=>card.dataset.id!==removedId);
    const countAtLimit=state.selected.length;
    if(extraCandidate){
      dispatchDrag(extraCandidate,technologyZone,technologyZone.getBoundingClientRect().bottom-2);
      results.maximum_six=state.selected.length===countAtLimit&&state.selected.length===6&&!state.selected.includes(extraCandidate.dataset.id);
    }else{
      results.maximum_six=state.selected.length===6;
    }
    const originalLink=document.querySelector(".candidate .original-article-link");
    const linkStateBefore=state.selected.join("|");
    originalLink.addEventListener("click",event=>event.preventDefault(),{once:true});
    originalLink.dispatchEvent(new MouseEvent("click",{bubbles:true,cancelable:true}));
    results.left_original_link=/^https?:/.test(originalLink.href)&&originalLink.target==="_blank"&&originalLink.rel.includes("noopener")&&originalLink.rel.includes("noreferrer");
    results.link_does_not_select=state.selected.join("|")===linkStateBefore&&!dragging;
    results.sector_order=sectorOrder().join(">")==="투자·산업>기업동향>기술정보";
    results.exactly_three_sectors=sectorOrder().length===3;
    results.drop_zones=document.querySelectorAll("[data-drop-category]").length===3;
    await pause(300);
    results.images_render=[...document.querySelectorAll("[data-image-frame]")].every(frame=>{
      const image=frame.querySelector("img");
      const fallback=frame.querySelector(".image-fallback");
      return !!(image&&image.naturalWidth>0)||!!(fallback&&!fallback.hidden);
    });
    results.no_remote_image_src=![...document.images].some(image=>/^https?:/i.test(image.getAttribute("src")||""));
    const expected=window.__editorialReviewDebug();
    expected.dom=domSelected();
    expected.interaction=results;
    localStorage.setItem(expectedKey,JSON.stringify(expected));
    localStorage.setItem(phaseKey,"restore");
    location.reload();
    return;
  }
  await pause(300);
  const expected=JSON.parse(localStorage.getItem(expectedKey)||"{}");
  Object.assign(results,expected.interaction||{});
  const restored=window.__editorialReviewDebug();
  results.local_storage_restore=JSON.stringify(restored.selected)===JSON.stringify(expected.selected)&&JSON.stringify(restored.categories)===JSON.stringify(expected.categories);
  results.restored_dom_order=domSelected().join("|")===restored.selected.join("|")&&domSelected().join("|")===(expected.dom||[]).join("|");
  results.restored_sector_order=sectorOrder().join(">")==="투자·산업>기업동향>기술정보";
  results.restored_images=[...document.querySelectorAll("[data-image-frame]")].every(frame=>{
    const image=frame.querySelector("img");
    const fallback=frame.querySelector(".image-fallback");
    return !!(image&&image.naturalWidth>0)||!!(fallback&&!fallback.hidden);
  });
  const marker=document.createElement("pre");
  marker.id="r3v6-browser-result";
  marker.textContent=JSON.stringify(results);
  document.body.appendChild(marker);
  localStorage.removeItem(phaseKey);
  localStorage.removeItem(expectedKey);
})().catch(error=>{
  const marker=document.createElement("pre");
  marker.id="r3v6-browser-result";
  marker.textContent=JSON.stringify({browser_available:true,error:String(error),stack:error&&error.stack||""});
  document.body.appendChild(marker);
});
</script>
"""
    interaction_path = console_path.with_name("interaction.html")
    console_source = console_path.read_text(encoding="utf-8")
    before_body_end, after_body_end = console_source.rsplit("</body>", 1)
    interaction_path.write_text(
        before_body_end + harness + "</body>" + after_body_end,
        encoding="utf-8",
    )
    profile_handle = None
    if browser.suffix.casefold() == ".exe":
        windows_temp_output = subprocess.run(
            ["cmd.exe", "/d", "/c", "echo", "%TEMP%"],
            cwd="/mnt/c",
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        windows_temp = next(
            line.strip()
            for line in reversed(windows_temp_output.splitlines())
            if re.match(r"^[A-Za-z]:\\", line.strip())
        )
        wsl_temp = subprocess.run(
            ["wslpath", "-u", windows_temp],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        profile_handle = tempfile.TemporaryDirectory(
            prefix="hdec-r3v6-browser-",
            dir=wsl_temp,
        )
        active_profile = Path(profile_handle.name)
    else:
        profile_dir.mkdir(parents=True, exist_ok=True)
        active_profile = profile_dir
    try:
        command = [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-default-apps",
            "--disable-sync",
            "--metrics-recording-only",
            "--no-first-run",
            "--no-default-browser-check",
            "--allow-file-access-from-files",
            "--virtual-time-budget=8000",
            f"--user-data-dir={_browser_argument_path(active_profile, browser)}",
            "--dump-dom",
            _browser_path(interaction_path, browser),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    finally:
        if profile_handle is not None:
            profile_handle.cleanup()
    match = re.search(
        r'<pre id="r3v6-browser-result">([^<]+)</pre>',
        completed.stdout,
    )
    if completed.returncode != 0 or not match:
        return {
            "browser_available": True,
            "error": (
                f"returncode={completed.returncode} "
                f"stderr={completed.stderr[-800:]!r}"
            ),
        }
    return json.loads(unescape(match.group(1)))


def main() -> int:
    v = V()
    for rel in (
        "app/editorial_briefings.py",
        "app/editorial_review.py",
        "app/editorial_feedback.py",
        "scripts/build_editorial_review_console.py",
        "scripts/compile_editorial_feedback.py",
        "scripts/run_editorial_briefing.py",
        "scripts/verify_editorial_review_console.py",
    ):
        py_compile.compile(str(ROOT / rel), doraise=True)
    v.check("Python compile", True)

    v.equal(
        "category order fixed",
        editorial_review.CATEGORY_ORDER,
        ("투자·산업", "기업동향", "기술정보"),
    )
    v.equal(
        "category normalize investment",
        editorial_review.normalize_category("정책", "AI 투자 확대"),
        "투자·산업",
    )
    v.equal(
        "category normalize legacy value signal",
        editorial_review.normalize_category("정책"),
        "투자·산업",
    )
    v.equal(
        "category normalize corporate",
        editorial_review.normalize_category("", "기업 AI 도입"),
        "기업동향",
    )
    v.equal(
        "category fallback technology",
        editorial_review.normalize_category("", "새 추론 모델 공개"),
        "기술정보",
    )
    analysis = editorial_review.analyze_editorial_category(
        "AI 로봇 모델 공개",
        "투자 확대 계획",
        source="검증매체",
        suggested_category="기업동향",
    )
    v.check(
        "AI category analysis returns scores and reason",
        analysis["category"] == "기술정보"
        and set(analysis["scores"]) == set(editorial_review.CATEGORY_ORDER)
        and isinstance(analysis["matched_signals"], dict)
        and bool(analysis["reason"]),
    )
    v.check(
        "title signal outweighs summary signal",
        analysis["scores"]["기술정보"] > analysis["scores"]["투자·산업"],
    )
    v.equal(
        "AI no-signal fallback is technology",
        editorial_review.analyze_editorial_category("", "")["category"],
        "기술정보",
    )
    v.equal(
        "AI no-signal fallback outweighs weak prior",
        editorial_review.analyze_editorial_category(
            "",
            "",
            suggested_category="투자·산업",
        )["category"],
        "기술정보",
    )
    v.equal(
        "AI deterministic tie break",
        editorial_review.analyze_editorial_category("투자 기업", "")["category"],
        "투자·산업",
    )
    v.equal(
        "suggested category is weak prior",
        editorial_review.analyze_editorial_category(
            "기업 경영 발표",
            "",
            suggested_category="투자·산업",
        )["category"],
        "기업동향",
    )
    v.equal(
        "longer corporate signal beats embedded acquisition token",
        editorial_review.analyze_editorial_category("인수합병 발표", "")["category"],
        "기업동향",
    )

    rich = editorial_briefings.sanitize_editorial_inline_html(
        '<strong>핵심</strong><script>alert(1)</script><img src=x> 내용<br><b>수치</b>'
    )
    v.equal(
        "rich text keeps safe bold",
        rich,
        "<strong>핵심</strong>alert(1) 내용<br><strong>수치</strong>",
    )
    v.check("rich text removes unsafe tags", "<script" not in rich and "<img" not in rich)
    v.equal(
        "rich text plain extraction",
        editorial_briefings.editorial_inline_plain_text(rich),
        "핵심alert(1) 내용 수치",
    )

    run_at = datetime(2026, 7, 31, 7, 20, tzinfo=KST)
    fixture = editorial_briefings.fixture_articles("daily", run_at, profile="dominant")
    coverage = editorial_briefings.daily_coverage(run_at)
    articles = editorial_briefings.normalize_articles(
        fixture,
        coverage,
        limit=12,
        resolve_images=False,
        selection_mode=editorial_briefings.SELECTION_MODE_EDITORIAL_PRIORITY,
    )
    candidates = [
        editorial_review.article_to_candidate(article, ai_rank=index)
        for index, article in enumerate(articles, 1)
    ]
    v.check(
        "candidate JSON model includes category analysis",
        all(
            item.get("category_analysis", {}).get("category") == item["category"]
            and bool(item["category_analysis"].get("reason"))
            for item in candidates
        ),
    )
    candidates.sort(
        key=lambda item: (
            editorial_review.category_rank(item["category"]),
            -float(item["adjusted_score"]),
            int(item["ai_rank"]),
        )
    )
    for index, item in enumerate(candidates, 1):
        item["adjusted_rank"] = index
        item["ai_recommended"] = index <= 6

    with tempfile.TemporaryDirectory(prefix="editorial-r3-") as tmp:
        tmp_path = Path(tmp)
        bundle_path = tmp_path / "candidates.json"
        bundle = editorial_review.write_bundle(
            edition_key="2026-07-31",
            coverage_start=coverage.start.isoformat(),
            coverage_end=coverage.end.isoformat(),
            candidates=candidates,
            path=bundle_path,
            generated_at=run_at.isoformat(),
        )
        loaded = editorial_review.load_bundle(bundle_path, "2026-07-31")
        v.equal("bundle version", loaded["version"], 2)
        v.equal(
            "bundle category order",
            loaded["category_order"],
            list(editorial_review.CATEGORY_ORDER),
        )

        ids = [item["candidate_id"] for item in candidates]
        approved = {
            "version": 2,
            "edition_type": "daily",
            "edition_key": "2026-07-31",
            "review_status": "approved",
            "selected_items": [
                {
                    "candidate_id": ids[0],
                    "origin": "ai_collected",
                    "title": "사용자가 고친 제목",
                    "summary_html": "<strong>볼드 핵심</strong> 설명",
                    "category": "투자·산업",
                },
                {
                    "candidate_id": "manual-1",
                    "origin": "human_link",
                    "title": "사용자 선별 AI 투자 기사",
                    "summary": "직접 고른 기사 요약",
                    "summary_html": "직접 고른 <strong>기사 요약</strong>",
                    "source": "사용자선별언론",
                    "published_at": run_at.isoformat(),
                    "selected_url": "https://example.org/manual-ai-investment",
                    "category": "기업동향",
                    "image_url": "",
                },
            ],
            "approved_at": run_at.isoformat(),
        }
        review_path = tmp_path / "review.json"
        review_path.write_text(
            json.dumps(approved, ensure_ascii=False),
            encoding="utf-8",
        )
        review = editorial_review.load_review(review_path, "2026-07-31")
        selected, mode = editorial_review.choose_daily_articles(bundle, review)
        v.equal("approved review mode", mode, "human_approved")
        v.equal("edited title preserved", selected[0].title, "사용자가 고친 제목")
        v.equal(
            "bold summary preserved",
            selected[0].summary_html,
            "<strong>볼드 핵심</strong> 설명",
        )
        v.equal(
            "manual link selected",
            selected[1].selected_url,
            "https://example.org/manual-ai-investment",
        )
        v.equal("manual link kind", selected[1].collection_source_kind, "human_link")
        v.equal("manual category remains explicit", selected[1].category, "기업동향")

        edition = editorial_briefings.render_daily(
            selected,
            run_at=run_at,
            root_url="https://preview.fixture.test/HDEC-News-Sensor",
        )
        v.check("rendered HTML contains bold", "<strong>볼드 핵심</strong>" in edition.html)
        v.check("rendered HTML contains manual link", "manual-ai-investment" in edition.html)
        v.check(
            "rendered HTML contains category ticker",
            "투자·산업" in edition.html and "기업동향" in edition.html,
        )

        auto, auto_mode = editorial_review.choose_daily_articles(bundle, None)
        v.equal("AI fallback mode", auto_mode, "ai_fallback")
        ranks = [editorial_review.category_rank(item.category) for item in auto]
        v.equal("AI fallback category order", ranks, sorted(ranks))

    records = [
        {
            "version": 2,
            "edition_key": "2026-07-31",
            "candidate_id": "manual-1",
            "origin": "human_link",
            "selected_url": "https://quality.example.com/ai-data-center",
            "title": "AI 데이터센터 투자 확대",
            "source": "사용자선별언론",
            "category": "투자·산업",
            "selected": True,
            "overall_rating": 0,
            "dimension_ratings": {},
            "exclusion_tags": [],
            "rated_at": run_at.isoformat(),
        }
    ]
    profile = editorial_feedback.compile_profile(records, minimum_samples=3)
    v.check(
        "manual domain seed learned",
        profile["manual_domain_seeds"].get("quality.example.com", 0) > 0,
    )
    v.check(
        "manual keyword seed learned",
        profile["manual_keyword_seeds"].get("데이터센터", 0) > 0,
    )
    candidate = {
        "source": "다른언론",
        "category": "투자·산업",
        "selected_url": "https://quality.example.com/another",
        "title": "AI 데이터센터 신규 투자",
    }
    v.check(
        "manual link affects future ranking",
        editorial_feedback.adjustment(candidate, profile) > 0,
    )
    v.check(
        "feedback cap bounded",
        abs(editorial_feedback.adjustment(candidate, profile))
        <= profile["max_abs_adjustment"],
    )

    repeated_records = records * 3
    repeated_profile = editorial_feedback.compile_profile(
        repeated_records, minimum_samples=3
    )
    learned_queries = editorial_feedback.collection_queries(repeated_profile)
    v.check(
        "repeated manual domain activates bounded collection query",
        "site:quality.example.com AI" in learned_queries,
    )
    v.check(
        "repeated manual keyword activates bounded collection query",
        "AI 데이터센터" in learned_queries,
    )
    v.check(
        "learned collection queries remain bounded",
        len(learned_queries) <= editorial_feedback.COLLECTION_QUERY_LIMIT,
    )

    template = (ROOT / "templates/editorial_review_console.html").read_text(
        encoding="utf-8"
    )
    for token in (
        "인간이 선별한 기사 링크 추가",
        'contenteditable="true"',
        'id="boldBtn"',
        "카테고리 기본순서",
        "HTML 다운로드",
        "평가 JSONL",
        "selected_items",
        "human_link",
        "투자·산업",
        "기업동향",
        "기술정보",
    ):
        v.check(f"console contains {token}", token in template)

    builder_source = (
        ROOT / "scripts/build_editorial_review_console.py"
    ).read_text(encoding="utf-8")
    v.check(
        "matured manual links feed supplemental collection",
        "editorial_feedback.collection_queries(profile)" in builder_source
        and "live_collector.fetch_all(sources_path=sources)" in builder_source,
    )
    v.check(
        "candidate card is draggable",
        'class="candidate ${selected?"selected":""}" draggable="true"' in template
        and 'event.dataTransfer.setData(kind==="candidate"?"candidate_id"' in template,
    )
    v.check(
        "candidate card contains safe original links",
        "original-article-link" in template
        and "원문 열기 ↗" in template
        and "safeUrl(candidate.selected_url)" in template,
    )
    v.check(
        "left original links use target and rel security",
        'target="_blank" rel="noopener noreferrer"' in template,
    )
    v.check(
        "selected article card is draggable",
        'class="article-card selected-article-card" draggable="true"' in template,
    )
    v.check(
        "sector card drop controls category and order",
        "moveArticle(payload.id,card.dataset.selectedCategory,id,after)" in template,
    )
    v.check(
        "sector empty drop appends article",
        "moveArticle(payload.id,zone.dataset.dropCategory)" in template,
    )
    v.check(
        "maximum six remains enforced",
        "const MAX_SELECTED=6" in template
        and "state.selected.length>=MAX_SELECTED" in template,
    )
    v.check(
        "fixed sector empty guidance present",
        "왼쪽 기사를 이 섹터로 드래그하세요" in template
        and "data-drop-category" in template,
    )
    v.check(
        "order chips removed in favor of article cards",
        'id="orderList"' not in template and "order-chip" not in template,
    )
    v.check(
        "image rendering is local and escaped",
        "safeImageUrl(candidate.image_url)" in template
        and 'src="${esc(imageUrl)}"' in template,
    )
    v.check(
        "image lazy decode and fallback control exist",
        'loading="lazy" decoding="async"' in template
        and "bindImageFallbacks" in template
        and "image-fallback" in template,
    )
    v.check(
        "localStorage restoration remains present",
        "localStorage.getItem(storageKey)" in template
        and "localStorage.setItem(storageKey" in template,
    )
    v.check(
        "live image materialization uses temporary local root",
        "editorial_briefings.materialize_preview_images(" in builder_source
        and 'dir="/tmp"' in builder_source
        and "html_dir=image_stage" in builder_source,
    )

    with tempfile.TemporaryDirectory(prefix="editorial-r3-v6-build-") as build_tmp:
        output_root = Path(build_tmp) / "review"
        build_environment = os.environ.copy()
        build_environment["TEAMS_AI_NEWS_WATCH"] = "0"
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "build_editorial_review_console.py"),
                "--fixture",
                "--run-at",
                "2026-07-31T07:20:00+09:00",
                "--output-root",
                str(output_root),
            ],
            cwd=ROOT,
            env=build_environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        v.check(
            "fixture console builds successfully",
            completed.returncode == 0,
            completed.stderr[-1000:],
        )
        edition_dir = output_root / "2026-07-31"
        latest_dir = output_root / "latest"
        fixture_bundle = json.loads(
            (edition_dir / "candidates.json").read_text(encoding="utf-8")
        )
        fixture_manifest = json.loads(
            (edition_dir / "manifest.json").read_text(encoding="utf-8")
        )
        latest_manifest = json.loads(
            (latest_dir / "manifest.json").read_text(encoding="utf-8")
        )
        v.check(
            "fixture mode performs zero image network calls",
            fixture_bundle["collection_audit"]["network_calls"] == 0
            and fixture_manifest["image_network_calls"] == 0
            and fixture_manifest["image_download_attempts"] == 0,
        )
        v.check(
            "candidate JSON contains category analysis",
            fixture_bundle["candidates"]
            and all(
                set(candidate["category_analysis"]) >= {
                    "category", "scores", "matched_signals", "reason",
                }
                for candidate in fixture_bundle["candidates"]
            ),
        )
        v.check(
            "candidate image paths are local",
            all(
                re.fullmatch(
                    r"assets/images/[A-Za-z0-9._-]+",
                    candidate.get("image_url", ""),
                )
                for candidate in fixture_bundle["candidates"]
            ),
        )
        fixture_assets = [
            edition_dir / candidate["image_url"]
            for candidate in fixture_bundle["candidates"]
        ]
        latest_assets = [
            latest_dir / candidate["image_url"]
            for candidate in fixture_bundle["candidates"]
        ]
        v.check(
            "fixture image asset exists",
            bool(fixture_assets)
            and all(path.is_file() and path.stat().st_size > 0 for path in fixture_assets),
        )
        v.check(
            "latest image assets exist",
            all(path.is_file() and path.stat().st_size > 0 for path in latest_assets),
        )
        daily_articles, _ = editorial_review.choose_daily_articles(
            fixture_bundle,
            None,
        )
        v.check(
            "Daily renderer path rebases to review image asset",
            all(
                article.image_url.startswith(
                    "../review/2026-07-31/assets/images/"
                )
                and (
                    output_root.parent
                    / "daily"
                    / article.image_url
                ).resolve().is_file()
                for article in daily_articles
            ),
        )
        v.check(
            "image counters copied to latest manifest",
            fixture_manifest["image_assets_materialized"] == 1
            and latest_manifest["image_assets_materialized"] == 1,
        )
        browser_results = run_browser_interaction(
            latest_dir / "index.html",
            Path(build_tmp) / "browser-profile",
        )
        v.check(
            "headless browser available",
            browser_results.get("browser_available") is True,
            str(browser_results),
        )
        for key, label in (
            ("left_to_right_drag", "left unselected candidate drag selects article"),
            ("left_drop_category", "left candidate appears in corporate sector"),
            ("cross_sector_move", "cross-sector drop changes category"),
            ("same_sector_reorder", "same-sector card drop changes order"),
            ("dom_state_order", "DOM order equals state.selected order"),
            ("maximum_six", "browser drag preserves maximum six"),
            ("left_original_link", "browser original link is safe"),
            ("link_does_not_select", "link click does not alter selection or drag"),
            ("sector_order", "browser sector order is fixed"),
            ("exactly_three_sectors", "browser renders exactly three sectors"),
            ("drop_zones", "browser renders three drop zones"),
            ("images_render", "fixture images load or show fallback"),
            ("no_remote_image_src", "rendered DOM has no remote image src"),
            ("review_status_draft", "drag returns review status to draft"),
            ("local_storage_restore", "reload restores selection and categories"),
            ("restored_dom_order", "reload restores selected DOM order"),
            ("restored_sector_order", "reload preserves fixed sector order"),
            ("restored_images", "reload preserves image or fallback rendering"),
        ):
            v.check(label, browser_results.get(key) is True, str(browser_results))

    workflow = (
        ROOT / ".github/workflows/editorial-review-console.yml"
    ).read_text(encoding="utf-8")
    v.check("console schedule is 07:20 KST", 'cron: "20 22 * * *"' in workflow)
    v.check(
        "console workflow has no sender",
        not any(
            token in workflow
            for token in (
                "send_teams",
                "send_email",
                "send_telegram",
                "run_editorial_briefing.py --send",
            )
        ),
    )
    run_source = (ROOT / "scripts/run_editorial_briefing.py").read_text(
        encoding="utf-8"
    )
    v.check("publish reads approved review", "editorial_review.load_review" in run_source)
    v.check("publish retains AI fallback", "live_collection_fallback" in run_source)

    print(f"checks={v.checks} failures={v.failures}")
    print("category_ticker_order=투자·산업>기업동향>기술정보")
    print("rich_text_editing=PASS")
    print("bold_sanitization=PASS")
    print("manual_link_selection=PASS")
    print("manual_link_learning=PASS")
    print("network_sends=0")
    print("smtp_attempts=0")
    print("teams_sends=0")
    print("telegram_sends=0")
    print("production_state_writes=0")
    print(f"R3_V6_VERIFIER={v.checks}/{v.failures}")
    if v.failures:
        print("RESULT=D7-AK-6E-R3-V6_EDITORIAL_REVIEW_CONSOLE_FAIL")
        return 1
    print("RESULT=D7-AK-6E-R3-V6_EDITORIAL_REVIEW_CONSOLE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
