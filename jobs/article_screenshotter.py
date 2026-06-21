"""기사 og:image 추출 → 썸네일.

requests 로 기사 HTML을 가져와 og:image 메타태그에서 이미지 URL을 추출하고
다운로드하여 static/thumbs/ 에 저장한다.

image_url 이 이미 있는 기사(빈 문자열 포함)는 건너뜀.
"""
import hashlib
import io
import logging
import pathlib

import requests
from bs4 import BeautifulSoup
from PIL import Image

from models import db, Article

logger = logging.getLogger(__name__)

THUMBS_DIR = pathlib.Path("static/thumbs")
JPEG_QUALITY = 85
# 카드 이미지 표준 크기 (16:9)
IMG_W, IMG_H = 1280, 720
HTTP_TIMEOUT = 8  # 초

# 소셜 미디어 등 og:image 없는 사이트
_BLOCKLIST = {
    "twitter.com", "x.com", "instagram.com", "facebook.com", "linkedin.com",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
    )
}


def _domain(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).netloc.removeprefix("www.")


def _get_og_image(url: str) -> str | None:
    """기사 HTML에서 og:image 또는 twitter:image URL 추출."""
    try:
        r = requests.get(url, headers=_HEADERS, timeout=HTTP_TIMEOUT, allow_redirects=True)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for prop in ["og:image", "twitter:image"]:
            tag = (
                soup.find("meta", property=prop)
                or soup.find("meta", attrs={"name": prop})
            )
            if tag and tag.get("content"):
                return tag["content"]
    except Exception as e:
        logger.debug(f"og:image 파싱 실패 {url}: {type(e).__name__}")
    return None


def _download_and_save(img_url: str, out_path: pathlib.Path) -> bool:
    """이미지 URL 다운로드 → 1280×720 JPEG 저장."""
    try:
        r = requests.get(img_url, headers=_HEADERS, timeout=HTTP_TIMEOUT, stream=True)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        # 비율 유지 썸네일 → 흰 캔버스 중앙에 붙여넣기
        img.thumbnail((IMG_W, IMG_H), Image.LANCZOS)
        canvas = Image.new("RGB", (IMG_W, IMG_H), (255, 255, 255))
        canvas.paste(img, ((IMG_W - img.width) // 2, (IMG_H - img.height) // 2))
        buf = io.BytesIO()
        canvas.save(buf, format="JPEG", quality=JPEG_QUALITY)
        out_path.write_bytes(buf.getvalue())
        return True
    except Exception as e:
        logger.debug(f"이미지 다운로드 실패 {img_url}: {type(e).__name__}")
    return False


def screenshot_articles(limit: int = 20) -> dict:
    """image_url 없는 기사의 og:image 수집."""
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

    for art in pending:
        if _domain(art.url) in _BLOCKLIST:
            art.image_url = ""
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
            stats["success"] += 1
            stats["processed"] += 1
            continue

        img_url = _get_og_image(art.url)
        if img_url and _download_and_save(img_url, out_path):
            art.image_url = f"/static/thumbs/{fname}"
            stats["success"] += 1
        else:
            art.image_url = ""  # 재시도 방지
            stats["failed"] += 1

        db.session.add(art)
        stats["processed"] += 1

    db.session.commit()
    return stats
