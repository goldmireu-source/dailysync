"""위비티(wevity.com) — 공모전 집계 플랫폼.

AI/빅데이터 관련 분야(cidx) 목록을 수집하고, AI 관련 공고는 상세페이지를
교차검증해 '정확한 접수기간 + 포스터 이미지'를 확보한다.
참가대상은 목록에 없으므로 target=None → 기업한정 게이트에서 보수적으로 통과.

마크업 주의(2026-06 확인):
- 목록 1건 = <li> 안에 div.tit > a(제목/링크) / div.organ(주최) / div.day(D-day).
  헤더 행 <li class="top">의 div.tit 은 <a>가 없으므로 자연히 스킵된다.
  (과거 div.hide-tit/.hide-dday 셀렉터는 상단 추천블록만 잡아 본 목록을 통째로
  누락했었음 — 에러는 안 나서 오래 묻혔던 silent failure. fetch()에 0건 가드 추가.)
- 목록의 D-day 산술은 ±1일 오차가 있고(예: 실제 마감 07-13인데 D-43→07-14),
  목록엔 포스터 썸네일도 없다. 그래서 AI 관련 공고는 상세페이지에서
  li.dday-area('접수기간 시작 ~ 마감')와 div.thumb img(포스터)를 직접 읽어 덮어쓴다.
  → 이미 마감된 공고는 정확한 마감일이 과거라 중앙 마감 게이트가 걸러낸다.
"""
import logging
import re
import time

from bs4 import BeautifulSoup

from jobs.contest_sources.base import (
    ContestDraft, register, http_get, clean, parse_dday, parse_date,
)

logger = logging.getLogger(__name__)

BASE = "https://www.wevity.com"
# AI 공모전이 몰리는 분야 카테고리 (cidx).
#   20 = 웹/모바일/IT, 21 = 게임/소프트웨어, 22 = 과학/공학
# (구버전 [20,25]는 25=네이밍/슬로건이라 낭비였고, 정작 21·22를 빠뜨렸음 — 2026-06 교정)
AI_CATEGORIES = [20, 21, 22]
PAGES_PER_CAT = 2  # 카테고리당 최근 N페이지 (2회/일 실행이라 최신분만으로 충분)
DETAIL_SLEEP = 0.7  # 상세페이지 교차검증 간 rate-limit

# 카테고리 기반 수집을 보완하는 키워드 검색 — 아이디어·창업·교육 등 IT 외 분야에
# 분류된 AI 공모전(예: AI 퀴즈, 인공지능 아이디어 등)을 전 카테고리에서 포착한다.
AI_SEARCH_TERMS = ["AI", "인공지능", "빅데이터"]
KEYWORD_SEARCH_PAGES = 2  # 키워드당 최대 2페이지 (결과 없으면 조기 종료)


def _parse_list_page(html: str, cidx: int) -> list[ContestDraft]:
    soup = BeautifulSoup(html, "lxml")
    drafts: list[ContestDraft] = []

    # 각 공고 = 제목 블록(div.tit) 기준으로 묶음. 헤더 행(div.tit="공모전명")은
    # <a>가 없어 자연히 스킵된다.
    for tit in soup.select("div.tit"):
        a = tit.find("a")
        if not a or not a.get("href"):
            continue
        href = a["href"]
        # 제목 끝 배지(span.stat: SPECIAL/IDEA/HOT 등) 제거 후 텍스트만.
        for badge in a.select("span.stat"):
            badge.extract()
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

        # 공고 1건 = <li> (제목/주최/D-day가 그 안 형제 div)
        container = tit.find_parent("li") or tit.parent
        deadline = None
        host = None
        image_url = None
        if container:
            day_el = container.select_one("div.day")
            deadline = parse_dday(day_el.get_text()) if day_el else None
            organ_el = container.select_one("div.organ")
            host = clean(organ_el.get_text()) if organ_el else None
            # 현재 텍스트-리스트 레이아웃엔 썸네일이 없지만, 있으면 채움.
            img = container.find("img")
            if img and img.get("src"):
                src = img["src"]
                image_url = src if src.startswith("http") else f"{BASE}/{src.lstrip('/')}"

        drafts.append(ContestDraft(
            source="wevity",
            external_id=f"wevity:{ix}" if ix else None,
            url=url,
            title=title,
            host=host,
            image_url=image_url,
            category="공모전",
            field_tags=[],  # 위비티 카테고리는 AI 신호로 부정확 → 중앙 AI 게이트가 제목으로 판정
            deadline=deadline,
        ))
    return drafts


def _parse_period(text: str | None) -> tuple:
    """'접수기간 2026-05-18 ~ 2026-07-13 D-43' → (start, deadline).

    날짜 1개면 (None, 그날=마감). 못 찾으면 (None, None).
    """
    dates = re.findall(r"\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}", text or "")
    if not dates:
        return None, None
    start = parse_date(dates[0]) if len(dates) >= 2 else None
    return start, parse_date(dates[-1])


_TARGET_LABELS = ("응모대상", "참가대상", "참가자격", "응모자격", "참가조건")


def _fetch_detail(ix: str) -> tuple:
    """상세페이지에서 (start_at, deadline, image_url, target) 교차검증.

    - 접수기간: li.dday-area 의 '시작 ~ 마감' 명시 날짜 (목록 D-day보다 정확)
    - 포스터: div.thumb 안 img (배너/광고 이미지 /upload/banner/ 와 구분 — /upload/ 만 채택)
    - 참가대상: li > span.tit='응모대상' 의 값 (소속원 한정 판정 근거; 예 '제한없음')
    실패 시 (None, None, None, None).
    """
    resp = http_get(
        BASE, params={"c": "find", "s": 1, "gbn": "view", "ix": ix}, encoding="utf-8",
    )
    soup = BeautifulSoup(resp.text, "lxml")

    start_at = deadline = image_url = target = None
    li = soup.select_one("li.dday-area")
    if li:
        start_at, deadline = _parse_period(li.get_text())

    img = soup.select_one("div.contest-detail div.thumb img") or soup.select_one("div.thumb img")
    if img and img.get("src"):
        src = img["src"]
        if "/upload/" in src:  # 실제 업로드 포스터만 (placeholder/아이콘 제외)
            image_url = src if src.startswith("http") else f"{BASE}/{src.lstrip('/')}"

    # 응모대상/참가자격 — span.tit 라벨 매칭 후 li 텍스트에서 라벨 제거
    for info_li in soup.select("li"):
        tit = info_li.select_one("span.tit")
        if not tit:
            continue
        label = clean(tit.get_text())
        if label in _TARGET_LABELS:
            val = clean(info_li.get_text()).replace(label, "", 1).strip()
            if val:
                target = val[:300]
                break

    return start_at, deadline, image_url, target


@register("wevity")
def fetch() -> list[ContestDraft]:
    # 1단계: 목록 파싱 (url 기준 dedup — 같은 ix가 카테고리/페이지 걸쳐 반복).
    by_url: dict[str, ContestDraft] = {}
    empty_pages = 0
    for cidx in AI_CATEGORIES:
        for gp in range(1, PAGES_PER_CAT + 1):
            try:
                resp = http_get(
                    BASE,
                    params={"c": "find", "s": 1, "gub": 1, "cidx": cidx, "gp": gp},
                    encoding="utf-8",
                )
                page_drafts = _parse_list_page(resp.text, cidx)
                # 응답은 200인데 0건 → 마크업 변경에 의한 silent failure 신호.
                if not page_drafts and len(resp.text) > 1000:
                    empty_pages += 1
                    logger.warning(
                        f"wevity cidx={cidx} gp={gp}: 응답 {len(resp.text)}B인데 파싱 0건 "
                        f"— 목록 마크업이 바뀌었을 수 있음(셀렉터 점검 필요)"
                    )
                for d in page_drafts:
                    by_url.setdefault(d.url, d)
                time.sleep(1.0)  # rate-limit 존중
            except Exception as e:
                logger.warning(f"wevity cidx={cidx} gp={gp} failed: {e}")
    if empty_pages and not by_url:
        logger.error("wevity: 전 페이지 파싱 0건 — 파서 점검 필요(div.tit 셀렉터 확인)")

    # 2단계: 키워드 검색으로 카테고리 미분류 AI 공모전 보완
    # 아이디어·창업·교육 등 IT 외 분야에 분류된 AI 공모전을 전 카테고리에서 포착.
    for term in AI_SEARCH_TERMS:
        for gp in range(1, KEYWORD_SEARCH_PAGES + 1):
            try:
                resp = http_get(
                    BASE,
                    params={"c": "find", "s": 1, "sp": "name", "sw": term, "gbn": "viewok", "gp": gp},
                    encoding="utf-8",
                )
                kw_drafts = _parse_list_page(resp.text, cidx=0)
                if not kw_drafts:
                    break  # 빈 페이지면 조기 종료
                new_count = sum(1 for d in kw_drafts if by_url.setdefault(d.url, d) is d)
                time.sleep(1.0)
                if new_count == 0:
                    break  # 전부 기존 항목과 중복이면 추가 페이지 불필요
            except Exception as e:
                logger.warning(f"wevity keyword={term!r} gp={gp} failed: {e}")

    # 3단계: AI 관련 공고만 상세페이지 교차검증 (정확한 접수기간 + 포스터).
    # 비관련 공고는 어차피 중앙 게이트에서 탈락하므로 상세 요청을 아낀다.
    from jobs.contest_collector import _is_ai_relevant  # 순환 임포트 회피 — 함수 내 지연 임포트

    enriched = 0
    for d in by_url.values():
        ix = d.external_id.split(":", 1)[1] if (d.external_id and ":" in d.external_id) else None
        if not ix:
            continue
        hay = " ".join(filter(None, [d.title, d.host or ""]))
        if not _is_ai_relevant(hay):
            continue
        try:
            start_at, deadline, image_url, target = _fetch_detail(ix)
            if deadline:           # 명시 마감일 — 목록 D-day(±1일 오차)보다 우선
                d.deadline = deadline
            if start_at:
                d.start_at = start_at
            if image_url:          # 포스터는 상세에만 있음
                d.image_url = image_url
            if target:             # 응모대상 — 소속원 한정 게이트 근거
                d.target = target
            enriched += 1
            time.sleep(DETAIL_SLEEP)
        except Exception as e:
            logger.warning(f"wevity 상세 교차검증 실패 ix={ix}: {e}")

    logger.info(f"wevity: 카테고리+키워드 목록 {len(by_url)}건 / 상세 교차검증 {enriched}건")
    return list(by_url.values())
