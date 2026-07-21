"""회사 기술블로그 RSS 일괄 수집 — data/techblogs.yaml 순회.

새 블로그 추가법: data/techblogs.yaml 에 name/url/lang/blog_key 항목을 추가.
RSS 유효성이 확인 안 된 소스는 active: false 로 등록해두고 여기서 건너뛴다.
"""
import logging
from pathlib import Path

import yaml

from jobs.techblog_sources.base import (
    TechPostDraft, register, fetch_feed, parse_published, extract_image,
)

logger = logging.getLogger(__name__)

YAML_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "techblogs.yaml"
_DESCRIPTION_MAX = 500


def _load_blogs() -> list[dict]:
    if not YAML_PATH.exists():
        logger.warning(f"techblogs.yaml 없음: {YAML_PATH}")
        return []
    with open(YAML_PATH, encoding="utf-8") as f:
        items = yaml.safe_load(f) or []
    return [b for b in items if b.get("active", True)]


@register("rss_blogs")
def fetch() -> list[TechPostDraft]:
    drafts: list[TechPostDraft] = []
    for blog in _load_blogs():
        blog_key = blog["blog_key"]
        try:
            feed = fetch_feed(blog["url"])
            if feed.bozo and not feed.entries:
                logger.warning(f"techblog source {blog_key} bozo + 0 entries: {feed.bozo_exception}")
                continue
            for entry in feed.entries:
                url = (entry.get("link") or "").strip()
                title = (entry.get("title") or "").strip()
                if not url or not title:
                    continue
                description = (entry.get("summary") or entry.get("description") or "").strip()[:_DESCRIPTION_MAX]
                drafts.append(TechPostDraft(
                    blog=blog_key,
                    url=url,
                    title=title,
                    description=description,
                    image_url=extract_image(entry),
                    published_at=parse_published(entry),
                ))
        except Exception:
            logger.exception(f"techblog source {blog_key} failed")
            continue
    return drafts
