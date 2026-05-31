"""헤드리스 브라우저 렌더링 헬퍼 (Playwright).

JS 로 목록을 그리는 SPA(loud.kr 등)를 위해 실제 브라우저로 렌더한 HTML 을 돌려준다.
Playwright 미설치 시 None 반환(해당 소스만 skip — 나머지는 정상).
"""
import logging

logger = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def render_html(url: str, *, wait_for: str | None = None, scrolls: int = 2,
                timeout: int = 30000) -> str | None:
    """url 을 헤드리스 크롬으로 렌더해 최종 HTML 반환. 실패/미설치 시 None."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("playwright 미설치 — JS 렌더 소스 skip (pip install playwright && playwright install chromium)")
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=UA)
                page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                if wait_for:
                    try:
                        page.wait_for_selector(wait_for, timeout=15000)
                    except Exception:
                        pass  # 셀렉터 안 떠도 현재까지 렌더된 내용으로 진행
                page.wait_for_timeout(2500)
                for _ in range(scrolls):
                    page.mouse.wheel(0, 5000)
                    page.wait_for_timeout(1000)
                return page.content()
            finally:
                browser.close()
    except Exception as e:
        logger.warning(f"render_html failed for {url}: {e}")
        return None
