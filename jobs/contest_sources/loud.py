"""라우드(loud.kr) — AI 공모전 전용 탭(/ai/contest/list).

라우드소싱 AI 공모전 플랫폼. 목록이 SPA 라 헤드리스 렌더(_render)로 긁는다.
목록 카드 내부 img(cdn-dantats)를 공모전 썸네일로 사용.
AI 공모전 전용 탭이므로 ai_exempt=True.
"""
import logging
import re
from datetime import date, timedelta

from bs4 import BeautifulSoup

from jobs.contest_sources.base import ContestDraft, register, clean, today_kst
from jobs.contest_sources._render import render_html

logger = logging.getLogger(__name__)

BASE       = "https://www.loud.kr"
LIST_URL   = f"{BASE}/ai/contest/list"
_ID_RE     = re.compile(r"/contest/view/(\d+)")
_DDAY_RE   = re.compile(r"(\d+)\s*일\s*남음")
_PERIOD_RE = re.compile(r"(\d{2})\.(\d{1,2})\.(\d{1,2})")
_OPEN_RE   = re.compile(r"\d+\s*일\s*남음")


def _card_image(a) -> str | None:
    """공모전 목록 카드 <a> 내부에서 cdn-dantats 썸네일 URL 추출."""
    img = a.find("img", src=re.compile(r"cdn-dantats\.stunning\.kr"))
    if img:
        # 쿼리 파라미터 유지, 크기만 카드용으로 교체 (기본값 s=40x40 → s=800x600)
        return re.sub(r"s=\d+x\d+", "s=800x600", img["src"])
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
