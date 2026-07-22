"""백그라운드 잡 통합 진입점.

각 단계를 JobRun 으로 기록하면서 실행. 실패 시 다음 단계도 시도 (best-effort).
"""
import logging
import traceback
from contextlib import contextmanager
from datetime import datetime

from models import db, JobRun

logger = logging.getLogger(__name__)

# 오류 메시지 잘라내기 한도 (DB Text 칼럼 및 JSON stats 저장용)
_ERR_MSG_MAX = 300
_ERR_TRACEBACK_MAX = 1500


def create_job_run(job_name: str, triggered_by: str = "manual") -> int:
    """JobRun 을 queued 상태로 미리 생성 → ID 즉시 반환.

    routes.py 에서 호출. 백그라운드 잡이 이 ID 를 받아 진행하면서 업데이트.
    """
    run = JobRun(
        job_name=job_name,
        started_at=datetime.utcnow(),
        status="queued",
        stats={},
        triggered_by=triggered_by,
    )
    db.session.add(run)
    db.session.commit()
    return run.id


def _update_phase(run_id: int | None, phase: str):
    """잡 진행 중 stats 의 phase 필드 갱신 (프론트 폴링용)."""
    if not run_id:
        return
    try:
        run = JobRun.query.get(run_id)
        if run:
            s = dict(run.stats or {})
            s["phase"] = phase
            run.stats = s
            db.session.commit()
    except Exception:
        logger.exception("phase update failed")


@contextmanager
def _track(job_name: str, triggered_by: str = "scheduler", run_id: int | None = None):
    """JobRun 트래킹 컨텍스트.

    run_id 가 주어지면 그 row 를 업데이트. 없으면 새로 생성.
    yield 는 stats 채울 수 있는 mutable dict.
    """
    if run_id:
        run = JobRun.query.get(run_id)
        if run is None:
            # fallback — 못 찾으면 새로 만듦
            run = JobRun(
                job_name=job_name,
                started_at=datetime.utcnow(),
                status="running",
                stats={},
                triggered_by=triggered_by,
            )
            db.session.add(run)
        else:
            run.status = "running"
            # started_at 은 queued 단계에서 찍은 시각 유지
        db.session.commit()
    else:
        run = JobRun(
            job_name=job_name,
            started_at=datetime.utcnow(),
            status="running",
            stats={},
            triggered_by=triggered_by,
        )
        db.session.add(run)
        db.session.commit()

    stats: dict = dict(run.stats or {})
    try:
        yield stats
        run.stats = stats
        run.status = "success"
    except Exception as e:
        run.status = "failed"
        run.error = f"{type(e).__name__}: {str(e)[:_ERR_MSG_MAX]}\n\n{traceback.format_exc()[:_ERR_TRACEBACK_MAX]}"
        run.stats = stats
        logger.exception(f"job {job_name} failed")
    finally:
        run.finished_at = datetime.utcnow()
        db.session.commit()


# ---------- 개별 잡 ----------
def job_collect_news(triggered_by: str = "scheduler", run_id: int | None = None) -> dict:
    from jobs.news_collector import collect_all
    with _track("collect_news", triggered_by, run_id=run_id) as stats:
        results = collect_all()
        stats["sources"] = len(results)
        stats["total_new"] = sum(r.get("new", 0) for r in results)
        stats["total_filtered"] = sum(r.get("filtered", 0) for r in results)
        stats["errors"] = sum(1 for r in results if r.get("error"))
        return stats


def job_fetch_bodies(triggered_by: str = "scheduler", run_id: int | None = None) -> dict:
    from jobs.body_fetcher import fetch_pending
    with _track("fetch_bodies", triggered_by, run_id=run_id) as stats:
        s = fetch_pending(limit=30)
        stats.update(s)
        return stats


def job_collect_papers(triggered_by: str = "scheduler", run_id: int | None = None) -> dict:
    from jobs.paper_collector import collect_all_papers
    with _track("collect_papers", triggered_by, run_id=run_id) as stats:
        results = collect_all_papers()
        a, h = results["arxiv"], results["huggingface"]
        stats["arxiv_new"] = a.get("new", 0)
        stats["arxiv_skipped"] = a.get("old_skipped", 0)
        stats["hf_new"] = h.get("new", 0)
        stats["hf_marked"] = h.get("marked", 0)
        stats["errors"] = len(a.get("errors", []))
        return stats


def job_collect_contests(triggered_by: str = "scheduler", run_id: int | None = None) -> dict:
    from jobs.contest_collector import collect_all_contests
    with _track("collect_contests", triggered_by, run_id=run_id) as stats:
        s = collect_all_contests()
        stats["total_fetched"] = s.get("total_fetched", 0)
        stats["total_new"] = s.get("total_new", 0)
        stats["total_updated"] = s.get("total_updated", 0)
        stats["rejected"] = s.get("rejected", {})
        stats["sources"] = s.get("sources", {})
        return stats


def job_collect_techblog(triggered_by: str = "scheduler", run_id: int | None = None) -> dict:
    from jobs.techblog_collector import collect_all_techblog
    from jobs.techblog_body_fetcher import fetch_pending as fetch_techpost_bodies
    from jobs.techblog_summarizer import summarize_pending as summarize_techposts
    with _track("collect_techblog", triggered_by, run_id=run_id) as stats:
        s = collect_all_techblog()
        stats["total_fetched"] = s.get("total_fetched", 0)
        stats["total_new"] = s.get("total_new", 0)
        stats["total_updated"] = s.get("total_updated", 0)
        stats["mentions_matched"] = s.get("mentions_matched", 0)
        stats["by_blog"] = s.get("by_blog", {})
        stats["sources"] = s.get("sources", {})
        b = fetch_techpost_bodies()
        stats["body_processed"] = b.get("processed", 0)
        stats["body_success"] = b.get("success", 0)
        stats["body_failed"] = b.get("failed", 0)
        stats["body_blocked"] = b.get("blocked", 0)
        sm = summarize_techposts()
        stats["summarized_picked"] = sm.get("picked", 0)
        stats["summarized_success"] = sm.get("success", 0)
        stats["summarized_failed"] = sm.get("failed", 0)
        return stats


def job_embed_and_cluster(triggered_by: str = "scheduler", run_id: int | None = None) -> dict:
    from jobs.embedder import embed_articles, embed_papers, cluster_articles
    with _track("embed_and_cluster", triggered_by, run_id=run_id) as stats:
        s_a = embed_articles(limit=500)
        s_p = embed_papers(limit=500)
        s_c = cluster_articles()
        stats["articles_embedded"] = s_a.get("success", 0)
        stats["articles_total"] = s_a.get("total", 0)
        stats["articles_failed"] = s_a.get("failed", 0)
        if s_a.get("error"):
            stats["articles_error"] = str(s_a["error"])[:300]
        stats["papers_embedded"] = s_p.get("success", 0)
        if s_p.get("error"):
            stats["papers_error"] = str(s_p["error"])[:_ERR_MSG_MAX]
        stats["clusters_processed"] = s_c.get("processed", 0)
        stats["clusters_created"] = s_c.get("created", 0)
        stats["clusters_joined"] = s_c.get("joined", 0)
        stats["clusters_merged_groups"] = s_c.get("merged_groups", 0)
        stats["clusters_absorbed"] = s_c.get("clusters_absorbed", 0)
        return stats


def job_summarize_news(triggered_by: str = "scheduler", run_id: int | None = None) -> dict:
    from jobs.news_summarizer import summarize_pending
    with _track("summarize_news", triggered_by, run_id=run_id) as stats:
        s = summarize_pending(limit=200)
        stats.update(s)
        return stats


def job_summarize_papers(triggered_by: str = "scheduler", run_id: int | None = None) -> dict:
    from jobs.paper_summarizer import summarize_today_picks
    with _track("summarize_papers", triggered_by, run_id=run_id) as stats:
        s = summarize_today_picks()
        stats.update(s)
        return stats


# ---------- 묶음 (07:00 트리거) ----------
def job_morning_pipeline(triggered_by: str = "scheduler") -> dict:
    """06:30 ~ 07:10 사이 한 번에 실행되는 묶음.

    수집은 별도 잡으로 분리되어 있으므로 여기선 페치~요약만.
    """
    with _track("morning_pipeline", triggered_by) as stats:
        from jobs.paper_collector import collect_all_papers
        from jobs.embedder import embed_articles, embed_papers, cluster_articles
        from jobs.news_summarizer import summarize_pending
        from jobs.paper_summarizer import summarize_today_picks

        # 1. 논문 수집
        p_res = collect_all_papers()
        stats["paper_arxiv_new"] = p_res["arxiv"].get("new", 0)
        stats["paper_hf_new"] = p_res["huggingface"].get("new", 0)

        # 2. 임베딩 + 클러스터링
        a_emb = embed_articles(limit=500)
        p_emb = embed_papers(limit=500)
        cl = cluster_articles()
        stats["articles_embedded"] = a_emb.get("success", 0)
        stats["papers_embedded"] = p_emb.get("success", 0)
        stats["clusters_created"] = cl.get("created", 0)
        stats["clusters_joined"] = cl.get("joined", 0)
        stats["clusters_merged_groups"] = cl.get("merged_groups", 0)
        stats["clusters_absorbed"] = cl.get("clusters_absorbed", 0)

        # 3. 뉴스 요약
        n_sum = summarize_pending(limit=200)
        stats["news_summarized"] = n_sum.get("success", 0)

        # 4. 논문 요약
        p_sum = summarize_today_picks()
        stats["papers_summarized"] = p_sum.get("success", 0)

        return stats


# ---------- 오래된 데이터 삭제 ----------
def job_cleanup_old_data(triggered_by: str = "scheduler", run_id: int | None = None, retention_days: int = 4) -> dict:
    """retention_days 이전 기사·논문 삭제. saved 처리된 항목은 보존."""
    with _track("cleanup_old_data", triggered_by, run_id=run_id) as stats:
        from jobs.cleanup import cleanup_old_data
        _update_phase(run_id, "삭제 대상 집계 중")
        s = cleanup_old_data(retention_days=retention_days)
        stats.update(s)
        return stats


# ---------- 썸네일 생성 ----------
def job_thumb_papers(triggered_by: str = "manual", run_id: int | None = None) -> dict:
    """논문 PDF 첫 페이지 → 썸네일 (figure_url 없는 것만)."""
    from jobs.pdf_thumbnailer import thumb_papers
    with _track("thumb_papers", triggered_by, run_id=run_id) as stats:
        s = thumb_papers(limit=30)
        stats.update(s)
        return stats


def job_screenshot_articles(triggered_by: str = "manual", run_id: int | None = None) -> dict:
    """기사 첫 화면 스크린샷 → 썸네일 (image_url 없는 것만)."""
    from jobs.article_screenshotter import screenshot_articles
    with _track("screenshot_articles", triggered_by, run_id=run_id) as stats:
        s = screenshot_articles(limit=20)
        stats.update(s)
        return stats


# ---------- 백필 (dirty 논문 일괄 처리) ----------
def job_backfill_papers(triggered_by: str = "manual", run_id: int | None = None) -> dict:
    """summary_dirty=True 논문 전부 (또는 limit개) 일괄 요약.

    pick 우선순위/일자 cutoff 무시 — backlog 청소 전용.
    소요 시간은 ~1.2s × N (50/min 제한). 623편이면 약 12~15분.
    """
    with _track("backfill_papers", triggered_by, run_id=run_id) as stats:
        from jobs.paper_summarizer import backfill_dirty_papers
        _update_phase(run_id, "논문 백필 시작")
        s = backfill_dirty_papers(run_id_for_progress=run_id)
        stats.update(s)
        return stats


# ---------- 백필 (dirty 테크블로그 일괄 처리) ----------
def job_backfill_techposts(triggered_by: str = "manual", run_id: int | None = None) -> dict:
    """숨김 안 된 TechPost 전부 summary_dirty=True 리셋 → 본문 fetch → 재요약.

    body fetch 로직 도입 이전에 티저만으로 요약된 기존 글들 소급 개선용 1회성 백필.
    """
    with _track("backfill_techposts", triggered_by, run_id=run_id) as stats:
        from jobs.techblog_summarizer import backfill_dirty_techposts
        _update_phase(run_id, "테크블로그 백필 시작")
        s = backfill_dirty_techposts()
        stats.update(s)
        return stats


# ---------- 원버튼 새로고침 (전체 흐름) ----------
def job_refresh_now(triggered_by: str = "manual", run_id: int | None = None) -> dict:
    """뉴스/논문 수집 → (변경 있으면) 본문 페치 → 임베딩 → 요약.

    한 번 실행 = 그 시점까지 발행된 모든 새 기사·논문 처리.
    재실행해도 이미 처리된 건 건너뜀 (idempotent).

    조기 종료: 신규 기사·논문 0건이고 dirty 큐도 비어 있으면
    `stats["skipped_reason"]="no_changes"` 로 표시하고 본문 페치 이후 단계는 건너뜀.
    """
    with _track("refresh_now", triggered_by, run_id=run_id) as stats:
        from jobs.news_collector import collect_all
        from jobs.body_fetcher import fetch_pending
        from jobs.paper_collector import collect_all_papers
        from jobs.embedder import embed_articles, embed_papers, cluster_articles
        from jobs.news_summarizer import summarize_pending
        from jobs.paper_summarizer import summarize_today_picks
        from jobs.contest_collector import collect_all_contests
        from jobs.techblog_collector import collect_all_techblog
        from jobs.techblog_body_fetcher import fetch_pending as fetch_techpost_bodies
        from models import Cluster, Paper

        # 1. 뉴스 RSS 수집
        _update_phase(run_id, "뉴스 수집 중")
        try:
            n_results = collect_all()
            stats["news_new"] = sum(r.get("new", 0) for r in n_results)
            stats["news_filtered"] = sum(r.get("filtered", 0) for r in n_results)
            stats["news_errors"] = sum(1 for r in n_results if r.get("error"))
        except Exception as e:
            logger.exception("collect_all failed in refresh_now")
            stats["news_new"] = 0
            stats["news_error"] = str(e)[:_ERR_MSG_MAX]

        # 2. 논문 수집 (페치/임베딩보다 먼저 — 스킵 판단에 필요)
        _update_phase(run_id, "논문 수집 중")
        try:
            p_res = collect_all_papers()
            stats["papers_new"] = p_res["arxiv"].get("new", 0) + p_res["huggingface"].get("new", 0)
        except Exception as e:
            logger.exception("collect_all_papers failed in refresh_now")
            stats["papers_new"] = 0

        # 3. 변경 없음 조기 종료 — 신규 0건이고 미처리 dirty/미임베딩/미클러스터링 도 없으면 이후 단계 스킵
        if stats.get("news_new", 0) == 0 and stats.get("papers_new", 0) == 0:
            from datetime import timedelta
            from models import Article, Paper as PaperModel
            dirty_clusters = Cluster.query.filter_by(summary_dirty=True).count()
            dirty_papers = Paper.query.filter_by(summary_dirty=True).count()
            cutoff_72h = datetime.utcnow() - timedelta(hours=72)
            # 미임베딩: embedding=NULL 인 최근 72h 기사
            unembedded = Article.query.filter(
                Article.embedding.is_(None),
                Article.published_at >= cutoff_72h,
            ).count()
            # 미클러스터링: embedding 있지만 cluster_id=NULL 인 최근 72h 기사
            unclustered = Article.query.filter(
                Article.embedding.isnot(None),
                Article.cluster_id.is_(None),
                Article.published_at >= cutoff_72h,
            ).count()
            stats["pending_dirty_clusters"] = dirty_clusters
            stats["pending_dirty_papers"] = dirty_papers
            stats["pending_unembedded"] = unembedded
            stats["pending_unclustered"] = unclustered
            if dirty_clusters == 0 and dirty_papers == 0 and unembedded == 0 and unclustered == 0:
                stats["skipped_reason"] = "no_changes"
                stats["anything_new"] = False
                _update_phase(run_id, "변경 없음 — 스킵")
                return stats

        # 4. 본문 페치 — 수동 새로고침은 20개 한도 (12s×20=240s 최대, 900s 버짓 확보)
        _update_phase(run_id, "본문 페치 중")
        try:
            fb = fetch_pending(limit=20)
            stats["bodies_fetched"] = fb.get("success", 0)
        except Exception as e:
            logger.exception("fetch_pending failed in refresh_now")
            stats["bodies_fetched"] = 0

        # 5. 임베딩 + 클러스터링
        _update_phase(run_id, "임베딩·클러스터링 중")
        _a_emb: dict = {"success": 0, "total": 0, "failed": 0}
        _p_emb: dict = {"success": 0}
        _cl: dict = {"created": 0, "joined": 0, "merged_groups": 0, "clusters_absorbed": 0}
        try:
            _a_emb = embed_articles(limit=500)
            _p_emb = embed_papers(limit=500)
            _cl = cluster_articles()
        except Exception as e:
            logger.exception("embed/cluster failed in refresh_now")
            stats["embed_error"] = f"{type(e).__name__}: {str(e)[:_ERR_MSG_MAX]}"
        finally:
            stats["articles_embedded"] = _a_emb.get("success", 0)
            stats["papers_embedded"] = _p_emb.get("success", 0)
            stats["clusters_created"] = _cl.get("created", 0)
            stats["clusters_joined"] = _cl.get("joined", 0)
            stats["clusters_merged_groups"] = _cl.get("merged_groups", 0)
            stats["clusters_absorbed"] = _cl.get("clusters_absorbed", 0)
            if _a_emb.get("error"):
                stats["articles_embed_error"] = str(_a_emb["error"])[:200]

        # 6. 뉴스 요약 (병렬)
        # Claude Haiku ~1.2s/건 × 100건 ≈ 2분, 900s 버짓 내 안전
        _update_phase(run_id, "뉴스 요약 중")
        try:
            n_sum = summarize_pending(limit=100)
            stats["clusters_summarized"] = n_sum.get("success", 0)
        except Exception as e:
            logger.exception("summarize_pending failed in refresh_now")
            stats["clusters_summarized"] = 0

        # 7. 논문 요약
        _update_phase(run_id, "논문 요약 중")
        try:
            p_sum = summarize_today_picks()
            stats["papers_summarized"] = p_sum.get("success", 0)
        except Exception as e:
            logger.exception("summarize_today_picks failed in refresh_now")
            stats["papers_summarized"] = 0

        # 8. 공모전 수집 (느리게 바뀜 — best-effort)
        _update_phase(run_id, "공모전 수집 중")
        try:
            c_res = collect_all_contests()
            stats["contests_new"] = c_res.get("total_new", 0)
        except Exception:
            logger.exception("collect_all_contests failed in refresh_now")
            stats["contests_new"] = 0

        # 9. 기술블로그 수집 + 요약 (느리게 바뀜 — best-effort)
        _update_phase(run_id, "기술블로그 수집 중")
        try:
            t_res = collect_all_techblog()
            stats["techposts_new"] = t_res.get("total_new", 0)
            b_res = fetch_techpost_bodies()
            stats["techposts_body_success"] = b_res.get("success", 0)
            from jobs.techblog_summarizer import summarize_pending as summarize_techposts
            t_sum = summarize_techposts()
            stats["techposts_summarized"] = t_sum.get("success", 0)
        except Exception:
            logger.exception("collect_all_techblog failed in refresh_now")
            stats["techposts_new"] = 0

        _update_phase(run_id, "완료")
        stats["anything_new"] = (
            stats.get("news_new", 0) > 0
            or stats.get("papers_new", 0) > 0
            or stats.get("clusters_summarized", 0) > 0
            or stats.get("papers_summarized", 0) > 0
            or stats.get("clusters_created", 0) > 0
            or stats.get("articles_embedded", 0) > 0
            or stats.get("contests_new", 0) > 0
            or stats.get("techposts_new", 0) > 0
        )

        return stats
