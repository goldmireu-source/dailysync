"""임베딩 + 뉴스 클러스터링.

흐름:
  1. embed_articles()  — 미임베딩 Article 의 임베딩 생성 (BGE-M3 로컬)
  2. embed_papers()    — 미임베딩 Paper 의 임베딩 생성
  3. cluster_articles()— 클러스터 미할당 Article 을 기존 클러스터에 편입하거나 신규 생성

클러스터링 정책:
  - 코사인 유사도 ≥ CLUSTER_SIMILARITY_THRESHOLD → 편입
  - 윈도우: 최근 CLUSTER_TIME_WINDOW_HOURS 시간 내 updated 된 클러스터만 매칭 후보
  - centroid 는 incremental mean 으로 갱신
"""
import logging
from datetime import datetime, timedelta

import numpy as np

from app import create_app
from config import Config
from models import db, Article, Paper, Cluster
from services.local_embed import embed_texts

logger = logging.getLogger(__name__)

MAX_INPUT_CHARS = 6000
BODY_PREFIX_CHARS = 2000


# ---------- 텍스트 빌더 ----------
def _build_article_text(article: Article) -> str:
    parts = [article.title or ""]
    if article.body and article.body_status == "success":
        parts.append(article.body[:BODY_PREFIX_CHARS])
    elif article.description:
        parts.append(article.description)
    return " ".join(parts)[:MAX_INPUT_CHARS]


def _build_paper_text(paper: Paper) -> str:
    return f"{paper.title} {paper.abstract or ''}"[:MAX_INPUT_CHARS]


# ---------- 공통 배치 임베딩 ----------
def _embed_items(items, text_fn, input_type: str = "document") -> dict:
    if not items:
        return {"total": 0, "success": 0, "failed": 0}

    pairs = [(item, text_fn(item)) for item in items]
    valid = [(item, txt) for item, txt in pairs if txt.strip()]

    if not valid:
        return {"total": len(items), "success": 0, "failed": len(items)}

    valid_items = [v[0] for v in valid]
    valid_texts = [v[1] for v in valid]

    print(f"  로컬 임베딩 호출 ({len(valid_texts)}건)...")
    try:
        embeddings = embed_texts(valid_texts, input_type="document")
    except Exception as e:
        logger.exception("local embed failed")
        return {"total": len(items), "success": 0, "failed": len(items), "error": str(e)}

    for item, emb in zip(valid_items, embeddings):
        item.embedding = emb

    db.session.commit()
    return {
        "total": len(items),
        "success": len(valid_items),
        "failed": len(items) - len(valid_items),
    }


def embed_articles(limit: int = 500) -> dict:
    pending = (
        Article.query
        .filter(Article.embedding.is_(None))
        .filter(Article.is_ai_relevant.is_(True))
        .order_by(Article.published_at.asc())
        .limit(limit)
        .all()
    )
    return _embed_items(pending, _build_article_text, input_type="document")


def embed_papers(limit: int = 500) -> dict:
    pending = (
        Paper.query
        .filter(Paper.embedding.is_(None))
        .order_by(Paper.published_at.desc())
        .limit(limit)
        .all()
    )
    return _embed_items(pending, _build_paper_text, input_type="document")


# ---------- 클러스터링 ----------
def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    if norm == 0:
        return 0.0
    return float(np.dot(a, b) / norm)


def cluster_articles() -> dict:
    threshold = Config.CLUSTER_SIMILARITY_THRESHOLD
    window_hours = Config.CLUSTER_TIME_WINDOW_HOURS
    cutoff = datetime.utcnow() - timedelta(hours=window_hours)

    unassigned = (
        Article.query
        .filter(Article.cluster_id.is_(None))
        .filter(Article.embedding.isnot(None))
        .order_by(Article.published_at.asc())
        .all()
    )

    active = (
        Cluster.query
        .filter(Cluster.updated_at >= cutoff)
        .all()
    )
    state = []
    for c in active:
        if not c.centroid:
            continue
        state.append({
            "id": c.id,
            "centroid": np.array(c.centroid, dtype=np.float32),
            "size": c.articles.count(),
        })

    stats = {"processed": 0, "joined": 0, "created": 0}

    for art in unassigned:
        emb = np.array(art.embedding, dtype=np.float32)

        best_idx, best_sim = -1, 0.0
        for i, cs in enumerate(state):
            sim = _cosine(emb, cs["centroid"])
            if sim > best_sim:
                best_sim = sim
                best_idx = i

        if best_idx >= 0 and best_sim >= threshold:
            cs = state[best_idx]
            cluster = db.session.get(Cluster, cs["id"])
            new_centroid = (cs["centroid"] * cs["size"] + emb) / (cs["size"] + 1)
            cluster.centroid = new_centroid.tolist()
            cluster.summary_dirty = True
            cluster.updated_at = datetime.utcnow()
            art.cluster_id = cluster.id

            cs["centroid"] = new_centroid
            cs["size"] += 1
            stats["joined"] += 1
        else:
            cluster = Cluster(
                centroid=art.embedding,
                topic=(art.title or "")[:300],
                summary_dirty=True,
            )
            db.session.add(cluster)
            db.session.flush()
            art.cluster_id = cluster.id

            state.append({"id": cluster.id, "centroid": emb, "size": 1})
            stats["created"] += 1

        stats["processed"] += 1

    db.session.commit()
    return stats


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        print(f"임베딩 모델: {Config.LOCAL_EMBEDDING_MODEL}")
        print(f"클러스터 임계값: {Config.CLUSTER_SIMILARITY_THRESHOLD}")
        print()

        print("=== 1. 뉴스 기사 임베딩 ===")
        s1 = embed_articles(limit=500)
        print(f"  total={s1['total']} success={s1['success']} failed={s1['failed']}")

        print("\n=== 2. 논문 임베딩 ===")
        s2 = embed_papers(limit=500)
        print(f"  total={s2['total']} success={s2['success']} failed={s2['failed']}")

        print("\n=== 3. 뉴스 클러스터링 ===")
        s3 = cluster_articles()
        print(f"  processed={s3['processed']} joined={s3['joined']} created={s3['created']}")

        total_clusters = Cluster.query.count()
        multi = sum(1 for c in Cluster.query.all() if c.articles.count() >= 2)
        big = sum(1 for c in Cluster.query.all() if c.articles.count() >= 3)
        print(f"\n총 클러스터: {total_clusters}  ≥2개 매체: {multi}  ≥3개 매체: {big}")
