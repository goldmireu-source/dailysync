"""씽굿(thinkcontest.com) — 공모전 미디어.

목록 페이지에서 ContestDetail.html?id=<id> 링크 + D-day + 썸네일을 best-effort 파싱.
페이지 구조가 바뀌면 0건 반환(fail-soft) — 플러그인 구조라 독립적으로 보수 가능.
AI 전용 목록이 아니므로 ai_exempt=False(중앙 AI 키워드 게이트가 필터).
"""
import logging
import re

from bs4 import BeautifulSoup

from jobs.contest_sources.base import (
    ContestDraft, register, http_get, clean, parse_dday,
)

logger = logging.getLogger(__name__)

BASE = "https://www.thinkcontest.com"
# 진행중(s=ing) 전체 공모전 목록. AI 필터는 중앙 게이트가 처리.
LIST_URL = f"{BASE}/Contest/ContestList.html?s=ing"
_ID_RE = re.compile(r"ContestDetail\.html\?[^\"']*id=(\d+)")
PAGES = 3  # 최대 3페이지 수집 (1페이지만 있으면 2·3페이지는 중복으로 조기 종료)


@register("thinkcontest")
def fetch() -> list[ContestDraft]:
    out: list[ContestDraft] = []
    seen: set[str] = set()

    for page in range(1, PAGES + 1):
        url_page = f"{LIST_URL}&cp={page}" if page > 1 else LIST_URL
        try:
            resp = http_get(url_page, encoding="utf-8")
            resp.encoding = resp.apparent_encoding or "utf-8"
        except Exception as e:
            logger.warning(f"thinkcontest page={page} failed: {e}")
            break

        soup = BeautifulSoup(resp.text, "lxml")
        new_count = 0
        for a in soup.find_all("a", href=_ID_RE):
            href = a.get("href", "")
            m = _ID_RE.search(href)
            if not m:
                continue
            cid = m.group(1)
            if cid in seen:
                continue
            seen.add(cid)
            new_count += 1

            title = clean(a.get_text())
            img = a.find("img")
            if not title and img:
                title = clean(img.get("alt"))
            if not title or len(title) < 4:
                continue

            container = a.find_parent(["li", "div"]) or a.parent
            deadline = None
            image_url = None
            if container:
                txt = container.get_text(" ", strip=True)
                deadline = parse_dday(txt)
                cimg = container.find("img")
                if cimg and cimg.get("src"):
                    src = cimg["src"]
                    image_url = src if src.startswith("http") else f"{BASE}/{src.lstrip('/')}"

            contest_url = href if href.startswith("http") else f"{BASE}/Contest/{href.lstrip('/')}"
            out.append(ContestDraft(
                source="thinkcontest",
                external_id=f"thinkcontest:{cid}",
                url=contest_url,
                title=title,
                image_url=image_url,
                category="공모전",
                field_tags=[],
                deadline=deadline,
            ))

        if new_count == 0:
            break  # 새 항목 없으면 다음 페이지도 불필요 (1페이지 반복 등)

    return out
