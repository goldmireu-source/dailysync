"""키워드 기반 온디맨드 뉴스 수집 — 구글 뉴스 RSS 검색.

사용자가 입력한 키워드로 구글 뉴스 RSS(news.google.com/rss/search)를 검색해
해당 기사를 Article 로 적재한다. 이후 기존 임베딩·클러스터·요약 파이프라인을 타고
카드뉴스가 된다(jobs/pipeline.job_collect_keyword).

- RSS 검색이라 프로젝트 규칙(RSS·공식 API만)에 부합.
- AI 키워드 필터는 적용하지 않는다 — 사용자가 명시한 키워드라 그대로 신뢰.
- 합성 소스 '구글뉴스검색' 한 개를 공유(get-or-create). active=False 라
  스케줄 collect_all() 에는 안 잡힌다(가짜 rss_url 을 페치하지 않음).
- 구글 뉴스 link 는 리다이렉트 URL — 본문 트래필라투라 추출이 무의미하므로
  body_status='skipped' 로 두고 제목+요약(description)만으로 클러스터·요약한다.
"""
import logging
from datetime import datetime
from urllib.parse import quote

from models import db, Source, Article
from jobs.news_collector import _hash_url, _parse_published, _fetch_feed

logger = logging.getLogger(__name__)

KEYWORD_SOURCE_NAME = "구글뉴스검색"
KEYWORD_SOURCE_RSS = "keyword-search://google-news"  # 합성 소스 식별용(실제 페치 X)
GOOGLE_NEWS_SEARCH = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
MAX_ITEMS = 25


def _get_or_create_source() -> Source:
    src = Source.query.filter_by(rss_url=KEYWORD_SOURCE_RSS).first()
    if src is None:
        src = Source(
            name=KEYWORD_SOURCE_NAME, rss_url=KEYWORD_SOURCE_RSS, lang="ko",
            tier=1, needs_ai_filter=False, active=False,
        )
        db.session.add(src)
        db.session.commit()
    return src


def collect_keyword(keyword: str, max_items: int = MAX_ITEMS) -> dict:
    """구글 뉴스 RSS 에서 keyword 기사를 받아 Article 로 적재. stats 반환."""
    keyword = (keyword or "").strip()
    stats = {"keyword": keyword, "fetched": 0, "new": 0, "error": None}
    if not keyword:
        stats["error"] = "empty_keyword"
        return stats

    src = _get_or_create_source()
    url = GOOGLE_NEWS_SEARCH.format(q=quote(keyword))
    try:
        feed = _fetch_feed(url)
        entries = feed.entries[:max_items]
        stats["fetched"] = len(entries)
        for entry in entries:
            link = (entry.get("link") or "").strip()
            if not link:
                continue
            url_hash = _hash_url(link)
            if Article.query.filter_by(url_hash=url_hash).first():
                continue
            title = (entry.get("title") or "").strip()
            if not title:
                continue
            description = (entry.get("summary") or entry.get("description") or "").strip()[:5000]
            db.session.add(Article(
                source_id=src.id,
                url=link,
                url_hash=url_hash,
                title=title[:500],
                description=description,
                published_at=_parse_published(entry),
                is_ai_relevant=True,           # 사용자 지정 키워드 → 필터 면제
                body_status="skipped",          # 구글 리다이렉트 URL — 본문 페치 생략
            ))
            stats["new"] += 1
        src.last_fetched_at = datetime.utcnow()
        src.last_error = None
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        stats["error"] = str(e)[:300]
        logger.exception(f"keyword collect failed: {keyword}")
    return stats
