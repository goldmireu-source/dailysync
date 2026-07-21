"""테크블로그 통합 수집기.

jobs/techblog_sources/ 의 1차 소스(rss_blogs)를 순회해 TechPostDraft 를 모아
url_hash 기준 upsert 하고, 2차(언급) 소스(geeknews) 결과를 제목 유사도로 매칭해
mentioned_by 를 보강한 뒤 hot_score 를 재계산한다. 각 소스는 best-effort(한
소스 실패가 전체를 막지 않음).

README 원칙 준수: RSS·공식 API만 사용, 본문 스크래핑 없음 — 각 소스는 RSS
entry 의 title/summary/link 만 읽는다. GeekNews 도 원문 URL이 아니라 RSS
제목만 읽고, 토픽 페이지 HTML은 열지 않는다.
"""
import hashlib
import logging
import re
import unicodedata
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from models import db, TechPost
from jobs.techblog_sources import SOURCES, MENTION_SOURCES

logger = logging.getLogger(__name__)

# hot_score 공식 파라미터
_FRESH_GRACE_HOURS = 24     # 24시간 이내는 decay 없이 1.0
_FRESH_HALF_LIFE_HOURS = 48  # 이후 48시간마다 절반으로 지수 감쇠
_MENTION_WEIGHT = 2.0
_PINNED_BONUS = 5.0

# 제목 유사도 매칭 대상 범위 — 오래된 글까지 스캔하면 오탐 위험 + 비용만 커짐
_MENTION_LOOKBACK_DAYS = 7
_SIMILARITY_THRESHOLD = 0.90


def _hash_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _norm_title(t: str) -> str:
    """제목 정규화 — contest_collector._norm_title 과 동일 패턴(NFC + 공백/기호 제거 + 소문자)."""
    return re.sub(r"[\s\W_]+", "", unicodedata.normalize("NFC", t or "").lower())


def _titles_match(a: str, b: str) -> bool:
    """두 제목이 같은 글을 가리키는지 — 완전일치 / 포함관계(≥10자) / 유사도(≥12자, 0.90)."""
    na, nb = _norm_title(a), _norm_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(short) >= 10 and short in long_:
        return True
    if len(short) >= 12 and SequenceMatcher(None, na, nb).ratio() >= _SIMILARITY_THRESHOLD:
        return True
    return False


def _freshness_decay(published_at) -> float:
    """24시간 이내 1.0, 이후 48시간 반감기로 지수 감쇠. published_at 없으면 0."""
    if not published_at:
        return 0.0
    hours = (datetime.utcnow() - published_at).total_seconds() / 3600
    if hours <= _FRESH_GRACE_HOURS:
        return 1.0
    return max(0.0, 2 ** (-(hours - _FRESH_GRACE_HOURS) / _FRESH_HALF_LIFE_HOURS))


def _compute_hot_score(post: TechPost) -> float:
    score = _freshness_decay(post.published_at) + _MENTION_WEIGHT * len(post.mentioned_by or [])
    if post.pinned_featured:
        score += _PINNED_BONUS
    return round(score, 4)


def _upsert(draft) -> tuple[str, "TechPost"]:
    """draft → TechPost upsert. return ('new'|'updated'|'skip', row).

    contest_collector._upsert 와 동일 정책 — 데이터성 필드(발행일·이미지·설명)는
    비어 있을 때만 채우고, 관리자가 만질 수 있는 필드(pinned 등)는 건드리지 않는다.
    """
    url_hash = _hash_url(draft.url)
    existing = TechPost.query.filter_by(url_hash=url_hash).first()
    if existing:
        filled = False
        if draft.published_at and existing.published_at != draft.published_at:
            existing.published_at = draft.published_at
            filled = True
        if not existing.image_url and draft.image_url:
            existing.image_url = draft.image_url
            filled = True
        if not existing.description and draft.description:
            existing.description = draft.description
            filled = True
        return ("updated" if filled else "skip"), existing

    row = TechPost(
        blog=draft.blog,
        url=draft.url,
        url_hash=url_hash,
        title=draft.title[:500],
        description=draft.description,
        image_url=draft.image_url,
        published_at=draft.published_at,
        mentioned_by=[],
        summary_dirty=True,
    )
    db.session.add(row)
    return "new", row


def collect_all_techblog() -> dict:
    """전 소스 수집 → upsert → 언급 매칭 → hot_score 재계산. stats 반환."""
    stats: dict = {
        "sources": {}, "by_blog": {},
        "total_fetched": 0, "total_new": 0, "total_updated": 0, "mentions_matched": 0,
    }

    # 1. 1차 소스 수집 (best-effort) — (registry name, draft) 쌍으로 보관해야
    #    소스별 new/updated 통계를 낼 수 있다 (draft.blog 는 개별 회사, name 은 등록 모듈명).
    all_drafts: list[tuple] = []
    for name, fetch_fn in SOURCES:
        s = {"fetched": 0, "new": 0, "updated": 0, "error": None}
        try:
            drafts = fetch_fn() or []
            s["fetched"] = len(drafts)
            all_drafts.extend((name, d) for d in drafts)
        except Exception as e:
            s["error"] = str(e)[:200]
            logger.exception(f"techblog source {name} failed")
        stats["sources"][name] = s
        stats["total_fetched"] += s["fetched"]

    # 2. dedup(url) + upsert
    seen_hashes: set[str] = set()
    touched_rows: list[TechPost] = []
    for name, draft in all_drafts:
        if not draft.url or not draft.title:
            continue
        h = _hash_url(draft.url)
        if h in seen_hashes:
            continue
        seen_hashes.add(h)
        try:
            result, row = _upsert(draft)
        except Exception:
            db.session.rollback()
            logger.exception(f"techblog upsert failed: {draft.url}")
            continue
        if result == "new":
            stats["total_new"] += 1
            stats["sources"][name]["new"] += 1
            stats["by_blog"][draft.blog] = stats["by_blog"].get(draft.blog, 0) + 1
            touched_rows.append(row)
        elif result == "updated":
            stats["total_updated"] += 1
            stats["sources"][name]["updated"] += 1
            touched_rows.append(row)

    db.session.commit()

    # 3. 2차(언급) 소스 매칭 — 제목 유사도 (url_hash 매칭 불가한 이유는 모듈 docstring 참고)
    candidate_posts = None  # lazy load, MENTION_SOURCES 있을 때만 조회
    for name, fetch_fn in MENTION_SOURCES:
        s = {"fetched": 0, "matched": 0, "error": None}
        try:
            titles = fetch_fn() or []
            s["fetched"] = len(titles)
        except Exception as e:
            s["error"] = str(e)[:200]
            logger.exception(f"techblog mention source {name} failed")
            titles = []
        stats["sources"][name] = s

        if not titles:
            continue
        if candidate_posts is None:
            cutoff = datetime.utcnow() - timedelta(days=_MENTION_LOOKBACK_DAYS)
            candidate_posts = TechPost.query.filter(
                TechPost.hidden_at.is_(None),
            ).filter(
                (TechPost.published_at.is_(None)) | (TechPost.published_at >= cutoff)
            ).all()

        for mtitle in titles:
            for post in candidate_posts:
                if name in (post.mentioned_by or []):
                    continue
                if _titles_match(mtitle, post.title):
                    post.mentioned_by = (post.mentioned_by or []) + [name]
                    if post not in touched_rows:
                        touched_rows.append(post)
                    s["matched"] += 1
                    stats["mentions_matched"] += 1
                    break  # 한 GeekNews 제목당 최대 1개 TechPost 만 매칭

    db.session.commit()

    # 4. hot_score 재계산 — 이번에 안 만진 기존 글도 시간이 지나 decay 되므로 전체 재계산
    for post in TechPost.query.filter(TechPost.hidden_at.is_(None)).all():
        post.hot_score = _compute_hot_score(post)
    db.session.commit()

    return stats


if __name__ == "__main__":
    from app import create_app

    app = create_app(with_scheduler=False)
    with app.app_context():
        print(f"테크블로그 수집 — 1차 소스 {len(SOURCES)}개, 2차(언급) 소스 {len(MENTION_SOURCES)}개")
        st = collect_all_techblog()
        print(f"\n총 fetched={st['total_fetched']} new={st['total_new']} updated={st['total_updated']} "
              f"mentions_matched={st['mentions_matched']}")
        print(f"블로그별 신규: {st['by_blog']}")
        for name, s in st["sources"].items():
            err = f"  ⚠️ {s['error']}" if s.get("error") else ""
            print(f"  {name:<14} {s}{err}")
