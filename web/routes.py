"""Web dashboard routes — 다이제스트 + 클러스터 상세 + 숨김 기능 + admin."""
import os
import re
import secrets
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone, date
from functools import wraps
from time import time as _time

from flask import Blueprint, render_template, abort, request, redirect, url_for, jsonify, g, current_app
from flask_login import current_user, login_user, logout_user, login_required
from werkzeug.utils import secure_filename

from config import Config
from models import db, Cluster, Article, Paper, Source, JobRun, Contest, AdminUser, Party, PartyMember, PartyMessage, UserActivity, KarrotPost, KarrotApplication, AppSetting, UserBookmark
from web.cardnews import build_cluster_cards, build_paper_cards, build_contest_tile

bp = Blueprint("web", __name__)

KST = timezone(timedelta(hours=9))

# ===== 인-메모리 레이트 리미터 (단일 프로세스 전용) =====
_rl_lock = threading.Lock()
_rl_store: dict = defaultdict(list)

def _rate_ok(key: str, limit: int, window_sec: int) -> bool:
    """True=허용, False=차단. window_sec 내 limit 회 초과 시 차단."""
    now = _time()
    with _rl_lock:
        _rl_store[key] = [t for t in _rl_store[key] if now - t < window_sec]
        if len(_rl_store[key]) >= limit:
            return False
        _rl_store[key].append(now)
        return True

def _client_ip() -> str:
    """요청 클라이언트 IP (리버스 프록시 고려)."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"

def _sec_warn(event: str, detail: str = "") -> None:
    """보안 이벤트 로깅."""
    ip = _client_ip()
    uid = current_user.get_id() if current_user.is_authenticated else "-"
    current_app.logger.warning(f"[SECURITY:{event}] ip={ip} uid={uid} {detail}")

def _log_activity(action: str, user_id=None, username: str = "", detail: str = "") -> None:
    """회원 활동 이력 DB 기록 (실패해도 메인 흐름 무중단)."""
    try:
        uid = user_id
        uname = username
        if uid is None and current_user.is_authenticated:
            uid = current_user.id
        if not uname and current_user.is_authenticated:
            uname = current_user.username
        record = UserActivity(
            user_id=uid,
            username=uname or None,
            action=action,
            ip=_client_ip(),
            detail=detail or None,
        )
        db.session.add(record)
        db.session.commit()
    except Exception as exc:
        current_app.logger.error(f"[ACTIVITY_LOG_ERROR] {exc}")

# 아이디 허용 문자: 한글·영문·숫자·밑줄
_USERNAME_RE = re.compile(r'^[a-zA-Z0-9가-힣_]+$')


def is_admin() -> bool:
    """현재 요청이 admin 권한인지 (로그인 + role='admin' 모두 충족)."""
    return current_user.is_authenticated and current_user.role == "admin"


def _get_setting(key: str, default: str = "") -> str:
    """app_settings 테이블에서 값 조회. 없으면 default 반환."""
    row = AppSetting.query.get(key)
    return row.value if row else default


def _set_setting(key: str, value: str) -> None:
    """app_settings 테이블에 값 저장 (upsert)."""
    row = AppSetting.query.get(key)
    if row:
        row.value = value
    else:
        db.session.add(AppSetting(key=key, value=value))
    db.session.commit()


def admin_required(f):
    """admin role 만 통과시키는 데코레이터.

    미로그인 → 로그인 페이지, user role → 홈으로, API/POST → 403.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("web.admin_login"))
        if not is_admin():
            is_api = request.path.startswith("/api/") or request.path.startswith("/admin/run/")
            if is_api or request.method == "POST":
                return jsonify({"ok": False, "error": "admin_only"}), 403
            return redirect(url_for("web.index"))
        return f(*args, **kwargs)
    return wrapper


@bp.before_request
def inject_admin_flag():
    """모든 요청 시작 시 g.is_admin / g.user_authenticated 세팅 (매크로에서 쓸 수 있게)."""
    g.is_admin = is_admin()
    g.user_authenticated = current_user.is_authenticated


@bp.app_context_processor
def context_admin():
    """모든 템플릿에서 is_admin + cardnews_bot URL + 보관함 카운트 자동 노출."""
    bm_count = 0
    if current_user.is_authenticated:
        try:
            bm_count = UserBookmark.query.filter_by(user_id=current_user.id).count()
        except Exception:
            pass
    return {
        "is_admin": is_admin(),
        "cardnews_bot_url": Config.CARDNEWS_BOT_URL,
        "timedelta": timedelta,
        "bm_count": bm_count,
    }


# ---------- Admin Login / Register ----------
@bp.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    """아이디/비밀번호 로그인 폼. AJAX(X-Requested-With: fetch) 시 JSON 반환."""
    ajax = request.headers.get("X-Requested-With") == "fetch"

    if current_user.is_authenticated:
        dest = url_for("web.admin") if is_admin() else url_for("web.index")
        return jsonify({"ok": True, "redirect": dest}) if ajax else redirect(dest)

    error = None
    next_url = request.args.get("next") or ""
    if request.method == "POST":
        ip = _client_ip()
        if not _rate_ok(f"login:{ip}", 8, 300):
            _sec_warn("LOGIN_RATE", "로그인 시도 초과")
            error = "잠시 후 다시 시도해주세요. (5분간 8회 제한)"
            if ajax:
                return jsonify({"error": error}), 429
        else:
            username = (request.form.get("username") or "").strip()[:14]
            password = (request.form.get("password") or "")[:10]
            next_url = request.form.get("next") or ""
            if next_url and (not next_url.startswith("/") or next_url.startswith("//")):
                next_url = ""
            user = AdminUser.query.filter_by(username=username).first()
            if user and user.check_password(password):
                login_user(user, remember=True)
                _log_activity("login_ok", user_id=user.id, username=user.username)
                dest = next_url or (url_for("web.admin") if is_admin() else url_for("web.index"))
                return jsonify({"ok": True, "redirect": dest}) if ajax else redirect(dest)
            error = "아이디 또는 비밀번호가 올바르지 않습니다."
            _log_activity("login_fail", username=username)
            _sec_warn("LOGIN_FAIL", f"username={username!r}")
        if ajax:
            return jsonify({"error": error}), 400

    return render_template("admin_login.html", error=error, next_url=next_url)


@bp.route("/admin-register", methods=["GET", "POST"])
def admin_register():
    """신규 회원가입 폼. 자동 승인. AJAX(X-Requested-With: fetch) 시 JSON 반환."""
    ajax = request.headers.get("X-Requested-With") == "fetch"
    error = None
    if request.method == "POST":
        ip = _client_ip()
        if not _rate_ok(f"register:{ip}", 3, 3600):
            _sec_warn("REGISTER_RATE", "회원가입 시도 초과")
            error = "잠시 후 다시 시도해주세요. (1시간에 3회 제한)"
            if ajax:
                return jsonify({"error": error}), 429
            return render_template("admin_register.html", error=error)

        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "")
        password_confirm = (request.form.get("password_confirm") or "")
        display_name = (request.form.get("display_name") or "").strip()
        class_num_raw = request.form.get("class_num") or ""

        try:
            class_num = int(class_num_raw) if class_num_raw else None
        except ValueError:
            class_num = None

        if not username or not password or not display_name:
            error = "모든 항목을 입력해주세요."
        elif class_num is None or class_num not in range(1, 8):
            error = "반을 선택해주세요."
        elif not _USERNAME_RE.match(username):
            error = "아이디는 한글·영문·숫자·밑줄(_)만 사용할 수 있습니다."
            _sec_warn("REGISTER_BADNAME", f"username={username!r}")
        elif password != password_confirm:
            error = "비밀번호가 일치하지 않습니다."
        elif len(username) > 14:
            error = "아이디는 최대 14자입니다."
        elif len(password) > 10:
            error = "비밀번호는 최대 10자입니다."
        elif AdminUser.query.filter_by(username=username).first():
            error = "이미 사용 중인 아이디입니다."
        else:
            has_korean = any('가' <= c <= '힣' or 'ㄱ' <= c <= 'ㅣ' for c in display_name)
            if has_korean and len(display_name) > 4:
                error = "이름은 한글 포함 시 최대 4글자입니다."
            elif not has_korean and len(display_name) > 14:
                error = "이름은 영문 기준 최대 14자입니다."
            else:
                role = "admin" if username == "admin" else "user"
                new_user = AdminUser(
                    username=username, display_name=display_name,
                    role=role, class_num=class_num,
                )
                new_user.set_password(password)
                db.session.add(new_user)
                db.session.commit()
                login_user(new_user, remember=True)
                _log_activity("register", user_id=new_user.id, username=new_user.username)
                dest = url_for("web.admin") if role == "admin" else url_for("web.index")
                return jsonify({"ok": True, "redirect": dest}) if ajax else redirect(dest)

        if ajax:
            return jsonify({"error": error}), 400

    return render_template("admin_register.html", error=error)


@bp.route("/admin-logout")
def admin_logout():
    if current_user.is_authenticated:
        _log_activity("logout")
    logout_user()
    return redirect(url_for("web.index"))


# ---------- 우선순위 정렬 키 ----------
def _cluster_score(cluster: Cluster) -> float:
    """디버그/표시용 단일 점수 (실제 정렬엔 _cluster_sort_key 사용)."""
    score = float(cluster.importance or 0)
    members = cluster.articles.all()
    n_sources = len(set(a.source_id for a in members))
    if len(members) >= 2:
        score += 3
    if n_sources >= 2:
        score += 2
    if any((a.source and a.source.tier == 1) for a in members):
        score += 1
    return score


def _cluster_sort_key(cluster: Cluster) -> tuple:
    """기본 정렬 — 교차검증(매체수) 우선, 그 다음 중요도, 그 다음 최신.

    내림차순 정렬을 위해 모두 -값.
    """
    members = cluster.articles.all()
    n_sources = len(set(a.source_id for a in members))
    importance = int(cluster.importance or 0)
    has_tier1 = any((a.source and a.source.tier == 1) for a in members)
    return (
        -n_sources,            # 1차: 매체 수 (교차검증)
        -importance,           # 2차: 중요도
        -1 if has_tier1 else 0,  # 3차: Tier 1 포함 시 가산
        -cluster.id,           # 4차: 최신
    )


# ---------- 날짜 헬퍼 ----------
def _kst_day_bounds(d: date) -> tuple[datetime, datetime]:
    start_kst = datetime(d.year, d.month, d.day, tzinfo=KST)
    end_kst = start_kst + timedelta(days=1)
    return (
        start_kst.astimezone(timezone.utc).replace(tzinfo=None),
        end_kst.astimezone(timezone.utc).replace(tzinfo=None),
    )


def _parse_date_arg(s: str | None) -> date:
    if s:
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            pass
    return datetime.now(KST).date()


# ---------- 다이제스트 메인 ----------
@bp.route("/")
def index():
    target = _parse_date_arg(request.args.get("date"))
    show_hidden = request.args.get("hidden") == "1"
    date_to_str = request.args.get("date_to")
    target_to = _parse_date_arg(date_to_str) if date_to_str else target
    if target_to < target:
        target_to = target
    if target_to > datetime.now(KST).date():
        target_to = datetime.now(KST).date()
    # tab 은 아래 클러스터 쿼리보다 앞에서 미리 읽어야 articles_flat 분기에 활용 가능
    tab = request.args.get("tab", "contests")
    if tab not in ("news", "papers", "contests", "parties", "karrot"):
        tab = "contests"

    # 인사교당근 공개 여부 — 비공개 시 관리자 외 접근 차단
    karrot_enabled = _get_setting("karrot_enabled", "false") == "true"
    if tab == "karrot" and not karrot_enabled and not is_admin():
        tab = "contests"

    # 회원별 즐겨찾기 ID 세트 (북마크 버튼 상태 표시용)
    user_bm_contest_ids = user_bm_cluster_ids = user_bm_paper_ids = set()
    if current_user.is_authenticated:
        try:
            _bms = UserBookmark.query.filter_by(user_id=current_user.id).all()
            user_bm_contest_ids = {b.item_id for b in _bms if b.item_type == "contest"}
            user_bm_cluster_ids = {b.item_id for b in _bms if b.item_type == "cluster"}
            user_bm_paper_ids = {b.item_id for b in _bms if b.item_type == "paper"}
        except Exception:
            pass

    start_utc, _ = _kst_day_bounds(target)
    _, end_utc = _kst_day_bounds(target_to)

    # 해당 KST 날짜에 발행된 article 들의 cluster (엄격히 당일만)
    arts_today = (
        Article.query
        .filter(Article.published_at >= start_utc, Article.published_at < end_utc)
        .filter(Article.cluster_id.isnot(None))
        .all()
    )

    # 뉴스 탭: 오늘 발행된 모든 기사 개별 표시 (클러스터 여부 무관)
    if tab == "news":
        _arts_all = (
            Article.query
            .filter(Article.published_at >= start_utc, Article.published_at < end_utc)
            .filter(Article.is_ai_relevant == True)
            .order_by(Article.published_at.desc())
            .all()
        )
        articles_flat = [
            {
                "id": a.id,
                "title": a.title or "(제목 없음)",
                "url": a.url,
                "source_name": a.source.name if a.source else "?",
                "source_tier": a.source.tier if a.source else 1,
                "pub_kst": (a.published_at + timedelta(hours=9)).strftime("%H:%M")
                           if a.published_at else "",
                "cluster_id": a.cluster_id,
            }
            for a in _arts_all
        ]
        total_articles_flat = len(articles_flat)
    else:
        articles_flat = []
        total_articles_flat = 0
    cluster_ids = sorted(set(a.cluster_id for a in arts_today))

    base_q = (
        Cluster.query
        .filter(Cluster.id.in_(cluster_ids))
        .filter(Cluster.summary_ko.isnot(None), Cluster.summary_ko != "")
    )

    # first_shown_date 중복 필터링:
    # - 이미 다른 날짜에 표시된 클러스터는 제외 (그 날짜 페이지에서만 보임)
    # - first_shown_date 가 NULL 인 클러스터는 이번에 target 으로 set 됨
    # - first_shown_date == target 인 클러스터는 표시
    if target_to == target:
        base_q = base_q.filter(
            (Cluster.first_shown_date == target) | (Cluster.first_shown_date.is_(None))
        )
    else:
        base_q = base_q.filter(
            ((Cluster.first_shown_date >= target) & (Cluster.first_shown_date <= target_to)) |
            (Cluster.first_shown_date.is_(None))
        )

    if show_hidden:
        visible_clusters = base_q.filter(Cluster.hidden_at.isnot(None)).all()
    else:
        # 일반 모드: 숨김 안 됨 + 저장 안 됨 (저장된 건 saved 페이지에서만)
        visible_clusters = base_q.filter(
            Cluster.hidden_at.is_(None), Cluster.saved_at.is_(None)
        ).all()

    # 숨김 카운트 (배지용) — 같은 날짜 필터 안에서
    hidden_count = base_q.filter(Cluster.hidden_at.isnot(None)).count()

    # first_shown_date 가 NULL 인 클러스터들에 target 박기 (한 번만)
    # 과거 날짜 조회 시에도 즉시 스탬핑해야 다른 날짜 뷰에 중복 노출되지 않음
    unstamped_ids = [c.id for c in visible_clusters if c.first_shown_date is None]
    if unstamped_ids:
        Cluster.query.filter(Cluster.id.in_(unstamped_ids)).update(
            {Cluster.first_shown_date: target},
            synchronize_session=False,
        )
        db.session.commit()
        # 메모리 객체도 갱신
        unstamped_set = set(unstamped_ids)
        for c in visible_clusters:
            if c.id in unstamped_set:
                c.first_shown_date = target

    # 정렬용 베이스 (cluster, score) — score 는 디버그/표시용
    scored = [(c, _cluster_score(c)) for c in visible_clusters]

    # ========== 정렬 옵션 ==========
    # sort: score (기본 — 교차검증→중요도) | recent | importance
    sort_mode = request.args.get("sort", "score")
    if sort_mode == "recent":
        scored.sort(key=lambda x: (-(x[0].updated_at.timestamp() if x[0].updated_at else 0), -(x[0].importance or 0)))
    elif sort_mode == "importance":
        scored.sort(key=lambda x: (-(x[0].importance or 0), -x[0].articles.count(), -x[0].id))
    else:
        # score (기본): 교차검증(매체수) → 중요도 → Tier1 → 최신
        scored.sort(key=lambda x: _cluster_sort_key(x[0]))
        sort_mode = "score"

    # ========== 카테고리 필터 ==========
    # cat: all (기본) | 정책/규제 | 산업/기업 | 연구/모델 | 윤리/사회
    cat_filter = request.args.get("cat", "all")
    if cat_filter != "all":
        scored = [
            (c, sc) for c, sc in scored
            if c.categories and cat_filter in c.categories
        ]

    # 전체(필터 적용 후) 클러스터 수
    total_filtered = len(scored)

    # ========== 페이지네이션 ==========
    PAGE_SIZE = 12
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    total_pages = max(1, (total_filtered + PAGE_SIZE - 1) // PAGE_SIZE)
    # 뉴스 탭에서만 클램핑 — 공모전/논문 탭은 각자 블록에서 처리
    # (전체 적용 시 tab=contests에서 total_filtered=뉴스수 기준으로 page가 1로 강제됨)
    if tab == "news" and page > total_pages:
        page = total_pages
    start_idx = (page - 1) * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    page_scored = scored[start_idx:end_idx]

    # 인라인 캐러셀용 카드 데이터 생성 — 현재 페이지만
    inline_clusters = []
    for c, score in page_scored:
        inline_clusters.append({
            "cluster": c,
            "cards": build_cluster_cards(c),
            "score": score,
        })

    # 카테고리별 카운트 (필터 칩 옆에 숫자 표시용)
    cat_counts = {"all": 0, "정책/규제": 0, "산업/기업": 0, "연구/모델": 0, "윤리/사회": 0}
    all_scored_for_count = [(c, sc) for c, sc in [(c, _cluster_score(c)) for c in visible_clusters]]
    for c, _sc in all_scored_for_count:
        cat_counts["all"] += 1
        for cat in (c.categories or []):
            if cat in cat_counts:
                cat_counts[cat] += 1

    # 논문 — fetched_at(수집일) 기준 필터. published_at은 arXiv 등록일이라 수집일과 다를 수 있음
    papers_q = (
        Paper.query
        .filter(Paper.summary_ko.isnot(None), Paper.summary_ko != "")
        .filter(Paper.fetched_at >= start_utc, Paper.fetched_at < end_utc)
    )
    if show_hidden:
        papers_all = papers_q.filter(Paper.hidden_at.isnot(None)).order_by(
            Paper.hidden_at.desc()
        ).all()
    else:
        papers_all = papers_q.filter(
            Paper.hidden_at.is_(None), Paper.saved_at.is_(None)
        ).order_by(
            Paper.hf_featured.desc(), Paper.hf_upvotes.desc(), Paper.published_at.desc()
        ).all()

    hidden_papers_count = papers_q.filter(Paper.hidden_at.isnot(None)).count()
    total_papers_filtered = len(papers_all)

    # 논문 탭이면 페이지네이션 12개씩 + total_pages 덮어쓰기
    PAPER_PAGE_SIZE = 12
    if tab == "papers":
        paper_total_pages = max(1, (total_papers_filtered + PAPER_PAGE_SIZE - 1) // PAPER_PAGE_SIZE)
        if page > paper_total_pages:
            page = paper_total_pages
        p_start = (page - 1) * PAPER_PAGE_SIZE
        papers = papers_all[p_start:p_start + PAPER_PAGE_SIZE]
        total_pages = paper_total_pages  # 페이저는 논문 페이지로
    else:
        papers = []

    # 뉴스 탭이 아니면 inline_clusters 비움 (논문 탭에서 클러스터 안 보임)
    if tab == "papers":
        inline_clusters = []

    # 저장 카운트 (전역 — 날짜 무관)
    saved_clusters_count = Cluster.query.filter(Cluster.saved_at.isnot(None)).count()
    saved_papers_count = Paper.query.filter(Paper.saved_at.isnot(None)).count()
    saved_contests_count = Contest.query.filter(Contest.saved_at.isnot(None)).count()
    saved_total = saved_clusters_count + saved_papers_count + saved_contests_count

    # 논문 카드뉴스 데이터
    paper_cardsets = [{"paper": p, "cards": build_paper_cards(p)} for p in papers]

    # ========== 공모전 탭 ==========
    # 마감 남은(deadline >= 오늘 또는 미정) + 숨김·저장 안 됨, 마감 임박순.
    today_kst_d = datetime.now(KST).date()
    contests_q = (
        Contest.query
        .filter(Contest.hidden_at.is_(None), Contest.saved_at.is_(None))
        .filter((Contest.deadline >= today_kst_d) | (Contest.deadline.is_(None)))
    )
    contests_all = contests_q.order_by(
        Contest.deadline.asc().nullslast(), Contest.id.desc()
    ).all()
    total_contests = len(contests_all)
    hidden_contests_count = Contest.query.filter(Contest.hidden_at.isnot(None)).count()

    # 3D 쇼케이스 — 마감 임박(deadline 가까운) 상위 N개
    contest_showcase = []
    if tab == "contests":
        _urgent = [c for c in contests_all if c.deadline is not None][:9]
        contest_showcase = [build_contest_tile(c) for c in _urgent]

    CONTEST_PAGE_SIZE = 12
    contest_tiles = []
    if tab == "contests":
        c_total_pages = max(1, (total_contests + CONTEST_PAGE_SIZE - 1) // CONTEST_PAGE_SIZE)
        if page > c_total_pages:
            page = c_total_pages
        c_start = (page - 1) * CONTEST_PAGE_SIZE
        page_contests = contests_all[c_start:c_start + CONTEST_PAGE_SIZE]
        contest_tiles = [build_contest_tile(c) for c in page_contests]
        total_pages = c_total_pages
        # 다른 탭 카드 비움
        inline_clusters = []
        paper_cardsets = []

    total_articles = len(arts_today)

    # ========== 파티 탭 ==========
    parties_list = []
    my_party_ids = set()
    total_parties = Party.query.count()
    if tab == "parties":
        parties_list = Party.query.order_by(Party.created_at.desc()).all()
        if current_user.is_authenticated:
            my_party_ids = {
                m.party_id for m in PartyMember.query.filter_by(user_id=current_user.id).all()
            }
        inline_clusters = []
        paper_cardsets = []
        contest_tiles = []

    # ========== 인사교당근 탭 ==========
    karrot_list = []
    total_karrot = KarrotPost.query.count()
    karrot_filter = request.args.get("kfilter", "all")
    karrot_class = request.args.get("kclass", "all")
    if tab == "karrot":
        kq = KarrotPost.query
        if karrot_filter in ("share", "loan"):
            kq = kq.filter_by(post_type=karrot_filter)
        if karrot_class.isdigit() and 1 <= int(karrot_class) <= 7:
            from sqlalchemy import or_
            kq = kq.filter(or_(
                KarrotPost.class_target == int(karrot_class),
                KarrotPost.class_target.is_(None),
            ))
        karrot_list = kq.order_by(KarrotPost.created_at.desc()).all()
        inline_clusters = []
        paper_cardsets = []
        contest_tiles = []

    return render_template(
        "digest.html",
        target_date=target,
        inline_clusters=inline_clusters,
        papers=papers,
        paper_cardsets=paper_cardsets,
        total_clusters=total_filtered,
        total_papers=total_papers_filtered,
        total_contests=total_contests,
        contest_tiles=contest_tiles,
        contest_showcase=contest_showcase,
        total_articles=total_articles,
        target_date_to=target_to,
        prev_date=target - timedelta(days=1),
        next_date=target + timedelta(days=1),
        prev_date_to=target_to - timedelta(days=1),
        next_date_to=target_to + timedelta(days=1),
        today=datetime.now(KST).date(),
        show_hidden=show_hidden,
        hidden_count=hidden_count,
        hidden_papers_count=hidden_papers_count,
        saved_total=saved_total,
        # 탭/페이지/필터/정렬
        tab=tab,
        page=page,
        total_pages=total_pages,
        sort_mode=sort_mode,
        cat_filter=cat_filter,
        cat_counts=cat_counts,
        articles_flat=articles_flat,
        total_articles_flat=total_articles_flat,
        parties_list=parties_list,
        my_party_ids=my_party_ids,
        total_parties=total_parties,
        active_contests=contests_all,
        karrot_list=karrot_list,
        total_karrot=total_karrot,
        karrot_filter=karrot_filter,
        karrot_class=karrot_class,
        karrot_enabled=karrot_enabled,
        user_bm_contest_ids=user_bm_contest_ids,
        user_bm_cluster_ids=user_bm_cluster_ids,
        user_bm_paper_ids=user_bm_paper_ids,
    )


# ---------- 클러스터 상세 → 카드뉴스 ----------
@bp.route("/cluster/<int:cluster_id>")
@login_required
def cluster_detail(cluster_id: int):
    cluster = Cluster.query.get(cluster_id)
    if not cluster:
        abort(404)
    cards = build_cluster_cards(cluster)
    return render_template(
        "cardnews.html",
        kind="cluster",
        cluster=cluster,
        cards=cards,
        total=len(cards),
    )


@bp.route("/paper/<int:paper_id>")
@login_required
def paper_detail(paper_id: int):
    paper = Paper.query.get(paper_id)
    if not paper:
        abort(404)
    cards = build_paper_cards(paper)
    return render_template(
        "cardnews.html",
        kind="paper",
        paper=paper,
        cards=cards,
        total=len(cards),
    )


# ---------- 공모전 수동 추가 ----------
CONTEST_CATEGORIES = ["공모전", "창업경진대회", "경진대회", "해커톤", "취업/채용", "기타"]


@bp.route("/api/contest/fetch-meta")
@admin_required
def contest_fetch_meta():
    """사용자가 붙여넣은 공모전 URL 의 og:title/og:image 추출(자동 채움용)."""
    import re
    import requests
    url = (request.args.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "no_url"}), 400
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; AINewsDigest/0.1)"}, timeout=12)
        html = r.content.decode(r.apparent_encoding or "utf-8", errors="replace")
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:120]}), 200

    def og(prop):
        m = (re.search(rf'property=["\']og:{prop}["\'][^>]*content=["\']([^"\']*)["\']', html)
             or re.search(rf'content=["\']([^"\']*)["\'][^>]*property=["\']og:{prop}["\']', html))
        return m.group(1).strip() if m else None

    title = og("title")
    if not title:
        m = re.search(r"<title[^>]*>([^<]*)</title>", html, re.I)
        title = m.group(1).strip() if m else None
    image = og("image")
    if image and image.startswith("//"):
        image = "https:" + image
    return jsonify({"ok": True, "title": title, "image_url": image, "url": url})


@bp.route("/contest/new", methods=["GET", "POST"])
@admin_required
def contest_new():
    if request.method == "POST":
        import hashlib
        f = request.form
        title = (f.get("title") or "").strip()
        url = (f.get("url") or "").strip()
        if url and not url.startswith(("http://", "https://")):
            url = "https://" + url
        if not title or not url:
            return render_template(
                "contest_new.html", categories=CONTEST_CATEGORIES,
                error="제목과 원문 URL은 필수입니다.", form=f,
            )

        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
        existing = Contest.query.filter_by(url_hash=url_hash).first()
        if existing:
            # 이미 있으면 그 상세로 (중복 방지)
            return redirect(url_for("web.contest_detail", contest_id=existing.id))

        cat = f.get("category") or "공모전"
        contest = Contest(
            source="manual",
            url=url,
            url_hash=url_hash,
            title=title[:500],
            host=(f.get("host") or "").strip() or None,
            image_url=(f.get("image_url") or "").strip() or None,
            category=cat if cat in CONTEST_CATEGORIES else "공모전",
            field_tags=[],
            target=(f.get("target") or "").strip() or None,
            prize=(f.get("prize") or "").strip() or None,
            start_at=_parse_iso_date(f.get("start_at")),
            deadline=_parse_iso_date(f.get("deadline")),
            is_ai_relevant=True,
            summary_dirty=False,
        )
        db.session.add(contest)
        db.session.commit()
        # 이미지 첨부·위치/크기 조정은 상세페이지에서
        return redirect(url_for("web.contest_detail", contest_id=contest.id))

    return render_template("contest_new.html", categories=CONTEST_CATEGORIES, form={})


# ---------- 공모전 상세 ----------
@bp.route("/contest/<int:contest_id>")
@login_required
def contest_detail(contest_id: int):
    contest = Contest.query.get(contest_id)
    if not contest:
        abort(404)
    today = datetime.now(KST).date()
    d_left = (contest.deadline - today).days if contest.deadline else None
    return render_template(
        "contest_detail.html",
        contest=contest,
        tile=build_contest_tile(contest),
        d_left=d_left,
    )


# ---------- 숨김/복구 API (JS 호출용) ----------
@bp.route("/api/cluster/<int:cluster_id>/hide", methods=["POST"])
@admin_required
def hide_cluster(cluster_id: int):
    cluster = Cluster.query.get(cluster_id)
    if not cluster:
        return jsonify({"ok": False, "error": "not_found"}), 404
    cluster.hidden_at = datetime.utcnow()
    cluster.saved_at = None  # 배타: 숨기면 저장 해제
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/cluster/<int:cluster_id>/show", methods=["POST"])
@admin_required
def show_cluster(cluster_id: int):
    cluster = Cluster.query.get(cluster_id)
    if not cluster:
        return jsonify({"ok": False, "error": "not_found"}), 404
    cluster.hidden_at = None
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/cluster/<int:cluster_id>/save", methods=["POST"])
@admin_required
def save_cluster(cluster_id: int):
    cluster = Cluster.query.get(cluster_id)
    if not cluster:
        return jsonify({"ok": False, "error": "not_found"}), 404
    cluster.saved_at = datetime.utcnow()
    cluster.hidden_at = None  # 배타: 저장하면 숨김 해제
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/cluster/<int:cluster_id>/unsave", methods=["POST"])
@admin_required
def unsave_cluster(cluster_id: int):
    cluster = Cluster.query.get(cluster_id)
    if not cluster:
        return jsonify({"ok": False, "error": "not_found"}), 404
    cluster.saved_at = None
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/paper/<int:paper_id>/hide", methods=["POST"])
@admin_required
def hide_paper(paper_id: int):
    paper = Paper.query.get(paper_id)
    if not paper:
        return jsonify({"ok": False, "error": "not_found"}), 404
    paper.hidden_at = datetime.utcnow()
    paper.saved_at = None
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/paper/<int:paper_id>/show", methods=["POST"])
@admin_required
def show_paper(paper_id: int):
    paper = Paper.query.get(paper_id)
    if not paper:
        return jsonify({"ok": False, "error": "not_found"}), 404
    paper.hidden_at = None
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/paper/<int:paper_id>/save", methods=["POST"])
@admin_required
def save_paper(paper_id: int):
    paper = Paper.query.get(paper_id)
    if not paper:
        return jsonify({"ok": False, "error": "not_found"}), 404
    paper.saved_at = datetime.utcnow()
    paper.hidden_at = None
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/paper/<int:paper_id>/unsave", methods=["POST"])
@admin_required
def unsave_paper(paper_id: int):
    paper = Paper.query.get(paper_id)
    if not paper:
        return jsonify({"ok": False, "error": "not_found"}), 404
    paper.saved_at = None
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/contest/<int:contest_id>/hide", methods=["POST"])
@admin_required
def hide_contest(contest_id: int):
    contest = Contest.query.get(contest_id)
    if not contest:
        return jsonify({"ok": False, "error": "not_found"}), 404
    contest.hidden_at = datetime.utcnow()
    contest.saved_at = None
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/contest/<int:contest_id>/show", methods=["POST"])
@admin_required
def show_contest(contest_id: int):
    contest = Contest.query.get(contest_id)
    if not contest:
        return jsonify({"ok": False, "error": "not_found"}), 404
    contest.hidden_at = None
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/contest/<int:contest_id>/save", methods=["POST"])
@admin_required
def save_contest(contest_id: int):
    contest = Contest.query.get(contest_id)
    if not contest:
        return jsonify({"ok": False, "error": "not_found"}), 404
    contest.saved_at = datetime.utcnow()
    contest.hidden_at = None
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/contest/<int:contest_id>/unsave", methods=["POST"])
@admin_required
def unsave_contest(contest_id: int):
    contest = Contest.query.get(contest_id)
    if not contest:
        return jsonify({"ok": False, "error": "not_found"}), 404
    contest.saved_at = None
    db.session.commit()
    return jsonify({"ok": True})


ALLOWED_IMAGE_EXT = {"png", "jpg", "jpeg", "webp", "gif"}


def _contest_upload_dir() -> str:
    d = os.path.join(current_app.static_folder, "uploads", "contests")
    os.makedirs(d, exist_ok=True)
    return d


@bp.route("/api/contest/<int:contest_id>/image", methods=["POST"])
@admin_required
def upload_contest_image(contest_id: int):
    """관리자 이미지 첨부 (multipart 'image'). 저장 후 image_url 세팅 + 위치/배율 초기화."""
    contest = Contest.query.get(contest_id)
    if not contest:
        return jsonify({"ok": False, "error": "not_found"}), 404
    file = request.files.get("image")
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "no_file"}), 400

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_IMAGE_EXT:
        return jsonify({"ok": False, "error": "bad_ext", "allowed": sorted(ALLOWED_IMAGE_EXT)}), 400

    fname = f"contest_{contest_id}_{secrets.token_hex(6)}.{ext}"
    fname = secure_filename(fname)
    path = os.path.join(_contest_upload_dir(), fname)

    # 이전 업로드 파일 삭제 (uploads 안의 것만)
    _delete_uploaded_image(contest.image_url)

    file.save(path)
    contest.image_url = url_for("static", filename=f"uploads/contests/{fname}")
    contest.image_pos_x = 50.0
    contest.image_pos_y = 50.0
    contest.image_scale = 1.0
    db.session.commit()
    return jsonify({
        "ok": True,
        "image_url": contest.image_url,
        "pos_x": contest.image_pos_x, "pos_y": contest.image_pos_y, "scale": contest.image_scale,
    })


@bp.route("/api/contest/<int:contest_id>/image-adjust", methods=["POST"])
@admin_required
def adjust_contest_image(contest_id: int):
    """이미지 위치(pos_x/pos_y 0~100) + 확대 배율(scale 1~4) 저장."""
    contest = Contest.query.get(contest_id)
    if not contest:
        return jsonify({"ok": False, "error": "not_found"}), 404
    data = request.get_json(silent=True) or {}

    def _clamp(v, lo, hi, default):
        try:
            return max(lo, min(hi, float(v)))
        except (TypeError, ValueError):
            return default

    if "pos_x" in data:
        contest.image_pos_x = _clamp(data["pos_x"], 0, 100, 50.0)
    if "pos_y" in data:
        contest.image_pos_y = _clamp(data["pos_y"], 0, 100, 50.0)
    if "scale" in data:
        contest.image_scale = _clamp(data["scale"], 1.0, 4.0, 1.0)
    db.session.commit()
    return jsonify({"ok": True, "pos_x": contest.image_pos_x, "pos_y": contest.image_pos_y, "scale": contest.image_scale})


def _delete_uploaded_image(image_url: str | None):
    """image_url 이 우리 업로드 경로면 파일 삭제 (외부 핫링크는 건드리지 않음)."""
    if not image_url or "/uploads/contests/" not in image_url:
        return
    fname = os.path.basename(image_url)
    try:
        p = os.path.join(_contest_upload_dir(), fname)
        if os.path.exists(p):
            os.remove(p)
    except OSError:
        pass


@bp.route("/api/contest/<int:contest_id>/image-remove", methods=["POST"])
@admin_required
def remove_contest_image(contest_id: int):
    """첨부 이미지 제거 → fallback 타일로 복귀."""
    contest = Contest.query.get(contest_id)
    if not contest:
        return jsonify({"ok": False, "error": "not_found"}), 404
    _delete_uploaded_image(contest.image_url)
    contest.image_url = None
    contest.image_pos_x = 50.0
    contest.image_pos_y = 50.0
    contest.image_scale = 1.0
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/restore-all", methods=["POST"])
@admin_required
def restore_all():
    """모든 숨김 복구."""
    n_c = Cluster.query.filter(Cluster.hidden_at.isnot(None)).update(
        {"hidden_at": None}, synchronize_session=False
    )
    n_p = Paper.query.filter(Paper.hidden_at.isnot(None)).update(
        {"hidden_at": None}, synchronize_session=False
    )
    n_ct = Contest.query.filter(Contest.hidden_at.isnot(None)).update(
        {"hidden_at": None}, synchronize_session=False
    )
    db.session.commit()
    return jsonify({"ok": True, "clusters": n_c, "papers": n_p, "contests": n_ct})


@bp.route("/api/clear-saved", methods=["POST"])
@admin_required
def clear_saved():
    """모든 저장 해제."""
    n_c = Cluster.query.filter(Cluster.saved_at.isnot(None)).update(
        {"saved_at": None}, synchronize_session=False
    )
    n_p = Paper.query.filter(Paper.saved_at.isnot(None)).update(
        {"saved_at": None}, synchronize_session=False
    )
    n_ct = Contest.query.filter(Contest.saved_at.isnot(None)).update(
        {"saved_at": None}, synchronize_session=False
    )
    db.session.commit()
    return jsonify({"ok": True, "clusters": n_c, "papers": n_p, "contests": n_ct})


@bp.route("/api/counts", methods=["GET"])
def api_counts():
    """숨김·저장 카운트 (실시간 배지 갱신용)."""
    hidden_clusters = Cluster.query.filter(Cluster.hidden_at.isnot(None)).count()
    hidden_papers = Paper.query.filter(Paper.hidden_at.isnot(None)).count()
    hidden_contests = Contest.query.filter(Contest.hidden_at.isnot(None)).count()
    saved_clusters = Cluster.query.filter(Cluster.saved_at.isnot(None)).count()
    saved_papers = Paper.query.filter(Paper.saved_at.isnot(None)).count()
    saved_contests = Contest.query.filter(Contest.saved_at.isnot(None)).count()
    return jsonify({
        "ok": True,
        "hidden": {
            "clusters": hidden_clusters, "papers": hidden_papers, "contests": hidden_contests,
            "total": hidden_clusters + hidden_papers + hidden_contests,
        },
        "saved": {
            "clusters": saved_clusters, "papers": saved_papers, "contests": saved_contests,
            "total": saved_clusters + saved_papers + saved_contests,
        },
    })


def _parse_iso_date(s: str):
    """YYYY-MM-DD 문자열 파싱 — 실패 시 None."""
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _resolve_search_preset(preset: str, from_str: str, to_str: str):
    """preset 키를 (from_date, to_date, normalized_preset) 으로 해석."""
    today_kst = datetime.now(KST).date()
    if preset == "today":
        return today_kst, today_kst, "today"
    if preset == "7d":
        return today_kst - timedelta(days=6), today_kst, "7d"
    if preset == "30d":
        return today_kst - timedelta(days=29), today_kst, "30d"
    if preset == "custom":
        f = _parse_iso_date(from_str)
        t = _parse_iso_date(to_str)
        if f or t:
            return f, t, "custom"
        return None, None, "all"
    return None, None, "all"


def _run_cluster_search(q: str, from_date, to_date, limit: int | None = None):
    """검색 본 쿼리 — 페이지 라우트·API 공통.

    return: (cluster_cardsets, total_count, truncated)
    """
    from sqlalchemy import cast, String, or_, func

    pat = f"%{q.lower()}%"
    keyword_filter = or_(
        func.lower(Cluster.topic).like(pat),
        func.lower(Cluster.summary_ko).like(pat),
        func.lower(cast(Cluster.agreed_facts, String)).like(pat),
        func.lower(cast(Cluster.divergences, String)).like(pat),
    )
    title_match_cids = (
        db.session.query(Article.cluster_id)
        .filter(Article.cluster_id.isnot(None))
        .filter(func.lower(Article.title).like(pat))
        .distinct()
        .subquery()
    )
    full_filter = or_(
        keyword_filter,
        Cluster.id.in_(db.session.query(title_match_cids.c.cluster_id)),
    )

    base_q = (
        Cluster.query
        .filter(full_filter)
        .filter(Cluster.summary_ko.isnot(None), Cluster.summary_ko != "")
        .filter(Cluster.hidden_at.is_(None))
    )
    if from_date:
        base_q = base_q.filter(Cluster.first_shown_date >= from_date)
    if to_date:
        base_q = base_q.filter(Cluster.first_shown_date <= to_date)

    total = base_q.count()
    ordered = base_q.order_by(
        Cluster.first_shown_date.desc().nullslast(),
        Cluster.importance.desc(),
        Cluster.id.desc(),
    )
    results = ordered.limit(limit).all() if limit else ordered.all()
    cardsets = [{"cluster": c, "cards": build_cluster_cards(c)} for c in results]
    return cardsets, total, (limit is not None and total > limit)


@bp.route("/search")
def search_page():
    """카드뉴스 본문 키워드 검색 + 기간 필터 (풀페이지, 페이저 포함)."""
    q = (request.args.get("q") or "").strip()
    preset = request.args.get("preset") or "all"
    from_str = (request.args.get("from") or "").strip()
    to_str = (request.args.get("to") or "").strip()
    from_date, to_date, preset = _resolve_search_preset(preset, from_str, to_str)

    cluster_cardsets = []
    total = 0
    total_pages = 1
    page = 1
    PAGE_SIZE = 12

    if q:
        all_cardsets, total, _ = _run_cluster_search(q, from_date, to_date, limit=None)
        try:
            page = max(1, int(request.args.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        if page > total_pages:
            page = total_pages
        start = (page - 1) * PAGE_SIZE
        cluster_cardsets = all_cardsets[start:start + PAGE_SIZE]

    return render_template(
        "search.html",
        q=q,
        preset=preset,
        from_str=(from_date.isoformat() if (preset == "custom" and from_date) else from_str),
        to_str=(to_date.isoformat() if (preset == "custom" and to_date) else to_str),
        from_date=from_date,
        to_date=to_date,
        cluster_cardsets=cluster_cardsets,
        total=total,
        page=page,
        total_pages=total_pages,
        today=datetime.now(KST).date(),
    )


@bp.route("/api/search")
def api_search():
    """인라인 라이브 검색용 HTML 조각 반환. 상위 INLINE_LIMIT 만 보여줌."""
    INLINE_LIMIT = 30
    q = (request.args.get("q") or "").strip()
    preset = request.args.get("preset") or "all"
    from_str = (request.args.get("from") or "").strip()
    to_str = (request.args.get("to") or "").strip()
    from_date, to_date, preset = _resolve_search_preset(preset, from_str, to_str)

    if not q:
        return ("", 200, {"Content-Type": "text/html; charset=utf-8"})

    cardsets, total, truncated = _run_cluster_search(q, from_date, to_date, limit=INLINE_LIMIT)
    html = render_template(
        "_search_results.html",
        cluster_cardsets=cardsets,
        total=total,
        truncated=truncated,
        q=q,
        preset=preset,
    )
    return (html, 200, {"Content-Type": "text/html; charset=utf-8"})


@bp.route("/api/contest-autocomplete")
@login_required
def api_contest_autocomplete():
    """파티 생성 시 공모전 검색 자동완성 (JSON)."""
    q = (request.args.get("q") or "").strip()
    limit = min(int(request.args.get("limit") or 8), 20)
    if not q:
        return jsonify([])
    contests = (
        Contest.query
        .filter(Contest.title.ilike(f"%{q}%"))
        .filter(Contest.hidden_at.is_(None))
        .order_by(Contest.fetched_at.desc())
        .limit(limit)
        .all()
    )
    return jsonify([{"id": c.id, "title": c.title} for c in contests])


@bp.route("/api/contests/active")
@login_required
def api_contests_active():
    """파티 생성/관리 바텀 시트용 활성 공모전 목록."""
    today = datetime.now(KST).date()
    contests = (
        Contest.query
        .filter(Contest.hidden_at.is_(None))
        .filter((Contest.deadline >= today) | (Contest.deadline.is_(None)))
        .order_by(Contest.deadline.asc().nullslast(), Contest.id.desc())
        .all()
    )
    return jsonify([
        {"id": c.id, "title": c.title, "tags": c.field_tags or []}
        for c in contests
    ])


@bp.route("/saved")
def saved_page():
    """저장된 사건 + 논문 모음 페이지."""
    saved_clusters = (
        Cluster.query
        .filter(Cluster.saved_at.isnot(None))
        .filter(Cluster.summary_ko.isnot(None), Cluster.summary_ko != "")
        .order_by(Cluster.saved_at.desc())
        .all()
    )
    saved_papers = (
        Paper.query
        .filter(Paper.saved_at.isnot(None))
        .filter(Paper.summary_ko.isnot(None), Paper.summary_ko != "")
        .order_by(Paper.saved_at.desc())
        .all()
    )

    saved_contests = (
        Contest.query
        .filter(Contest.saved_at.isnot(None))
        .order_by(Contest.saved_at.desc())
        .all()
    )

    cluster_cardsets = [
        {"cluster": c, "cards": build_cluster_cards(c)}
        for c in saved_clusters
    ]
    paper_cardsets = [
        {"paper": p, "cards": build_paper_cards(p)}
        for p in saved_papers
    ]
    contest_tiles = [build_contest_tile(c) for c in saved_contests]

    return render_template(
        "saved.html",
        cluster_cardsets=cluster_cardsets,
        paper_cardsets=paper_cardsets,
        contest_tiles=contest_tiles,
        total_clusters=len(saved_clusters),
        total_papers=len(saved_papers),
        total_contests=len(saved_contests),
    )


# ---------- Admin (Day 6) ----------
JOB_LABELS = {
    "refresh_now": "🔄 지금 새로고침 (전체)",
    "collect_news": "뉴스 수집 (RSS 9개 폴링)",
    "fetch_bodies": "본문 페치 (30건)",
    "collect_papers": "논문 수집 (arXiv + HF)",
    "embed_and_cluster": "임베딩 + 클러스터링",
    "summarize_news": "뉴스 요약 (Claude)",
    "summarize_papers": "논문 요약 (Claude)",
    "morning_pipeline": "전체 묶음 (논문→임베딩→요약)",
    "backfill_papers": "📚 논문 백필 (dirty 전부)",
    "cleanup_old_data": "🗑️ 4일 이상 데이터 삭제",
    "collect_contests": "🏆 공모전 수집 (위비티·데이콘 등)",
}


@bp.route("/admin")
@admin_required
def admin():
    from scheduler import get_scheduler

    recent_runs = (
        JobRun.query.order_by(JobRun.started_at.desc()).limit(30).all()
    )

    last_success: dict = {}
    for job_id in JOB_LABELS.keys():
        last = (
            JobRun.query.filter_by(job_name=job_id, status="success")
            .order_by(JobRun.started_at.desc()).first()
        )
        if last:
            last_success[job_id] = last

    sched = get_scheduler()
    scheduled_jobs = []
    if sched:
        for job in sched.get_jobs():
            if job.id.startswith("manual_"):
                continue
            scheduled_jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time,
                "trigger": str(job.trigger),
            })

    # 회원 목록 (가입 순)
    admin_users = AdminUser.query.order_by(AdminUser.created_at.asc()).all()

    # 최근 활동 이력 (최신 200건)
    recent_activity = (
        UserActivity.query
        .order_by(UserActivity.created_at.desc())
        .limit(200)
        .all()
    )

    # 회원별 가입 IP (user_activity.action='register' 첫 번째 레코드)
    register_logs = (
        UserActivity.query
        .filter(UserActivity.action == "register")
        .order_by(UserActivity.created_at.asc())
        .all()
    )
    register_ip_map: dict = {}   # username -> UserActivity 레코드
    for r in register_logs:
        if r.username and r.username not in register_ip_map:
            register_ip_map[r.username] = r

    return render_template(
        "admin.html",
        recent_runs=recent_runs,
        last_success=last_success,
        scheduled_jobs=scheduled_jobs,
        job_labels=JOB_LABELS,
        scheduler_active=sched is not None,
        kst_offset=timedelta(hours=9),
        admin_users=admin_users,
        recent_activity=recent_activity,
        register_ip_map=register_ip_map,
        karrot_enabled=_get_setting("karrot_enabled", "false") == "true",
    )


# ---------- 보관함 (회원별 즐겨찾기) ----------
@bp.route("/api/bookmark/<item_type>/<int:item_id>", methods=["POST"])
@login_required
def toggle_bookmark(item_type: str, item_id: int):
    """즐겨찾기 토글 — 있으면 제거, 없으면 추가."""
    if item_type not in ("contest", "cluster", "paper"):
        return jsonify({"ok": False, "error": "invalid_type"}), 400
    existing = UserBookmark.query.filter_by(
        user_id=current_user.id, item_type=item_type, item_id=item_id
    ).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        return jsonify({"ok": True, "bookmarked": False})
    bm = UserBookmark(user_id=current_user.id, item_type=item_type, item_id=item_id)
    db.session.add(bm)
    db.session.commit()
    return jsonify({"ok": True, "bookmarked": True})


@bp.route("/bookmarks")
@login_required
def bookmarks():
    uid = current_user.id
    try:
        bms = UserBookmark.query.filter_by(user_id=uid).order_by(UserBookmark.saved_at.desc()).all()
    except Exception:
        bms = []

    contest_ids = [b.item_id for b in bms if b.item_type == "contest"]
    cluster_ids = [b.item_id for b in bms if b.item_type == "cluster"]
    paper_ids = [b.item_id for b in bms if b.item_type == "paper"]

    contests = Contest.query.filter(Contest.id.in_(contest_ids)).all() if contest_ids else []
    clusters = Cluster.query.filter(Cluster.id.in_(cluster_ids)).all() if cluster_ids else []
    papers = Paper.query.filter(Paper.id.in_(paper_ids)).all() if paper_ids else []

    # 북마크 저장 순서 유지
    co = {bid: i for i, bid in enumerate(contest_ids)}
    clo = {bid: i for i, bid in enumerate(cluster_ids)}
    po = {bid: i for i, bid in enumerate(paper_ids)}
    contests.sort(key=lambda x: co.get(x.id, 999))
    clusters.sort(key=lambda x: clo.get(x.id, 999))
    papers.sort(key=lambda x: po.get(x.id, 999))

    # 기사·논문 합쳐서 날짜 내림차순 정렬
    def _item_date(item):
        if hasattr(item, "published_at"):
            return item.published_at or datetime.min
        a = item.articles.first()
        return (a.published_at if a and a.published_at else datetime.min)

    news_items = sorted(
        [("cluster", c) for c in clusters] + [("paper", p) for p in papers],
        key=lambda x: _item_date(x[1]),
        reverse=True,
    )

    return render_template(
        "bookmarks.html",
        contests=contests,
        news_items=news_items,
        tab=request.args.get("tab", "contest"),
    )


@bp.route("/admin/toggle-karrot", methods=["POST"])
@admin_required
def admin_toggle_karrot():
    """인사교당근 탭 공개/비공개 토글."""
    current = _get_setting("karrot_enabled", "false")
    new_val = "false" if current == "true" else "true"
    _set_setting("karrot_enabled", new_val)
    return jsonify({"ok": True, "karrot_enabled": new_val == "true"})


@bp.route("/admin/run/<job_id>", methods=["POST"])
@admin_required
def admin_run_job(job_id: str):
    from flask import current_app
    from scheduler import trigger_job_now
    from jobs.pipeline import create_job_run

    if job_id not in JOB_LABELS:
        return jsonify({"ok": False, "error": "unknown_job"}), 404

    # JobRun 을 queued 상태로 미리 만들고 ID 확보 → 프론트가 정확한 row 폴링
    pre_run_id = create_job_run(job_id, triggered_by="manual")

    # 백그라운드로 잡 실행 (run_id 전달)
    trigger_job_now(job_id, current_app._get_current_object(), run_id=pre_run_id)

    return jsonify({
        "ok": True,
        "job_id": job_id,
        "run_id": pre_run_id,
    })


@bp.route("/api/job/<job_id>/latest", methods=["GET"])
def api_job_latest(job_id: str):
    """가장 최근 JobRun 상태 조회 (폴링용)."""
    if job_id not in JOB_LABELS:
        return jsonify({"ok": False, "error": "unknown_job"}), 404

    run = (
        JobRun.query
        .filter_by(job_name=job_id)
        .order_by(JobRun.started_at.desc())
        .first()
    )
    if not run:
        return jsonify({"ok": True, "run": None})

    duration = None
    if run.finished_at and run.started_at:
        duration = round((run.finished_at - run.started_at).total_seconds(), 1)

    return jsonify({
        "ok": True,
        "run": {
            "id": run.id,
            "status": run.status,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "duration": duration,
            "stats": run.stats or {},
            "error": (run.error[:300] if run.error else None),
            "triggered_by": run.triggered_by,
        },
    })


@bp.route("/api/job/run/<int:run_id>", methods=["GET"])
def api_job_run(run_id: int):
    """특정 run_id 상태 조회."""
    run = JobRun.query.get(run_id)
    if not run:
        return jsonify({"ok": False, "error": "not_found"}), 404

    duration = None
    if run.finished_at and run.started_at:
        duration = round((run.finished_at - run.started_at).total_seconds(), 1)

    return jsonify({
        "ok": True,
        "run": {
            "id": run.id,
            "job_name": run.job_name,
            "status": run.status,
            "duration": duration,
            "stats": run.stats or {},
            "error": (run.error[:300] if run.error else None),
        },
    })


# ---------- 카드뉴스 봇 전용 JSON API ----------
def _cardnews_api_auth() -> bool:
    """X-Api-Key 헤더로 인증. CARDNEWS_API_KEY 미설정 시 항상 허용."""
    key = Config.CARDNEWS_API_KEY
    if not key:
        return True
    return request.headers.get("X-Api-Key") == key


@bp.route("/api/cluster/<int:cluster_id>", methods=["GET"])
def api_cluster_json(cluster_id: int):
    """클러스터 + 원기사 JSON — 카드뉴스 봇이 HTTP로 호출."""
    if not _cardnews_api_auth():
        return jsonify({"error": "unauthorized"}), 401
    cluster = Cluster.query.get(cluster_id)
    if not cluster:
        return jsonify({"error": "not found"}), 404
    members = cluster.articles.all()
    return jsonify({
        "id": cluster.id,
        "topic": cluster.topic,
        "summary_ko": cluster.summary_ko,
        "agreed_facts": cluster.agreed_facts or [],
        "divergences": cluster.divergences or [],
        "categories": cluster.categories or [],
        "importance": cluster.importance,
        "articles": [
            {
                "id": a.id,
                "title": a.title,
                "description": a.description,
                "url": a.url,
                "published_at": a.published_at.isoformat() if a.published_at else None,
                "source": a.source.name if a.source else None,
            }
            for a in members[:20]
        ],
    })


@bp.route("/api/clusters/recent", methods=["GET"])
def api_clusters_recent():
    """최근 클러스터 목록 JSON — 카드뉴스 봇 auto 선정용."""
    if not _cardnews_api_auth():
        return jsonify({"error": "unauthorized"}), 401
    since = (datetime.utcnow() + timedelta(hours=9)).date().isoformat()  # 오늘 KST 날짜만
    clusters = (
        Cluster.query
        .filter(Cluster.summary_ko.isnot(None))
        .filter(Cluster.hidden_at.is_(None))
        .filter(
            db.or_(
                Cluster.first_shown_date >= since,
                Cluster.first_shown_date.is_(None),
            )
        )
        .order_by(
            Cluster.importance.desc(),
            Cluster.saved_at.isnot(None).desc(),
            Cluster.id.desc(),
        )
        .limit(30)
        .all()
    )
    return jsonify([
        {
            "id": c.id,
            "topic": c.topic,
            "importance": c.importance,
            "saved": c.saved_at is not None,
            "first_shown_date": c.first_shown_date,
        }
        for c in clusters
    ])


# ---------- Glossary ----------
@bp.route("/api/glossary", methods=["GET"])
def api_glossary_all():
    """전체 글로서리 (사이드바 초기 로딩)."""
    from services.glossary import get_all_terms
    return jsonify({"ok": True, "terms": get_all_terms()})


@bp.route("/glossary", methods=["GET"])
def glossary_page():
    """글로서리 전체 페이지 (카테고리별)."""
    from models import GlossaryTerm
    terms = GlossaryTerm.query.order_by(GlossaryTerm.category, GlossaryTerm.term).all()
    by_cat = {}
    for t in terms:
        by_cat.setdefault(t.category, []).append(t)
    return render_template("glossary.html", terms_by_category=by_cat, total=len(terms))


# ============================================================
# 파티 (팀 빌딩)
# ============================================================

def _party_member_ids(party: Party) -> set[int]:
    return {m.user_id for m in party.members}


def _is_member(party: Party) -> bool:
    if not current_user.is_authenticated:
        return False
    return current_user.id in _party_member_ids(party)


@bp.route("/parties")
@login_required
def parties():
    """파티 목록 페이지."""
    filter_ = request.args.get("filter", "")
    contest_id = request.args.get("contest_id")

    q = Party.query
    if contest_id:
        q = q.filter(Party.contest_id == int(contest_id))

    my_party_ids = {
        m.party_id for m in PartyMember.query.filter_by(user_id=current_user.id).all()
    }

    if filter_ == "open":
        q = q.filter(Party.is_open == True)
    elif filter_ == "mine":
        q = q.filter(Party.id.in_(my_party_ids))

    all_parties = q.order_by(Party.created_at.desc()).all()
    return render_template(
        "parties.html",
        parties=all_parties,
        my_party_ids=my_party_ids,
        filter_open=(filter_ == "open"),
        filter_mine=(filter_ == "mine"),
    )


@bp.route("/parties/<int:party_id>")
@login_required
def party_detail(party_id: int):
    """파티 상세 (채팅) 페이지."""
    party = Party.query.get(party_id)
    if not party:
        abort(404)
    is_member_ = _is_member(party)
    today_kst_d = datetime.now(KST).date()
    active_contests = (
        Contest.query
        .filter(Contest.hidden_at.is_(None))
        .filter((Contest.deadline >= today_kst_d) | (Contest.deadline.is_(None)))
        .order_by(Contest.title)
        .all()
    )
    return render_template(
        "party_detail.html",
        party=party,
        is_member=is_member_,
        member_ids=_party_member_ids(party),
        active_contests=active_contests,
    )


@bp.route("/api/parties", methods=["POST"])
@login_required
def api_create_party():
    """파티 생성."""
    if not _rate_ok(f"party_create:{current_user.id}", 5, 86400):
        _sec_warn("PARTY_CREATE_RATE")
        return jsonify({"error": "파티 생성은 하루 5개까지 가능합니다."}), 429

    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()
    contest_id = data.get("contest_id")
    max_members = int(data.get("max_members") or 6)

    if not title:
        return jsonify({"error": "파티 이름을 입력해주세요."}), 400
    if max_members < 2 or max_members > 20:
        max_members = 6

    contest_title = None
    if contest_id:
        c = Contest.query.get(int(contest_id))
        if c:
            contest_title = c.title

    party = Party(
        title=title,
        description=description or None,
        contest_id=int(contest_id) if contest_id else None,
        contest_title=contest_title,
        leader_id=current_user.id,
        max_members=max_members,
        is_open=True,
    )
    db.session.add(party)
    db.session.flush()  # party.id 확보

    # 파티장 자동 입장
    db.session.add(PartyMember(party_id=party.id, user_id=current_user.id))
    db.session.commit()
    _log_activity("party_create", detail=f"party_id={party.id}")
    return jsonify({"ok": True, "party_id": party.id})


@bp.route("/api/parties/<int:party_id>/join", methods=["POST"])
@login_required
def api_join_party(party_id: int):
    """파티 참가."""
    party = Party.query.get(party_id)
    if not party:
        return jsonify({"error": "파티를 찾을 수 없습니다."}), 404
    if not party.is_open:
        return jsonify({"error": "모집이 마감된 파티입니다."}), 400
    if len(party.members) >= party.max_members:
        return jsonify({"error": "파티 정원이 꽉 찼습니다."}), 400
    if _is_member(party):
        return jsonify({"error": "이미 참가한 파티입니다."}), 400

    db.session.add(PartyMember(party_id=party_id, user_id=current_user.id))
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/parties/<int:party_id>/leave", methods=["POST"])
@login_required
def api_leave_party(party_id: int):
    """파티 탈퇴 (파티장은 불가)."""
    party = Party.query.get(party_id)
    if not party:
        return jsonify({"error": "파티를 찾을 수 없습니다."}), 404
    if party.leader_id == current_user.id:
        return jsonify({"error": "파티장은 탈퇴할 수 없습니다. 파티를 삭제해주세요."}), 400
    member = PartyMember.query.filter_by(party_id=party_id, user_id=current_user.id).first()
    if not member:
        return jsonify({"error": "참가하지 않은 파티입니다."}), 400
    db.session.delete(member)
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/parties/<int:party_id>/close", methods=["POST"])
@login_required
def api_close_party(party_id: int):
    """파티 모집 마감 / 재개 토글 (파티장 전용)."""
    party = Party.query.get(party_id)
    if not party or party.leader_id != current_user.id:
        return jsonify({"error": "권한이 없습니다."}), 403
    party.is_open = not party.is_open
    db.session.commit()
    return jsonify({"ok": True, "is_open": party.is_open})


@bp.route("/api/parties/<int:party_id>/delete", methods=["POST"])
@login_required
def api_delete_party(party_id: int):
    """파티 삭제 (파티장 또는 admin 전용)."""
    party = Party.query.get(party_id)
    if not party:
        return jsonify({"error": "파티를 찾을 수 없습니다."}), 404
    if party.leader_id != current_user.id and not is_admin():
        return jsonify({"error": "권한이 없습니다."}), 403
    db.session.delete(party)
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/parties/<int:party_id>/messages", methods=["GET"])
@login_required
def api_party_messages(party_id: int):
    """파티 메시지 폴링 (since_id 이후 최대 50개)."""
    party = Party.query.get(party_id)
    if not party:
        return jsonify({"error": "not_found"}), 404
    if not _is_member(party):
        return jsonify({"error": "참가자만 읽을 수 있습니다."}), 403

    since_id = request.args.get("since_id", 0, type=int)
    msgs = (
        PartyMessage.query
        .filter(PartyMessage.party_id == party_id, PartyMessage.id > since_id)
        .order_by(PartyMessage.created_at)
        .limit(50)
        .all()
    )
    return jsonify([
        {
            "id": m.id,
            "user_id": m.user_id,
            "display_name": m.user.display_name,
            "class_num": m.user.class_num,
            "content": m.content,
            "ts": (m.created_at + timedelta(hours=9)).strftime("%H:%M"),
        }
        for m in msgs
    ])


@bp.route("/api/profile/update", methods=["POST"])
@login_required
def api_profile_update():
    """이름 / 비밀번호 변경 (아이디 변경 불가)."""
    data = request.get_json(silent=True) or {}
    display_name     = (data.get("display_name") or "").strip()
    class_num        = data.get("class_num")
    password         = data.get("password") or ""
    password_confirm = data.get("password_confirm") or ""

    KO_RE = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")

    if display_name:
        has_ko = bool(KO_RE.search(display_name))
        max_len = 4 if has_ko else 14
        if len(display_name) > max_len:
            return jsonify({"error": f"이름은 {'한글 포함 시 최대 4자' if has_ko else '영문 최대 14자'}입니다."}), 400
        current_user.display_name = display_name

    if class_num is not None:
        try:
            cn = int(class_num)
        except (TypeError, ValueError):
            return jsonify({"error": "올바른 반을 선택해주세요."}), 400
        if cn not in range(1, 8):
            return jsonify({"error": "올바른 반을 선택해주세요."}), 400
        current_user.class_num = cn

    if password:
        if not _rate_ok(f"pw_change_user:{current_user.id}", 5, 86400):
            _log_activity("rate_limit", detail="pw_change 5/day 초과")
            return jsonify({"error": "비밀번호 변경은 하루 5회까지 가능합니다."}), 429
        if not _rate_ok(f"pw_change_ip:{_client_ip()}", 10, 86400):
            _log_activity("rate_limit", detail="pw_change ip 10/day 초과")
            return jsonify({"error": "비밀번호 변경은 하루 5회까지 가능합니다."}), 429
        if len(password) > 10:
            return jsonify({"error": "비밀번호는 최대 10자입니다."}), 400
        if password != password_confirm:
            return jsonify({"error": "비밀번호가 일치하지 않습니다."}), 400
        current_user.set_password(password)
        _log_activity("password_change")

    db.session.commit()
    _log_activity("profile_update")
    return jsonify({"ok": True, "display_name": current_user.display_name, "class_num": current_user.class_num})


@bp.route("/api/parties/<int:party_id>/update", methods=["POST"])
@login_required
def api_party_update(party_id: int):
    """파티 정보 수정 (파티장 전용)."""
    party = Party.query.get(party_id)
    if not party or party.leader_id != current_user.id:
        return jsonify({"error": "권한이 없습니다."}), 403

    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "파티 이름을 입력해주세요."}), 400
    if len(title) > 80:
        return jsonify({"error": "파티 이름은 최대 80자입니다."}), 400
    party.title = title
    party.description = (data.get("description") or "").strip() or None

    contest_id = data.get("contest_id")
    if contest_id:
        c = Contest.query.get(int(contest_id))
        party.contest_id = c.id if c else None
        party.contest_title = c.title if c else None
    else:
        party.contest_id = None
        party.contest_title = None

    max_members = data.get("max_members")
    if max_members:
        max_members = int(max_members)
        current_count = PartyMember.query.filter_by(party_id=party_id).count()
        if max_members < current_count:
            return jsonify({"error": f"현재 파티원이 {current_count}명이므로 {max_members}명으로 줄일 수 없습니다."}), 400
        party.max_members = max_members

    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/parties/<int:party_id>/kick/<int:user_id>", methods=["POST"])
@login_required
def api_party_kick(party_id: int, user_id: int):
    """파티원 추방 (파티장 전용)."""
    party = Party.query.get(party_id)
    if not party or party.leader_id != current_user.id:
        return jsonify({"error": "권한이 없습니다."}), 403
    if user_id == current_user.id:
        return jsonify({"error": "자신은 추방할 수 없습니다."}), 400

    member = PartyMember.query.filter_by(party_id=party_id, user_id=user_id).first()
    if not member:
        return jsonify({"error": "해당 파티원이 없습니다."}), 404

    db.session.delete(member)
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/parties/<int:party_id>/messages", methods=["POST"])
@login_required
def api_party_post_message(party_id: int):
    """파티 메시지 전송."""
    party = Party.query.get(party_id)
    if not party:
        return jsonify({"error": "not_found"}), 404
    if not _is_member(party):
        return jsonify({"error": "참가자만 메시지를 보낼 수 있습니다."}), 403

    if not _rate_ok(f"msg:{current_user.id}", 30, 60):
        _sec_warn("MSG_RATE", f"party={party_id}")
        return jsonify({"error": "메시지를 너무 빨리 보내고 있습니다. 잠시 후 다시 시도해주세요."}), 429

    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "내용을 입력해주세요."}), 400
    if len(content) > 1000:
        return jsonify({"error": "메시지는 최대 1000자입니다."}), 400

    msg = PartyMessage(party_id=party_id, user_id=current_user.id, content=content)
    db.session.add(msg)
    db.session.commit()
    return jsonify({
        "ok": True,
        "id": msg.id,
        "user_id": msg.user_id,
        "display_name": current_user.display_name,
        "class_num": current_user.class_num,
        "content": msg.content,
        "ts": (msg.created_at + timedelta(hours=9)).strftime("%H:%M"),
    })


# ============================================================
# 인사교당근 API
# ============================================================

def _karrot_upload_dir() -> str:
    d = os.path.join(current_app.static_folder, "uploads", "karrot")
    os.makedirs(d, exist_ok=True)
    return d


def _delete_karrot_image(image_url: str | None):
    if not image_url or "/uploads/karrot/" not in image_url:
        return
    fname = os.path.basename(image_url)
    try:
        p = os.path.join(_karrot_upload_dir(), fname)
        if os.path.exists(p):
            os.remove(p)
    except OSError:
        pass


@bp.route("/api/karrot", methods=["POST"])
@login_required
def api_karrot_create():
    """당근 게시글 생성 (multipart/form-data — image 포함 가능)."""
    if not _rate_ok(f"karrot_create:{current_user.id}", 10, 86400):
        _sec_warn("KARROT_CREATE_RATE")
        return jsonify({"error": "게시글은 하루 10개까지 작성할 수 있습니다."}), 429

    post_type = (request.form.get("post_type") or "share").strip()
    if post_type not in ("share", "loan"):
        post_type = "share"
    title = (request.form.get("title") or "").strip()
    if not title:
        return jsonify({"error": "제목을 입력해주세요."}), 400
    if len(title) > 100:
        return jsonify({"error": "제목은 최대 100자입니다."}), 400
    content = (request.form.get("content") or "").strip()
    if len(content) > 1000:
        return jsonify({"error": "내용은 최대 1000자입니다."}), 400

    class_target_raw = request.form.get("class_target", "").strip()
    class_target = int(class_target_raw) if class_target_raw.isdigit() and 1 <= int(class_target_raw) <= 7 else None

    loan_period = None
    if post_type == "loan":
        lp = (request.form.get("loan_period") or "").strip()
        loan_period = lp if lp else "무기한"

    post = KarrotPost(
        post_type=post_type,
        title=title,
        content=content or None,
        class_target=class_target,
        loan_period=loan_period,
        author_id=current_user.id,
    )
    db.session.add(post)
    db.session.flush()  # id 확보

    file = request.files.get("image")
    if file and file.filename:
        ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        if ext in ALLOWED_IMAGE_EXT:
            fname = secure_filename(f"karrot_{post.id}_{secrets.token_hex(6)}.{ext}")
            file.save(os.path.join(_karrot_upload_dir(), fname))
            post.image_url = url_for("static", filename=f"uploads/karrot/{fname}")

    db.session.commit()
    return jsonify({"ok": True, "post_id": post.id})


@bp.route("/api/karrot/<int:post_id>/edit", methods=["POST"])
@login_required
def api_karrot_edit(post_id: int):
    """당근 게시글 제목/내용 수정 (작성자 본인만, 완료 전)."""
    post = KarrotPost.query.get(post_id)
    if not post:
        return jsonify({"error": "not_found"}), 404
    if post.author_id != current_user.id:
        return jsonify({"error": "수정 권한이 없습니다."}), 403
    if post.status == "completed":
        return jsonify({"error": "완료된 게시글은 수정할 수 없습니다."}), 400
    title = (request.form.get("title") or "").strip()
    if not title:
        return jsonify({"error": "제목을 입력해주세요."}), 400
    if len(title) > 100:
        return jsonify({"error": "제목은 최대 100자입니다."}), 400
    content = (request.form.get("content") or "").strip()
    if len(content) > 1000:
        return jsonify({"error": "내용은 최대 1000자입니다."}), 400
    post.title = title
    post.content = content or None

    class_target_raw = request.form.get("class_target", "").strip()
    post.class_target = int(class_target_raw) if class_target_raw.isdigit() and 1 <= int(class_target_raw) <= 7 else None

    if post.post_type == "loan":
        lp = (request.form.get("loan_period") or "").strip()
        if lp:
            post.loan_period = lp

    if request.form.get("remove_image") == "1":
        _delete_karrot_image(post.image_url)
        post.image_url = None
    else:
        file = request.files.get("image")
        if file and file.filename:
            ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
            if ext in ALLOWED_IMAGE_EXT:
                _delete_karrot_image(post.image_url)
                fname = secure_filename(f"karrot_{post.id}_{secrets.token_hex(6)}.{ext}")
                file.save(os.path.join(_karrot_upload_dir(), fname))
                post.image_url = url_for("static", filename=f"uploads/karrot/{fname}")

    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/karrot/<int:post_id>/delete", methods=["POST"])
@login_required
def api_karrot_delete(post_id: int):
    """당근 게시글 삭제 (작성자 본인만)."""
    post = KarrotPost.query.get(post_id)
    if not post:
        return jsonify({"error": "not_found"}), 404
    if post.author_id != current_user.id and not is_admin():
        return jsonify({"error": "삭제 권한이 없습니다."}), 403
    _delete_karrot_image(post.image_url)
    db.session.delete(post)
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/karrot/<int:post_id>")
@login_required
def karrot_detail(post_id: int):
    """당근 게시글 상세 페이지."""
    post = KarrotPost.query.get(post_id)
    if not post:
        abort(404)
    my_app = KarrotApplication.query.filter_by(post_id=post_id, user_id=current_user.id).first()
    applications = (
        KarrotApplication.query.filter_by(post_id=post_id).all()
        if post.author_id == current_user.id else []
    )
    return render_template(
        "karrot_detail.html",
        post=post,
        my_app=my_app,
        applications=applications,
        is_author=(post.author_id == current_user.id),
    )


@bp.route("/api/karrot/<int:post_id>/apply", methods=["POST"])
@login_required
def api_karrot_apply(post_id: int):
    """당근 게시글 신청."""
    post = KarrotPost.query.get(post_id)
    if not post:
        return jsonify({"error": "not_found"}), 404
    if post.author_id == current_user.id:
        return jsonify({"error": "본인 게시글에는 신청할 수 없습니다."}), 400
    if post.status != "open":
        return jsonify({"error": "이미 완료된 거래입니다."}), 400
    if not _rate_ok(f"karrot_apply:{current_user.id}", 20, 86400):
        return jsonify({"error": "신청 횟수를 초과했습니다."}), 429
    existing = KarrotApplication.query.filter_by(post_id=post_id, user_id=current_user.id).first()
    if existing:
        return jsonify({"error": "이미 신청한 게시글입니다."}), 400
    app_ = KarrotApplication(post_id=post_id, user_id=current_user.id)
    db.session.add(app_)
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/karrot/<int:post_id>/match/<int:user_id>", methods=["POST"])
@login_required
def api_karrot_match(post_id: int, user_id: int):
    """당근 매칭 확정 (작성자만). 완료 처리 후 matched_user_id 세팅."""
    post = KarrotPost.query.get(post_id)
    if not post:
        return jsonify({"error": "not_found"}), 404
    if post.author_id != current_user.id:
        return jsonify({"error": "권한이 없습니다."}), 403
    if post.status != "open":
        return jsonify({"error": "이미 완료된 거래입니다."}), 400
    app_ = KarrotApplication.query.filter_by(post_id=post_id, user_id=user_id).first()
    if not app_:
        return jsonify({"error": "해당 신청자를 찾을 수 없습니다."}), 404
    post.status = "completed"
    post.completed_at = datetime.utcnow()
    post.matched_user_id = user_id
    db.session.commit()
    label = "나눔완료" if post.post_type == "share" else "교환완료"
    return jsonify({"ok": True, "label": label})
