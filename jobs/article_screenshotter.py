"""기사 URL 첫 화면 스크린샷 → 썸네일.

Playwright 헤드리스 크로미엄으로 기사 페이지 뷰포트(1280×850)를 JPEG로 캡처.
static/thumbs/ 에 저장하고 Article.image_url 을 업데이트한다.

image_url 이 이미 있는 기사는 건너뜀.
"""
import hashlib
import logging
import pathlib
import time

from models import db, Article

logger = logging.getLogger(__name__)

THUMBS_DIR = pathlib.Path("static/thumbs")
VIEWPORT = {"width": 1280, "height": 1800}
JPEG_QUALITY = 80
NAV_TIMEOUT = 15_000   # ms
PAGE_TIMEOUT = 12_000  # ms

# 스크린샷 남기지 않는 도메인 (봇 차단 심한 곳)
_BLOCKLIST = {
    "twitter.com", "x.com", "instagram.com", "facebook.com", "linkedin.com",
    "techcrunch.com",   # Playwright 봇 차단으로 반복 타임아웃
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def _domain(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).netloc.removeprefix("www.")


def screenshot_articles(limit: int = 20) -> dict:
    """image_url 없는 기사 최신순으로 스크린샷 촬영."""
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)

    pending = (
        Article.query
        .filter(Article.image_url.is_(None))
        .filter(Article.url.isnot(None))
        .order_by(Article.published_at.desc().nullslast())
        .limit(limit)
        .all()
    )

    if not pending:
        return {"processed": 0, "success": 0, "failed": 0, "skipped": 0}

    stats = {"processed": 0, "success": 0, "failed": 0, "skipped": 0}

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        logger.error("playwright 미설치 또는 playwright install chromium 필요")
        return {"error": "playwright not available"}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport=VIEWPORT,
            extra_http_headers={"User-Agent": _HEADERS["User-Agent"]},
            java_script_enabled=True,
        )
        ctx.set_default_timeout(PAGE_TIMEOUT)

        for art in pending:
            if _domain(art.url) in _BLOCKLIST:
                art.image_url = ""   # 재시도 방지
                db.session.add(art)
                stats["skipped"] += 1
                stats["processed"] += 1
                continue

            url_hash = hashlib.sha256(art.url.encode()).hexdigest()[:14]
            fname = f"article_{url_hash}.jpg"
            out_path = THUMBS_DIR / fname

            if out_path.exists():
                art.image_url = f"/static/thumbs/{fname}"
                db.session.add(art)
                stats["skipped"] += 1
                stats["processed"] += 1
                continue

            page = ctx.new_page()
            try:
                page.goto(art.url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
                # 짧게 대기: 광고/레이아웃 안정화
                time.sleep(0.8)
                page.screenshot(
                    path=str(out_path),
                    type="jpeg",
                    quality=JPEG_QUALITY,
                    full_page=False,
                    clip={"x": 0, "y": 0, "width": VIEWPORT["width"], "height": VIEWPORT["height"]},
                )
                art.image_url = f"/static/thumbs/{fname}"
                db.session.add(art)
                stats["success"] += 1
            except (PWTimeout, Exception) as e:
                logger.warning(f"스크린샷 실패 {art.url}: {type(e).__name__}")
                art.image_url = ""   # 재시도 방지
                db.session.add(art)
                stats["failed"] += 1
            finally:
                page.close()

            stats["processed"] += 1

        browser.close()

    db.session.commit()
    return stats
