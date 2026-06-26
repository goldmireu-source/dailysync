"""캠퍼스픽(campuspick.com) — 대학생 대외활동·공모전 플랫폼 (브랜드: 에브리커리어).

SSR — requests 로 HTML 파싱 가능.
목록에 마감일 없음 → AI 관련 공고에 한해 상세 페이지에서 마감일·참가대상 보강.
robots.txt: /activity 경로 허용 (/download, /login 등만 차단).

마크업 주의(2026-06 확인):
- 각 항목 = <a href="/activity/view?id=N"> 안에 <h3>(제목) + 주최기관 텍스트.
- 이미지: cf-tabs-image.campuspick.com 도메인 썸네일.
- 마감일: 목록에 없음 → 상세 페이지에서 추출.
"""
import logging
import re
import time

from bs4 import BeautifulSoup

from jobs.contest_sources.base import (
    ContestDraft, register, http_get, clean, parse_date,
)

logger = logging.getLogger(__name__)

BASE = "https://www.campuspick.com"
LIST_URL = f"{BASE}/activity"
PAGES_MAX = 5
DETAIL_SLEEP = 0.5

_ID_RE = re.compile(r"/activity/view\?id=(\d+)")
_TARGET_LABELS = (
    "모집 대상", "모집대상", "참가 대상", "참가대상", "지원 자격", "지원자격",
    "참가 자격", "참가자격", "응모 대상", "응모대상", "지원 대상", "지원대상",
)
_DEADLINE_RES = [
    re.compile(r"(?:접수|신청|모집).*?~\s*(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2})"),
    re.compile(r"마감\s*[:·]?\s*(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2})"),
    re.compile(r"(\d{4}[.\-]\d{1,2}[.\-]\d{1,2})\s*(?:까지|마감)"),
    re.compile(r"(\d{4}[.\-]\d{1,2}[.\-]\d{1,2})\s*~\s*(\d{4}[.\-]\d{1,2}[.\-]\d{1,2})"),
]


def _fetch_detail(activity_id: str) -> tuple:
    """상세 페이지에서 (deadline, target) 추출. 실패 시 (None, None)."""
    try:
        resp = http_get(f"{BASE}/activity/view?id={activity_id}", encoding="utf-8")
    except Exception:
        return None, None

    soup = BeautifulSoup(resp.text, "lxml")
    text = soup.get_text(" ")

    deadline = None
    for pat in _DEADLINE_RES:
        m = pat.search(text)
        if not m:
            continue
        # 날짜쌍이면 마지막 그룹(마감일)
        date_str = m.group(len(m.groups())) if m.lastindex and m.lastindex > 1 else m.group(1)
        deadline = parse_date(date_str)
        if deadline:
            break

    target = None
    for lab in _TARGET_LABELS:
        i = text.find(lab)
        if i < 0:
            continue
        seg = text[i + len(lab): i + len(lab) + 400].strip(" :\n")
        seg = re.sub(r"\s+", " ", seg).strip()
        if seg and len(seg) > 2:
            target = seg[:300]
            break

    return deadline, target


def _parse_list_page(html: str) -> list[ContestDraft]:
    soup = BeautifulSoup(html, "lxml")
    drafts: list[ContestDraft] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=_ID_RE):
        href = a.get("href", "")
        m = _ID_RE.search(href)
        if not m:
            continue
        activity_id = m.group(1)
        if activity_id in seen:
            continue
        seen.add(activity_id)

        url = BASE + href if href.startswith("/") else href

        h3 = a.find("h3")
        if h3:
            title = clean(h3.get_text())
        else:
            title = clean(a.get_text())[:120]
        if not title or len(title) < 4:
            continue

        # 주최: h3 다음 형제 요소 또는 부모 내 나머지 텍스트
        host = None
        if h3:
            nxt = h3.find_next_sibling()
            if nxt:
                host = clean(nxt.get_text())[:80]
            elif h3.parent:
                full = clean(h3.parent.get_text(" "))
                rest = full.replace(title, "", 1).strip()
                if rest and len(rest) < 60:
                    host = rest[:80]

        # 이미지: data: URI 제외
        image_url = None
        img = a.find("img")
        if img and img.get("src") and not img["src"].startswith("data:"):
            src = img["src"]
            image_url = src if src.startswith("http") else BASE + src

        drafts.append(ContestDraft(
            source="campuspick",
            external_id=f"campuspick:{activity_id}",
            url=url,
            title=title,
            host=host,
            image_url=image_url,
            category="공모전",
        ))

    return drafts


@register("campuspick")
def fetch() -> list[ContestDraft]:
    from jobs.contest_collector import _is_ai_relevant

    by_url: dict[str, ContestDraft] = {}
    for page in range(1, PAGES_MAX + 1):
        try:
            resp = http_get(LIST_URL, params={"page": page}, encoding="utf-8")
            drafts = _parse_list_page(resp.text)
            if not drafts:
                if len(resp.text) > 2000:
                    logger.warning(
                        f"campuspick page={page}: 응답 {len(resp.text)}B인데 파싱 0건 "
                        "— 마크업 변경 확인 필요(/activity/view?id= 패턴 점검)"
                    )
                break
            added = sum(1 for d in drafts if by_url.setdefault(d.url, d) is d)
            if added == 0:
                break
            time.sleep(1.0)
        except Exception as e:
            logger.warning(f"campuspick page={page} failed: {e}")
            break

    # AI 관련 공고에 한해 상세 페이지에서 마감일·참가대상 보강
    enriched = 0
    for d in by_url.values():
        hay = " ".join(filter(None, [d.title, d.host or ""]))
        if not _is_ai_relevant(hay):
            continue
        activity_id = d.external_id.split(":", 1)[1] if (d.external_id and ":" in d.external_id) else None
        if not activity_id:
            continue
        try:
            deadline, target = _fetch_detail(activity_id)
            if deadline:
                d.deadline = deadline
            if target:
                d.target = target
            enriched += 1
            time.sleep(DETAIL_SLEEP)
        except Exception as e:
            logger.warning(f"campuspick 상세 보강 실패 id={activity_id}: {e}")

    logger.info(f"campuspick: 목록 {len(by_url)}건 / 상세 보강 {enriched}건")
    return list(by_url.values())
