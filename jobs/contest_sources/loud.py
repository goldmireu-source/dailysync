"""라우드(loud.kr) — AI 공모전 전용 탭(/ai/contest/list).

라우드소싱 AI 공모전 플랫폼. 목록이 SPA 라 헤드리스 렌더(_render)로 긁는다.
LOUD_EMAIL/LOUD_PASSWORD 설정 시 로그인 세션으로 목록 렌더 → 카드 CDN 썸네일 추출.
미설정 시 비로그인 렌더(이미지 없음).
AI 공모전 전용 탭이므로 ai_exempt=True.
"""
import logging
import os
import re
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


def _fetch_list_html_logged_in() -> str | None:
    """로그인 세션으로 목록 페이지 HTML 반환. 미설정/실패 시 None."""
    email = os.environ.get("LOUD_EMAIL", "").strip()
    password = os.environ.get("LOUD_PASSWORD", "").strip()
    if not email or not password:
        logger.info("LOUD_EMAIL/LOUD_PASSWORD 미설정 — 비로그인 목록 렌더로 진행")
        return None

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        return None

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=UA)
            page = ctx.new_page()

            page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)
            page.fill("input[name='email']", email)
            page.fill("input[name='password']", password)
            # 클릭과 동시에 네비게이션(리다이렉트) 대기
            with page.expect_navigation(wait_until="networkidle", timeout=15000):
                page.click("button:has-text('로그인')")
            # 리다이렉트 후 URL 확인: mypage 또는 loud.kr이면 성공
            if "login" in page.url:
                logger.warning(f"loud.kr 로그인 실패 — 비로그인 목록으로 진행 (URL: {page.url})")
                browser.close()
                return None
            logger.info(f"loud.kr 로그인 성공: {page.url}")

            page.goto(LIST_URL, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_selector("a[href*='/contest/view/']", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(2500)
            for _ in range(3):
                page.mouse.wheel(0, 5000)
                page.wait_for_timeout(1000)
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        logger.warning(f"loud 로그인 목록 렌더 실패: {e}")
        return None


def _card_image(a) -> str | None:
    """카드 내 alt='배너 이미지'인 공모전 포스터만 추출. 주최자 아바타(profile-image)는 제외."""
    img = a.find("img", alt="배너 이미지")
    if img and img.get("src") and "cdn-dantats" in img["src"]:
        return img["src"].split("?")[0]
    return None


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
    # 로그인 세션 우선(CDN 썸네일 가시), 실패/미설정 시 비로그인 렌더(이미지 없음)
    html = _fetch_list_html_logged_in()
    if not html:
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

        # 목록 카드 내부 썸네일 이미지 (상세 페이지는 공통 배너만 나옴)
        image_url = _card_image(a)

        drafts.append({"cid": cid, "title": title, "deadline": deadline, "image_url": image_url})

    if not drafts:
        return []

    return [
        ContestDraft(
            source="loud",
            external_id=f"loud:{d['cid']}",
            url=f"{BASE}/contest/view/{d['cid']}",
            title=d["title"][:500],
            image_url=d["image_url"],
            category="공모전",
            field_tags=["AI"],
            deadline=d["deadline"],
            ai_exempt=True,
        )
        for d in drafts
    ]
