"""논문 arXiv 페이지 스크린샷 → 썸네일 이미지 변환.

Playwright 브라우저로 arXiv abs 페이지를 1280×1800 뷰포트로 캡처.
기사 스크린샷(article_screenshotter)과 동일한 방식 — 동일한 CSS cover scale 보장.
arxiv_id 없는 논문은 PyMuPDF PDF 렌더링으로 폴백.
"""
import logging
import pathlib
import time

import requests

from models import db, Paper

logger = logging.getLogger(__name__)

THUMBS_DIR = pathlib.Path("static/thumbs")
MAX_PDF_BYTES = 30 * 1024 * 1024
JPEG_QUALITY = 82
VIEWPORT = {"width": 1280, "height": 1800}   # 기사 스크린샷과 동일

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _UA}


def _screenshot_arxiv(arxiv_id: str, out_path: pathlib.Path) -> bool:
    """arXiv abs 페이지를 브라우저로 스크린샷 — 1280×1800 JPEG."""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        logger.error("playwright 미설치")
        return False

    url = f"https://arxiv.org/abs/{arxiv_id}"
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(
                viewport=VIEWPORT,
                extra_http_headers={"User-Agent": _UA},
            )
            ctx.set_default_timeout(15000)
            page = ctx.new_page()
            try:
                page.goto(url, timeout=20000, wait_until="domcontentloaded")
                time.sleep(1)
                page.screenshot(
                    path=str(out_path),
                    type="jpeg",
                    quality=JPEG_QUALITY,
                    full_page=False,
                    clip={"x": 0, "y": 0,
                          "width": VIEWPORT["width"], "height": VIEWPORT["height"]},
                )
                return True
            except (PWTimeout, Exception) as e:
                logger.warning(f"arXiv 스크린샷 실패 {arxiv_id}: {type(e).__name__}")
                return False
            finally:
                browser.close()
    except Exception as e:
        logger.warning(f"Playwright 초기화 실패: {e}")
        return False


def _render_pdf_fallback(pdf_url: str, out_path: pathlib.Path) -> bool:
    """arxiv_id 없는 논문용 PyMuPDF 폴백 — 1280px 폭 고정."""
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz
        except ImportError:
            return False
    try:
        import io
        from PIL import Image
        resp = requests.get(pdf_url, timeout=30, headers=_HEADERS)
        resp.raise_for_status()
        doc = fitz.open(stream=resp.content, filetype="pdf")
        if doc.page_count == 0:
            return False
        page = doc.load_page(0)
        scale = VIEWPORT["width"] / page.rect.width
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
        doc.close()
        img = Image.open(io.BytesIO(pix.tobytes(output="png")))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY)
        out_path.write_bytes(buf.getvalue())
        return True
    except Exception as e:
        logger.warning(f"PDF 폴백 실패: {e}")
        return False


def thumb_papers(limit: int = 30) -> dict:
    """arXiv abs 페이지 스크린샷으로 논문 썸네일 생성 (Playwright, 1280×1800)."""
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)

    pending = (
        Paper.query
        .filter(Paper.figure_url.is_(None))
        .filter(db.or_(Paper.arxiv_id.isnot(None), Paper.pdf_url.isnot(None)))
        .order_by(Paper.published_at.desc().nullslast())
        .limit(limit)
        .all()
    )

    stats = {"processed": 0, "success": 0, "failed": 0, "skipped": 0}

    for paper in pending:
        arxiv_safe = (paper.arxiv_id or str(paper.id)).replace("/", "_")
        fname = f"paper_{arxiv_safe}.jpg"
        out_path = THUMBS_DIR / fname

        if out_path.exists():
            paper.figure_url = f"/static/thumbs/{fname}"
            db.session.add(paper)
            stats["skipped"] += 1
            stats["processed"] += 1
            continue

        ok = False
        if paper.arxiv_id:
            ok = _screenshot_arxiv(paper.arxiv_id, out_path)
        if not ok and paper.pdf_url:
            ok = _render_pdf_fallback(paper.pdf_url, out_path)

        if ok:
            paper.figure_url = f"/static/thumbs/{fname}"
            db.session.add(paper)
            stats["success"] += 1
        else:
            stats["failed"] += 1

        stats["processed"] += 1

    db.session.commit()
    return stats
