"""요즘것들(allforyoung.com) — 공모전·대외활동 큐레이션.

Next.js App Router(RSC) 사이트 — 목록 데이터가 `self.__next_f` 스트리밍 페이로드에
React Query dehydrated state 로 들어있다. 별도 API 없이 HTML 에서 추출.
각 post: {id, category, title, organization, poster_url, thumbnail_url, dday, tags, is_expired}.
AI 전용 목록이 아니므로 ai_exempt=False(중앙 AI 키워드 게이트가 필터).
"""
import json
import logging
import re
import time

from jobs.contest_sources.base import (
    ContestDraft, register, http_get, clean, parse_dday,
)

logger = logging.getLogger(__name__)

LIST_URL = "https://www.allforyoung.com/posts/contest"
_CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[\d+,\s*"((?:[^"\\]|\\.)*)"\]\)')

# 상세 본문에서 참가대상 섹션을 여는 라벨(우선순위 순). GET 1회로 추출 가능(렌더 불필요).
_TARGET_LABELS = (
    "참여 대상", "참여대상", "참가 대상", "참가대상", "응모 대상", "응모대상",
    "응모자격", "참가자격", "지원자격", "모집 대상", "모집대상",
)
_TAG_RE = re.compile(r"<[^>]+>")
DETAIL_SLEEP = 0.3  # 상세 교차검증 간 rate-limit


def _fetch_target(pid) -> str | None:
    """상세 본문의 참가대상 텍스트를 추출 → 일반인 개방 판정 근거(target).

    실패/없음이면 None → 중앙 게이트가 보수적으로 통과시킴.
    """
    try:
        resp = http_get(f"https://www.allforyoung.com/posts/{pid}", encoding="utf-8")
    except Exception:
        return None
    payload = _decode_payload(resp.text)
    for lab in _TARGET_LABELS:
        i = payload.find(lab)
        if i < 0:
            continue
        seg = payload[i + len(lab): i + len(lab) + 400]
        # JSON 재진입 마커(RSC 배열/객체) 전까지만 잘라 본문 텍스트만 남김.
        for marker in ('["', '"}', '\\u', '},'):
            j = seg.find(marker)
            if j > 0:
                seg = seg[:j]
        seg = _TAG_RE.sub(" ", seg).replace("&nbsp;", " ")
        # 앞뒤 JSON/마크업 잔여물(괄호·따옴표·콜론·불릿) 제거
        seg = re.sub(r"\s+", " ", seg).strip(" -:·●▶▷[]{}\"',")
        if seg:
            return seg[:300]
    return None


def _decode_payload(html: str) -> str:
    """__next_f 청크들을 이어붙여 디코딩. 한글은 UTF-8 바이트가 \\u00XX 로
    이스케이프돼 있어 unicode_escape → latin-1 → utf-8 2단 복원."""
    raw = "".join(_CHUNK_RE.findall(html))
    if not raw:
        return ""
    try:
        step1 = raw.encode("utf-8", "replace").decode("unicode_escape", "replace")
        return step1.encode("latin-1", "replace").decode("utf-8", "replace")
    except Exception:
        return raw


def _extract_posts(payload: str) -> list:
    """페이로드에서 "success":true,"data":[...] 배열을 꺼내 파싱."""
    m = re.search(r'"success":true,"data":(\[)', payload)
    if not m:
        return []
    start = m.start(1)
    depth = 0
    for i in range(start, len(payload)):
        c = payload[i]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(payload[start:i + 1])
                except Exception as e:
                    logger.warning(f"allforyoung array parse failed: {e}")
                    return []
    return []


@register("allforyoung")
def fetch() -> list[ContestDraft]:
    out: list[ContestDraft] = []
    try:
        resp = http_get(LIST_URL, encoding="utf-8")
    except Exception as e:
        logger.warning(f"allforyoung fetch failed: {e}")
        return out

    posts = _extract_posts(_decode_payload(resp.text))
    # 참가대상 교차검증은 AI 관련 공고에만(비관련은 어차피 AI 게이트에서 탈락 → 요청 절약).
    from jobs.contest_collector import _is_ai_relevant  # 순환 임포트 회피 — 함수 내 지연 임포트

    for item in posts:
        d = item.get("data") if isinstance(item.get("data"), dict) else item
        if not isinstance(d, dict):
            continue
        if d.get("is_expired"):
            continue
        title = clean(d.get("title"))
        pid = d.get("id")
        if not title or not pid:
            continue

        tags = d.get("tags") or []
        tag_strs = [clean(t.get("name") if isinstance(t, dict) else t) for t in tags]
        host = clean(d.get("organization"))

        # AI 관련 공고면 상세에서 참가대상 확보 (일반인 개방 게이트 근거)
        target = None
        hay = " ".join(filter(None, [title, " ".join(tag_strs), host]))
        if _is_ai_relevant(hay):
            target = _fetch_target(pid)
            time.sleep(DETAIL_SLEEP)

        out.append(ContestDraft(
            source="allforyoung",
            external_id=f"allforyoung:{pid}",
            url=f"https://www.allforyoung.com/posts/{pid}",
            title=title,
            host=host,
            image_url=d.get("poster_url") or d.get("thumbnail_url"),
            category="공모전",
            field_tags=[t for t in tag_strs if t],
            target=target,
            deadline=parse_dday(d.get("dday")),
        ))
    return out
