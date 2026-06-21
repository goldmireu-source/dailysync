"""라우드(loud.kr) — AI 공모전 전용 탭(/ai/contest/list).

라우드소싱 AI 공모전 플랫폼. 목록이 SPA 라 헤드리스 렌더(_render)로 긁는다.
LOUD_EMAIL / LOUD_PASSWORD 환경변수가 있으면 로그인 후 포스터 이미지 추출.
없으면 이미지 없이 텍스트 정보만 수집.
AI 공모전 전용 탭이므로 ai_exempt=True.
"""
import logging
import os
import re
import time
from datetime import date, timedelta

from bs4 import BeautifulSoup

from jobs.contest_sources.base import ContestDraft, register, clean, today_kst
from jobs.contest_sources._render import render_html

logger = logging.getLogger(__name__)

BASE       = "https://www.loud.kr"
LOGIN_URL  = "https://accounts.stunning.kr/v2/auth/login?from_url=Loud&redirect_url=https%3A%2F%2Fwww.loud.kr%2F"
LIST_URL   = f"{BASE}/ai/contest/list"
_ID_RE     = re.compile(r"/contest/view/(\d+)")
_DDAY_RE   = re.compile(r"(\d+)\s*일\s*남음")
_PERIOD_RE = re.compile(r"(\d{2})\.(\d{1,2})\.(\d{1,2})")
_OPEN_RE   = re.compile(r"\d+\s*일\s*남음")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _extract_poster(html: str) -> str | None:
    """렌더된 HTML에서 공모전 고유 포스터 이미지 URL 추출."""
    soup = BeautifulSoup(html, "lxml")
    # prod/banner/ 경로 이미지 중 프로모션 배너(banner-link) 제외한 것 우선
    for img in soup.find_all("img", src=True):
        src = img.get("src", "")
        if "cdn-dantats" not in src or "/prod/banner/" not in src:
            continue
        parent = img.parent
        # banner-link 클래스(사이트 공통 프로모션)는 제외
        if parent and "banner-link" in " ".join(parent.get("class") or []):
            continue
        src = src.split("?")[0]
        return src
    # 없으면 banner-link 포함해서 첫 번째라도 반환
    for img in soup.find_all("img", src=True):
        src = img.get("src", "")
        if "cdn-dantats" in src and "/prod/banner/" in src:
            return src.split("?")[0]
    return None


def _fetch_posters_logged_in(cids: list[str]) -> dict[str, str | None]:
    """로그인 세션 하나로 여러 공모전 상세페이지 포스터 추출."""
    email    = os.environ.get("LOUD_EMAIL", "").strip()
    password = os.environ.get("LOUD_PASSWORD", "").strip()
    if not email or not password:
        logger.info("LOUD_EMAIL/LOUD_PASSWORD 미설정 — 이미지 수집 건너뜀")
        return {cid: None for cid in cids}

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        return {cid: None for cid in cids}

    results: dict[str, str | None] = {}

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=UA)
            page = ctx.new_page()

            # 로그인
            page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)
            page.fill("input[name='email']", email)
            page.fill("input[name='password']", password)
            page.click("button:has-text('로그인')")
            try:
                page.wait_for_url("https://www.loud.kr/**", timeout=15000)
                logger.info("loud.kr 로그인 성공")
            except PWTimeout:
                logger.warning("loud.kr 로그인 실패 — 이미지 없이 진행")
                browser.close()
                return {cid: None for cid in cids}

            # 공모전별 상세페이지
            for cid in cids:
                try:
                    page.goto(f"{BASE}/contest/view/{cid}", wait_until="networkidle", timeout=25000)
                    page.wait_for_timeout(2000)
                    results[cid] = _extract_poster(page.content())
                except Exception as e:
                    logger.debug(f"loud 상세페이지 렌더 실패 {cid}: {e}")
                    results[cid] = None
                time.sleep(1.0)

            browser.close()
    except Exception as e:
        logger.warning(f"loud 로그인 세션 오류: {e}")
        return {cid: None for cid in cids}

    return results


def _is_open(a) -> bool:
    badge = a.select_one('[class*="sc-kAyceB"]')
    badge_txt = clean(badge.get_text()) if badge else clean(a.get_text(" "))
    if any(kw in badge_txt for kw in ["심사중", "종료", "발표", "접수예정"]):
        return False
    return bool(_OPEN_RE.search(badge_txt))


def _period_end(a) -> date | None:
    el = a.select_one('[class*="date"]')
    if not el:
        return None
    dates = _PERIOD_RE.findall(el.get_text(" "))
    if not dates:
        return None
    yy, mm, dd = dates[-1]
    try:
        return date(2000 + int(yy), int(mm), int(dd))
    except ValueError:
        return None


@register("loud")
def fetch() -> list[ContestDraft]:
    html = render_html(LIST_URL, wait_for="a[href*='/contest/view/']", scrolls=3)
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    drafts: list[dict] = []
    seen: set[str] = set()
    base_day = today_kst()

    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if "/contest/view/" not in href:
            continue
        m = _ID_RE.search(href)
        if not m:
            continue
        cid = m.group(1)
        if cid in seen:
            continue

        h2 = a.find("h2")
        title = clean(h2.get_text()) if h2 else ""
        if not title or len(title) < 4:
            continue
        if not _is_open(a):
            continue
        seen.add(cid)

        deadline = _period_end(a)
        if deadline is None:
            dm = _DDAY_RE.search(a.get_text(" "))
            if dm:
                deadline = base_day + timedelta(days=int(dm.group(1)))

        drafts.append({"cid": cid, "title": title, "deadline": deadline})

    if not drafts:
        return []

    # 로그인 세션으로 포스터 일괄 수집
    cids = [d["cid"] for d in drafts]
    posters = _fetch_posters_logged_in(cids)

    return [
        ContestDraft(
            source="loud",
            external_id=f"loud:{d['cid']}",
            url=f"{BASE}/contest/view/{d['cid']}",
            title=d["title"][:500],
            image_url=posters.get(d["cid"]),
            category="공모전",
            field_tags=["AI"],
            deadline=d["deadline"],
            ai_exempt=True,
        )
        for d in drafts
    ]
