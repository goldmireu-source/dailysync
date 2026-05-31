"""데이콘(dacon.io) — AI/데이터 경진대회 전문 플랫폼.

competitions 페이지의 Nuxt 임베디드 상태(window.__NUXT__)에 대회 목록이 들어있다.
공식 JSON API 가 비공개라 임베디드 상태를 정규식으로 추출(IIFE 형태라 JSON.parse 불가).
데이콘은 전 항목이 AI/데이터 대회이므로 ai_exempt=True.
"""
import logging
import re
from datetime import timedelta

import requests

from jobs.contest_sources.base import (
    ContestDraft, register, http_get, clean, today_kst, parse_date, USER_AGENT,
)

logger = logging.getLogger(__name__)

LIST_URL = "https://dacon.io/competitions"
# 대회 대표 이미지(og:image)는 cpt_id 로 결정되는 고정 패턴 — 상세페이지 통째 페치 불필요.
POSTER_URL = "https://dacon.s3.ap-northeast-2.amazonaws.com/competition/{}/meta_cpt.jpeg"


def _verify_poster(cpt_id: str) -> str | None:
    """대표 이미지 URL 을 HEAD 로 확인 — 200(이미지)일 때만 반환(없으면 fallback)."""
    url = POSTER_URL.format(cpt_id)
    try:
        r = requests.head(url, headers={"User-Agent": USER_AGENT}, timeout=8)
        if r.status_code == 200 and "image" in (r.headers.get("content-type") or ""):
            return url
    except Exception:
        pass
    return None


def _records(blob: str):
    """__NUXT__ blob 의 compData 배열에서 대회 레코드 청크들을 잘라 반환."""
    start = blob.find("compData:[")
    if start < 0:
        return []
    seg = blob[start:]
    end = seg.find("}]")
    if end > 0:
        seg = seg[: end + 2]
    return re.split(r"\{cpt_id:", seg)[1:]


@register("dacon")
def fetch() -> list[ContestDraft]:
    out: list[ContestDraft] = []
    try:
        resp = http_get(LIST_URL, encoding="utf-8")
    except Exception as e:
        logger.warning(f"dacon list fetch failed: {e}")
        return out

    m = re.search(r"window\.__NUXT__\s*=\s*(.*?)</script>", resp.text, re.DOTALL)
    if not m:
        logger.warning("dacon __NUXT__ not found")
        return out
    blob = m.group(1)
    base = today_kst()

    for ch in _records(blob):
        idm = re.match(r"(\d+)", ch)
        if not idm:
            continue
        cpt_id = idm.group(1)
        name_m = re.search(r'name:"((?:[^"\\]|\\.)*)"', ch)
        title = clean(name_m.group(1)) if name_m else None
        if not title:
            continue

        # 마감: period_end 문자열 우선, 없으면 period_dday(오늘+N)
        deadline = None
        pend = re.search(r'period_end:"([\d \-:]+)"', ch)
        if pend:
            deadline = parse_date(pend.group(1))
        if deadline is None:
            dday = re.search(r"period_dday:(-?\d+)", ch)
            if dday:
                deadline = base + timedelta(days=int(dday.group(1)))

        # 데이콘 목록엔 종료된 대회도 섞여 있음 → 마감일 확인 불가하면 skip
        if deadline is None:
            continue

        start_at = None
        pstart = re.search(r'period_start:"([\d \-:]+)"', ch)
        if pstart:
            start_at = parse_date(pstart.group(1))

        out.append(ContestDraft(
            source="dacon",
            external_id=f"dacon:{cpt_id}",
            url=f"https://dacon.io/competitions/official/{cpt_id}/overview",
            title=title,
            host="데이콘",
            image_url=_verify_poster(cpt_id),  # 대표 포스터 (HEAD 확인)
            category="경진대회",
            field_tags=["AI", "데이터"],
            start_at=start_at,
            deadline=deadline,
            ai_exempt=True,  # 데이콘 = 전 항목 AI/데이터 대회
        ))
    return out
