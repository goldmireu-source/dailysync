"""NTIS(국가과학기술지식정보서비스) — 국가 R&D 통합공고.

공개 검색 페이지(ThSearchResultAnnouncementList.do)를 AI 키워드로 조회.
각 결과: view.do?roRndUid=<id> 링크 + 제목 + '접수 YYYY.MM.DD ~ YYYY.MM.DD'.

주의: NTIS 는 연구개발 과제공고(대학·연구자·기관 대상)가 많아 일반 공모전과 결이
다르다. AI 관련 R&D 공고를 폭넓게 담되, 중앙 게이트(AI/마감)가 필터. 포스터는 없음
(fallback 타일). 노이즈가 많다 싶으면 소스 등록만 빼면 됨.
"""
import logging
import re
import time

from bs4 import BeautifulSoup

from jobs.contest_sources.base import (
    ContestDraft, register, http_get, clean, parse_date,
)

logger = logging.getLogger(__name__)

BASE = "https://www.ntis.go.kr"
SEARCH_URL = f"{BASE}/ThSearchResultAnnouncementList.do"
SEARCH_TERMS = ["인공지능", "빅데이터", "AI"]
_UID_RE = re.compile(r"ra/view\.do\?roRndUid=(\d+)")
_DATE_RE = re.compile(r"20\d{2}[.\-]\d{1,2}[.\-]\d{1,2}")


def _parse_page(html: str) -> list[ContestDraft]:
    soup = BeautifulSoup(html, "lxml")
    drafts: list[ContestDraft] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=_UID_RE):
        href = a.get("href", "")
        m = _UID_RE.search(href)
        if not m:
            continue
        uid = m.group(1)
        if uid in seen:
            continue
        title = clean(a.get_text())
        if not title or len(title) < 4:
            continue
        seen.add(uid)

        li = a.find_parent("li") or a.find_parent("div")
        start_at = deadline = None
        if li:
            dates = _DATE_RE.findall(li.get_text(" ", strip=True))
            if dates:
                start_at = parse_date(dates[0])
                deadline = parse_date(dates[-1])

        url = href if href.startswith("http") else f"{BASE}/{href.lstrip('/')}"
        url = url.replace("http://", "https://")
        drafts.append(ContestDraft(
            source="ntis",
            external_id=f"ntis:{uid}",
            url=url,
            title=title,
            host="NTIS 국가R&D",
            category="R&D공고",
            field_tags=["R&D"],
            start_at=start_at,
            deadline=deadline,
            # 국가R&D 통합공고는 주관연구기관(기업·대학·연구소) 대상 — 개인 참여 공모전이 아님.
            # 사용자 기준(기업/기관 한정 제외)에 따라 기업 대상으로 분류 → 게이트에서 제외.
            company_targeted=True,
        ))
    return drafts


@register("ntis")
def fetch() -> list[ContestDraft]:
    out: list[ContestDraft] = []
    for term in SEARCH_TERMS:
        try:
            resp = http_get(
                SEARCH_URL,
                params={"searchWord": term, "searchSentence": "", "sort": "RANK/DESC,SS01/DESC"},
                encoding="utf-8",
            )
            out.extend(_parse_page(resp.text))
            time.sleep(1.0)
        except Exception as e:
            logger.warning(f"ntis term={term} failed: {e}")
    return out
