"""AI 논문 수집기.

소스별 수집:
1. arXiv API        — cs.AI·cs.LG·cs.CL·cs.CV·cs.RO·cs.NE·stat.ML (요청 간 3초)
2. HuggingFace Daily — 큐레이션된 오늘의 픽 (hf_featured 마킹)
3. Papers With Code — 코드 공개 트렌딩 논문 (arxiv_id 기준 중복 제거)
4. Semantic Scholar — 최신 AI/ML 논문 키워드 검색 (arXiv ID 기준 중복 제거)

PAPER_RECENT_DAYS (기본 3일) 윈도우 외 논문은 skip.
"""
import logging
import re
import time
from datetime import datetime, timedelta

import feedparser
import requests

from app import create_app
from config import Config
from models import db, Paper

logger = logging.getLogger(__name__)

ARXIV_API = "http://export.arxiv.org/api/query"
ARXIV_CATEGORIES = ["cs.AI", "cs.LG", "cs.CL", "cs.CV", "cs.RO", "cs.NE", "stat.ML"]
ARXIV_RATE_LIMIT = 3.0
HF_DAILY_API          = "https://huggingface.co/api/daily_papers"
PAPERS_WITH_CODE_API  = "https://paperswithcode.com/api/v1/papers/"
SEMANTIC_SCHOLAR_API  = "https://api.semanticscholar.org/graph/v1/paper/search"

# Semantic Scholar: 수집할 AI/ML 핵심 쿼리 목록
_SS_QUERIES = [
    "large language model",
    "diffusion model generative",
    "multimodal vision language model",
    "reinforcement learning agent",
]


def _extract_arxiv_id(url_or_id: str) -> str | None:
    m = re.search(r"(\d{4}\.\d{4,5})", url_or_id)
    return m.group(1) if m else None


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _cutoff() -> datetime:
    return datetime.utcnow() - timedelta(days=Config.PAPER_RECENT_DAYS)


# ---------- arXiv ----------
def fetch_arxiv_recent(max_per_category: int = 30) -> dict:
    stats = {"fetched": 0, "new": 0, "old_skipped": 0, "errors": []}
    cutoff = _cutoff()

    for cat in ARXIV_CATEGORIES:
        try:
            params = {
                "search_query": f"cat:{cat}",
                "start": 0,
                "max_results": max_per_category,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            }
            resp = requests.get(ARXIV_API, params=params, timeout=30)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)

            for entry in feed.entries:
                stats["fetched"] += 1
                arxiv_id = _extract_arxiv_id(entry.get("id", ""))
                if not arxiv_id:
                    continue

                try:
                    pub = datetime(*entry.published_parsed[:6])
                except (AttributeError, TypeError):
                    pub = datetime.utcnow()

                if pub < cutoff:
                    stats["old_skipped"] += 1
                    continue

                if Paper.query.filter_by(arxiv_id=arxiv_id).first():
                    continue

                authors = [a.get("name", "") for a in entry.get("authors", [])]
                cats = [t.get("term", "") for t in entry.get("tags", []) if t.get("term")]
                pdf_url = next(
                    (l.href for l in entry.get("links", []) if l.get("type") == "application/pdf"),
                    f"https://arxiv.org/pdf/{arxiv_id}.pdf",
                )

                db.session.add(Paper(
                    arxiv_id=arxiv_id,
                    source_type="arxiv",
                    title=_clean_text(entry.title)[:500],
                    authors=authors,
                    abstract=_clean_text(entry.summary),
                    categories=cats,
                    published_at=pub,
                    html_url=entry.get("link", f"https://arxiv.org/abs/{arxiv_id}"),
                    pdf_url=pdf_url,
                ))
                stats["new"] += 1

            db.session.commit()
            time.sleep(ARXIV_RATE_LIMIT)

        except Exception as e:
            db.session.rollback()
            stats["errors"].append(f"{cat}: {str(e)[:120]}")
            logger.exception(f"arxiv fetch failed for {cat}")

    return stats


# ---------- Hugging Face Daily Papers ----------
def fetch_huggingface_daily() -> dict:
    stats = {"fetched": 0, "marked": 0, "new": 0, "old_skipped": 0, "error": None}
    cutoff = _cutoff()

    try:
        resp = requests.get(HF_DAILY_API, timeout=20)
        resp.raise_for_status()
        items = resp.json()

        for item in items:
            stats["fetched"] += 1
            paper_info = item.get("paper") or {}
            arxiv_id = paper_info.get("id") or item.get("id")
            if not arxiv_id:
                continue

            upvotes = paper_info.get("upvotes", 0) or item.get("upvotes", 0)

            published_raw = paper_info.get("publishedAt") or item.get("publishedAt") or ""
            try:
                pub = datetime.fromisoformat(published_raw.replace("Z", "+00:00")).replace(tzinfo=None)
            except (ValueError, AttributeError):
                pub = datetime.utcnow()

            existing = Paper.query.filter_by(arxiv_id=arxiv_id).first()
            if existing:
                # HF 마킹은 윈도우와 무관하게 갱신 (이미 DB에 있는 논문)
                existing.hf_featured = True
                existing.hf_upvotes = max(existing.hf_upvotes or 0, upvotes)
                existing.summary_dirty = True
                stats["marked"] += 1
                continue

            if pub < cutoff:
                stats["old_skipped"] += 1
                continue

            authors = [
                a.get("name", "") if isinstance(a, dict) else str(a)
                for a in (paper_info.get("authors") or [])
            ]

            db.session.add(Paper(
                arxiv_id=arxiv_id,
                source_type="huggingface",
                title=_clean_text(paper_info.get("title", ""))[:500],
                authors=authors,
                abstract=_clean_text(paper_info.get("summary", "")),
                categories=[],
                published_at=pub,
                html_url=f"https://huggingface.co/papers/{arxiv_id}",
                pdf_url=f"https://arxiv.org/pdf/{arxiv_id}.pdf",
                hf_featured=True,
                hf_upvotes=upvotes,
            ))
            stats["new"] += 1

        db.session.commit()

    except Exception as e:
        db.session.rollback()
        stats["error"] = str(e)[:200]
        logger.exception(f"HF daily fetch failed: {e}")

    return stats


# ---------- Papers With Code ----------
def fetch_papers_with_code(page_size: int = 50) -> dict:
    """Papers With Code: 코드 공개 최신 논문 수집."""
    stats = {"fetched": 0, "new": 0, "old_skipped": 0, "error": None}
    cutoff = _cutoff()

    try:
        resp = requests.get(
            PAPERS_WITH_CODE_API,
            params={"ordering": "-submission_date", "page_size": page_size},
            timeout=20,
        )
        resp.raise_for_status()
        items = resp.json().get("results", [])

        for item in items:
            stats["fetched"] += 1
            arxiv_id = item.get("arxiv_id")
            if not arxiv_id:
                continue

            try:
                pub = datetime.fromisoformat(item.get("published", ""))
            except (ValueError, AttributeError):
                pub = datetime.utcnow()

            if pub < cutoff:
                stats["old_skipped"] += 1
                continue

            if Paper.query.filter_by(arxiv_id=arxiv_id).first():
                continue

            raw_authors = item.get("authors") or []
            authors = [
                a.get("name", "") if isinstance(a, dict) else str(a)
                for a in raw_authors
            ]

            db.session.add(Paper(
                arxiv_id=arxiv_id,
                source_type="paperswithcode",
                title=_clean_text(item.get("title", ""))[:500],
                authors=authors,
                abstract=_clean_text(item.get("abstract") or ""),
                categories=[],
                published_at=pub,
                html_url=item.get("url_abs") or f"https://arxiv.org/abs/{arxiv_id}",
                pdf_url=item.get("url_pdf") or f"https://arxiv.org/pdf/{arxiv_id}.pdf",
            ))
            stats["new"] += 1

        db.session.commit()

    except Exception as e:
        db.session.rollback()
        stats["error"] = str(e)[:200]
        logger.exception("Papers With Code fetch failed")

    return stats


# ---------- Semantic Scholar ----------
def fetch_semantic_scholar_ai(per_query: int = 20) -> dict:
    """Semantic Scholar: 핵심 AI/ML 키워드로 최신 논문 검색."""
    stats = {"fetched": 0, "new": 0, "old_skipped": 0, "errors": []}
    cutoff = _cutoff()
    seen: set[str] = set()

    for query in _SS_QUERIES:
        try:
            resp = requests.get(
                SEMANTIC_SCHOLAR_API,
                params={
                    "query": query,
                    "fields": "paperId,externalIds,title,abstract,authors,publicationDate",
                    "fieldsOfStudy": "Computer Science",
                    "limit": per_query,
                },
                headers={"User-Agent": "ai-news-digest/1.0"},
                timeout=20,
            )
            resp.raise_for_status()
            items = resp.json().get("data", [])

            for item in items:
                stats["fetched"] += 1
                arxiv_id = (item.get("externalIds") or {}).get("ArXiv")
                if not arxiv_id or arxiv_id in seen:
                    continue
                seen.add(arxiv_id)

                pub_raw = item.get("publicationDate") or ""
                try:
                    pub = datetime.fromisoformat(pub_raw)
                except (ValueError, AttributeError):
                    pub = datetime.utcnow()

                if pub < cutoff:
                    stats["old_skipped"] += 1
                    continue

                if Paper.query.filter_by(arxiv_id=arxiv_id).first():
                    continue

                authors = [a.get("name", "") for a in (item.get("authors") or [])]

                db.session.add(Paper(
                    arxiv_id=arxiv_id,
                    source_type="semanticscholar",
                    title=_clean_text(item.get("title", ""))[:500],
                    authors=authors,
                    abstract=_clean_text(item.get("abstract") or ""),
                    categories=[],
                    published_at=pub,
                    html_url=f"https://arxiv.org/abs/{arxiv_id}",
                    pdf_url=f"https://arxiv.org/pdf/{arxiv_id}.pdf",
                ))
                stats["new"] += 1

            db.session.commit()
            time.sleep(1.0)

        except Exception as e:
            db.session.rollback()
            stats["errors"].append(f"{query}: {str(e)[:100]}")
            logger.exception(f"Semantic Scholar fetch failed (query={query!r})")

    return stats


def collect_all_papers() -> dict:
    return {
        "arxiv":           fetch_arxiv_recent(max_per_category=30),
        "huggingface":     fetch_huggingface_daily(),
        "paperswithcode":  fetch_papers_with_code(),
        "semanticscholar": fetch_semantic_scholar_ai(),
    }


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        print(f"논문 수집 윈도우: 최근 {Config.PAPER_RECENT_DAYS}일")
        results = collect_all_papers()

        a = results["arxiv"]
        print(f"\narXiv ({len(ARXIV_CATEGORIES)}개 카테고리): "
              f"fetched={a['fetched']} new={a['new']} "
              f"old_skipped={a['old_skipped']} errors={len(a['errors'])}")
        for err in a["errors"]:
            print(f"  ⚠️ {err}")

        h = results["huggingface"]
        print(f"HF Daily:           fetched={h['fetched']} "
              f"marked={h.get('marked', 0)} new={h.get('new', 0)} "
              f"old_skipped={h.get('old_skipped', 0)}"
              + (f"  ⚠️ {h['error']}" if h.get("error") else ""))

        p = results["paperswithcode"]
        print(f"Papers With Code:   fetched={p['fetched']} new={p['new']} "
              f"old_skipped={p['old_skipped']}"
              + (f"  ⚠️ {p['error']}" if p.get("error") else ""))

        s = results["semanticscholar"]
        print(f"Semantic Scholar:   fetched={s['fetched']} new={s['new']} "
              f"old_skipped={s['old_skipped']} errors={len(s['errors'])}")
        for err in s["errors"]:
            print(f"  ⚠️ {err}")
