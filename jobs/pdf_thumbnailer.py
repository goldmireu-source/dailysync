"""논문 PDF 첫 페이지 → 썸네일 이미지 변환.

PyMuPDF로 PDF 첫 페이지를 1280×1800 JPEG로 렌더링.
기사 스크린샷(article_screenshotter)과 동일한 치수 → 동일한 CSS cover scale 보장.
PDF 없는 논문은 arXiv abs 페이지 Playwright 스크린샷으로 폴백.
"""
import io
import logging
import pathlib
import time

import requests

from models import db, Paper

logger = logging.getLogger(__name__)

THUMBS_DIR = pathlib.Path("static/thumbs")
MAX_PDF_BYTES = 30 * 1024 * 1024
JPEG_QUALITY = 85
W = 1280
H = 1800   # 기사 스크린샷(1280×1800)과 동일

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _UA}


def _render_pdf_first_page(pdf_bytes: bytes, out_path: pathlib.Path) -> bool:
    """PDF 첫 페이지 → 1280×1800 JPEG (PyMuPDF + PIL)."""
    try:
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz
    except ImportError:
        logger.error("pymupdf 미설치 — pip install pymupdf")
        return False

    try:
        from PIL import Image
    except ImportError:
        logger.error("pillow 미설치")
        return False

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if doc.page_count == 0:
            return False
        page = doc.load_page(0)
        # PDF 폭 → 1280px로 정규화
        scale = W / page.rect.width
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
        doc.close()

        img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")

        if img.height >= H:
            # 첫 1800px만 크롭
            img = img.crop((0, 0, W, H))
        else:
            # 부족한 하단을 흰색으로 패딩
            canvas = Image.new("RGB", (W, H), (255, 255, 255))
            canvas.paste(img, (0, 0))
            img = canvas

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY)
        out_path.write_bytes(buf.getvalue())
        return True
    except Exception as e:
        logger.warning(f"PDF 렌더링 실패: {e}")
        return False


def _fetch_pdf(pdf_url: str) -> bytes | None:
    """PDF 다운로드 — 크기 초과 시 None 반환."""
    try:
        resp = requests.get(pdf_url, timeout=30, headers=_HEADERS, stream=True)
        resp.raise_for_status()
        cl = int(resp.headers.get("Content-Length", 0))
        if cl > MAX_PDF_BYTES:
            return None
        data = b""
        for chunk in resp.iter_content(65536):
            data += chunk
            if len(data) > MAX_PDF_BYTES:
                return None
        return data
    except Exception as e:
        logger.warning(f"PDF 다운로드 실패 {pdf_url}: {e}")
        return None


def _screenshot_arxiv_fallback(arxiv_id: str, out_path: pathlib.Path) -> bool:
    """PDF 없을 때 arXiv abs 페이지 Playwright 스크린샷으로 대체."""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        return False

    url = f"https://arxiv.org/abs/{arxiv_id}"
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(
                viewport={"width": W, "height": H},
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
                    clip={"x": 0, "y": 0, "width": W, "height": H},
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


def thumb_papers(limit: int = 30) -> dict:
    """PDF 첫 페이지 렌더링으로 논문 썸네일 생성 (1280×1800)."""
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

        # 1순위: PDF 다운로드 → 첫 페이지 직접 렌더링
        pdf_url = paper.pdf_url or (
            f"https://arxiv.org/pdf/{paper.arxiv_id}" if paper.arxiv_id else None
        )
        if pdf_url:
            pdf_bytes = _fetch_pdf(pdf_url)
            if pdf_bytes:
                ok = _render_pdf_first_page(pdf_bytes, out_path)

        # 2순위: PDF 실패 시 arXiv abs 페이지 스크린샷
        if not ok and paper.arxiv_id:
            ok = _screenshot_arxiv_fallback(paper.arxiv_id, out_path)

        if ok:
            paper.figure_url = f"/static/thumbs/{fname}"
            db.session.add(paper)
            stats["success"] += 1
        else:
            stats["failed"] += 1

        stats["processed"] += 1

    db.session.commit()
    return stats
