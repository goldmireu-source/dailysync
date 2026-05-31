"""공모전 소스 공용 — ContestDraft + 레지스트리 + 헬퍼.

각 소스는 원천 파싱만 담당하고 정규화 전 값을 ContestDraft 로 반환한다.
필터(AI/기업한정/마감) 와 DB 적재는 jobs/contest_collector.py 가 중앙에서 처리.
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
USER_AGENT = "Mozilla/5.0 (compatible; AINewsDigest/0.1; +personal-use)"
FETCH_TIMEOUT = 20

# 소스 레지스트리 — (name, fetch_fn) 튜플 리스트
SOURCES: list[tuple] = []


def register(name: str):
    """소스 fetch 함수를 SOURCES 에 등록하는 데코레이터."""
    def deco(fn):
        SOURCES.append((name, fn))
        return fn
    return deco


@dataclass
class ContestDraft:
    """정규화 전 원천 공고 데이터. Contest 모델 필드와 1:1."""
    source: str
    url: str
    title: str
    external_id: str | None = None
    host: str | None = None
    image_url: str | None = None
    category: str | None = None
    field_tags: list = field(default_factory=list)
    target: str | None = None
    prize: str | None = None
    start_at: date | None = None
    deadline: date | None = None
    posted_at: date | None = None
    # AI 전용 카테고리/플랫폼에서 온 항목 → AI 키워드 게이트 면제
    ai_exempt: bool = False
    # 소스 성격상 기관·기업 대상이 확실한 경우(예: 국가R&D 과제공고) → 기업한정 게이트로 제외
    company_targeted: bool = False


# ---------- HTTP ----------
def http_get(url: str, *, headers: dict | None = None, params: dict | None = None,
             encoding: str | None = None, timeout: int = FETCH_TIMEOUT) -> requests.Response:
    h = {"User-Agent": USER_AGENT, "Accept-Language": "ko,en;q=0.8"}
    if headers:
        h.update(headers)
    resp = requests.get(url, headers=h, params=params, timeout=timeout)
    resp.raise_for_status()
    if encoding:
        resp.encoding = encoding
    return resp


# ---------- 텍스트/날짜 헬퍼 ----------
def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def today_kst() -> date:
    return datetime.now(KST).date()


def parse_dday(text: str | None, *, base: date | None = None) -> date | None:
    """'D-43' / 'D-DAY' / 'D-0' 같은 카운트다운 → 마감 날짜.

    - D-<n>  → 기준일 + n
    - D-DAY / D-0 → 기준일 (오늘)
    - D+<n> / '마감' → 이미 종료 (None 반환 → 게이트에서 탈락)
    파싱 불가 시 None.
    """
    if not text:
        return None
    base = base or today_kst()
    t = text.upper().replace(" ", "")
    if "D-DAY" in t:
        return base
    m = re.search(r"D-(\d+)", t)
    if m:
        return base + timedelta(days=int(m.group(1)))
    if "D+" in t or "마감" in (text or ""):
        return None
    return None


_DATE_PATTERNS = [
    (re.compile(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})"), (0, 1, 2)),
    (re.compile(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일"), (0, 1, 2)),
    (re.compile(r"(\d{8})"), None),  # YYYYMMDD
]


def parse_date(text: str | None) -> date | None:
    """'2026-06-26', '2026.06.26', '2026년 6월 26일', '20260626' → date."""
    if not text:
        return None
    s = text.strip()
    for pat, idx in _DATE_PATTERNS:
        m = pat.search(s)
        if not m:
            continue
        try:
            if idx is None:  # YYYYMMDD
                g = m.group(1)
                return date(int(g[:4]), int(g[4:6]), int(g[6:8]))
            y, mo, d = (int(m.group(i + 1)) for i in idx)
            return date(y, mo, d)
        except (ValueError, IndexError):
            continue
    return None
