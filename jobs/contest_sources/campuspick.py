"""캠퍼스픽(campuspick.com) — 대학생 대외활동·공모전 플랫폼 (브랜드: 에브리커리어).

목록은 SSR — requests 로 파싱 가능.
상세페이지는 클라이언트 렌더 SPA(Vue) — plain requests 로는 빈 셸(~9KB, 본문 없음)만
받아진다(2026-07 확인). Playwright 렌더 필수 — 안 그러면 마감일·참가대상이 항상
None 으로 남아 (1) '상시'로 잘못 표시되고 (2) 참가대상 미상 취급돼
일반인 비개방(학생 한정 등) 필터를 우회한다.
robots.txt: /activity 경로 허용 (/download, /login 등만 차단).

마크업 주의(2026-06 확인):
- 목록: 각 항목 = <a href="/activity/view?id=N"> 안에 <h3>(제목) + 주최기관 텍스트.
  이미지: cf-tabs-image.campuspick.com 도메인 썸네일.
- 상세(렌더 후): <h2>접수 기간</h2><p class="dday">D-N</p> — 연도 추정 불필요, D-day 그대로 신뢰.
  참가대상은 구조화 필드가 아니라 <article class="description"> 자유 텍스트 안에
  '활동대상' 등 라벨 + 다음 불릿(•) 줄들로 등장 — 라벨 다음 줄부터 불릿 줄이
  이어지는 동안만 추출(불릿 아닌 줄 = 다음 섹션 제목).
"""
import logging
import re
import time

from bs4 import BeautifulSoup

from jobs.contest_sources.base import (
    ContestDraft, register, http_get, clean, parse_date, parse_dday,
)
from jobs.contest_sources._render import render_html

logger = logging.getLogger(__name__)

BASE = "https://www.campuspick.com"
LIST_URL = f"{BASE}/activity"
PAGES_MAX = 5
DETAIL_SLEEP = 0.5

_ID_RE = re.compile(r"/activity/view\?id=(\d+)")
_TARGET_LABELS = (
    "모집 대상", "모집대상", "참가 대상", "참가대상", "지원 자격", "지원자격",
    "참가 자격", "참가자격", "응모 대상", "응모대상", "지원 대상", "지원대상",
    "활동 대상", "활동대상",
)
# 상세 실패 시(구 버전 markup 등) 최소한의 데이터라도 건지기 위한 텍스트 폴백 —
# 위 D-day 위젯 파싱이 우선이며, 이건 그게 없을 때만 쓰인다.
_DEADLINE_RES = [
    re.compile(r"(?:접수|신청|모집).*?~\s*(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2})"),
    re.compile(r"마감\s*[:·]?\s*(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2})"),
    re.compile(r"(\d{4}[.\-]\d{1,2}[.\-]\d{1,2})\s*(?:까지|마감)"),
    re.compile(r"(\d{4}[.\-]\d{1,2}[.\-]\d{1,2})\s*~\s*(\d{4}[.\-]\d{1,2}[.\-]\d{1,2})"),
]


def _extract_target_from_description(soup) -> str | None:
    """<article class="description"> 자유 텍스트에서 라벨 다음 내용만 추출.

    구조화 필드가 아니라 '활동대상\\n    • ...' 처럼 줄바꿈으로만 구분되고,
    인접한 <br><br>는 BeautifulSoup get_text 에서 빈 줄로 안 잡혀(공백 노드가
    없으면 구분자가 안 생김) '빈 줄까지'로는 다음 섹션과 못 가른다.
    대신 라벨 다음 줄부터 '•/·/-' 로 시작하는 불릿 줄이 이어지는 동안만 모으고,
    불릿이 아닌 줄(다음 섹션 제목)을 만나면 멈춘다."""
    article = soup.find("article", class_="description")
    if not article:
        return None
    lines = [l.strip() for l in article.get_text("\n").split("\n") if l.strip()]
    _BULLET = ("•", "·", "-", "*")
    for i, line in enumerate(lines):
        if not any(lab in line for lab in _TARGET_LABELS):
            continue
        seg_lines = []
        for l in lines[i + 1:]:
            if not l.startswith(_BULLET):
                break
            seg_lines.append(l)
        if not seg_lines and i + 1 < len(lines):
            seg_lines = [lines[i + 1]]  # 불릿 없이 바로 다음 줄에 내용인 경우
        seg = clean(" ".join(seg_lines)).lstrip("".join(_BULLET) + " ").strip()
        if seg and len(seg) > 2:
            return seg[:300]
    return None


def _fetch_detail(activity_id: str) -> tuple:
    """상세 페이지에서 (deadline, target) 추출. 실패 시 (None, None).

    캠퍼스픽 상세는 클라이언트 렌더 SPA라 plain requests 로는 빈 셸만 받힌다 —
    Playwright 로 렌더링한 HTML을 파싱해야 실제 본문이 보인다.
    """
    html = render_html(f"{BASE}/activity/view?id={activity_id}", wait_for=".dday, .description")
    if not html:
        return None, None

    soup = BeautifulSoup(html, "lxml")

    # 마감일: '접수 기간' 섹션의 D-day 뱃지 우선 — 연도 표기 없이도 정확.
    deadline = None
    for h2 in soup.find_all("h2"):
        if "접수" in h2.get_text():
            dday_el = h2.find_next_sibling("p", class_="dday")
            if dday_el:
                deadline = parse_dday(clean(dday_el.get_text()))
            break

    # 폴백: 옛 markup 등에서 D-day 뱃지를 못 찾으면 전체 텍스트에서 날짜 패턴 탐색.
    if not deadline:
        text = soup.get_text(" ")
        for pat in _DEADLINE_RES:
            m = pat.search(text)
            if not m:
                continue
            date_str = m.group(len(m.groups())) if m.lastindex and m.lastindex > 1 else m.group(1)
            deadline = parse_date(date_str)
            if deadline:
                break

    target = _extract_target_from_description(soup)

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
