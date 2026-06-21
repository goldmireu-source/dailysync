"""오래된 데이터 자동 삭제 — 4일 이상된 기사/논문 제거.

규칙:
  - `published_at` 기준 retention_days 이전이면 삭제 대상.
  - `Paper.saved_at IS NOT NULL` (저장된 논문) → 영구 보존.
  - `Cluster.saved_at IS NOT NULL` (저장된 클러스터) → 클러스터와 그 안의 모든 기사 영구 보존.
  - 저장 안 된 클러스터에서 오래된 기사만 제거됨. 결과적으로 비어버린 unsaved 클러스터도 함께 정리.
  - 삭제된 기사/논문의 static/thumbs/ 이미지 파일도 함께 삭제.

호출:
  cleanup_old_data(retention_days=4)
"""
import logging
import pathlib
from datetime import datetime, timedelta

from sqlalchemy import or_, select

from models import db, Article, Cluster, Paper, Contest, KarrotPost

_THUMBS = pathlib.Path("static/thumbs")


def _delete_thumb(url: str | None) -> None:
    """로컬 썸네일 파일 삭제 (없으면 무시)."""
    if not url or not url.startswith("/static/thumbs/"):
        return
    try:
        _THUMBS.joinpath(pathlib.Path(url).name).unlink(missing_ok=True)
    except OSError:
        pass

logger = logging.getLogger(__name__)


def cleanup_completed_karrot(hours: int = 24) -> int:
    """완료 후 hours 시간이 지난 당근 게시글 삭제. 삭제 건수 반환."""
    import os
    from flask import current_app
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    posts = (
        KarrotPost.query
        .filter(KarrotPost.status == "completed")
        .filter(KarrotPost.completed_at < cutoff)
        .all()
    )
    count = 0
    for post in posts:
        if post.image_url and "/uploads/karrot/" in post.image_url:
            fname = os.path.basename(post.image_url)
            try:
                p = os.path.join(current_app.static_folder, "uploads", "karrot", fname)
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
        db.session.delete(post)
        count += 1
    if count:
        db.session.commit()
        logger.info(f"cleanup_completed_karrot — {count}건 삭제")
    return count


def cleanup_old_data(retention_days: int = 4) -> dict:
    # 삭제 직전 안전 백업 — 자동/수동 어느 경로로 호출돼도 한 부 보존.
    from jobs.backup import backup_database
    backup_info = backup_database(keep_days=7)

    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    stats: dict = {"retention_days": retention_days, "cutoff": cutoff.isoformat(), "backup": backup_info}

    # 사전 집계 (삭제 전 상태)
    stats["articles_before"] = Article.query.count()
    stats["clusters_before"] = Cluster.query.count()
    stats["papers_before"] = Paper.query.count()

    # 1. 저장된 클러스터 id 모음 (그 안의 기사는 모두 보존)
    saved_cluster_ids_sel = select(Cluster.id).where(Cluster.saved_at.isnot(None))

    # 2. 오래된 기사 삭제 — cluster_id NULL 이거나 saved 안 된 클러스터 소속
    article_thumb_urls = [
        row[0] for row in
        db.session.execute(
            select(Article.image_url)
            .where(Article.published_at < cutoff)
            .where(Article.image_url.isnot(None))
            .where(or_(
                Article.cluster_id.is_(None),
                ~Article.cluster_id.in_(saved_cluster_ids_sel),
            ))
        ).all()
    ]
    deleted_articles = (
        Article.query
        .filter(Article.published_at < cutoff)
        .filter(or_(
            Article.cluster_id.is_(None),
            ~Article.cluster_id.in_(saved_cluster_ids_sel),
        ))
        .delete(synchronize_session=False)
    )
    for url in article_thumb_urls:
        _delete_thumb(url)
    stats["articles_deleted"] = deleted_articles

    # 3. 비어버린 unsaved 클러스터 삭제
    used_cluster_ids_sel = (
        select(Article.cluster_id)
        .where(Article.cluster_id.isnot(None))
        .distinct()
    )
    deleted_clusters = (
        Cluster.query
        .filter(Cluster.saved_at.is_(None))
        .filter(~Cluster.id.in_(used_cluster_ids_sel))
        .delete(synchronize_session=False)
    )
    stats["clusters_deleted"] = deleted_clusters

    # 4. 오래된 논문 삭제 — 저장 안 된 것만
    paper_thumb_urls = [
        row[0] for row in
        db.session.execute(
            select(Paper.figure_url)
            .where(Paper.published_at < cutoff)
            .where(Paper.saved_at.is_(None))
            .where(Paper.figure_url.isnot(None))
        ).all()
    ]
    deleted_papers = (
        Paper.query
        .filter(Paper.published_at < cutoff)
        .filter(Paper.saved_at.is_(None))
        .delete(synchronize_session=False)
    )
    for url in paper_thumb_urls:
        _delete_thumb(url)
    stats["papers_deleted"] = deleted_papers

    # 5. 마감 지난 공모전 삭제 — deadline 이 (오늘 - grace) 이전, 저장 안 된 것만.
    #    deadline=None(마감 미상)은 보존. saved_at 처리된 것도 보존.
    from datetime import date, timezone, timedelta as _td
    from config import Config
    kst_today = (datetime.utcnow() + _td(hours=9)).date()
    contest_cutoff = kst_today - _td(days=Config.CONTEST_RETENTION_DAYS)
    stats["contests_before"] = Contest.query.count()
    deleted_contests = (
        Contest.query
        .filter(Contest.deadline.isnot(None))
        .filter(Contest.deadline < contest_cutoff)
        .filter(Contest.saved_at.is_(None))
        .delete(synchronize_session=False)
    )
    stats["contests_deleted"] = deleted_contests
    stats["contest_cutoff"] = contest_cutoff.isoformat()

    db.session.commit()

    stats["contests_after"] = Contest.query.count()
    stats["articles_after"] = Article.query.count()
    stats["clusters_after"] = Cluster.query.count()
    stats["papers_after"] = Paper.query.count()

    logger.info(
        f"cleanup_old_data — retention={retention_days}d, "
        f"articles -{deleted_articles}, clusters -{deleted_clusters}, papers -{deleted_papers}"
    )
    return stats
