"""데이콘(dacon.io) — AI/데이터 경진대회 전문 플랫폼.

competitions 페이지를 BeautifulSoup으로 파싱.
(2026-06 기준: window.__NUXT__ 임베디드 방식 → SSR HTML 방식으로 변경됨)
데이콘은 전 항목이 AI/데이터 대회이므로 ai_exempt=True.
"""
import logging
import re
import time

import requests
from bs4 import BeautifulSoup

from jobs.contest_sources.base import (
    ContestDraft, register, http_get, clean, parse_date, USER_AGENT,
)

logger = logging.getLogger(__name__)

LIST_URL = "https://dacon.io/competitions"
POSTER_URL = "https://dacon.s3.ap-northeast-2.amazonaws.com/competition/{}/meta_cpt.jpeg"
_COMP_RE = re.compile(r"/competitions/official/(\d+)")
_ELIG_LABELS = ("참가자격", "참가 자격", "참가대상", "참가 대상", "응모자격", "지원자격")
# 상세페이지 '접수기간 YYYY-MM-DD ~ YYYY-MM-DD' 또는 'YYYY-MM-DD ~ YYYY-MM-DD'
_PERIOD_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})")
DETAIL_SLEEP = 0.3


def _verify_poster(cpt_id: str) -> str | None:
    """대표 이미지 URL 을 HEAD 로 확인 — 200(이미지)일 때만 반환."""
    url = POSTER_URL.format(cpt_id)
    try:
        r = requests.head(url, headers={"User-Agent": USER_AGENT}, timeout=8)
        if r.status_code == 200 and "image" in (r.headers.get("content-type") or ""):
            return url
    except Exception:
        pass
    return None


def _fetch_detail(cpt_id: str) -> tuple:
    """상세페이지에서 (start_at, deadline, eligibility) 추출.

    URL: /competitions/official/{id}/overview/description
    접수기간 'YYYY-MM-DD ~ YYYY-MM-DD' 패턴으로 시작일·마감일 추출.
    실패 시 (None, None, None).
    """
    try:
        resp = http_get(
            f"https://dacon.io/competitions/official/{cpt_id}/overview/description",
            encoding="utf-8",
        )
    except Exception:
        return None, None, None

    soup = BeautifulSoup(resp.text, "lxml")
    text = soup.get_text(" ")

    start_at = deadline = None
    # 첫 번째 날짜쌍 = 접수기간 (예선·본선 일정보다 앞에 나옴)
    m = _PERIOD_RE.search(text)
    if m:
        start_at = parse_date(m.group(1))
        deadline = parse_date(m.group(2))

    elig = None
    for hdr in soup.find_all(["h3", "h4"]):
        if not any(lab in clean(hdr.get_text()) for lab in _ELIG_LABELS):
            continue
        parts: list[str] = []
        for sib in hdr.find_next_siblings():
            if sib.name in ("h3", "h4"):
                break
            txt = clean(sib.get_text())
            if txt:
                parts.append(txt)
            if sum(len(p) for p in parts) > 200:
                break
        elig = " ".join(parts)[:300].strip()
        if elig:
            break

    return start_at, deadline, elig


@register("dacon")
def fetch() -> list[ContestDraft]:
    out: list[ContestDraft] = []
    try:
        resp = http_get(LIST_URL, encoding="utf-8")
    except Exception as e:
        logger.warning(f"dacon list fetch failed: {e}")
        return out

    soup = BeautifulSoup(resp.text, "lxml")
    seen: set[str] = set()

    for a in soup.find_all("a", href=_COMP_RE):
        href = a.get("href", "")
        m = _COMP_RE.search(href)
        if not m:
            continue
        cpt_id = m.group(1)
        if cpt_id in seen:
            continue

        card_text = a.get_text(" ", strip=True)

        # "마감" 상태 제외 (접수중·연습은 포함)
        if "마감" in card_text and "참가신청중" not in card_text:
            continue

        # 제목: h2/h3/h4 우선, 없으면 img alt, 최후 수단으로 첫 긴 텍스트
        title = ""
        title_el = a.find(["h2", "h3", "h4"])
        if title_el:
            title = clean(title_el.get_text())
        if not title:
            img = a.find("img")
            if img and img.get("alt") and len(img["alt"].strip()) > 5:
                title = clean(img["alt"])
        if not title or len(title) < 4:
            continue

        seen.add(cpt_id)

        # 상세페이지: 마감일 + 참가자격
        start_at, deadline, elig = _fetch_detail(cpt_id)
        time.sleep(DETAIL_SLEEP)

        if deadline is None:
            continue  # 마감일 불명 = 종료 대회 가능성 → skip

        # 파이프 구분 카테고리 태그 (카드 텍스트에서 추출)
        tags = [t.strip() for t in card_text.split("|") if 2 <= len(t.strip()) <= 20][:3]

        out.append(ContestDraft(
            source="dacon",
            external_id=f"dacon:{cpt_id}",
            url=f"https://dacon.io/competitions/official/{cpt_id}/overview",
            title=title,
            host="데이콘",
            image_url=_verify_poster(cpt_id),
            category="경진대회",
            field_tags=["AI", "데이터"] + tags,
            target=elig,
            start_at=start_at,
            deadline=deadline,
            ai_exempt=True,
        ))

    if not out and len(resp.text) > 5000:
        logger.warning(
            f"dacon: 응답 {len(resp.text)}B인데 파싱 0건 — "
            f"마크업 변경 가능성 (_COMP_RE 셀렉터 점검 필요)"
        )

    return out
