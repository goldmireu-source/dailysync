"""위비티(wevity.com) — 공모전 집계 플랫폼.

AI/빅데이터 관련 분야(cidx) 목록만 수집. 목록 페이지에 포스터 썸네일 + D-day +
제목/링크가 모두 있어 list-level 파싱으로 충분(본문 비재현).
참가대상은 목록에 없으므로 target=None → 기업한정 게이트에서 보수적으로 통과.
"""
import logging
import time

from bs4 import BeautifulSoup

from jobs.contest_sources.base import (
    ContestDraft, register, http_get, clean, parse_dday,
)

logger = logging.getLogger(__name__)

BASE = "https://www.wevity.com"
# AI/데이터 관련 분야 카테고리 (cidx). 빅데이터/데이터/AI 계열.
AI_CATEGORIES = [20, 25]
PAGES_PER_CAT = 2  # 카테고리당 최근 N페이지


def _parse_list_page(html: str, cidx: int) -> list[ContestDraft]:
    soup = BeautifulSoup(html, "lxml")
    drafts: list[ContestDraft] = []

    # 각 공고 = 제목 블록(div.hide-tit) 기준으로 묶음.
    for tit in soup.select("div.hide-tit"):
        a = tit.find("a")
        if not a or not a.get("href"):
            continue
        href = a["href"]
        title = clean(a.get_text())
        if not title:
            continue

        # ix= 추출 → external_id + 절대 URL
        ix = None
        for part in href.split("&"):
            if part.strip().startswith("ix="):
                ix = part.split("=", 1)[1].split("#")[0]
        # ix 기반 정규 permalink — 카테고리/페이지가 달라도 같은 공고면 같은 URL (dedup)
        if ix:
            url = f"{BASE}/?c=find&s=1&gbn=view&ix={ix}"
        else:
            url = href if href.startswith("http") else f"{BASE}/{href.lstrip('/')}"

        # 공고 1건 = <li> (썸네일은 li 레벨, D-day/제목은 그 안)
        container = tit.find_parent("li") or tit.find_parent("div") or tit.parent
        deadline = None
        image_url = None
        if container:
            dday_el = container.select_one(".hide-dday")
            deadline = parse_dday(dday_el.get_text()) if dday_el else None
            img = container.find("img")
            if img and img.get("src"):
                src = img["src"]
                image_url = src if src.startswith("http") else f"{BASE}/{src.lstrip('/')}"

        drafts.append(ContestDraft(
            source="wevity",
            external_id=f"wevity:{ix}" if ix else None,
            url=url,
            title=title,
            image_url=image_url,
            category="공모전",
            field_tags=[],  # 위비티 카테고리는 AI 신호로 부정확 → 중앙 AI 게이트가 제목으로 판정
            deadline=deadline,
        ))
    return drafts


@register("wevity")
def fetch() -> list[ContestDraft]:
    out: list[ContestDraft] = []
    for cidx in AI_CATEGORIES:
        for gp in range(1, PAGES_PER_CAT + 1):
            try:
                resp = http_get(
                    BASE,
                    params={"c": "find", "s": 1, "gub": 1, "cidx": cidx, "gp": gp},
                    encoding="utf-8",
                )
                out.extend(_parse_list_page(resp.text, cidx))
                time.sleep(1.0)  # rate-limit 존중
            except Exception as e:
                logger.warning(f"wevity cidx={cidx} gp={gp} failed: {e}")
    return out
