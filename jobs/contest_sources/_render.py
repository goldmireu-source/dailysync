"""헤드리스 브라우저 렌더링 헬퍼 (Playwright).

JS 로 목록을 그리는 SPA(loud.kr 등)를 위해 실제 브라우저로 렌더한 HTML 을 돌려준다.
Playwright 미설치 시 None 반환(해당 소스만 skip — 나머지는 정상).
"""
import logging
import multiprocessing
import os
import signal

logger = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# render_html() 전체(브라우저 launch~close)를 별도 프로세스(그룹)에서 실행하고
# 이 시간 안에 안 끝나면 그룹째 강제 종료한다.
#
# 배경(2026-07-09 실측 장애): browser.close() 가 CDP 응답 없이 영원히 안 돌아와
# campuspick 상세 페이지 렌더 1건이 멈췄고, 그 안에 타임아웃 가드가 하나도 없어서
# refresh_now 잡 전체가 6일간 block → APScheduler max_instances=1 때문에 그 이후
# 모든 06/12/18/00시 파이프라인 실행이 통째로 스킵되는 사고로 이어졌다.
# page.goto/wait_for_selector 자체엔 타임아웃이 있어도 close() 등 SDK 호출엔 없어서
# 안쪽 타임아웃만으론 못 막는다 — 바깥에서 프로세스 단위로 끊어야 한다.
_RENDER_TIMEOUT_SEC = 45


def _render_worker(url: str, wait_for: str | None, scrolls: int, timeout: int, queue) -> None:
    if hasattr(os, "setpgrp"):
        os.setpgrp()  # 이 프로세스를 그룹 리더로 — 타임아웃 시 자식(node/chromium)까지 그룹째 kill
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("playwright 미설치 — JS 렌더 소스 skip (pip install playwright && playwright install chromium)")
        queue.put(None)
        return

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
                queue.put(page.content())
            finally:
                browser.close()
    except Exception as e:
        logger.warning(f"render_html failed for {url}: {e}")
        queue.put(None)


def render_html(url: str, *, wait_for: str | None = None, scrolls: int = 2,
                 timeout: int = 30000) -> str | None:
    """url 을 헤드리스 크롬으로 렌더해 최종 HTML 반환. 실패/미설치/타임아웃 시 None."""
    ctx = multiprocessing.get_context("fork") if hasattr(os, "fork") else multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    proc = ctx.Process(target=_render_worker, args=(url, wait_for, scrolls, timeout, queue))
    proc.start()
    proc.join(_RENDER_TIMEOUT_SEC)

    if proc.is_alive():
        logger.warning(f"render_html timed out for {url} — 프로세스(그룹) 강제 종료")
        if hasattr(os, "killpg"):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            proc.terminate()
        proc.join(5)
        if proc.is_alive():
            proc.kill()
            proc.join(5)
        return None

    try:
        return queue.get_nowait()
    except Exception:
        return None
