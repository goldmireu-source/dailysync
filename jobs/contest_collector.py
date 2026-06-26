"""공모전 통합 수집기.

jobs/contest_sources/ 의 모든 소스를 순회해 ContestDraft 를 모으고,
3가지 추출 게이트(AI 관련 / 기업한정 제외 / 마감 안 지남)를 통과한 것만
Contest 테이블에 upsert. 각 소스는 best-effort(한 소스 실패가 전체를 막지 않음).

게이트는 여기 한 곳(_passes_gates)에 모은다 — 소스 파일은 원천 파싱만 담당.
"""
import hashlib
import logging
import re
from difflib import SequenceMatcher

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
    "기업", "법인", "사업자", "중소기업", "중견기업", "소상공인", "자영업자",
    "벤처기업", "스타트업", "창업기업", "기관", "단체", "운영사", "주관기관",
    "주관연구기관", "연구기관", "연구소", "컨소시엄", "산학협력단", "협회", "재단",
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


# ---------- 게이트 2b: 특정 단체 소속원 한정 ----------
# 참가자격 '원문'에 고유 기관명이 소속/재학/임직원 같은 멤버십 표현과 붙어 있으면
# 그 단체 소속원만 참여 가능 → 제외. 제목/주최가 아니라 '참가자격'으로만 판정한다:
# 'OO대학교 멀티모달 챌린지'처럼 주최가 특정 기관이어도 자격이 '대학(원)생'(학교
# 무관)이면 누구나 참여 가능하므로 통과시켜야 하기 때문.
# (개방 자격 '대학(원)생'·'대학생'·'누구나'는 기관명이 '대'에 붙지 않아 매칭 안 됨 —
#  open/restricted 23개 예시로 검증, 오탐·미탐 0.)
_MEMBER_ONLY_PATTERNS = (
    # 특정 학교 재학생: 'OO대학교 학생' / 'OO대 재학생'(약칭) / 본교·교내
    re.compile(r"[가-힣A-Za-z]{2,}대학교\s*(?:재학생|재학|휴학생|학부생|대학원생|원생|학생|학우)"),
    re.compile(r"[가-힣A-Za-z]{2,}대\s+(?:재학생|휴학생|학부생|대학원생|원생|학우|학생)"),
    re.compile(r"(?:본교|교내)\s*(?:재학생|학생)"),
    # 특정 회사/그룹 소속: 임직원/재직자/회원사/계열사 등
    re.compile(r"임직원|재직자|회원사|계열사|그룹사|당사\s*직원|사내\s*(?:임직원|직원)|자사\s*(?:임직원|직원)"),
    # 'OO 소속원/구성원'
    re.compile(r"[가-힣A-Za-z]{2,}\s*(?:소속원|구성원)"),
)


def _is_members_only(target: str | None) -> bool:
    """참가자격이 특정 단체 소속원(특정 학교 재학생·특정 회사 임직원 등)에만
    열려 있으면 True(→ 제외). 자격 미상(None)이면 False(보수적 통과)."""
    if not target:
        return False
    return any(p.search(target) for p in _MEMBER_ONLY_PATTERNS)


# ---------- 게이트 2c: 일반인 개방 여부 ----------
# 정책: '일반인에게 열린' 공모전만 남긴다. 참가대상이 학생·청소년처럼 특정 대상층에만
# 한정되고 일반인은 참여 불가하면 제외. (예: 대학(원)생 한정, 특정 사 임직원 한정)
_PUBLIC_OPEN_TOKENS = (
    "누구나", "누구든", "일반인", "일반 성인",
    "전국민", "전 국민", "국민", "시민", "내국인", "성인",
)
# '제한없음' / '제한 없음' 은 "전공 및 학년 제한 없음" 처럼 세부조건(전공·학년·나이 등)
# 해제에도 쓰여 단순 서브스트링 매칭 시 오탐이 생긴다.
# → 앞 20자 이내에 세부 한정어가 없을 때만 '참가 개방' 신호로 인정한다.
_RESTRICT_NONE_RE = re.compile(r"제한\s?없음")
_SCOPE_QUALIFIERS = (
    "전공", "학년", "나이", "성별", "지역", "연령", "국적", "소속", "나이대",
    "분야", "주제", "카테고리", "출품", "제출",  # 공모 분야·주제 제한해제 오탐 방지
)


def _is_open_to_public(target: str | None) -> bool:
    """참가대상에 일반인 개방 신호가 있으면 True. 미상(None)이면 True(보수적 통과).

    '당사 임직원 및 일반인'처럼 특정 대상이 끼어 있어도 일반인이 포함되면 통과 —
    그래서 이 검사를 소속/학생 한정 검사보다 '먼저' 본다.
    """
    if not target:
        return True
    if any(tok in target for tok in _PUBLIC_OPEN_TOKENS):
        return True
    # "제한없음/제한 없음" — 세부조건 해제가 아닌 참가 개방 맥락인지 확인
    for m in _RESTRICT_NONE_RE.finditer(target):
        window = target[max(0, m.start() - 20): m.start()]
        if not any(w in window for w in _SCOPE_QUALIFIERS):
            return True
    return False


# ---------- 게이트 0b: 제목 기반 학교급/청소년 제한 감지 ----------
# target 추출에 실패해 None 인 경우에도 제목만으로 명백히 판별 가능한 패턴 차단.
# "(중고등부)", "청소년 경진대회" 같은 강한 신호만 매칭 — 오탐 최소화.
_TITLE_RESTRICTED_RE = re.compile(
    r"[(\[]\s*(?:초등|중등|중학|고등|중고등|초중고|초·중|중·고)\s*부\s*[)\]]"  # (중고등부), [고등부]
    r"|청소년\s*(?:경진|공모|챌린지|대회)"                                      # 청소년 경진대회
    r"|중고등학생|초등학생|중학생|고등학생"                                       # 학년 직접 명시
    r"|(?:초등학교|중학교|고등학교|중고등학교)\s*(?:학생\s*)?(?:경진|공모|챌린지|대회|전용|한정|대상)"
)


# ---------- 제목 기반 사업자 참가 주체 추론 ----------
# target=None 인 공고에서도 소상공인·자영업자가 '참가자(주체)'임을 제목에서 추론.
# 추론 결과를 _is_company_only() 에 그대로 넘겨 기존 필터와 동일하게 처리한다.
#
# 핵심 구분:
#  - 참가자(주체): "소상공인 AI 활용지원사업 소상공인 모집"
#                  "소상공인 AI 경진대회" (소상공인이 직접 참가)
#    → 추론 결과 "소상공인" 반환 → company_only 로 탈락
#  - 수혜자(대상):  "소상공인을 위한 AI 서비스 공모전" (개발자가 소상공인 위해 개발)
#    → 수혜자 구문 감지 후 None 반환 → 통과
#
# 이 방식이 _TITLE_BUSINESS_EXCL_RE 같은 패턴 열거보다 근본적인 이유:
#   소상공인이 참가자여야 하는 공모전이라면 이름/표현에 관계없이 제목에 '소상공인'이
#   수혜자 구문 없이 나타남 → 제목 형태를 일일이 열거할 필요 없음.

# 소상공인이 '서비스/솔루션을 제공받는 쪽'임을 나타내는 수혜자 구문
_TITLE_BENEFICIARY_RE = re.compile(
    r"소상공인.{0,6}(?:을|를)\s*(?:위한|위해|돕|지원)"   # 소상공인을 위한, 소상공인을 돕는
    r"|(?:소상공인|자영업자).{0,6}(?:서비스|솔루션|도구|앱)\s*(?:개발|제공|공모)"
    r"|을\s*위한\s*(?:소상공인|자영업자)"                 # ~을 위한 소상공인
)

# 제목에서 탐색할 사업자 토큰 — 소상공인·자영업자는 참가자 맥락이 뚜렷해 포함
_COMPANY_AUDIENCE_TOKENS = ("소상공인", "자영업자")


def _infer_company_target_from_title(title: str) -> str | None:
    """target=None일 때 제목에서 사업자가 참가 주체임을 추론.

    소상공인·자영업자가 수혜자 구문 없이 제목에 등장하면 해당 토큰 반환.
    반환값을 _is_company_only() 에 넘기면 기존 company 게이트와 동일하게 처리된다.
    수혜자 구문이 있거나 일반인 개방 신호가 있으면 None → 보수적 통과.
    """
    if not title:
        return None
    if _TITLE_BENEFICIARY_RE.search(title):
        return None
    if any(tok in title for tok in _PUBLIC_OPEN_TOKENS):
        return None
    for tok in _COMPANY_AUDIENCE_TOKENS:
        if tok in title:
            return tok
    return None


# ---------- 게이트 3: 마감 안 지남 ----------
def _deadline_ok(deadline) -> bool:
    """deadline 이 오늘(KST) 이후이거나 미상(None)이면 통과."""
    if deadline is None:
        return True  # 미상 → '마감 미정'으로 유지(표시에서 뒤로)
    return deadline >= today_kst()


def _passes_gates(draft) -> tuple[bool, str]:
    """게이트 통과 여부 + 탈락 사유."""
    # 0. AI 사용 금지 명시 — 'AI 활용' 공모전 큐레이션 취지에 반함
    if draft.ai_prohibited:
        return False, "ai_prohibited"
    # 0b. 제목에서 학교급/청소년 대상 명시 — target 미추출 시에도 사전 차단
    if draft.title and _TITLE_RESTRICTED_RE.search(draft.title):
        return False, "not_public"
    # 1. AI 관련 (AI 전용 카테고리/플랫폼은 면제)
    if not draft.ai_exempt:
        hay = " ".join(filter(None, [draft.title, " ".join(draft.field_tags or []), draft.host or ""]))
        if not _is_ai_relevant(hay):
            return False, "not_ai"
    # 2. 소스가 기관/기업 대상으로 명시 (국가R&D 과제 등)
    if draft.company_targeted:
        return False, "company_only"
    # 3. 참가대상 판정
    #    target 있으면 그 값, 없으면 제목에서 사업자 참가 주체 추론(소상공인·자영업자).
    #    추론값도 _is_company_only 등 기존 로직과 동일하게 처리 — 별도 gate 불필요.
    effective_target = draft.target or _infer_company_target_from_title(draft.title)
    if effective_target and not _is_open_to_public(effective_target):
        if _is_company_only(effective_target):
            return False, "company_only"
        if _is_members_only(effective_target):
            return False, "members_only"
        return False, "not_public"  # 학생·청소년 등 특정 대상층 한정
    # 4. 마감 안 지남
    if not _deadline_ok(draft.deadline):
        return False, "expired"
    return True, "ok"


def _hash_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _upsert(draft) -> str:
    """draft → Contest upsert. return 'new' | 'updated' | 'skip'.

    이미 게시된 공모전(수동 추가·이미지 직접 작업 포함)의 '관리자 작업물'은
    건드리지 않는다: image_url·image_pos_x/y·image_scale 은 관리자가 직접 만든
    결과이므로 재수집 시 비어 있을 때만 채우고 덮어쓰지 않는다(과거엔 소스
    핫링크로 덮어써서 작업물이 사라졌음).
    단 deadline·start_at·target(참가대상)은 '데이터'라 소스의 최신(교차검증) 값으로
    갱신한다 — 안 그러면 초기 수집 때의 부정확한 값(목록 D-day ±1일 오차, 추출
    잔여물 섞인 참가대상 등)이 영구히 굳는다. (추출 실패로 None 이면 덮지 않음)
    (마감 지난 공모전 자동 삭제는 cleanup 잡이 deadline 기준으로 계속 수행)
    """
    url_hash = _hash_url(draft.url)
    existing = Contest.query.filter_by(url_hash=url_hash).first()
    if existing:
        # 기존 타일 보존 — 관리자 작업물은 덮지 않되, 마감/시작일은 최신값으로 갱신.
        filled = False
        if draft.deadline and existing.deadline != draft.deadline:
            existing.deadline = draft.deadline
            filled = True
        if draft.start_at and existing.start_at != draft.start_at:
            existing.start_at = draft.start_at
            filled = True
        if not existing.image_url and draft.image_url:
            existing.image_url = draft.image_url
            filled = True
        if draft.target and existing.target != draft.target:
            # 마감일과 동일 정책 — 참가대상은 '데이터'라 소스 최신값으로 갱신.
            # (None 추출 실패 시엔 덮지 않음 → 기존 값 보존)
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


# 문자 유사도 임계값 — 측정상 같은 공모전은 0.98+, 다른 공모전(접미사 긴 '공공데이터
# AI 경진대회' 계열 포함)은 ≤0.77 로 간극이 큼. 0.90 이면 안전하게 갈린다.
_SIMILARITY_THRESHOLD = 0.90


def _same_contest(a: str, b: str) -> bool:
    """두 정규화 제목이 같은 공모전인지.

    완전일치 / 한쪽이 다른 쪽을 포함(짧은 쪽 ≥10자) / 문자 유사도 ≥ 임계값.
    유사도 보강 이유: '공모' vs '공모전', '·' vs 공백처럼 글자 1~2개가 제목
    '중간'에서 달라지면 부분문자열 포함 판정이 깨져 같은 공모전이 안 합쳐진다.
    (짧은 제목의 우연한 유사 매칭을 막으려 둘 다 ≥12자일 때만 유사도 적용.)
    """
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= 10 and short in long:
        return True
    if len(short) >= 12 and SequenceMatcher(None, a, b).ratio() >= _SIMILARITY_THRESHOLD:
        return True
    return False


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


def purge_restricted_contests() -> int:
    """참가대상이 '일반인 비개방'으로 판정된 unsaved 공모전을 제거. 삭제 수 반환.

    게이트는 '수집 시점'에만 걸러서, 이미 적재된 행이나 정책 변경 전에 들어온 행은
    그대로 남는다(upsert 는 통과 draft 만 건드림). 그걸 소급 정리한다.
    저장(saved)된 것은 보존. target 미상(None)이어도 제목 패턴으로 판정.
    """
    rows = Contest.query.filter(Contest.saved_at.is_(None)).all()
    deleted = 0
    for c in rows:
        # 1) 제목에서 학교급/청소년 제한 감지
        if c.title and _TITLE_RESTRICTED_RE.search(c.title):
            db.session.delete(c)
            deleted += 1
            continue
        # 2) 제목에서 소상공인·자영업자 참가 주체 감지 — target 필드 우선 정책의 허점 보완.
        #    contestkorea 등 일부 소스가 '일반인'을 target 에 기재해도 제목이 참가 주체를
        #    명시하면 우선 삭제 (target 필드보다 제목이 더 직접적인 증거).
        title_inferred = _infer_company_target_from_title(c.title)
        if title_inferred and _is_company_only(title_inferred):
            db.session.delete(c)
            deleted += 1
            continue
        # 3) target 필드 기반 판정 (제목 추론은 target=None 일 때만 보조)
        effective_target = c.target or title_inferred
        if effective_target and not _is_open_to_public(effective_target):
            db.session.delete(c)
            deleted += 1
    if deleted:
        db.session.commit()
    return deleted


def collect_all_contests() -> dict:
    """전 소스 수집 → 게이트 → upsert. stats 반환."""
    stats: dict = {
        "sources": {},
        "total_fetched": 0, "total_new": 0, "total_updated": 0,
        "rejected": {"ai_prohibited": 0, "not_ai": 0, "company_only": 0, "members_only": 0, "not_public": 0, "expired": 0},
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
    # 4. 일반인 비개방(학생·청소년·소속원 한정) 소급 정리
    stats["purged_restricted"] = purge_restricted_contests()
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
