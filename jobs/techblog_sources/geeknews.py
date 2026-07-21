"""GeekNews — "오늘 언급된" 글 제목만 추출하는 2차(언급) 소스.

GeekNews RSS(news.hada.io/rss/news)는 원문 URL을 주지 않고 자사 토픽 페이지
링크만 노출한다(news.hada.io/topic?id=...) — url_hash 매칭이 불가능해 제목
유사도로 매칭한다(techblog_collector._titles_match). 토픽 페이지를 열어 원문
링크를 긁어내는 방식은 RSS 범위를 벗어난 스크래핑이라 하지 않는다.

TechPost 를 새로 만들지 않는다 — techblog_collector 가 제목이 비슷한 기존
TechPost 를 찾아 mentioned_by 에 "geeknews" 를 추가하는 데만 이 결과를 쓴다.
"""
import logging

from jobs.techblog_sources.base import register_mention, fetch_feed

logger = logging.getLogger(__name__)

FEED_URL = "https://news.hada.io/rss/news"


@register_mention("geeknews")
def fetch() -> list[str]:
    """오늘 GeekNews 에 올라온 글 제목 목록 (URL 아님 — 제목 유사도 매칭용)."""
    feed = fetch_feed(FEED_URL)
    return [t for t in (
        (entry.get("title") or "").strip() for entry in feed.entries
    ) if t]
