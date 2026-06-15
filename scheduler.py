"""APScheduler 설정 + 잡 등록.

KST 기준 스케줄:
- 매시 정각 (08~22시): 뉴스 RSS 수집 (최신성 보장)
- 매 2시간 (08, 10, 12, ..., 22): 본문 페치
- 00, 06, 12, 18시 정각: 전체 파이프라인 1회 (수집→페치→논문→임베딩/클러스터링→요약)
- 04:00 KST: 4일 이상 된 기사·논문 삭제 (saved 처리된 항목은 보존)

→ 06:00 실행분이 08:00 아침 다이제스트를 채우고, 이후 6시간마다 갱신.

Flask debug 모드의 reloader 가 잡을 두 번 등록하지 않도록 환경변수 체크.
"""
import inspect
import logging
import os
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

_scheduler: BackgroundScheduler | None = None

# 모든 잡 공통 옵션
_JOB_DEFAULTS = {"replace_existing": True, "max_instances": 1, "coalesce": True}


def _wrap(app, job_func, triggered_by: str = "scheduler", run_id: int | None = None):
    """Flask 앱 컨텍스트를 자동으로 push 하는 래퍼. run_id 가 있으면 잡에 전달."""
    sig = inspect.signature(job_func)

    def wrapped():
        with app.app_context():
            try:
                kwargs = {"triggered_by": triggered_by}
                if "run_id" in sig.parameters:
                    kwargs["run_id"] = run_id
                job_func(**kwargs)
            except Exception as e:
                logger.exception(f"scheduled job {job_func.__name__} crashed: {e}")

    wrapped.__name__ = job_func.__name__
    return wrapped


def init_scheduler(app) -> BackgroundScheduler | None:
    """Flask 앱에 스케줄러 부착.

    Werkzeug reloader 가 두 번 실행하는 걸 막기 위해 WERKZEUG_RUN_MAIN 체크.
    debug=False 일 때는 reloader 자체가 안 도므로 항상 등록됨.
    """
    global _scheduler

    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") is None:
        logger.info("scheduler skipped (debug reloader parent)")
        return None

    if _scheduler is not None:
        logger.info("scheduler already initialized")
        return _scheduler

    from jobs.pipeline import (
        job_collect_news,
        job_fetch_bodies,
        job_refresh_now,
        job_cleanup_old_data,
        job_collect_contests,
    )
    from jobs.cleanup import cleanup_completed_karrot

    def _job_karrot_cleanup():
        cleanup_completed_karrot(hours=24)

    sched = BackgroundScheduler(timezone=KST)

    # 매시 정각 (08~22시), 뉴스 수집
    sched.add_job(
        _wrap(app, job_collect_news),
        CronTrigger(hour="8-22", minute=0, timezone=KST),
        id="collect_news_hourly",
        name="뉴스 수집 (매시)",
        **_JOB_DEFAULTS,
    )

    # 매 2시간 (08, 10, ..., 22시), 본문 페치
    sched.add_job(
        _wrap(app, job_fetch_bodies),
        CronTrigger(hour="8-22/2", minute=5, timezone=KST),
        id="fetch_bodies",
        name="본문 페치 (2시간)",
        **_JOB_DEFAULTS,
    )

    # 00, 06, 12, 18시 정각 — 전체 파이프라인 1회 (6시간마다)
    sched.add_job(
        _wrap(app, job_refresh_now, triggered_by="scheduler"),
        CronTrigger(hour="0,6,12,18", minute=0, timezone=KST),
        id="refresh_6h",
        name="전체 파이프라인 (6시간마다)",
        **_JOB_DEFAULTS,
    )

    # 04:00 KST — 4일 이상 된 기사·논문 삭제 (saved 처리된 항목은 보존)
    sched.add_job(
        _wrap(app, job_cleanup_old_data, triggered_by="scheduler"),
        CronTrigger(hour=4, minute=0, timezone=KST),
        id="cleanup_old_data_daily",
        name="오래된 데이터 삭제 (매일 04:00)",
        **_JOB_DEFAULTS,
    )

    # 매시 30분 — 완료된 당근 게시글 24h 후 자동 삭제
    sched.add_job(
        _wrap(app, _job_karrot_cleanup),
        CronTrigger(minute=30, timezone=KST),
        id="karrot_cleanup_hourly",
        name="당근 완료 게시글 정리 (매시 :30)",
        **_JOB_DEFAULTS,
    )

    # 07:30, 19:30 KST — 공모전 수집 (하루 2회면 충분 — 시간단위로 안 바뀜)
    sched.add_job(
        _wrap(app, job_collect_contests, triggered_by="scheduler"),
        CronTrigger(hour="7,19", minute=30, timezone=KST),
        id="collect_contests_daily",
        name="공모전 수집 (07:30, 19:30)",
        **_JOB_DEFAULTS,
    )

    sched.start()
    _scheduler = sched

    logger.info("Scheduler started — 등록된 잡:")
    for job in sched.get_jobs():
        logger.info(f"  - {job.id} ({job.name}): 다음 실행 {job.next_run_time}")

    return sched


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler


def trigger_job_now(job_id: str, app, run_id: int | None = None) -> bool:
    """잡을 즉시 한 번 실행 (수동 트리거).

    run_id 가 있으면 잡 함수에 전달 — 미리 생성된 JobRun row 를 업데이트.
    """
    from jobs import pipeline

    mapping = {
        "collect_news": pipeline.job_collect_news,
        "fetch_bodies": pipeline.job_fetch_bodies,
        "collect_papers": pipeline.job_collect_papers,
        "embed_and_cluster": pipeline.job_embed_and_cluster,
        "summarize_news": pipeline.job_summarize_news,
        "summarize_papers": pipeline.job_summarize_papers,
        "morning_pipeline": pipeline.job_morning_pipeline,
        "refresh_now": pipeline.job_refresh_now,
        "backfill_papers": pipeline.job_backfill_papers,
        "cleanup_old_data": pipeline.job_cleanup_old_data,
        "collect_contests": pipeline.job_collect_contests,
    }
    fn = mapping.get(job_id)
    if not fn:
        return False

    sched = get_scheduler()
    if sched is None:
        with app.app_context():
            sig = inspect.signature(fn)
            if "run_id" in sig.parameters:
                fn(triggered_by="manual", run_id=run_id)
            else:
                fn(triggered_by="manual")
        return True

    now = datetime.now(KST)
    sched.add_job(
        _wrap(app, fn, triggered_by="manual", run_id=run_id),
        "date",
        run_date=now,
        id=f"manual_{job_id}_{int(now.timestamp())}",
        replace_existing=False,
    )
    return True
