"""씽유(thinkyou.co.kr) — 공모전/대외활동 큐레이션.

SSR — requests 로 HTML 파싱 가능.
robots.txt: 일반 봇 전체 허용 (GPTBot·AhrefsBot 등 SEO봇만 차단).

마크업(2026-06 확인):
  <a class="banLog" onclick="location.href='https://thinkyou.co.kr/contest/{id}'">
    <div class="thumb">
      <img class="bg_thumb" src="/upload/mainbanner/..." alt="제목"/>
    </div>
    <div class="area">
      <div class="in_area">
        <p class="day">YYYY.MM.DD ~ YYYY.MM.DD ( D-N )</p>
        <p class="title">공모전 제목</p>
      </div>
    </div>
  </a>

주의:
- serfield 파라미터는 클라이언트 측 JavaScript 필터라 HTTP 요청으로는 효과 없음
  → /contest/ 를 한 번만 요청해 전체 목록 수집, AI 게이트는 중앙에서 처리.
- 페이지네이션: ?page=N (없으면 전체 1페이지로 제공)
"""
import logging
import re

from bs4 import BeautifulSoup

from jobs.contest_sources.base import (
    ContestDraft, register, http_get, clean, parse_date, parse_dday,
)

logger = logging.getLogger(__name__)

BASE = "https://thinkyou.co.kr"
LIST_URL = f"{BASE}/contest/"

_ONCLICK_RE = re.compile(r"location\.href='(https?://[^']+/contest/(\d+)[^']*)'")
_DATE_RANGE_RE = re.compile(
    r"(\d{4}[.\-]\d{1,2}[.\-]\d{1,2})\s*~\s*(\d{4}[.\-]\d{1,2}[.\-]\d{1,2})"
)
_DDAY_RE = re.compile(r"\(\s*D-(\d+)\s*\)")


def _parse_page(html: str) -> list[ContestDraft]:
    soup = BeautifulSoup(html, "lxml")
    drafts: list[ContestDraft] = []
    seen: set[str] = set()

    for a in soup.find_all("a", class_="banLog"):
        onclick = a.get("onclick", "")
        m = _ONCLICK_RE.search(onclick)
        if not m:
            continue
        url = m.group(1)
        contest_id = m.group(2)
        if contest_id in seen:
            continue
        seen.add(contest_id)

        # 제목: p.title 우선, img alt 차선
        p_title = a.select_one("p.title")
        if p_title:
            title = clean(p_title.get_text())
        else:
            img = a.find("img", class_="bg_thumb")
            title = clean(img.get("alt", "")) if img else ""
        if not title or len(title) < 4:
            continue

        # 날짜: p.day → 'YYYY.MM.DD ~ YYYY.MM.DD ( D-N )'
        start_at = deadline = None
        p_day = a.select_one("p.day")
        if p_day:
            day_text = p_day.get_text()
            dr = _DATE_RANGE_RE.search(day_text)
            if dr:
                start_at = parse_date(dr.group(1))
                deadline = parse_date(dr.group(2))
            if not deadline:
                # D-day fallback
                dd = _DDAY_RE.search(day_text)
                if dd:
                    deadline = parse_dday(f"D-{dd.group(1)}")

        # 이미지: bg_thumb → /upload/ 경로만
        image_url = None
        img_el = a.find("img", class_="bg_thumb")
        if img_el:
            src = img_el.get("src", "")
            if "/upload/" in src:
                image_url = src if src.startswith("http") else BASE + src

        drafts.append(ContestDraft(
            source="thinkyou",
            external_id=f"thinkyou:{contest_id}",
            url=url,
            title=title,
            image_url=image_url,
            category="공모전",
            start_at=start_at,
            deadline=deadline,
        ))

    return drafts


@register("thinkyou")
def fetch() -> list[ContestDraft]:
    by_url: dict[str, ContestDraft] = {}

    try:
        resp = http_get(LIST_URL, encoding="utf-8")
        drafts = _parse_page(resp.text)
        if not drafts and len(resp.text) > 2000:
            logger.warning(
                f"thinkyou: 응답 {len(resp.text)}B인데 파싱 0건 "
                "— a.banLog onclick 패턴 점검 필요"
            )
        for d in drafts:
            by_url.setdefault(d.url, d)
    except Exception as e:
        logger.warning(f"thinkyou fetch failed: {e}")

    logger.info(f"thinkyou: {len(by_url)}건 수집")
    return list(by_url.values())
