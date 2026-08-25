#!/usr/bin/env python3
"""R4-OPS-10E Editor presentation-parity acceptance verifier.

Runs the real generated Daily Editor in headless Chrome/Edge, exercises its
canonical state and persistence, and compares the same review data with the
actual Python Daily publication renderer. No production network or writes.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app import editorial_briefings, editorial_operator_review, editorial_review  # noqa: E402
from verify_editorial_review_console import (  # noqa: E402
    _browser_argument_path,
    _browser_executable,
    _browser_path,
)


class Verifier:
    def __init__(self) -> None:
        self.checks = 0
        self.failures = 0

    def check(self, label: str, condition: object, detail: object = "") -> bool:
        self.checks += 1
        ok = bool(condition)
        if not ok:
            self.failures += 1
        suffix = f" — {detail}" if detail and not ok else ""
        print(f"{'PASS' if ok else 'FAIL'}: {label}{suffix}")
        return ok


HARNESS = r"""
<script>
(async()=>{
  const result={browser_available:true};
  const pause=milliseconds=>new Promise(resolve=>setTimeout(resolve,milliseconds));
  let authMode=false;
  let externalRequests=0;
  window.alert=()=>{};
  window.confirm=()=>false;
  window.fetch=(url,options={})=>new Promise(resolve=>setTimeout(()=>{
    const requestUrl=String(url||"");
    if(requestUrl.endsWith("/api/auth/session")){
      resolve({ok:true,status:200,json:async()=>authMode?{authenticated:true,login:"operator-fixture"}:{authenticated:false}});
      return;
    }
    if(requestUrl.endsWith("/api/editorial/contributor/session")){
      resolve({ok:true,status:200,json:async()=>({authenticated:false,role:""})});
      return;
    }
    if(requestUrl.endsWith("/manifest.json")||requestUrl==="manifest.json"){
      resolve({ok:true,status:200,json:async()=>({review_snapshot_id:`review-${bundle.edition_key}-0000000000000000`})});
      return;
    }
    externalRequests+=1;
    resolve({ok:false,status:503,json:async()=>({ok:false})});
  },15));
  const phaseKey=storageKey+":r4ops10e-phase";
  const expectedKey=storageKey+":r4ops10e-expected";
  function previewIds(selector){
    return [...document.querySelectorAll(selector)].map(node=>node.dataset.articleId);
  }
  function drag(source,target,clientY=0){
    if(!source||!target||typeof DataTransfer!=="function")return false;
    const transfer=new DataTransfer();
    source.dispatchEvent(new DragEvent("dragstart",{bubbles:true,cancelable:true,dataTransfer:transfer,clientY}));
    target.dispatchEvent(new DragEvent("dragover",{bubbles:true,cancelable:true,dataTransfer:transfer,clientY}));
    target.dispatchEvent(new DragEvent("drop",{bubbles:true,cancelable:true,dataTransfer:transfer,clientY}));
    source.dispatchEvent(new DragEvent("dragend",{bubbles:true,cancelable:true,dataTransfer:transfer,clientY}));
    return true;
  }
  async function downloadedBrief(){
    let blob=null;
    const previousCreate=URL.createObjectURL;
    const previousClick=HTMLAnchorElement.prototype.click;
    URL.createObjectURL=value=>{blob=value;return "blob:r4ops10e-local-download";};
    HTMLAnchorElement.prototype.click=function(){};
    try{
      document.getElementById("htmlBtn").click();
      return blob?await blob.text():"";
    }finally{
      URL.createObjectURL=previousCreate;
      HTMLAnchorElement.prototype.click=previousClick;
    }
  }
  await pause(450);
  if(localStorage.getItem(phaseKey)!=="restore"){
    const initialText=document.body.innerText;
    const operatorPanel=document.getElementById("operatorPanel");
    result.default_intake=/기사 URL로 자동 불러오기/.test(initialText)&&document.getElementById("importUrl").disabled===false;
    result.default_anonymous_hint=initialText.includes("기사 분석은 로그인 없이 가능합니다.");
    result.default_team=initialText.includes("팀원 검토 요청")&&initialText.includes("팀원 인증");
    result.default_no_github=!initialText.includes("GitHub");
    result.operator_collapsed=!!operatorPanel&&!operatorPanel.open;
    result.privileged_logged_out=["publishBtn","saveDraftBtn","loadTeamBtn"].every(id=>document.getElementById(id).disabled);

    operatorPanel.open=true;
    await pause(30);
    const login=document.querySelector("#operatorAuthCta a");
    result.generic_operator_login=!!login&&login.textContent.trim()==="운영자 로그인"&&!operatorPanel.innerText.includes("GitHub");
    result.oauth_route=!!login&&new URL(login.href).pathname==="/api/auth/github/login";

    authMode=true;
    await probeImportAuth();
    serverContext.editionId=`daily-${bundle.edition_key}-0000000000000000`;
    serverContext.snapshotId=`review-${bundle.edition_key}-0000000000000000`;
    refreshServerButtons();
    renderOperatorAuthCta();
    result.operator_authenticated=serverContext.authenticated===true&&document.getElementById("operatorSummary").textContent.trim()==="운영자 기능 · 인증됨"&&document.getElementById("operatorAuthCta").textContent.includes("operator-fixture");
    result.privileged_authenticated=["publishBtn","saveDraftBtn","loadTeamBtn"].every(id=>document.getElementById(id).disabled===false);

    const ids=bundle.candidates.slice(0,3).map(candidate=>candidate.candidate_id);
    const [a,b,c]=ids;
    state.selected=[];
    render();
    result.zero_headline=!!document.querySelector('#preview [data-role="headline-empty"]')&&!document.querySelector('#preview [data-role="headline"]')&&document.querySelector('#preview [data-role="headline-empty"]').textContent.includes("선정된 헤드라인이 없습니다");
    result.zero_briefing=previewIds('#preview [data-role="article-card"]').length===0&&document.querySelector('#preview [data-role="briefing-empty"]').textContent.includes("오늘의 브리핑에 포함할 기사가 없습니다");

    state.selected=[a];
    render();
    const oneHeadline=document.querySelector('#preview [data-role="headline"]');
    const oneView=view(a);
    const oneImage=oneHeadline.querySelector("img");
    const oneFallback=oneHeadline.querySelector(".image-fallback");
    result.one_headline=previewIds('#preview [data-role="headline"]').join("")===a&&oneHeadline.querySelector("h2").textContent.trim()===oneView.title&&oneHeadline.querySelector("select").value===oneView.category;
    const oneFacts=[...document.querySelectorAll('#preview [data-role="editor-summary"] [data-role="fact-points"] li')];
    result.one_summary_source=!!document.querySelector('#preview [data-role="editor-summary"]')&&oneFacts.length>=2&&oneFacts.length<=3&&!document.querySelector('#preview [data-role="editor-summary"] .summary-synthesis').textContent.includes(oneView.summary)&&document.querySelector('#preview [data-role="editor-summary"] .src').textContent.includes(oneView.source);
    result.one_safe_link=!!document.querySelector('#preview [data-role="editor-summary"] .src a[href^="https://"]');
    result.one_image=!!(oneImage&&oneImage.naturalWidth>0)||!!(oneFallback&&!oneFallback.hidden);
    result.one_not_duplicated=previewIds('#preview [data-role="article-card"]').length===0&&document.querySelector('#preview [data-role="briefing-empty"]').textContent.includes("추가로 선정된 주요 기사 없음");

    const originalImage=bundle.candidates[0].image_url;
    bundle.candidates[0].image_url="";
    render();
    result.image_fallback=!document.querySelector('#preview [data-role="headline"] img')&&!document.querySelector('#preview [data-role="headline"] .image-fallback').hidden;
    bundle.candidates[0].image_url="https://unsafe.example.test/raw.jpg";
    render();
    result.image_remote_rejected=!document.querySelector('#preview [data-role="headline"] img')&&![...document.images].some(image=>/^https?:/i.test(image.getAttribute("src")||""));
    bundle.candidates[0].image_url=originalImage;

    const sourceCandidate=bundle.candidates[0];
    const originalSelectedUrl=sourceCandidate.selected_url;
    sourceCandidate.selected_url="javascript:alert(1)";
    render();
    result.unsafe_source_rejected=!document.querySelector('#preview [data-role="editor-summary"] .src a');
    sourceCandidate.selected_url=originalSelectedUrl;

    state.selected=[a,b,c];
    render();
    result.multi_headline=previewIds('#preview [data-role="headline"]').join("")===a;
    result.multi_briefing=JSON.stringify(previewIds('#preview [data-role="article-card"]'))===JSON.stringify([b,c]);
    result.multi_unique=new Set([...previewIds('#preview [data-role="headline"]'),...previewIds('#preview [data-role="article-card"]')]).size===3;
    result.multi_categories=state.selected.every(id=>document.querySelector(`#preview [data-article-id="${id}"] [data-category-id="${id}"]`).value===view(id).category);

    document.querySelector(`#preview [data-headline-id="${c}"]`).click();
    result.headline_action=JSON.stringify(state.selected)===JSON.stringify([c,a,b])&&previewIds('#preview [data-role="headline"]').join("")===c&&JSON.stringify(previewIds('#preview [data-role="article-card"]'))===JSON.stringify([a,b]);

    const editedTitle="편집된 AI 헤드라인 제목";
    const editedSummary='<strong>편집된 AI 요약</strong><br><a href="https://evidence.example.test/fact">근거 링크</a>';
    const editedFact="편집된 임원용 사실 포인트";
    const heroTitle=document.querySelector(`#preview [data-role="headline"] [data-field="title"]`);
    heroTitle.textContent=editedTitle;
    heroTitle.dispatchEvent(new InputEvent("input",{bubbles:true,inputType:"insertText",data:"편집"}));
    const heroSummary=document.querySelector(`#preview [data-role="editor-summary"] [data-field="summary_html"]`);
    heroSummary.innerHTML=editedSummary;
    heroSummary.dispatchEvent(new InputEvent("input",{bubbles:true,inputType:"insertText",data:"편집"}));
    const heroFact=document.querySelector(`#preview [data-role="editor-summary"] [data-field="fact_point"]`);
    heroFact.textContent=editedFact;
    heroFact.dispatchEvent(new InputEvent("input",{bubbles:true,inputType:"insertText",data:"편집"}));
    const heroCategory=document.querySelector(`#preview [data-role="headline"] [data-category-id="${c}"]`);
    heroCategory.value="기업동향";
    heroCategory.dispatchEvent(new Event("change",{bubbles:true}));
    result.edit_canonical=view(c).title===editedTitle&&view(c).summary_html.includes("<strong>편집된 AI 요약</strong>")&&view(c).summary_html.includes('href="https://evidence.example.test/fact"')&&view(c).executive_context.fact_points[0]===editedFact&&view(c).category==="기업동향"&&state.selected[0]===c;
    result.edit_preview=document.querySelector('#preview [data-role="headline"] h2').textContent.trim()===editedTitle&&document.querySelector('#preview [data-role="editor-summary"] [data-role="fact-points"]').textContent.includes(editedFact)&&document.querySelector('#preview [data-role="headline"] select').value==="기업동향";
    result.edit_left=document.querySelector(`.candidate[data-id="${c}"] .candidate-title`).textContent.trim()===editedTitle;
    result.inline_sanitizer=sanitizeInline('<a href="javascript:alert(1)">위험</a><strong>허용</strong>')==="위험<strong>허용</strong>";

    const technologyZone=document.querySelector('[data-drop-category="기술정보"]');
    const dragSource=document.querySelector(`#preview [data-article-id="${a}"]`);
    const dragSupported=drag(dragSource,technologyZone,technologyZone.getBoundingClientRect().bottom-2);
    result.drag_category=dragSupported&&view(a).category==="기술정보"&&state.selected[0]===c;

    state.selected=bundle.candidates.slice(0,6).map(candidate=>candidate.candidate_id);
    const extra={...bundle.candidates[0],candidate_id:"candidate-r4ops10e-extra",title:"선택 제한 검증 기사",selected_url:"https://extra.fixture.test/article",image_url:""};
    state.manualCandidates.push(extra);
    render();
    const beforeLimit=[...state.selected];
    const limitSource=document.querySelector('.candidate[data-id="candidate-r4ops10e-extra"]');
    const limitDragSupported=drag(limitSource,document.querySelector('[data-drop-category="기업동향"]'));
    result.max_six=limitDragSupported&&state.selected.length===6&&JSON.stringify(state.selected)===JSON.stringify(beforeLimit)&&!state.selected.includes(extra.candidate_id);

    state.manualCandidates=state.manualCandidates.filter(candidate=>candidate.candidate_id!==extra.candidate_id);
    state.selected=[c,a,b];
    save();
    render();
    const localBrief=await downloadedBrief();
    const headlineIndex=localBrief.indexOf(`data-role="headline" data-article-id="${c}"`);
    const briefingA=localBrief.indexOf(`data-role="article-card" data-article-id="${a}"`);
    const briefingB=localBrief.indexOf(`data-role="article-card" data-article-id="${b}"`);
    result.local_download=headlineIndex>=0&&briefingA>headlineIndex&&briefingB>briefingA&&localBrief.match(new RegExp(`data-article-id="${c}"`,"g")).length===2&&localBrief.includes("오늘의 헤드라인")&&localBrief.includes("Editor's Summary")&&localBrief.includes("오늘의 브리핑")&&localBrief.includes(editedTitle)&&localBrief.includes(editedFact)&&!localBrief.includes("수집 레이더")&&!localBrief.includes("정보 분류 기준");
    // The headline appears in Hero + its attached Summary panel, never as a card.
    result.local_no_headline_card=!localBrief.includes(`data-role="article-card" data-article-id="${c}"`);
    const expected={selected:[...state.selected],title:view(c).title,summary_html:view(c).summary_html,fact:view(c).executive_context.fact_points[0],category:view(c).category,items:selectedItems(),local_download:result.local_download&&result.local_no_headline_card};
    localStorage.setItem(expectedKey,JSON.stringify(expected));
    localStorage.setItem(phaseKey,"restore");
    result.external_requests=externalRequests;
  }else{
    const expected=JSON.parse(localStorage.getItem(expectedKey)||"{}");
    result.reload_selected=JSON.stringify(state.selected)===JSON.stringify(expected.selected);
    result.reload_headline=previewIds('#preview [data-role="headline"]').join("")===expected.selected[0]&&JSON.stringify(previewIds('#preview [data-role="article-card"]'))===JSON.stringify(expected.selected.slice(1));
    result.reload_edits=view(expected.selected[0]).title===expected.title&&view(expected.selected[0]).summary_html===expected.summary_html&&view(expected.selected[0]).executive_context.fact_points[0]===expected.fact&&view(expected.selected[0]).category===expected.category;
    const localBrief=await downloadedBrief();
    result.reload_download=expected.local_download&&localBrief.includes(expected.title)&&localBrief.includes(expected.fact)&&!localBrief.includes("수집 레이더")&&!localBrief.includes("정보 분류 기준")&&!localBrief.includes(`data-role="article-card" data-article-id="${expected.selected[0]}"`);
    result.items=selectedItems();
    result.external_requests=externalRequests;
  }
  const marker=document.createElement("pre");
  marker.id="r4ops10e-result";
  marker.textContent=JSON.stringify(result);
  document.body.appendChild(marker);
})().catch(error=>{
  const marker=document.createElement("pre");
  marker.id="r4ops10e-result";
  marker.textContent=JSON.stringify({browser_available:true,error:String(error),stack:error&&error.stack||""});
  document.body.appendChild(marker);
});
</script>
"""


def _windows_temp_root() -> Path:
    output = subprocess.run(
        ["cmd.exe", "/d", "/c", "echo", "%TEMP%"],
        cwd="/mnt/c",
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    value = next(
        line.strip()
        for line in reversed(output.splitlines())
        if re.match(r"^[A-Za-z]:\\", line.strip())
    )
    return Path(
        subprocess.run(
            ["wslpath", "-u", value],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )


def run_browser_page(page: Path, profile: Path, browser: Path) -> dict[str, Any]:
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
        "--virtual-time-budget=5000",
        f"--user-data-dir={_browser_argument_path(profile, browser)}",
        "--dump-dom",
        _browser_path(page, browser),
    ]
    if browser.suffix.casefold() != ".exe":
        command[2:2] = ["--no-sandbox", "--disable-dev-shm-usage"]
    completed: subprocess.CompletedProcess[str] | None = None
    for attempt in range(2):
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
            break
        except subprocess.TimeoutExpired:
            if attempt:
                return {"browser_available": True, "error": "headless browser timeout after one retry"}
    assert completed is not None
    match = re.search(r'<pre id="r4ops10e-result">([^<]+)</pre>', completed.stdout)
    if completed.returncode != 0 or not match:
        return {
            "browser_available": True,
            "error": f"returncode={completed.returncode} stderr={completed.stderr[-1200:]!r}",
        }
    return json.loads(unescape(match.group(1)))


def publication_parity(
    bundle: dict[str, Any], items: list[dict[str, Any]]
) -> tuple[bool, dict[str, Any]]:
    # Exercise the server's real durable-review normalization before handing the
    # same ordered record to the actual publication selection/renderer path.
    review = editorial_operator_review.normalize_operator_review(
        {
            "product": "daily",
            "edition_key": bundle["edition_key"],
            "review_snapshot_id": (
                f"review-{bundle['edition_key']}-0000000000000000"
            ),
            "selected_items": items,
        },
        operator_login="operator-fixture",
        review_status="approved",
    )
    articles, mode = editorial_review.choose_daily_articles(bundle, review)
    edition = editorial_briefings.render_daily(
        articles,
        run_at=datetime.fromisoformat("2026-07-31T07:20:00+09:00"),
        root_url="https://publication.fixture.test",
    )
    published = edition.edition_manifest["articles"]
    id_by_url = {item["selected_url"]: item["candidate_id"] for item in items}
    publication_ids = [id_by_url.get(row["publisher_url"], "") for row in published]
    editor_ids = [item["candidate_id"] for item in items]
    detail = {
        "mode": mode,
        "editor_ids": editor_ids,
        "publication_ids": publication_ids,
        "headline": publication_ids[0] if publication_ids else "",
        "briefing": publication_ids[1:],
    }
    matches = (
        mode == "human_approved"
        and publication_ids == editor_ids
        and bool(published)
        and published[0]["headline"] is True
        and all(row["headline"] is False for row in published[1:])
        and [row["title"] for row in published] == [item["title"] for item in items]
        and [row["category"] for row in published] == [item["category"] for item in items]
        and len(publication_ids) == len(set(publication_ids))
    )
    return matches, detail


def main() -> int:
    verifier = Verifier()
    browser = _browser_executable()
    verifier.check("real Chrome/Edge executable available", browser is not None, browser or "")
    if browser is None:
        print("OPS10E_FOCUSED=FAIL")
        return 1

    with tempfile.TemporaryDirectory(prefix="r4-ops10e-") as temp_value:
        temp_root = Path(temp_value)
        review_root = temp_root / "review"
        build = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "build_editorial_review_console.py"),
                "--fixture",
                "--run-at",
                "2026-07-31T07:20:00+09:00",
                "--output-root",
                str(review_root),
                "--article-import-api-url",
                "https://operator.example.test/api/editorial/import-article",
            ],
            cwd=ROOT,
            env={**os.environ, "TEAMS_AI_NEWS_WATCH": "0"},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        verifier.check("fixture Editor builds", build.returncode == 0, build.stderr[-1000:])
        edition_dir = review_root / "2026-07-31"
        bundle = json.loads((edition_dir / "candidates.json").read_text(encoding="utf-8"))
        page = edition_dir / "r4-ops10e-browser.html"
        source = (edition_dir / "index.html").read_text(encoding="utf-8")
        before_body_end, after_body_end = source.rsplit("</body>", 1)
        page.write_text(
            before_body_end + HARNESS + "</body>" + after_body_end,
            encoding="utf-8",
        )

        profile_owner: tempfile.TemporaryDirectory[str] | None = None
        if browser.suffix.casefold() == ".exe":
            profile_owner = tempfile.TemporaryDirectory(
                prefix="hdec-r4-ops10e-browser-",
                dir=_windows_temp_root(),
                ignore_cleanup_errors=True,
            )
            profile = Path(profile_owner.name)
        else:
            profile = temp_root / "chrome-profile"
            profile.mkdir()
        try:
            first = run_browser_page(page, profile, browser)
            second = run_browser_page(page, profile, browser)
        finally:
            if profile_owner is not None:
                profile_owner.cleanup()

        verifier.check("browser phase one completed", "error" not in first, first.get("error", ""))
        verifier.check("browser persistence phase completed", "error" not in second, second.get("error", ""))
        first_checks = {
            "default article intake": "default_intake",
            "anonymous analysis wording": "default_anonymous_hint",
            "team review controls": "default_team",
            "no visible GitHub wording": "default_no_github",
            "operator section collapsed": "operator_collapsed",
            "logged-out privileged controls disabled": "privileged_logged_out",
            "generic operator login": "generic_operator_login",
            "underlying OAuth route": "oauth_route",
            "authenticated operator state": "operator_authenticated",
            "authenticated privileged controls": "privileged_authenticated",
            "zero selected headline": "zero_headline",
            "zero selected briefing": "zero_briefing",
            "one selected headline": "one_headline",
            "one selected summary/source": "one_summary_source",
            "one selected safe original link": "one_safe_link",
            "one selected image": "one_image",
            "one selected not duplicated": "one_not_duplicated",
            "branded image fallback": "image_fallback",
            "unsafe remote image rejected": "image_remote_rejected",
            "unsafe source URL rejected": "unsafe_source_rejected",
            "multiple selected headline": "multi_headline",
            "multiple selected briefing order": "multi_briefing",
            "multiple selected unique identities": "multi_unique",
            "multiple selected categories": "multi_categories",
            "explicit headline designation": "headline_action",
            "headline edits update canonical state": "edit_canonical",
            "headline edits update preview": "edit_preview",
            "headline title syncs left editor": "edit_left",
            "inline sanitizer remains authoritative": "inline_sanitizer",
            "cross-category drag/drop": "drag_category",
            "maximum six enforced": "max_six",
            "local download Daily hierarchy": "local_download",
            "local download has no headline card duplicate": "local_no_headline_card",
        }
        for label, key in first_checks.items():
            verifier.check(label, first.get(key), first)
        verifier.check("browser fixture made no external request", first.get("external_requests") == 0, first)
        for label, key in {
            "reload preserves canonical order": "reload_selected",
            "reload preserves headline/briefing split": "reload_headline",
            "reload preserves headline edits": "reload_edits",
            "reload preserves local download parity": "reload_download",
        }.items():
            verifier.check(label, second.get(key), second)
        verifier.check("reload fixture made no external request", second.get("external_requests") == 0, second)

        items = second.get("items") if isinstance(second.get("items"), list) else []
        parity, parity_detail = publication_parity(bundle, items) if items else (False, {})
        verifier.check("actual Daily renderer semantic parity", parity, parity_detail)

    reference = (ROOT / "docs/editorial/daily/2026-08-21.html").read_text(encoding="utf-8")
    reference_order = [
        reference.find("오늘의 헤드라인"),
        reference.find("AI 전환 ‘발상의 전환’…돈 없는 중기, 대학 눈돌려라"),
        reference.find("Editor's Summary"),
        reference.find("오늘의 브리핑"),
        reference.find("추가로 선정된 주요 기사 없음"),
    ]
    verifier.check(
        "2026-08-21 production semantic reference",
        all(index >= 0 for index in reference_order)
        and reference_order == sorted(reference_order),
        reference_order,
    )
    template = (ROOT / "templates/editorial_review_console.html").read_text(encoding="utf-8")
    verifier.check(
        "OAuth mechanism retained without UI bypass",
        '"/api/auth/github/login?"' in template
        and "/api/editorial/save-draft" in template
        and "/api/editorial/publish-daily" in template
        and "serverWritesReady()" in template,
    )
    print(f"checks={verifier.checks} failures={verifier.failures}")
    print(f"REAL_BROWSER_USED={'true' if browser is not None else 'false'}")
    print(f"OPS10E_FOCUSED={'PASS' if verifier.failures == 0 else 'FAIL'}")
    return 0 if verifier.failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
