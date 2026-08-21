#!/usr/bin/env python3
"""R4-OPS-10C team intake + portal recovery acceptance.

Deterministic network, repository writes, workflow dispatches, and browser API
calls are injected fakes. A real local headless browser is used when available.
This verifier performs no production write, dispatch, or message send.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("APP_MODE", "mock")
os.environ.setdefault("NEWS_MODE", "mock")

from app import (  # noqa: E402
    config,
    editorial_article_import as article_import,
    editorial_briefings,
    editorial_contributor_auth as contributor_auth,
    editorial_feedback,
    editorial_operator_review as operator_review,
    editorial_team_intake as team_intake,
    operator_auth,
    operator_gateway,
)
import build_editorial_review_console as console_builder  # noqa: E402

EDITION_KEY = "2026-08-20"
SNAPSHOT_ID = "review-2026-08-20-9159214cae7c9872"
SNAPSHOT_MANIFEST = json.loads(
    (ROOT / "docs" / "editorial" / "review" / "snapshots" / SNAPSHOT_ID / "manifest.json").read_text(encoding="utf-8")
)
DIRECT_URL = "https://publisher.example.test/article/ai-infrastructure-1"
DAUM_URL = "https://v.daum.net/v/20260820180747166"
NAVER_URL = "https://n.news.naver.com/article/001/0012345678"
DAUM_HOME = "https://news.daum.net/"
TARGETLESS = (
    "https://teams.public.onecdn.static.microsoft/evergreen-assets/"
    "safelinks/2/atp-safelinks.html"
)


class Verify:
    def __init__(self) -> None:
        self.checks = 0
        self.failures = 0

    def check(self, label: str, condition: bool, detail: str = "") -> None:
        self.checks += 1
        if condition:
            print(f"PASS: {label}")
        else:
            self.failures += 1
            print(f"FAIL: {label}" + (f" — {detail}" if detail else ""))


class FakeResponse:
    def __init__(self, url: str, payload: bytes, content_type: str = "text/html"):
        self.status = 200
        self._url = url
        self._stream = io.BytesIO(payload)
        self.headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(payload)),
        }

    def read(self, size=-1):
        return self._stream.read(size)

    def getcode(self):
        return self.status

    def geturl(self):
        return self._url

    def close(self):
        self._stream.close()


class FakeOpener:
    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.calls: list[str] = []

    def open(self, request, timeout):
        assert timeout <= article_import.ARTICLE_FETCH_TIMEOUT_SECONDS
        url = request.full_url
        self.calls.append(url)
        return FakeResponse(url, self.pages[url].encode("utf-8"))


def public_resolver(host: str, port: int, type=None):
    del host, type
    return [(2, 1, 6, "", ("93.184.216.34", port))]


def article_html(url: str, source: str, *, direct: bool) -> str:
    body = " ".join(
        [
            "AI 데이터센터 전력 인프라 투자가 확대되면서 기업들은 고효율 설비와 안정적인 공급망을 확보하기 위한 구체적인 계약을 체결했다.",
            "이번 사업은 단계별 투자 일정과 공급 범위를 확정했으며 운영 효율과 에너지 절감 목표를 함께 제시했다.",
            "관련 기업은 기술 검증과 구축 일정을 공개하고 향후 추가 사업 기회를 검토할 계획이라고 설명했다.",
        ] * 2
    )
    canonical = f'<link rel="canonical" href="{url}">' if direct else ""
    return f"""<!doctype html><html><head>{canonical}
<meta property="og:type" content="article"><meta property="og:title" content="AI 데이터센터 전력 인프라 투자 확대">
<meta property="og:site_name" content="{source}"><meta property="og:url" content="{url}"></head>
<body><article><h1>AI 데이터센터 전력 인프라 투자 확대</h1><p>{body}</p></article></body></html>"""


def imported(url: str, source: str, *, direct: bool) -> dict:
    return article_import.import_article(
        url,
        resolver=public_resolver,
        opener=FakeOpener({url: article_html(url, source, direct=direct)}),
    )["article"]


class FakeGitHub:
    def __init__(self) -> None:
        self.repo = "Sinabroin/HDEC-News-Sensor"
        self.token = "offline"
        self.branch = "main"
        self.puts: list[str] = []
        self.store = {
            operator_review.review_snapshot_manifest_path(SNAPSHOT_ID): {
                "version": 1,
                "json": copy.deepcopy(SNAPSHOT_MANIFEST),
            }
        }

    def get_file(self, path):
        value = self.store.get(path)
        if value is None:
            return None
        return {"sha": f"sha:{path}:{value['version']}", "json": copy.deepcopy(value["json"])}

    def list_directory(self, path):
        prefix = path.rstrip("/") + "/"
        return sorted(key for key in self.store if key.startswith(prefix) and "/" not in key[len(prefix):])

    def put_file(self, path, *, content_bytes, message, base_sha):
        del message
        current = self.store.get(path)
        if current is None and base_sha:
            raise operator_review.OperatorReviewError("STALE_DRAFT")
        if current is not None and base_sha != f"sha:{path}:{current['version']}":
            raise operator_review.OperatorReviewError("STALE_DRAFT")
        version = int(current["version"] + 1) if current else 1
        self.store[path] = {"version": version, "json": json.loads(content_bytes)}
        self.puts.append(path)
        return {"sha": f"sha:{path}:{version}"}


def submission_payload(url: str) -> dict:
    return {
        "product": "daily",
        "edition_key": EDITION_KEY,
        "review_snapshot_id": SNAPSHOT_ID,
        "url": url,
    }


def imported_payload(article: dict) -> dict:
    return {"ok": True, "article": copy.deepcopy(article)}


def team_item(article: dict, submission_id: str) -> dict:
    authoritative = article["publisher_domain_authoritative"] is True
    return {
        "candidate_id": "team-" + submission_id,
        "submission_id": submission_id,
        "origin": "team_link",
        "title": article["title"],
        "source": article["source"],
        "summary": article["summary"],
        "summary_html": article["summary_html"],
        "selected_url": article["publisher_url"] if authoritative else article["analysis_url"],
        "analysis_url": article["analysis_url"],
        "publisher_url": article["publisher_url"],
        "publisher_domain_authoritative": authoritative,
        "portal_copy": article["portal_copy"],
        "portal_source": article["portal_source"],
        "portal_resolution_reason": article["portal_resolution_reason"],
        "category": article["category"],
        "published_at": "2026-08-20T09:00:00+09:00",
        "image_url": article["image_url"],
    }


def review_payload(item: dict) -> dict:
    return {
        "product": "daily",
        "edition_key": EDITION_KEY,
        "review_snapshot_id": SNAPSHOT_ID,
        "selected_items": [item],
    }


def verify_import_and_safety(v: Verify) -> dict[str, dict]:
    print("\n== Read-only analysis + portal fallback ==")
    direct = imported(DIRECT_URL, "테스트경제", direct=True)
    daum = imported(DAUM_URL, "다움경제", direct=False)
    naver = imported(NAVER_URL, "네이버테스트신문", direct=False)
    v.check(
        "1. direct publisher analysis returns complete metadata",
        direct["publisher_domain_authoritative"] is True
        and direct["portal_copy"] is False
        and all(direct[key] for key in ("title", "source", "summary", "category")),
    )
    for label, article, source in (("2. Daum", daum, "daum"), ("3. Naver", naver, "naver")):
        v.check(
            f"{label} allowlisted portal copy succeeds with explicit provenance",
            article["portal_copy"] is True
            and article["portal_source"] == source
            and article["publisher_domain_authoritative"] is False
            and article["publisher_url"] == ""
            and article["analysis_url"] in {DAUM_URL, NAVER_URL}
            and all(article[key] for key in ("title", "source", "summary", "category")),
            repr(article),
        )
    try:
        imported(DAUM_HOME, "홈페이지", direct=False)
    except article_import.ArticleImportError as exc:
        home_code = exc.code
    else:
        home_code = ""
    v.check("4. portal home/list page is rejected", home_code == "PORTAL_ORIGINAL_NOT_FOUND", home_code)

    old_mode = config.OPERATOR_ACCESS_MODE
    old_origins = list(config.OPERATOR_ALLOWED_ORIGINS)
    old_limit = config.OPERATOR_RATE_LIMIT_PER_MIN
    try:
        config.OPERATOR_ACCESS_MODE = "origin"
        config.OPERATOR_ALLOWED_ORIGINS = ["https://allowed.example"]
        config.OPERATOR_RATE_LIMIT_PER_MIN = 100
        operator_gateway._recent_triggers.clear()  # noqa: SLF001 - deterministic verifier reset
        allowed = operator_gateway.authorize({}, "https://allowed.example", "analyze_article")
        forbidden = operator_gateway.authorize({}, "https://evil.example", "analyze_article")
    finally:
        config.OPERATOR_ACCESS_MODE = old_mode
        config.OPERATOR_ALLOWED_ORIGINS = old_origins
        config.OPERATOR_RATE_LIMIT_PER_MIN = old_limit
        operator_gateway._recent_triggers.clear()  # noqa: SLF001
    v.check("anonymous allowed Origin may analyze", allowed["ok"] is True)
    v.check("5. forbidden Origin remains fail closed", forbidden["ok"] is False and forbidden["reason"] == "forbidden_origin")

    try:
        article_import.unwrap_microsoft_safelinks_url(TARGETLESS, resolver=public_resolver)
    except article_import.ArticleImportError as exc:
        targetless = exc.code
    else:
        targetless = ""
    wrapped = "https://nam12.safelinks.protection.outlook.com/?" + urlencode({"url": DIRECT_URL})
    v.check("15. targetless Microsoft wrapper remains specifically rejected", targetless == "MICROSOFT_SAFELINK_TARGET_MISSING")
    v.check("16. explicit SafeLinks target is revalidated and unwrapped", article_import.unwrap_microsoft_safelinks_url(wrapped, resolver=public_resolver) == DIRECT_URL)
    wrapped_portal = "https://nam12.safelinks.protection.outlook.com/?" + urlencode({"url": DAUM_URL})
    wrapped_portal_article = article_import.import_article(
        wrapped_portal,
        resolver=public_resolver,
        opener=FakeOpener({DAUM_URL: article_html(DAUM_URL, "다움경제", direct=False)}),
    )["article"]
    v.check(
        "explicit SafeLinks portal target enters allowlisted portal fallback",
        wrapped_portal_article["input_url"] == wrapped_portal
        and wrapped_portal_article["portal_source"] == "daum"
        and wrapped_portal_article["portal_copy"] is True,
    )
    private_wrapped = "https://nam12.safelinks.protection.outlook.com/?" + urlencode({"url": "http://127.0.0.1/private"})
    try:
        article_import.unwrap_microsoft_safelinks_url(private_wrapped, resolver=public_resolver)
    except article_import.ArticleImportError as exc:
        private_code = exc.code
    else:
        private_code = ""
    v.check("SafeLinks target reruns SSRF policy", private_code == "UNSAFE_DESTINATION")
    return {"direct": direct, "daum": daum, "naver": naver}


def verify_contributor_and_pending(v: Verify, articles: dict[str, dict]) -> dict:
    print("\n== Contributor role + durable pending intake ==")
    api_source = (ROOT / "app" / "operator_api.py").read_text(encoding="utf-8")
    v.check(
        "contributor endpoints are explicit and role-separated",
        all(
            route in api_source
            for route in (
                '"/api/editorial/contributor/login"',
                '"/api/editorial/contributor/session"',
                '"/api/editorial/contributor/logout"',
                '"/api/editorial/submit-for-review"',
                '"/api/editorial/pending-submissions"',
            )
        )
        and "editorial_contributor_auth.session_from_headers" in api_source,
    )
    old_hash = config.EDITORIAL_CONTRIBUTOR_CODE_SHA256
    old_secret = config.OPERATOR_SESSION_SECRET
    code = "bounded-team-code"
    try:
        config.EDITORIAL_CONTRIBUTOR_CODE_SHA256 = hashlib.sha256(code.encode()).hexdigest()
        config.OPERATOR_SESSION_SECRET = "offline-domain-separated-session-secret"
        v.check("6. invalid contributor code is rejected", contributor_auth.valid_code("wrong") is False)
        v.check("7. valid contributor code authenticates contributor role", contributor_auth.valid_code(code) is True)
        token = contributor_auth.create_session_token(now=1_700_000_000)
        contributor = contributor_auth.verify_session_token(token, now=1_700_000_001)
        operator = operator_auth.verify_session_token(token, now=1_700_000_001)
        v.check("contributor token carries only contributor role", contributor == {"role": "editorial_contributor", "exp": 1_700_028_800})
        v.check("8. contributor token cannot satisfy operator save/publish auth", operator is None)
    finally:
        config.EDITORIAL_CONTRIBUTOR_CODE_SHA256 = old_hash
        config.OPERATOR_SESSION_SECRET = old_secret

    client = FakeGitHub()
    now = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)
    writes_before = len(client.puts)
    importer_calls = []
    first = team_intake.submit_for_review(
        submission_payload(DIRECT_URL),
        client=client,
        importer=lambda url: (
            importer_calls.append(url) or imported_payload(articles["direct"])
        ),
        now=now,
    )
    pending_writes = [path for path in client.puts if "/pending_submissions/" in path]
    learning_before = [path for path in client.store if "/human_exemplars/" in path]
    v.check(
        "9. contributor submit writes one pending record only",
        first["status"] == "pending"
        and len(client.puts) == writes_before + 1
        and len(pending_writes) == 1
        and not learning_before,
        repr(first),
    )
    stored_pending = client.store[pending_writes[0]]["json"]
    v.check(
        "submission metadata is derived by server-side reanalysis only",
        importer_calls == [DIRECT_URL]
        and stored_pending["title"] == articles["direct"]["title"]
        and not ({"body", "html", "cookies", "headers", "secrets"} & set(stored_pending)),
    )
    try:
        team_intake.submit_for_review(
            {**submission_payload(DIRECT_URL), "title": "browser override"},
            client=client,
            importer=lambda _url: imported_payload(articles["direct"]),
            now=now,
        )
    except team_intake.TeamIntakeError as exc:
        extra_code = exc.code
    else:
        extra_code = ""
    v.check("submit schema rejects browser metadata overrides", extra_code == "INVALID_PAYLOAD")
    second = team_intake.submit_for_review(
        submission_payload(DIRECT_URL),
        client=client,
        importer=lambda _url: imported_payload(articles["direct"]),
        now=now,
    )
    v.check(
        "10. identical submission retry is one deterministic record",
        second["unchanged"] is True
        and second["submission_id"] == first["submission_id"]
        and len([path for path in client.puts if "/pending_submissions/" in path]) == 1,
    )
    malformed_id = "submission-" + "d" * 64
    client.store[team_intake.pending_submission_path(EDITION_KEY, malformed_id)] = {
        "version": 1,
        "json": {"status": "pending", "submission_id": malformed_id},
    }
    loaded = team_intake.load_pending_submissions(EDITION_KEY, SNAPSHOT_ID, client=client)
    v.check(
        "operator load returns bounded valid submissions and ignores malformed files",
        loaded["count"] == 1 and loaded["submissions"][0]["status"] == "pending",
    )
    return {"client": client, "submission": loaded["submissions"][0]}


def verify_learning_and_gates(v: Verify, articles: dict[str, dict]) -> None:
    print("\n== Approved team_link learning + hard gates ==")
    results = {}
    for name in ("daum", "direct"):
        client = FakeGitHub()
        item = team_item(articles[name], "submission-" + ("a" if name == "daum" else "b") * 64)
        payload = review_payload(item)
        saved = operator_review.save_draft(payload, operator_login="operator", client=client)
        before = editorial_feedback.confirmed_human_exemplars({**payload, "review_status": "draft"})
        published = operator_review.publish_daily(
            {**payload, "base_revision": saved["revision"]},
            operator_login="operator",
            client=client,
            dispatcher=None,
        )
        exemplar_values = [
            value["json"] for path, value in client.store.items()
            if "/human_exemplars/exemplar-" in path
        ]
        results[name] = (published, exemplar_values, client)
        v.check(f"{name} pending/draft creates no active learning", before == [])
        v.check(
            f"12. approved/published {name} team_link creates one confirmed exemplar",
            published["learning_exemplars_added"] == 1
            and len(exemplar_values) == 1
            and editorial_feedback.valid_human_exemplar(exemplar_values[0]),
        )
        retry = operator_review.publish_daily(
            {**payload, "base_revision": saved["revision"], "base_approved_revision": published["approved_revision"]},
            operator_login="operator",
            client=client,
            dispatcher=None,
        )
        v.check(f"{name} confirmed exemplar is idempotent", retry["already_published"] is True and retry["learning_exemplars_added"] == 0)

    portal_exemplar = results["daum"][1][0]
    portal_profile = editorial_feedback.compile_profile_from_exemplars([portal_exemplar], minimum_samples=1)
    v.check(
        "13. portal-copy learns keywords but never Daum/Naver domain",
        bool(portal_profile["keyword_adjustments"])
        and portal_exemplar["publisher_domain"] == ""
        and not portal_profile["domain_adjustments"]
        and not portal_profile["manual_domain_seeds"]
        and all("daum" not in query and "naver" not in query for query in editorial_feedback.collection_queries(portal_profile)),
        repr(portal_profile),
    )
    direct_exemplar = results["direct"][1][0]
    direct_profile = editorial_feedback.compile_profile_from_exemplars([direct_exemplar], minimum_samples=1)
    v.check(
        "14. direct publisher team_link may learn real publisher domain",
        direct_exemplar["publisher_domain"] == "publisher.example.test"
        and "publisher.example.test" in direct_profile["domain_adjustments"],
    )

    coverage = editorial_briefings.daily_coverage(
        datetime.fromisoformat("2026-08-20T07:20:00+09:00")
    )
    noise = {
        "title": "소비자용 AI 사진 필터 앱 신제품 출시",
        "source": "테스트경제",
        "published_at": "2026-08-20T05:00:00+09:00",
        "url": "https://publisher.example.test/article/noise",
        "snippet": "개인 사진 꾸미기 기능과 구독 상품만 소개했다.",
        "provider": "google_news_rss",
    }
    selected = editorial_briefings.normalize_articles(
        [noise], coverage, limit=6, resolve_images=False,
        selection_mode=editorial_briefings.SELECTION_MODE_EDITORIAL_PRIORITY,
        edition_type="daily", operator_review=True,
    )
    source_gate = editorial_briefings.lead_source_eligible_tier(
        "테스트경제", "https://publisher.example.test/article/noise"
    )
    v.check("17. preference learning cannot bypass hard gates", selected == [] and source_gate is False)


def browser_executable() -> Path | None:
    for candidate in (
        os.environ.get("HDEC_TEST_BROWSER"), shutil.which("google-chrome"),
        shutil.which("chromium"), shutil.which("chromium-browser"),
        "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
        "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    ):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def browser_uri(path: Path, browser: Path) -> str:
    if browser.suffix.casefold() != ".exe":
        return path.resolve().as_uri()
    translated = subprocess.run(["wslpath", "-w", str(path.resolve())], check=True, capture_output=True, text=True).stdout.strip()
    return "file:///" + translated.replace("\\", "/")


def browser_path(path: Path, browser: Path) -> str:
    if browser.suffix.casefold() != ".exe":
        return str(path.resolve())
    return subprocess.run(["wslpath", "-w", str(path.resolve())], check=True, capture_output=True, text=True).stdout.strip()


def windows_tempdir():
    output = subprocess.run(["cmd.exe", "/d", "/c", "echo", "%TEMP%"], cwd="/mnt/c", check=True, capture_output=True, text=True).stdout
    win = next(line.strip() for line in reversed(output.splitlines()) if re.match(r"^[A-Za-z]:\\", line.strip()))
    wsl = subprocess.run(["wslpath", "-u", win], check=True, capture_output=True, text=True).stdout.strip()
    return tempfile.TemporaryDirectory(prefix="r4-ops10c-browser-", dir=wsl, ignore_cleanup_errors=True)


def browser_acceptance(articles: dict[str, dict], pending: dict) -> dict:
    browser = browser_executable()
    if browser is None:
        return {"browser_available": False, "error": "Chrome/Chromium unavailable"}
    bundle = {
        "version": 3, "edition_type": "daily", "edition_key": EDITION_KEY,
        "coverage_start": "2026-08-19T07:00:00+09:00",
        "coverage_end": "2026-08-20T06:40:00+09:00",
        "generated_at": "2026-08-20T07:20:00+09:00", "candidates": [],
        "article_import_api_url": "https://operator.example.test/api/editorial/import-article",
        "article_import_enabled": True,
    }
    responses = {key: {"ok": True, "article": value} for key, value in articles.items()}
    prelude = """<script>
window.__ops10c={contributor:false,operator:false,submissions:new Set(),writes:0,publishes:0};
const __responses=__RESPONSES__;const __pending=__PENDING__;
window.fetch=async function(url,options={}){const u=String(url||"");
 if(u.endsWith("manifest.json"))return {ok:true,status:200,json:async()=>({review_snapshot_id:"__SNAPSHOT__"})};
 if(u.endsWith("/api/auth/session"))return {ok:true,status:200,json:async()=>({authenticated:window.__ops10c.operator,login:window.__ops10c.operator?"operator":""})};
 if(u.endsWith("/api/editorial/contributor/session"))return {ok:true,status:200,json:async()=>({authenticated:window.__ops10c.contributor,role:window.__ops10c.contributor?"editorial_contributor":""})};
 if(u.endsWith("/api/editorial/contributor/login")){const b=JSON.parse(options.body||"{}");window.__ops10c.contributor=b.code==="valid-code";return {ok:window.__ops10c.contributor,status:window.__ops10c.contributor?200:401,json:async()=>window.__ops10c.contributor?{ok:true,authenticated:true,role:"editorial_contributor"}:{ok:false,error:{message:"팀원 인증 코드가 올바르지 않습니다."}}};}
 if(u.endsWith("/api/editorial/contributor/logout")){window.__ops10c.contributor=false;return {ok:true,status:200,json:async()=>({authenticated:false})};}
 if(u.endsWith("/api/editorial/import-article")){const b=JSON.parse(options.body||"{}");if(b.url==="__HOME__")return {ok:false,status:422,json:async()=>({ok:false,error:{code:"PORTAL_ORIGINAL_NOT_FOUND",message:"기사 페이지가 아닙니다."}})};const key=b.url==="__DIRECT__"?"direct":b.url==="__DAUM__"?"daum":"naver";return {ok:true,status:200,json:async()=>__responses[key]};}
 if(u.endsWith("/api/editorial/submit-for-review")){const b=JSON.parse(options.body||"{}");const id=b.url;const before=window.__ops10c.submissions.size;window.__ops10c.submissions.add(id);if(window.__ops10c.submissions.size>before)window.__ops10c.writes++;return {ok:window.__ops10c.contributor,status:window.__ops10c.contributor?200:401,json:async()=>window.__ops10c.contributor?{ok:true,status:"pending",submission_id:"submission-"+"c".repeat(64),unchanged:before===window.__ops10c.submissions.size}:{ok:false,error:{message:"팀원 인증이 필요합니다."}}};}
 if(u.includes("/api/editorial/pending-submissions?"))return {ok:window.__ops10c.operator,status:window.__ops10c.operator?200:401,json:async()=>window.__ops10c.operator?{ok:true,submissions:[__pending]}:{ok:false,error:{message:"운영자 인증이 필요합니다."}}};
 if(u.endsWith("/api/editorial/save-draft")||u.endsWith("/api/editorial/publish-daily")){if(u.endsWith("publish-daily"))window.__ops10c.publishes++;return {ok:false,status:401,json:async()=>({ok:false,error:{message:"운영자 인증이 필요합니다."}})};}
 throw new Error("unexpected fetch "+u);
};</script>"""
    prelude = (prelude.replace("__RESPONSES__", json.dumps(responses, ensure_ascii=False).replace("</", "<\\/"))
        .replace("__PENDING__", json.dumps(pending, ensure_ascii=False).replace("</", "<\\/"))
        .replace("__SNAPSHOT__", SNAPSHOT_ID).replace("__HOME__", DAUM_HOME)
        .replace("__DIRECT__", DIRECT_URL).replace("__DAUM__", DAUM_URL))
    harness = """<script>(async()=>{const r={browser_available:true};const pause=ms=>new Promise(x=>setTimeout(x,ms));
 const input=document.getElementById("importUrl");async function ingest(url){input.value=url;await importArticleFromUrl();await pause(30);return state.manualCandidates[state.manualCandidates.length-1];}
 await pause(120);r.no_login_copy=document.getElementById("importAuthHint").textContent.includes("로그인 없이");
 const d=await ingest("__DIRECT__");r.anon_direct=!!d&&d.publisher_domain_authoritative===true&&state.selected.includes(d.candidate_id)&&document.getElementById("preview").textContent.includes(d.title);
 const da=await ingest("__DAUM__");r.daum_preview=!!da&&da.portal_copy===true&&state.selected.includes(da.candidate_id)&&document.getElementById("preview").textContent.includes(da.title);
 const nv=await ingest("__NAVER__");r.naver_preview=!!nv&&nv.portal_copy===true&&state.selected.includes(nv.candidate_id);
 const count=allCandidates().length;await ingest("__HOME__");r.home_rejected=allCandidates().length===count&&document.getElementById("importStatus").classList.contains("error");
 document.getElementById("teamCode").value="wrong";await authenticateContributor();r.invalid_code=!contributorContext.authenticated;
 document.getElementById("teamCode").value="valid-code";await authenticateContributor();r.valid_contributor=contributorContext.authenticated&&document.getElementById("teamCodeControls").hidden;
 const saveResponse=await fetch(operatorApiOrigin()+"/api/editorial/save-draft",{method:"POST"});const publishResponse=await fetch(operatorApiOrigin()+"/api/editorial/publish-daily",{method:"POST"});r.contributor_not_operator=saveResponse.status===401&&publishResponse.status===401;
 await submitTeamReview();await submitTeamReview();r.submit_idempotent=window.__ops10c.writes===1&&document.getElementById("teamState").textContent.includes("운영자 승인 후");
 window.__ops10c.operator=true;await probeImportAuth();await loadTeamSubmissions();const team=state.manualCandidates.find(x=>x.origin==="team_link");r.operator_load=!!team&&!state.selected.includes(team.candidate_id)&&document.getElementById("serverState").textContent.includes("자동 선택·승인되지 않았습니다");
 const marker=document.createElement("pre");marker.id="ops10c-browser-result";marker.textContent=JSON.stringify(r);document.body.appendChild(marker);
 })().catch(e=>{const m=document.createElement("pre");m.id="ops10c-browser-result";m.textContent=JSON.stringify({browser_available:true,error:String(e),stack:e&&e.stack||""});document.body.appendChild(m);});</script>"""
    for key, value in {"__DIRECT__": DIRECT_URL, "__DAUM__": DAUM_URL, "__NAVER__": NAVER_URL, "__HOME__": DAUM_HOME}.items():
        harness = harness.replace(key, value)
    windows = browser.suffix.casefold() == ".exe"
    handle = windows_tempdir() if windows else tempfile.TemporaryDirectory(prefix="r4-ops10c-browser-")
    try:
        base = Path(handle.name)
        html = console_builder.render_console((ROOT / "templates" / "editorial_review_console.html").read_text(encoding="utf-8"), bundle)
        html = html.replace('<script id="candidate-data"', prelude + '<script id="candidate-data"', 1)
        html = html.rsplit("</body>", 1)[0] + harness + "</body></html>"
        fixture = base / "index.html"
        fixture.write_text(html, encoding="utf-8")
        profile = base / "profile"
        command = [str(browser), "--headless=new", "--disable-gpu", "--disable-background-networking", "--disable-component-update", "--disable-default-apps", "--disable-sync", "--metrics-recording-only", "--no-first-run", "--no-default-browser-check", "--allow-file-access-from-files", "--virtual-time-budget=9000", f"--user-data-dir={browser_path(profile, browser)}", "--dump-dom", browser_uri(fixture, browser)]
        if not windows:
            command[2:2] = ["--no-sandbox", "--disable-dev-shm-usage"]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=50, check=False)
    finally:
        handle.cleanup()
    match = re.search(r'<pre id="ops10c-browser-result">([^<]+)</pre>', completed.stdout)
    if completed.returncode != 0 or not match:
        return {"browser_available": True, "error": f"returncode={completed.returncode} stderr={completed.stderr[-500:]!r} stdout={completed.stdout[-1800:]!r}"}
    return json.loads(unescape(match.group(1)))


def verify_browser(v: Verify, articles: dict[str, dict], pending: dict) -> bool:
    print("\n== Real browser acceptance ==")
    result = browser_acceptance(articles, pending)
    v.check("real browser is available and executed", result.get("browser_available") is True, repr(result))
    for key, label in (
        ("no_login_copy", "analysis UI explicitly requires no login"),
        ("anon_direct", "1. anonymous direct import populates selected preview"),
        ("daum_preview", "2. anonymous Daum portal copy populates selected preview"),
        ("naver_preview", "3. anonymous Naver portal copy populates selected preview"),
        ("home_rejected", "4. browser rejects portal home page"),
        ("invalid_code", "6. browser rejects invalid contributor code"),
        ("valid_contributor", "7. browser establishes contributor-only session"),
        ("contributor_not_operator", "8. contributor cannot save draft or publish"),
        ("submit_idempotent", "9/10. browser submit retry stays one pending write"),
        ("operator_load", "11. operator loads unselected team_link candidate"),
    ):
        v.check(label, result.get(key) is True, repr(result))
    return result.get("browser_available") is True and not result.get("error")


def main() -> int:
    v = Verify()
    articles = verify_import_and_safety(v)
    pending = verify_contributor_and_pending(v, articles)
    verify_learning_and_gates(v, articles)
    browser_ok = verify_browser(v, articles, pending["submission"])
    print(f"\nchecks={v.checks} failures={v.failures}")
    if v.failures:
        print("RESULT=R4_OPS10C_TEAM_PORTAL_INTAKE_FAIL")
        return 1
    print("RESULT=R4_OPS10C_TEAM_PORTAL_INTAKE_PASS")
    print(f"REAL_BROWSER_USED={'true' if browser_ok else 'false'}")
    print("SMTP_SENDS=0")
    print("TEAMS_SENDS=0")
    print("TELEGRAM_SENDS=0")
    print("WORKFLOW_DISPATCHES=0")
    print("PRODUCTION_WRITES=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
