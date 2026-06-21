"""라우드(loud.kr) — AI 공모전 전용 탭(/ai/contest/list).

라우드소싱 AI 공모전 플랫폼. 목록이 SPA 라 헤드리스 렌더(_render)로 긁는다.
카드 렌더 HTML 에서 이미지를 우선 추출하고, 없으면 상세페이지 og:image 를 HTTP GET 으로
시도한다(Next.js SSR 메타태그는 로그인 없이 접근 가능).
AI 공모전 전용 탭이므로 ai_exempt=True. 개인/크리에이터 참여 → target 없음(통과).
"""
import logging
import re
import time
from datetime import date, timedelta

from bs4 import BeautifulSoup

from jobs.contest_sources.base import ContestDraft, register, clean, today_kst, http_get
from jobs.contest_sources._render import render_html

logger = logging.getLogger(__name__)

BASE = "https://www.loud.kr"
LIST_URL = f"{BASE}/ai/contest/list"
_ID_RE = re.compile(r"/contest/view/(\d+)")
_DDAY_RE = re.compile(r"(\d+)\s*일\s*남음")
# 카드의 접수기간 라벨: "26.05.06 ~ 26.06.05 (24시마감)" → YY.MM.DD 쌍
_PERIOD_RE = re.compile(r"(\d{2})\.(\d{1,2})\.(\d{1,2})")
# 상태 배지: '5일 남음'(접수중) | '심사중' | '종료' | '발표' | '접수예정' ...
_OPEN_RE = re.compile(r"\d+\s*일\s*남음")
DETAIL_SLEEP = 1.5


def _fetch_banner_image(cid: str) -> str | None:
    """상세페이지를 Playwright로 렌더해 배너 이미지 URL 추출.

    loud.kr 상세페이지는 SPA라 SSR og:image가 사이트 공통 썸네일만 반환함.
    렌더 후 alt='배너 이미지' img 태그에서 실제 포스터 URL을 추출한다.
    """
    from jobs.contest_sources._render import render_html
    html = render_html(f"{BASE}/contest/view/{cid}", wait_for="img[alt='배너 이미지']", scrolls=1)
    if not html:
        return None
    soup = BeautifulSoup(html, "lxml")
    img = soup.find("img", alt="배너 이미지")
    if img:
        src = img.get("src", "").strip()
        if src and src.startswith("http") and "cdn-dantats" in src:
            # CDN 사이즈 파라미터 제거해서 원본 크기로
            src = src.split("?")[0]
            return src
    return None


def _is_open(a) -> bool:
    """카드 상태 배지가 '접수중(N일 남음)'인지. 심사중·종료·발표·접수예정은 참가 불가 → False.

    배지는 카드 첫 상태 요소(styled-component 'sc-kAyceB'). 해시 클래스가 바뀔 수 있어
    텍스트 기준으로 판정: '심사중' 등 비-접수 표지가 있으면 제외, 'N일 남음'이면 접수중.
    """
    badge = a.select_one('[class*="sc-kAyceB"]')
    badge_txt = clean(badge.get_text()) if badge else clean(a.get_text(" "))
    if "심사중" in badge_txt or "종료" in badge_txt or "발표" in badge_txt or "접수예정" in badge_txt:
        return False
    return bool(_OPEN_RE.search(badge_txt))


def _period_end(a) -> date | None:
    """카드의 접수기간 라벨에서 종료일(마지막 YY.MM.DD) 추출 → date.

    상단 'N일 남음' 카운트다운은 실제 접수마감과 다를 수 있어 신뢰하지 않는다.
    """
    el = a.select_one('[class*="date"]')
    if not el:
        return None
    dates = _PERIOD_RE.findall(el.get_text(" "))
    if not dates:
        return None
    yy, mm, dd = dates[-1]  # 마지막 = 접수 종료일
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
    out: list[ContestDraft] = []
    seen: set[str] = set()
    base_day = today_kst()

    for a in soup.select("a[href*='/contest/view/']"):
        href = a.get("href", "")
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
        # 참가 가능(접수중)인 것만 — 심사중/종료/발표/접수예정 제외
        if not _is_open(a):
            continue
        seen.add(cid)

        # 마감 = 접수기간 종료일(정확) → 없으면 'N일 남음' 카운트다운(보조)
        deadline = _period_end(a)
        if deadline is None:
            dm = _DDAY_RE.search(a.get_text(" "))
            if dm:
                deadline = base_day + timedelta(days=int(dm.group(1)))

        # 이미지: 상세페이지 Playwright 렌더로 배너 이미지 추출
        # (목록 카드에는 포스터가 없고 아바타만 있음)
        image_url = _fetch_banner_image(cid)
        time.sleep(DETAIL_SLEEP)

        out.append(ContestDraft(
            source="loud",
            external_id=f"loud:{cid}",
            url=f"{BASE}/contest/view/{cid}",
            title=title[:500],
            image_url=image_url,
            category="공모전",
            field_tags=["AI"],
            deadline=deadline,
            ai_exempt=True,
        ))
    return out
