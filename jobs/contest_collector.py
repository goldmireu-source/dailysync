"""공모전 통합 수집기.

jobs/contest_sources/ 의 모든 소스를 순회해 ContestDraft 를 모으고,
3가지 추출 게이트(AI 관련 / 기업한정 제외 / 마감 안 지남)를 통과한 것만
Contest 테이블에 upsert. 각 소스는 best-effort(한 소스 실패가 전체를 막지 않음).

게이트는 여기 한 곳(_passes_gates)에 모은다 — 소스 파일은 원천 파싱만 담당.
"""
import hashlib
import logging
import re

from app import create_app
from config import Config
from models import db, Contest
from jobs.contest_sources import SOURCES
from jobs.contest_sources.base import today_kst

logger = logging.getLogger(__name__)


# ---------- 게이트 1: AI 관련 ----------
# 공모전 맥락의 데이터/AI 키워드 — news_collector 의 AI 키워드에 더해 보강.
# (공모전 제목엔 'AI 활용', '빅데이터', '데이터 분석' 류 표현이 흔함)
_CONTEST_AI_KEYWORDS = {
    "ai", "a.i", "인공지능", "에이아이", "머신러닝", "딥러닝", "생성형", "생성ai",
    "llm", "gpt", "챗봇", "빅데이터", "데이터분석", "데이터 분석", "데이터활용",
    "데이터 활용", "자연어", "컴퓨터비전", "캐글", "ai활용", "ai 활용", "ai기반",
    "ai 기반", "ml", "데이터사이언스", "데이터 사이언스",
}


def _is_ai_relevant(text: str) -> bool:
    """제목+분야+주최 합산 텍스트에 AI/데이터 키워드가 있는지.

    news_collector 의 AI 키워드 세트를 재사용하고, 공모전 특화 키워드를 더한다.
    """
    from jobs.news_collector import AI_KEYWORDS_KO, AI_KEYWORDS_EN
    hay = (text or "").lower()
    if any(kw in hay for kw in _CONTEST_AI_KEYWORDS):
        return True
    return any(kw in hay for kw in AI_KEYWORDS_KO) or any(kw in hay for kw in AI_KEYWORDS_EN)


# ---------- 게이트 2: 참여대상이 기업에 국한 ----------
_COMPANY_TOKENS = (
    "기업", "법인", "사업자", "중소기업", "중견기업", "소상공인", "벤처기업",
    "스타트업", "창업기업", "기관", "단체", "운영사", "주관기관", "주관연구기관",
    "연구기관", "연구소", "컨소시엄", "산학협력단", "협회", "재단",
)
_INDIVIDUAL_TOKENS = (
    "개인", "대학생", "대학원생", "일반", "누구나", "제한없음", "제한 없음",
    "팀", "학생", "청년", "예비창업", "시민", "국민", "내국인", "외국인",
    "재학생", "졸업생", "전공자", "직장인", "성인",
)


def _is_company_only(target: str | None) -> bool:
    """참가대상이 기업/기관에만 국한되면 True(→ 제외).

    target 미상이면 False(개인 참여 가능 공모전이 기본값 → 통과).
    기업 토큰이 있어도 개인 토큰이 하나라도 있으면 False(개인 참여 허용).
    """
    if not target:
        return False
    t = target
    if any(tok in t for tok in _INDIVIDUAL_TOKENS):
        return False
    return any(tok in t for tok in _COMPANY_TOKENS)


# ---------- 게이트 3: 마감 안 지남 ----------
def _deadline_ok(deadline) -> bool:
    """deadline 이 오늘(KST) 이후이거나 미상(None)이면 통과."""
    if deadline is None:
        return True  # 미상 → '마감 미정'으로 유지(표시에서 뒤로)
    return deadline >= today_kst()


def _passes_gates(draft) -> tuple[bool, str]:
    """3 게이트 통과 여부 + 탈락 사유."""
    # 1. AI 관련 (AI 전용 카테고리/플랫폼은 면제)
    if not draft.ai_exempt:
        hay = " ".join(filter(None, [draft.title, " ".join(draft.field_tags or []), draft.host or ""]))
        if not _is_ai_relevant(hay):
            return False, "not_ai"
    # 2. 기업 한정 대상 제외 (소스가 기관/기업 대상으로 명시했거나, target 텍스트가 기업 한정)
    if draft.company_targeted or _is_company_only(draft.target):
        return False, "company_only"
    # 3. 마감 안 지남
    if not _deadline_ok(draft.deadline):
        return False, "expired"
    return True, "ok"


def _hash_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _upsert(draft) -> str:
    """draft → Contest upsert. return 'new' | 'updated' | 'skip'.

    이미 게시된 공모전(수동 추가·이미지 직접 작업 포함)은 '건드리지 않는다':
    기존 값은 절대 덮어쓰지 않고, 비어 있는(None) 칸만 보강한다.
    특히 image_url·image_pos_x/y·image_scale 은 관리자가 직접 작업한 결과이므로
    재수집 시 손대지 않는다(과거엔 소스 핫링크로 덮어써서 작업물이 사라졌음).
    (마감 지난 공모전 자동 삭제는 cleanup 잡이 deadline 기준으로 계속 수행)
    """
    url_hash = _hash_url(draft.url)
    existing = Contest.query.filter_by(url_hash=url_hash).first()
    if existing:
        # 기존 타일 보존 — 비어 있던 필드만 채움(덮어쓰기 금지).
        filled = False
        if existing.deadline is None and draft.deadline:
            existing.deadline = draft.deadline
            filled = True
        if not existing.image_url and draft.image_url:
            existing.image_url = draft.image_url
            filled = True
        if not existing.target and draft.target:
            existing.target = draft.target
            filled = True
        if not existing.host and draft.host:
            existing.host = draft.host
            filled = True
        return "updated" if filled else "skip"

    db.session.add(Contest(
        source=draft.source,
        external_id=draft.external_id,
        url=draft.url,
        url_hash=url_hash,
        title=draft.title[:500],
        host=draft.host,
        image_url=draft.image_url,
        category=draft.category or "공모전",
        field_tags=draft.field_tags or [],
        target=draft.target,
        prize=draft.prize,
        start_at=draft.start_at,
        deadline=draft.deadline,
        posted_at=draft.posted_at,
        is_ai_relevant=True,
        summary_dirty=True,
    ))
    return "new"


# ---------- 중복 제거 (소스 간) ----------
def _norm_title(t: str) -> str:
    """제목 정규화 — 공백·기호 제거, 소문자화. 한글은 \\w 라 보존됨.
    예: '[무신사] 무진장 성공 기원 AI 영상 광고제' → '무신사무진장성공기원ai영상광고제'."""
    return re.sub(r"[\s\W_]+", "", (t or "").lower())


def _same_contest(a: str, b: str) -> bool:
    """두 정규화 제목이 같은 공모전인지 — 완전일치 또는 한쪽이 다른 쪽을 포함(짧은 쪽 ≥10자)."""
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 10 and short in long


def _is_user_worked(c) -> bool:
    """관리자가 직접 작업한 타일인지 — 수동 추가거나 업로드 이미지 보유."""
    return c.source == "manual" or bool(c.image_url and "/uploads/contests/" in c.image_url)


def _pick_keeper(group: list):
    """그룹에서 남길 1건 선택.

    사용자 작업물을 우선 보존하고, 새로 긁힌 타일이 기존 타일을 밀어내지
    않도록 결정적으로(랜덤 X) 고른다. 동률이면 더 오래된(기존) id 우선.
    우선순위: 저장됨 > 수동추가/업로드이미지 > 이미지 있음 > 기존(낮은 id).
    """
    saved = [c for c in group if c.saved_at is not None]
    if saved:
        return min(saved, key=lambda c: c.id)
    worked = [c for c in group if _is_user_worked(c)]
    if worked:
        return min(worked, key=lambda c: c.id)
    with_img = [c for c in group if c.image_url]
    if with_img:
        return min(with_img, key=lambda c: c.id)
    return min(group, key=lambda c: c.id)


def dedup_contests() -> int:
    """DB의 공모전을 제목 기준으로 묶어 그룹당 1건만 남기고 삭제. 삭제 수 반환."""
    contests = Contest.query.all()
    remaining = list(contests)
    deleted = 0
    while remaining:
        c = remaining.pop(0)
        nc = _norm_title(c.title)
        group, rest = [c], []
        for o in remaining:
            if _same_contest(nc, _norm_title(o.title)):
                group.append(o)
            else:
                rest.append(o)
        remaining = rest
        if len(group) < 2:
            continue
        keeper = _pick_keeper(group)
        for o in group:
            if o.id != keeper.id:
                db.session.delete(o)
                deleted += 1
    if deleted:
        db.session.commit()
    return deleted


def collect_all_contests() -> dict:
    """전 소스 수집 → 게이트 → upsert. stats 반환."""
    stats: dict = {
        "sources": {},
        "total_fetched": 0, "total_new": 0, "total_updated": 0,
        "rejected": {"not_ai": 0, "company_only": 0, "expired": 0},
    }

    # 1. 소스별 수집 (best-effort)
    all_drafts = []
    for name, fetch_fn in SOURCES:
        s = {"fetched": 0, "new": 0, "updated": 0, "error": None}
        try:
            drafts = fetch_fn() or []
            s["fetched"] = len(drafts)
            all_drafts.extend(drafts)
        except Exception as e:
            s["error"] = str(e)[:200]
            logger.exception(f"contest source {name} failed")
        stats["sources"][name] = s
        stats["total_fetched"] += s["fetched"]

    # 2. 게이트 + dedup + upsert
    seen_hashes: set[str] = set()
    for draft in all_drafts:
        if not draft.url or not draft.title:
            continue
        ok, reason = _passes_gates(draft)
        if not ok:
            stats["rejected"][reason] = stats["rejected"].get(reason, 0) + 1
            continue
        h = _hash_url(draft.url)
        if h in seen_hashes:
            continue
        seen_hashes.add(h)
        try:
            result = _upsert(draft)
        except Exception:
            db.session.rollback()
            logger.exception(f"contest upsert failed: {draft.url}")
            continue
        if result == "new":
            stats["total_new"] += 1
            stats["sources"][draft.source]["new"] += 1
        elif result == "updated":
            stats["total_updated"] += 1
            stats["sources"][draft.source]["updated"] += 1

    db.session.commit()

    # 3. 소스 간 중복 제거 (이미지 보유 우선 보존, 동률 랜덤)
    stats["deduped"] = dedup_contests()
    return stats


if __name__ == "__main__":
    app = create_app(with_scheduler=False)
    with app.app_context():
        print(f"공모전 수집 — 소스 {len(SOURCES)}개 "
              f"(DATA_GO_KR_KEY {'있음' if Config.DATA_GO_KR_KEY else '없음 → kstartup skip'})")
        st = collect_all_contests()
        print(f"\n총 fetched={st['total_fetched']} new={st['total_new']} updated={st['total_updated']} dedup삭제={st.get('deduped', 0)}")
        print(f"탈락: {st['rejected']}")
        for name, s in st["sources"].items():
            err = f"  ⚠️ {s['error']}" if s["error"] else ""
            print(f"  {name:<14} fetched={s['fetched']:>3} new={s['new']:>3} updated={s['updated']:>3}{err}")
