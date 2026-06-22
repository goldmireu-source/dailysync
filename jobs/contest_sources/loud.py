"""라우드(loud.kr) — AI 공모전 전용 탭(/ai/contest/list).

라우드소싱 AI 공모전 플랫폼. 목록이 SPA라 헤드리스 렌더(_render)로 긁는다.
LOUD_EMAIL/LOUD_PASSWORD 설정 시 로그인 세션에서 목록 파싱 + 상세 페이지의
iframe[2] 내 포스터 이미지 URL 수집. 미설정 시 비로그인 렌더(이미지 없음).
AI 공모전 전용 탭이므로 ai_exempt=True.
"""
import logging
import os
import re
from datetime import date, timedelta

from bs4 import BeautifulSoup

from jobs.contest_sources.base import ContestDraft, register, clean, today_kst
from jobs.contest_sources._render import render_html

logger = logging.getLogger(__name__)

BASE       = "https://www.loud.kr"
LOGIN_URL  = "https://accounts.stunning.kr/v2/auth/login?from_url=Loud&redirect_url=https%3A%2F%2Fwww.loud.kr%2F"
LIST_URL   = f"{BASE}/ai/contest/list"
_ID_RE     = re.compile(r"/contest/view/(\d+)")
_DDAY_RE   = re.compile(r"(\d+)\s*일\s*남음")
_PERIOD_RE = re.compile(r"(\d{2})\.(\d{1,2})\.(\d{1,2})")
_OPEN_RE   = re.compile(r"\d+\s*일\s*남음")

# 추출된 참가대상 텍스트의 신뢰도 판정 — 이 중 하나라도 있어야 실제 참가대상 문구로 인정.
# 없으면 잘못된 텍스트를 뽑은 것이므로 None 반환(보수적 통과).
_TARGET_CONFIDENCE_TOKENS = (
    "누구나", "일반인", "일반 성인", "대학생", "대학원생", "재학생", "졸업생",
    "초등", "중학", "고등", "초·중", "중·고", "청소년", "학생", "학부생",
    "직장인", "성인", "시민", "국민", "내국인", "외국인", "전공자",
    "기업", "법인", "개인", "팀", "제한없음", "제한 없음", "나이 무관",
)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _extract_target(page) -> str | None:
    """상세 페이지 메인 프레임에서 참가대상 텍스트를 추출한다.

    DOM 구조(dt→dd, th→td, 인접 형제) 우선, 폴백으로 렌더드 텍스트에서
    레이블 직후 줄을 읽는다. 신뢰도 토큰이 없으면 None 반환(보수적 통과).
    """
    _LABELS = ["참가대상", "참가 대상", "지원자격", "지원 자격",
               "참여대상", "참여 대상", "참가자격"]
    try:
        result = page.evaluate("""
            (labels) => {
                // 1. dt→dd / th→td 구조
                for (const label of labels) {
                    for (const el of document.querySelectorAll('dt, th')) {
                        if (el.textContent.trim() === label) {
                            const val = el.nextElementSibling;
                            if (val) return val.textContent.trim().slice(0, 300);
                        }
                    }
                }
                // 2. 리프 span/div/p가 레이블 역할인 경우 → 다음 형제
                for (const label of labels) {
                    for (const el of document.querySelectorAll('span, div, p')) {
                        if (el.childElementCount === 0 && el.textContent.trim() === label) {
                            const next = el.nextElementSibling;
                            if (next && next.textContent.trim().length > 1) {
                                return next.textContent.trim().slice(0, 300);
                            }
                        }
                    }
                }
                // 3. innerText 폴백 — 레이블 직후 첫 줄
                const body = document.body.innerText || '';
                for (const label of labels) {
                    const idx = body.indexOf(label);
                    if (idx === -1) continue;
                    const after = body.slice(idx + label.length).replace(/^[\\s:*]+/, '').trim();
                    const line = after.split(/\\n/)[0].trim();
                    // 너무 짧거나 다음 레이블로 이어지면 무시
                    if (line.length > 2 && line.length < 150 &&
                            !labels.some(l => line.startsWith(l))) {
                        return line;
                    }
                }
                return null;
            }
        """, _LABELS)
        extracted = (result or "").strip() or None
    except Exception:
        return None

    if not extracted:
        return None
    # 신뢰도 검사 — 참가대상 관련 단어가 하나라도 없으면 잘못 추출된 것으로 간주
    if not any(tok in extracted for tok in _TARGET_CONFIDENCE_TOKENS):
        logger.debug(f"loud target 신뢰도 미달 → 무시: {extracted!r}")
        return None
    return extracted


def _fetch_loud_data() -> tuple[str | None, dict[str, str | None], dict[str, str | None]]:
    """로그인 세션 하나에서 목록 HTML + 상세 페이지 포스터/참가대상 반환.

    포스터는 상세 페이지의 iframe[2] 내 img.src에서 추출 (주최자 기관 외부 도메인 호스팅).
    참가대상(target)은 메인 프레임에서 추출 — 신뢰도 미달 시 None(보수적 통과).
    미설정/실패 시 (None, {}, {}) 반환.
    """
    email    = os.environ.get("LOUD_EMAIL", "").strip()
    password = os.environ.get("LOUD_PASSWORD", "").strip()
    if not email or not password:
        logger.info("LOUD_EMAIL/LOUD_PASSWORD 미설정 — 이미지 없이 수집")
        return None, {}, {}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, {}, {}

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})
            page = ctx.new_page()

            # 로그인
            page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)
            page.fill("input[name='email']", email)
            page.fill("input[name='password']", password)
            with page.expect_navigation(wait_until="networkidle", timeout=15000):
                page.click("button:has-text('로그인')")
            if "login" in page.url:
                logger.warning(f"loud.kr 로그인 실패 (URL: {page.url})")
                browser.close()
                return None, {}, {}
            logger.info(f"loud.kr 로그인 성공: {page.url}")

            # 목록 페이지 렌더
            page.goto(LIST_URL, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_selector("a[href*='/contest/view/']", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(2500)
            for _ in range(3):
                page.mouse.wheel(0, 5000)
                page.wait_for_timeout(1000)
            list_html = page.content()

            # 목록에서 접수중인 cid 선추출 (상세 방문 대상)
            soup = BeautifulSoup(list_html, "lxml")
            open_cids: list[str] = []
            seen_pre: set[str] = set()
            for a in soup.find_all("a", href=True):
                m = _ID_RE.search(a.get("href", ""))
                if not m or m.group(1) in seen_pre:
                    continue
                cid = m.group(1)
                h2 = a.find("h2")
                if not h2 or len(clean(h2.get_text())) < 4:
                    continue
                if not _is_open(a):
                    continue
                seen_pre.add(cid)
                open_cids.append(cid)

            # 상세 페이지별 포스터 + 참가대상 추출
            posters: dict[str, str | None] = {}
            targets: dict[str, str | None] = {}
            for cid in open_cids:
                try:
                    page.goto(f"{BASE}/contest/view/{cid}",
                              wait_until="domcontentloaded", timeout=25000)
                    page.wait_for_timeout(2000)
                    # 페이지 끝까지 스크롤 (모든 요소 로드)
                    for _ in range(15):
                        page.mouse.wheel(0, 800)
                        page.wait_for_timeout(300)
                    page.wait_for_timeout(1500)

                    # iframe[2] 내 모든 img.src 추출
                    poster_url = None
                    frames = page.frames
                    if len(frames) > 2:
                        try:
                            frame_imgs = frames[2].evaluate("""
                                () => {
                                    const imgs = Array.from(document.querySelectorAll('img[src]'));
                                    return imgs
                                        .map(i => i.src)
                                        .filter(s => s && !s.includes('/static/') && !s.includes('google') && !s.includes('pagead'));
                                }
                            """)
                            if frame_imgs:
                                poster_url = frame_imgs[0]
                        except Exception:
                            pass

                    # 메인 프레임에서 참가대상 추출 (신뢰도 미달 시 None)
                    target_text = _extract_target(page)

                    posters[cid] = poster_url
                    targets[cid] = target_text
                    if poster_url:
                        logger.debug(f"loud {cid} 포스터: {poster_url[:80]}")
                    if target_text:
                        logger.debug(f"loud {cid} 참가대상: {target_text[:60]}")
                except Exception as e:
                    logger.debug(f"loud 상세 {cid} 렌더 실패: {e}")
                    posters[cid] = None
                    targets[cid] = None

            browser.close()
            return list_html, posters, targets

    except Exception as e:
        logger.warning(f"loud 수집 오류: {e}")
        return None, {}, {}


def _is_open(a) -> bool:
    badge = a.select_one('[class*="sc-kAyceB"]')
    badge_txt = clean(badge.get_text()) if badge else clean(a.get_text(" "))
    if any(kw in badge_txt for kw in ["심사중", "종료", "발표", "접수예정"]):
        return False
    return bool(_OPEN_RE.search(badge_txt))


def _period_end(a) -> date | None:
    el = a.select_one('[class*="date"]')
    if not el:
        return None
    dates = _PERIOD_RE.findall(el.get_text(" "))
    if not dates:
        return None
    yy, mm, dd = dates[-1]
    try:
        return date(2000 + int(yy), int(mm), int(dd))
    except ValueError:
        return None


@register("loud")
def fetch() -> list[ContestDraft]:
    list_html, posters, targets = _fetch_loud_data()
    # 로그인 실패/미설정 시 비로그인 렌더로 폴백(이미지·참가대상 없음)
    if not list_html:
        list_html = render_html(LIST_URL, wait_for="a[href*='/contest/view/']", scrolls=3)
    if not list_html:
        return []

    soup = BeautifulSoup(list_html, "lxml")
    drafts: list[dict] = []
    seen: set[str] = set()
    base_day = today_kst()

    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if "/contest/view/" not in href:
            continue
        m = _ID_RE.search(href)
        if not m:
            continue
        cid = m.group(1)
        if cid in seen:
            continue

        h2 = a.find("h2")
        title = clean(h2.get_text()) if h2 else ""
        if not title or len(title) < 4:
            continue
        if not _is_open(a):
            continue
        seen.add(cid)

        deadline = _period_end(a)
        if deadline is None:
            dm = _DDAY_RE.search(a.get_text(" "))
            if dm:
                deadline = base_day + timedelta(days=int(dm.group(1)))

        drafts.append({
            "cid": cid,
            "title": title,
            "deadline": deadline,
            "image_url": posters.get(cid) if posters else None,
            "target": targets.get(cid) if targets else None,
        })

    if not drafts:
        return []

    with_img = sum(1 for d in drafts if d["image_url"])
    with_target = sum(1 for d in drafts if d["target"])
    logger.info(f"loud: {len(drafts)}건 수집, 이미지 {with_img}건, 참가대상 {with_target}건")

    return [
        ContestDraft(
            source="loud",
            external_id=f"loud:{d['cid']}",
            url=f"{BASE}/contest/view/{d['cid']}",
            title=d["title"][:500],
            image_url=d["image_url"],
            target=d["target"],
            category="공모전",
            field_tags=["AI"],
            deadline=d["deadline"],
            ai_exempt=True,
        )
        for d in drafts
    ]
