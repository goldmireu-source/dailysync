"""요즘것들(allforyoung.com) — 공모전·대외활동 큐레이션.

Next.js App Router(RSC) 사이트 — 목록 데이터가 `self.__next_f` 스트리밍 페이로드에
React Query dehydrated state 로 들어있다. 별도 API 없이 HTML 에서 추출.
각 post: {id, category, title, organization, poster_url, thumbnail_url, dday, tags, is_expired}.
AI 전용 목록이 아니므로 ai_exempt=False(중앙 AI 키워드 게이트가 필터).
"""
import json
import logging
import re

from jobs.contest_sources.base import (
    ContestDraft, register, http_get, clean, parse_dday,
)

logger = logging.getLogger(__name__)

LIST_URL = "https://www.allforyoung.com/posts/contest"
_CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[\d+,\s*"((?:[^"\\]|\\.)*)"\]\)')


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

        out.append(ContestDraft(
            source="allforyoung",
            external_id=f"allforyoung:{pid}",
            url=f"https://www.allforyoung.com/posts/{pid}",
            title=title,
            host=clean(d.get("organization")),
            image_url=d.get("poster_url") or d.get("thumbnail_url"),
            category="공모전",
            field_tags=[t for t in tag_strs if t],
            deadline=parse_dday(d.get("dday")),
        ))
    return out
