"""라우드(loud.kr) — AI 공모전 전용 탭(/ai/contest/list).

라우드소싱 AI 공모전 플랫폼. 목록이 SPA라 헤드리스 렌더(_render)로 긁는다.
LOUD_EMAIL/LOUD_PASSWORD 설정 시 로그인 세션에서 목록 파싱 + 상세 페이지의
iframe[2] 내 포스터 이미지 URL 수집. 미설정 시 비로그인 렌더(이미지 없음).
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


def _fetch_loud_data() -> tuple[str | None, dict[str, str | None]]:
    """로그인 세션 하나에서 목록 HTML + 상세 페이지 포스터 {cid: url} 반환.

    포스터는 상세 페이지의 iframe[2] 내 img.src에서 추출 (주최자 기관 외부 도메인 호스팅).
    미설정/실패 시 (None, {}) 반환.
    """
    email    = os.environ.get("LOUD_EMAIL", "").strip()
    password = os.environ.get("LOUD_PASSWORD", "").strip()
    if not email or not password:
        logger.info("LOUD_EMAIL/LOUD_PASSWORD 미설정 — 이미지 없이 수집")
        return None, {}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, {}

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})
            page = ctx.new_page()

            # 로그인
            page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)
            page.fill("input[name='email']", email)
            page.fill("input[name='password']", password)
            with page.expect_navigation(wait_until="networkidle", timeout=15000):
                page.click("button:has-text('로그인')")
            if "login" in page.url:
                logger.warning(f"loud.kr 로그인 실패 (URL: {page.url})")
                browser.close()
                return None, {}
            logger.info(f"loud.kr 로그인 성공: {page.url}")

            # 목록 페이지 렌더
            page.goto(LIST_URL, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_selector("a[href*='/contest/view/']", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(2500)
            for _ in range(3):
                page.mouse.wheel(0, 5000)
                page.wait_for_timeout(1000)
            list_html = page.content()

            # 목록에서 접수중인 cid 선추출 (상세 방문 대상)
            soup = BeautifulSoup(list_html, "lxml")
            open_cids: list[str] = []
            seen_pre: set[str] = set()
            for a in soup.find_all("a", href=True):
                m = _ID_RE.search(a.get("href", ""))
                if not m or m.group(1) in seen_pre:
                    continue
                cid = m.group(1)
                h2 = a.find("h2")
                if not h2 or len(clean(h2.get_text())) < 4:
                    continue
                if not _is_open(a):
                    continue
                seen_pre.add(cid)
                open_cids.append(cid)

            # 상세 페이지별 포스터 추출 (iframe[2] 내 img)
            posters: dict[str, str | None] = {}
            for cid in open_cids:
                try:
                    page.goto(f"{BASE}/contest/view/{cid}",
                              wait_until="domcontentloaded", timeout=25000)
                    page.wait_for_timeout(2000)
                    # 페이지 끝까지 스크롤 (모든 요소 로드)
                    for _ in range(15):
                        page.mouse.wheel(0, 800)
                        page.wait_for_timeout(300)
                    page.wait_for_timeout(1500)

                    # iframe[2] 내 모든 img.src 추출
                    poster_url = None
                    frames = page.frames
                    if len(frames) > 2:
                        try:
                            frame_imgs = frames[2].evaluate("""
                                () => {
                                    const imgs = Array.from(document.querySelectorAll('img[src]'));
                                    return imgs
                                        .map(i => i.src)
                                        .filter(s => s && !s.includes('/static/') && !s.includes('google') && !s.includes('pagead'));
                                }
                            """)
                            if frame_imgs:
                                poster_url = frame_imgs[0]
                        except Exception:
                            pass

                    posters[cid] = poster_url
                    if poster_url:
                        logger.debug(f"loud {cid} 포스터: {poster_url[:80]}")
                except Exception as e:
                    logger.debug(f"loud 상세 {cid} 렌더 실패: {e}")
                    posters[cid] = None

            browser.close()
            return list_html, posters

    except Exception as e:
        logger.warning(f"loud 수집 오류: {e}")
        return None, {}


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
    list_html, posters = _fetch_loud_data()
    # 로그인 실패/미설정 시 비로그인 렌더로 폴백(이미지 없음)
    if not list_html:
        list_html = render_html(LIST_URL, wait_for="a[href*='/contest/view/']", scrolls=3)
    if not list_html:
        return []

    soup = BeautifulSoup(list_html, "lxml")
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

        drafts.append({
            "cid": cid,
            "title": title,
            "deadline": deadline,
            "image_url": posters.get(cid) if posters else None,
        })

    if not drafts:
        return []

    with_img = sum(1 for d in drafts if d["image_url"])
    logger.info(f"loud: {len(drafts)}건 수집, 이미지 {with_img}건")

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
