"""테크블로그 글 본문 페치 — 요약 입력 전용 (body_fetcher.py 와 동일 원칙).

⚠️ TechPost.body 는 LLM 입력(요약)에만 사용한다. 이메일·웹 UI 출력에는
   절대로 노출하지 않는다 (README 원칙 1).

RSS 티저(500자)만으로는 key_points/summary_ko 가 빈약해지는 문제 때문에
도입 — trafilatura 로 원문을 가져와 techblog_summarizer.py 의 1차 입력으로
쓴다 (README 원칙 2: "trafilatura 가 원 페이지에서 추출하는 것"은 스크래핑
금지 예외).

수집 빈도가 하루 1회(06:15)라 body_fetcher.py 처럼 별도 cron 슬롯을 두지
않고, job_collect_techblog 안에서 수집 직후 동기로 실행한다.
"""
import logging
import time
from datetime import datetime
from urllib.parse import urlparse

from jobs.body_fetcher import extract_body
from models import db, TechPost

logger = logging.getLogger(__name__)

MIN_DOMAIN_INTERVAL = 2.0
MAX_BODY_LEN = 50_000
MIN_BODY_LEN = 200


def fetch_pending(limit: int = 40) -> dict:
    """body_status='pending' 인 TechPost 를 처리."""
    pending = (
        TechPost.query
        .filter_by(body_status="pending")
        .order_by(TechPost.hot_score.desc(), TechPost.fetched_at.desc())
        .limit(limit)
        .all()
    )

    stats = {"processed": 0, "success": 0, "failed": 0, "blocked": 0}
    last_request: dict[str, float] = {}

    for post in pending:
        domain = urlparse(post.url).netloc
        wait = MIN_DOMAIN_INTERVAL - (time.time() - last_request.get(domain, 0))
        if wait > 0:
            time.sleep(wait)

        try:
            body, image_url = extract_body(post.url)
            last_request[domain] = time.time()

            if image_url and not post.image_url:
                post.image_url = image_url[:1000]

            if body and len(body) >= MIN_BODY_LEN:
                post.body = body[:MAX_BODY_LEN]
                post.body_status = "success"
                stats["success"] += 1
            else:
                post.body_status = "failed"
                stats["failed"] += 1
        except Exception as e:
            msg = str(e).lower()
            if any(c in msg for c in ("403", "401", "429", "forbidden")):
                post.body_status = "blocked"
                stats["blocked"] += 1
            else:
                post.body_status = "failed"
                stats["failed"] += 1
            logger.warning(f"techpost body fetch failed {post.url}: {e}")

        post.body_fetched_at = datetime.utcnow()
        stats["processed"] += 1

    db.session.commit()
    return stats


if __name__ == "__main__":
    from app import create_app

    app = create_app(with_scheduler=False)
    with app.app_context():
        stats = fetch_pending(limit=40)
        print(
            f"테크블로그 본문 수집: processed={stats['processed']} "
            f"success={stats['success']} failed={stats['failed']} blocked={stats['blocked']}"
        )
