"""요즘것들(allforyoung.com) — 공모전·대외활동 큐레이션.

Next.js App Router(RSC) 기반 SPA — Playwright 헤드리스 렌더링으로 콘텐츠 수집.
(구버전: self.__next_f RSC 페이로드 직접 파싱 → 2026-06 이후 HTTP GET 응답에
 페이로드 미포함으로 파싱 불가. Playwright 방식으로 교체.)

렌더 후 /posts/<숫자> 링크 패턴으로 공모전 항목 추출. 제목·D-day·주최를 파싱하고,
AI 관련 공고에 한해 상세페이지에서 참가대상 보강.
Playwright 미설치 시 이 소스만 skip(나머지 소스 정상 동작).
"""
import logging
import re
import time

from bs4 import BeautifulSoup

from jobs.contest_sources.base import (
    ContestDraft, register, http_get, clean, parse_dday,
)

logger = logging.getLogger(__name__)

LIST_URL = "https://www.allforyoung.com/posts/contest"
_POST_RE = re.compile(r"^/posts/(\d+)$")
_TARGET_LABELS = (
    "참여 대상", "참여대상", "참가 대상", "참가대상", "응모 대상", "응모대상",
    "응모자격", "참가자격", "지원자격", "모집 대상", "모집대상",
)
_TAG_RE = re.compile(r"<[^>]+>")
DETAIL_SLEEP = 0.3


def _fetch_target(pid: str) -> str | None:
    """상세 본문의 참가대상 텍스트 추출."""
    try:
        resp = http_get(f"https://www.allforyoung.com/posts/{pid}", encoding="utf-8")
    except Exception:
        return None

    # 렌더된 HTML에서 직접 파싱 시도
    soup = BeautifulSoup(resp.text, "lxml")
    text = soup.get_text(" ")

    for lab in _TARGET_LABELS:
        i = text.find(lab)
        if i < 0:
            continue
        seg = text[i + len(lab): i + len(lab) + 400].strip(" :")
        seg = re.sub(r"\s+", " ", seg).strip(" -:·●▶▷[]{}\"',")
        if seg and len(seg) > 2:
            return seg[:300]
    return None


def _parse_rendered_html(html: str) -> list[ContestDraft]:
    """Playwright 렌더링된 HTML에서 공모전 항목 추출."""
    soup = BeautifulSoup(html, "lxml")
    out: list[ContestDraft] = []
    seen: set[str] = set()

    from jobs.contest_collector import _is_ai_relevant

    for a in soup.find_all("a", href=_POST_RE):
        href = a.get("href", "")
        m = _POST_RE.match(href)
        if not m:
            continue
        pid = m.group(1)
        if pid in seen:
            continue
        seen.add(pid)

        # 제목: h2/h3/h4 우선, 없으면 img alt, 최후 수단 첫 유의미 텍스트
        title = ""
        title_el = a.find(["h2", "h3", "h4"])
        if title_el:
            title = clean(title_el.get_text())
        if not title:
            img = a.find("img")
            if img and img.get("alt") and len(img["alt"].strip()) > 5:
                title = clean(img["alt"])
        if not title:
            title = clean(a.get_text())[:100]
        if not title or len(title) < 4:
            continue

        # 주최
        card = a.find_parent() or a
        card_text = clean(card.get_text(" "))
        host = None
        # 괄호 안 주최명 패턴: [주최명] 또는 주최: OOO
        bm = re.search(r"\[([^\[\]]{2,20})\]", card_text)
        if bm:
            host = bm.group(1)

        # D-day
        deadline = parse_dday(card_text)

        # AI 관련이면 상세에서 참가대상 보강
        target = None
        hay = " ".join(filter(None, [title, host or ""]))
        if _is_ai_relevant(hay):
            target = _fetch_target(pid)
            time.sleep(DETAIL_SLEEP)

        # 이미지
        image_url = None
        img_el = card.find("img", src=True)
        if img_el:
            src = img_el.get("src", "")
            if src and not src.endswith(".svg") and "logo" not in src.lower():
                image_url = src if src.startswith("http") else f"https://www.allforyoung.com{src}"

        out.append(ContestDraft(
            source="allforyoung",
            external_id=f"allforyoung:{pid}",
            url=f"https://www.allforyoung.com/posts/{pid}",
            title=title,
            host=host,
            image_url=image_url,
            category="공모전",
            field_tags=[],
            target=target,
            deadline=deadline,
        ))

    return out


@register("allforyoung")
def fetch() -> list[ContestDraft]:
    # 1단계: Playwright 렌더링 시도 (SPA 콘텐츠 로딩 필수)
    from jobs.contest_sources._render import render_html
    html = render_html(
        LIST_URL,
        wait_for="a[href*='/posts/']",
        scrolls=3,
    )

    if html:
        out = _parse_rendered_html(html)
        if out:
            logger.info(f"allforyoung: Playwright 렌더링 {len(out)}건")
            return out
        logger.warning(
            "allforyoung: Playwright 렌더링 성공했지만 파싱 0건 "
            "— a[href*='/posts/'] 셀렉터 또는 마크업 변경 확인 필요"
        )
    else:
        logger.warning("allforyoung: Playwright 미설치 또는 렌더링 실패 — 소스 skip")

    return []
