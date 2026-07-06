"""콘테스트코리아(contestkorea.com) — 공모전/대외활동 포털.

SSR — requests 로 HTML 직접 파싱 가능.
목록 페이지에 제목·주최·대상·접수기간이 포함되어 있어 상세 페이지 별도 요청 불필요.
robots.txt: Allow: / (전체 허용)

마크업(2026-06 확인):
  <li>
    <div class="title">
      <a href="view.php?...&str_no=YYYYMMDDNNNN">
        <span class="category">분야명</span>
        <span class="txt">공모전 제목</span>
      </a>
    </div>
    <ul class="host">
      <li class="icon_1"><strong>주최</strong>. 기관명</li>
      <li class="icon_2"><strong>대상</strong>. 참가대상 ▶</li>
    </ul>
    <div class="date">
      <span class="step-1"><em>접수</em>MM.DD~MM.DD</span>
      ...
    </div>
    <div class="d-day ..."><span class="day">D-38</span></div>
  </li>

수집 전략(2026-06 확인):
  - list.php?kind=con                    : 일반 공모전 목록 (디자인·글·사진 등)
  - list.php?kind=con&Txt_bcode=030310001: 학문·과학·IT 카테고리
    → 해커톤·IT·데이터 경진대회가 이 카테고리에만 존재하며 일반 목록에는 미노출.
    두 경로를 모두 수집 후 URL 기준 중복 제거.
"""
import logging
import re
import time
from datetime import date as date_t

from bs4 import BeautifulSoup

from jobs.contest_sources.base import (
    ContestDraft, register, http_get, clean, parse_dday, today_kst,
)

logger = logging.getLogger(__name__)

BASE = "https://www.contestkorea.com"
LIST_URL = f"{BASE}/sub/list.php"
PAGES_MAX = 5

# 일반 목록(kind=con)에 포함되지 않는 IT·학문·과학 카테고리 코드.
# 해커톤·데이터 경진대회 등이 여기에만 노출된다.
_IT_BCODE = "030310001"

_STR_NO_RE = re.compile(r"str_no=(\w+)")
_MMDD_RANGE_RE = re.compile(r"(\d{1,2})[.\-](\d{1,2})\s*~\s*(\d{1,2})[.\-](\d{1,2})")
# 상세페이지 '접수기간' 표는 연도가 명시돼 있어(YYYY.MM.DD ~ YYYY.MM.DD) 목록 페이지의
# 연도 추정(MM.DD만) 보다 정확 — 목록에서 마감일 추출 실패 시 폴백으로 사용.
_FULL_DATE_RANGE_RE = re.compile(
    r"(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})\s*~\s*(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})"
)

# 해커톤 AI 검증 — 제목에 'AI' 없는 해커톤은 상세 페이지 본문으로 재확인.
# 이유: '해커톤' 단어 자체는 AI 신호가 아님(요리/디자인 해커톤 등 존재).
_HACKATHON_RE = re.compile(r"해커톤|hackathon", re.IGNORECASE)
# 페이지 전문에서 AI 신호를 찾는 정규식.
# 'ai'는 gmail·email·available·trail 등 영어 단어 안에 포함될 수 있어
# 라틴 문자에 둘러싸인 경우는 매칭 제외 — (?<![a-zA-Z])ai(?![a-zA-Z]).
# 한국어 맥락의 'AI기반', 'AI를' 등은 라틴 문자가 아닌 한글이 인접하므로 정상 매칭.
_QUICK_AI_RE = re.compile(
    r"(?<![a-zA-Z])ai(?![a-zA-Z])|인공지능|머신러닝|딥러닝|llm|gpt|빅데이터|챗봇|생성형|자연어|컴퓨터비전"
    r"|데이터\s*분석|데이터\s*활용|데이터\s*사이언스|데이터\s*경진|데이터\s*해커톤",
    re.IGNORECASE,
)


def _mmdd_to_date(month: str, day: str) -> date_t | None:
    """MM, DD → 올해 date.

    이 폴백은 d-day 위젯이 없을 때만 쓰이는데, 실제로는 '접수중/접수예정' 항목엔
    거의 항상 d-day 위젯이 있어 여기 걸리는 건 대개 이미 접수가 종료된 항목이다.
    과거 날짜라고 내년으로 미루면(예전 로직) 이미 끝난 공모전이 D-360 같은
    가짜 미래 마감일을 갖게 되어 마감 정리(cleanup)가 영원히 안 걸린다.
    그대로 반환해 실제 마감일로 남기고, 정리는 cleanup 잡이 처리하게 둔다."""
    today = today_kst()
    try:
        return date_t(today.year, int(month), int(day))
    except ValueError:
        return None


def _text_after_strong(li_el) -> str:
    """<strong> 다음 텍스트 노드 반환, 선행 '. ' 제거."""
    strong = li_el.find("strong")
    if not strong:
        return ""
    parts = []
    for node in strong.next_siblings:
        if hasattr(node, "get_text"):
            parts.append(node.get_text())
        else:
            parts.append(str(node))
    raw = " ".join(parts)
    raw = re.sub(r"^\s*\.\s*", "", raw)  # 선행 '. ' 제거
    return clean(raw)


def _parse_list_page(html: str) -> list[ContestDraft]:
    soup = BeautifulSoup(html, "lxml")
    drafts: list[ContestDraft] = []

    for a in soup.find_all("a", href=_STR_NO_RE):
        # 목록 항목 = <li><div class="title"><a>...</a></div></li>. 같은 페이지의
        # '인기 대회·공모전' 랭킹 사이드바(<ol id="cate03"><li><a>1. 제목</a></li>)도
        # str_no= 패턴을 공유하지만 div.title 래핑이 없고 순번("1. ")이 텍스트에
        # 그대로 섞여 나온다 — div.title 조상 없으면 스킵해 오염 방지.
        if not a.find_parent("div", class_="title"):
            continue

        href = a.get("href", "")
        m = _STR_NO_RE.search(href)
        str_no = m.group(1) if m else None

        if href.startswith("http"):
            url = href
        elif href.startswith("/"):
            url = BASE + href
        else:
            url = f"{BASE}/sub/{href}"

        # 제목: span.txt 우선, 없으면 category span 제거 후 전체 텍스트
        span_txt = a.find("span", class_="txt")
        if span_txt:
            title = clean(span_txt.get_text())
        else:
            for badge in a.find_all("span"):
                badge.decompose()
            title = clean(a.get_text())
        if not title or len(title) < 3:
            continue

        li = a.find_parent("li")
        if not li:
            continue

        # 주최: ul.host li.icon_1 > strong 뒤 텍스트
        host = None
        icon1 = li.select_one("ul.host li.icon_1")
        if icon1:
            host = _text_after_strong(icon1)[:100] or None

        # 대상: ul.host li.icon_2 > strong 뒤 텍스트 ('▶' 이후 제거)
        target = None
        icon2 = li.select_one("ul.host li.icon_2")
        if icon2:
            val = _text_after_strong(icon2).split("▶")[0].strip()
            target = val[:300] if val else None

        # D-day: div.d-day span.day → 'D-38' 형식 (가장 신뢰성 높음)
        deadline = None
        day_span = li.select_one("div.d-day span.day")
        if day_span:
            deadline = parse_dday(clean(day_span.get_text()))

        # 접수기간 MM.DD~MM.DD → 연도 보정 (D-day fallback)
        if not deadline:
            step1 = li.select_one("span.step-1")
            if step1:
                em = step1.find("em")
                if em:
                    em.extract()
                md = _MMDD_RANGE_RE.search(step1.get_text())
                if md:
                    deadline = _mmdd_to_date(md.group(3), md.group(4))

        # 이미지는 목록 페이지에서 수집 불가 — li 내 <img>는 뱃지·공유배너이며
        # 개별 포스터가 아님. 상세 페이지 og:image는 fetch()의 step 4 에서 일괄 처리.
        drafts.append(ContestDraft(
            source="contestkorea",
            external_id=f"contestkorea:{str_no}" if str_no else None,
            url=url,
            title=title,
            host=host,
            category="공모전",
            target=target,
            deadline=deadline,
        ))

    return drafts


def _has_quick_ai(text: str) -> bool:
    return bool(_QUICK_AI_RE.search(text or ""))


def _extract_og_image(soup) -> str | None:
    """BeautifulSoup 객체에서 OG 이미지 URL 추출."""
    meta = soup.find("meta", property="og:image")
    if meta:
        content = meta.get("content", "").strip()
        if content and not content.endswith(".svg"):
            return content
    return None


def _extract_deadline(soup) -> date_t | None:
    """상세페이지 '접수기간' 표에서 마감일(종료일) 추출 — 연도 명시라 정확."""
    for th in soup.find_all("th"):
        if "접수기간" in th.get_text():
            td = th.find_next_sibling("td")
            if not td:
                continue
            m = _FULL_DATE_RANGE_RE.search(td.get_text())
            if m:
                try:
                    return date_t(int(m.group(4)), int(m.group(5)), int(m.group(6)))
                except ValueError:
                    return None
    return None


def _fetch_detail_page(url: str) -> tuple[bool | None, str | None, date_t | None]:
    """상세 페이지를 한 번 요청해 (AI여부, og:image URL, 마감일) 반환.

    AI 판정이 불필요한 경우엔 첫 번째 값을 None 으로 해석해도 무방.
    네트워크 실패 시 (None, None, None) 반환.
    """
    try:
        resp = http_get(url, encoding="utf-8")
        soup = BeautifulSoup(resp.text, "lxml")
        image_url = _extract_og_image(soup)
        deadline = _extract_deadline(soup)
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        return _has_quick_ai(text), image_url, deadline
    except Exception as e:
        logger.debug(f"contestkorea detail 조회 실패: {url}: {e}")
        return None, None, None


def _verify_hackathon_ai(url: str) -> tuple[bool, str | None]:
    """해커톤 상세 페이지 AI 검증. (is_ai, og_image_url) 반환.

    실패 시 (False, None) — 검증 불가 = 수집 안 함(보수적).
    """
    is_ai, image_url, _deadline = _fetch_detail_page(url)
    return bool(is_ai), image_url


def _fetch_pages(params_base: dict, label: str, by_url: dict) -> None:
    """params_base 기준으로 PAGES_MAX 페이지까지 수집해 by_url 에 추가."""
    for page in range(1, PAGES_MAX + 1):
        try:
            resp = http_get(LIST_URL, params={**params_base, "page": page}, encoding="utf-8")
            drafts = _parse_list_page(resp.text)
            if not drafts:
                if len(resp.text) > 2000:
                    logger.warning(
                        f"contestkorea {label} page={page}: 응답 {len(resp.text)}B인데 파싱 0건 "
                        "— 마크업 변경 확인 필요(str_no= / ul.host 셀렉터 점검)"
                    )
                break
            added = sum(1 for d in drafts if by_url.setdefault(d.url, d) is d)
            if added == 0:
                break  # 전부 중복 → 조기 종료
            time.sleep(1.0)
        except Exception as e:
            logger.warning(f"contestkorea {label} page={page} failed: {e}")
            break


@register("contestkorea")
def fetch() -> list[ContestDraft]:
    by_url: dict[str, ContestDraft] = {}

    # 1) 일반 공모전 목록
    _fetch_pages({"kind": "con"}, "general", by_url)

    # 2) 학문·과학·IT 카테고리 — 해커톤·데이터 경진대회가 여기에만 노출
    _fetch_pages({"kind": "con", "Txt_bcode": _IT_BCODE}, "IT", by_url)

    # 3) 해커톤 AI 검증: 제목/주최에 AI 신호가 없는 해커톤은 상세 페이지를 확인.
    #    AI 무관으로 판정되면 제외 — 요리·디자인 해커톤 등 오수집 방지.
    #    상세 페이지 조회 시 og:image도 함께 수집.
    result: dict[str, ContestDraft] = {}
    for url, d in by_url.items():
        if _HACKATHON_RE.search(d.title or ""):
            basic_signal = _has_quick_ai(d.title or "") or _has_quick_ai(d.host or "")
            if basic_signal:
                result[url] = d
            else:
                is_ai, og_img = _verify_hackathon_ai(url)
                if is_ai:
                    # 상세 페이지에서 AI 확인 → field_tags 에 신호 추가해 gate 1 통과
                    d.field_tags = ["인공지능"]
                    if og_img and not d.image_url:
                        d.image_url = og_img
                    result[url] = d
                else:
                    logger.info(f"contestkorea: 해커톤 AI 무관 제외 — {d.title!r}")
                time.sleep(0.5)
        else:
            result[url] = d

    # 4) 목록 페이지에서 이미지/마감일을 못 긁은 항목 → 상세 페이지로 보완.
    #    마감일은 상세 '접수기간' 표에 연도까지 명시돼 있어 목록의 d-day 파싱이
    #    실패해도(신규 등록 직후 위젯 누락 등) 여기서 정확히 복구된다.
    for url, d in result.items():
        if not d.image_url or not d.deadline:
            _, og_img, detail_deadline = _fetch_detail_page(url)
            if og_img and not d.image_url:
                d.image_url = og_img
            if detail_deadline and not d.deadline:
                d.deadline = detail_deadline
            time.sleep(0.3)

    logger.info(f"contestkorea: {len(result)}건 수집 (해커톤 미검증 제외 포함)")
    return list(result.values())
