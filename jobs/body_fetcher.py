"""기사 본문 페치 — 사적이용 분석 전용.

⚠️ Article.body 는 LLM 입력·임베딩 등 내부 분석에만 사용한다.
   이메일·웹 UI 출력에는 절대로 노출하지 않는다.

도메인별 rate limit (2초) 을 두어 서버에 부담을 주지 않는다.
trafilatura 가 robots.txt 도 자동 확인한다.
"""
import logging
import time
from datetime import datetime
from urllib.parse import urlparse

import trafilatura

from app import create_app
from models import db, Article

logger = logging.getLogger(__name__)

MIN_DOMAIN_INTERVAL = 2.0
MAX_BODY_LEN = 50_000   # DB 저장 상한
MIN_BODY_LEN = 200      # 이보다 짧으면 실패로 간주


def extract_body(url: str) -> tuple[str | None, str | None]:
    """본문 텍스트와 OG 이미지 URL 동시 추출 (techblog_body_fetcher.py 도 재사용)."""
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return None, None
    text = trafilatura.extract(
        downloaded,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
    )
    try:
        meta = trafilatura.extract_metadata(downloaded, default_url=url)
        image_url = getattr(meta, "image", None)
    except Exception:
        image_url = None
    return text, image_url


def fetch_pending(limit: int = 30) -> dict:
    """body_status='pending' 인 Article 을 처리."""
    pending = (
        Article.query
        .filter_by(body_status="pending")
        .order_by(Article.published_at.desc().nullslast())
        .limit(limit)
        .all()
    )

    stats = {"processed": 0, "success": 0, "failed": 0, "blocked": 0}
    last_request: dict[str, float] = {}

    for art in pending:
        domain = urlparse(art.url).netloc
        wait = MIN_DOMAIN_INTERVAL - (time.time() - last_request.get(domain, 0))
        if wait > 0:
            time.sleep(wait)

        try:
            body, image_url = extract_body(art.url)
            last_request[domain] = time.time()

            if image_url and not art.image_url:
                art.image_url = image_url[:1000]

            if body and len(body) >= MIN_BODY_LEN:
                art.body = body[:MAX_BODY_LEN]
                art.body_status = "success"
                stats["success"] += 1
            else:
                art.body_status = "failed"
                stats["failed"] += 1
        except Exception as e:
            msg = str(e).lower()
            if any(c in msg for c in ("403", "401", "429", "forbidden")):
                art.body_status = "blocked"
                stats["blocked"] += 1
            else:
                art.body_status = "failed"
                stats["failed"] += 1
            logger.warning(f"body fetch failed {art.url}: {e}")

        art.body_fetched_at = datetime.utcnow()
        stats["processed"] += 1

    db.session.commit()
    return stats


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        stats = fetch_pending(limit=30)
        print(
            f"본문 수집: processed={stats['processed']} "
            f"success={stats['success']} failed={stats['failed']} blocked={stats['blocked']}"
        )
