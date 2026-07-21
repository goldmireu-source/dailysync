"""테크블로그 소스 공용 — TechPostDraft + 레지스트리 + 헬퍼.

각 소스는 원천 파싱만 담당하고 정규화 전 값을 TechPostDraft 로 반환한다.
dedup·upsert·hot_score 계산은 jobs/techblog_collector.py 가 중앙에서 처리.

1차 소스(SOURCES)는 글 자체를 만들어내고(fetch() -> list[TechPostDraft]),
2차 소스(MENTION_SOURCES)는 기존 글에 "오늘 언급됨" 표시만 붙인다
(fetch() -> list[str], 제목 목록 — url_hash 가 아니라 제목 유사도로 매칭한다.
GeekNews 처럼 RSS가 원문 URL을 안 주고 자사 페이지 링크만 노출하는 소스가 있어서다).
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import feedparser
import requests

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
USER_AGENT = "Mozilla/5.0 (compatible; AINewsDigest/0.1; +personal-use)"
FETCH_TIMEOUT = 20

# 1차 소스 레지스트리 — (name, fetch_fn) 튜플 리스트. fetch_fn() -> list[TechPostDraft]
SOURCES: list[tuple] = []
# 2차(언급) 소스 레지스트리 — (name, fetch_fn) 튜플 리스트. fetch_fn() -> list[str] (제목 목록)
MENTION_SOURCES: list[tuple] = []


def register(name: str):
    """1차 소스 fetch 함수를 SOURCES 에 등록하는 데코레이터."""
    def deco(fn):
        SOURCES.append((name, fn))
        return fn
    return deco


def register_mention(name: str):
    """2차(언급) 소스 fetch 함수를 MENTION_SOURCES 에 등록하는 데코레이터."""
    def deco(fn):
        MENTION_SOURCES.append((name, fn))
        return fn
    return deco


@dataclass
class TechPostDraft:
    """정규화 전 원천 글 데이터. TechPost 모델 필드와 1:1."""
    blog: str
    url: str
    title: str
    description: str | None = None
    image_url: str | None = None
    published_at: datetime | None = None


def fetch_feed(rss_url: str):
    """RSS/Atom 피드 파싱. requests 로 받아 feedparser 에 넘김
    (feedparser 자체 fetcher는 SSL 인증서 문제가 있어 회피 — news_collector.py 와 동일 이유)."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    }
    resp = requests.get(rss_url, headers=headers, timeout=FETCH_TIMEOUT)
    resp.raise_for_status()
    return feedparser.parse(resp.content)


def parse_published(entry) -> datetime | None:
    """entry 의 published_parsed/updated_parsed → naive UTC datetime. 없으면 None."""
    for f in ("published_parsed", "updated_parsed"):
        ts = entry.get(f)
        if ts:
            try:
                return datetime(*ts[:6])
            except (TypeError, ValueError):
                continue
    return None


def extract_image(entry) -> str | None:
    """RSS entry 에서 대표 이미지 URL 추출 (media:content → media:thumbnail → image enclosure 순)."""
    media = entry.get("media_content") or []
    if media and media[0].get("url"):
        return media[0]["url"]
    thumb = entry.get("media_thumbnail") or []
    if thumb and thumb[0].get("url"):
        return thumb[0]["url"]
    for link in entry.get("links", []):
        if link.get("rel") == "enclosure" and (link.get("type") or "").startswith("image/"):
            return link.get("href")
    return None
