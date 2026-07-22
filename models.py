"""SQLAlchemy models.

원칙:
- Article.body 는 본인 사적이용 분석용으로만 사용한다.
  이메일·UI 출력에는 절대 노출하지 않는다 (헤드라인 + LLM 자체 요약 + 출처 링크만).
- Paper 는 뉴스와 완전히 별도 트랙으로 관리한다.
"""
from datetime import datetime
import secrets

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

db = SQLAlchemy()


def _gen_token() -> str:
    return secrets.token_urlsafe(32)


# ---------- AdminUser (로그인 계정) ----------
class AdminUser(UserMixin, db.Model):
    """관리자/사용자 로그인 계정.

    뉴스레터 구독자 User 와 완전히 별개 테이블.
    username == 'admin' 인 계정만 role='admin' 부여, 나머지는 'user'.
    """
    __tablename__ = "admin_users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(14), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(128), nullable=False)
    display_name = db.Column(db.String(50), nullable=False)
    role = db.Column(db.String(10), default="user", nullable=False)  # admin | user
    class_num = db.Column(db.Integer, nullable=True)  # 1~6반 (NULL = 미설정)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def set_password(self, raw: str) -> None:
        import bcrypt as _bcrypt
        self.password_hash = _bcrypt.hashpw(raw.encode(), _bcrypt.gensalt()).decode()

    def check_password(self, raw: str) -> bool:
        import bcrypt as _bcrypt
        return _bcrypt.checkpw(raw.encode(), self.password_hash.encode())

    def __repr__(self):
        return f"<AdminUser {self.username} ({self.role})>"


# ---------- UserActivity (회원 활동 이력) ----------
class UserActivity(db.Model):
    """회원 로그인·가입·프로필 수정 등 활동 이력.

    user_id 는 이벤트 발생 시점 FK (탈퇴 시 NULL 유지).
    username 은 이벤트 시점 스냅샷이므로 항상 기록.
    created_at 은 UTC naive; 표시 시 +9h KST 변환.
    """
    __tablename__ = "user_activity"

    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True, index=True)
    username   = db.Column(db.String(14), nullable=True)   # 이벤트 시점 스냅샷
    action     = db.Column(db.String(40), nullable=False)  # login_ok / login_fail / register / logout / profile_update / ...
    ip         = db.Column(db.String(45), nullable=True)   # IPv4 또는 IPv6
    detail     = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)


# ---------- User ----------
class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100))

    timezone = db.Column(db.String(50), default="Asia/Seoul", nullable=False)
    delivery_time = db.Column(db.String(5), default="08:00", nullable=False)
    frequency = db.Column(db.String(20), default="daily", nullable=False)
    categories = db.Column(db.JSON, default=list, nullable=False)
    language_pref = db.Column(db.String(10), default="ko", nullable=False)

    # 논문 섹션 수신 여부
    include_papers = db.Column(db.Boolean, default=True, nullable=False)
    paper_categories = db.Column(db.JSON, default=list, nullable=False)

    unsubscribe_token = db.Column(db.String(64), unique=True, default=_gen_token, nullable=False)
    opt_in_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    status = db.Column(db.String(20), default="active", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<User {self.email}>"


# ---------- Source ----------
class Source(db.Model):
    __tablename__ = "sources"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    rss_url = db.Column(db.String(500), unique=True, nullable=False)
    lang = db.Column(db.String(10), nullable=False)
    tier = db.Column(db.Integer, default=1, nullable=False)
    needs_ai_filter = db.Column(db.Boolean, default=False, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    # 'rss' (기본) 또는 'sitemap' (Google News Sitemap XML)
    feed_type = db.Column(db.String(20), default="rss", nullable=False)

    last_fetched_at = db.Column(db.DateTime)
    last_error = db.Column(db.Text)

    articles = db.relationship("Article", backref="source", lazy="dynamic")

    def __repr__(self):
        return f"<Source {self.name}>"


# ---------- Cluster ----------
class Cluster(db.Model):
    __tablename__ = "clusters"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    centroid = db.Column(db.JSON)
    topic = db.Column(db.String(300))
    summary_ko = db.Column(db.Text)
    agreed_facts = db.Column(db.JSON, default=list)
    divergences = db.Column(db.JSON, default=list)
    categories = db.Column(db.JSON, default=list)
    importance = db.Column(db.Integer, default=3)
    summary_dirty = db.Column(db.Boolean, default=True, nullable=False)
    hidden_at = db.Column(db.DateTime, nullable=True, index=True)  # NULL = 표시, 값 = 숨김
    saved_at = db.Column(db.DateTime, nullable=True, index=True)   # NULL = 미저장, 값 = 저장됨
    first_shown_date = db.Column(db.Date, nullable=True, index=True)  # 처음 표시된 KST 날짜 (중복 노출 방지)
    primary_article_id = db.Column(db.Integer, nullable=True)  # 클러스터를 처음 생성한 기사 ID (원문 링크 기준)
    pinned_featured = db.Column(db.Boolean, default=False, nullable=False)  # 관리자 피처드 고정
    pinned_at = db.Column(db.DateTime, nullable=True)  # 고정 시각 (NULL = 고정 안 됨)
    cover_image_position = db.Column(db.String(32), nullable=True)  # CSS object-position (NULL = "top center")

    articles = db.relationship("Article", backref="cluster", lazy="dynamic")

    def __repr__(self):
        return f"<Cluster {self.id} {self.topic!r}>"


# ---------- Article ----------
class Article(db.Model):
    __tablename__ = "articles"

    id = db.Column(db.Integer, primary_key=True)
    source_id = db.Column(db.Integer, db.ForeignKey("sources.id"), nullable=False)
    cluster_id = db.Column(db.Integer, db.ForeignKey("clusters.id"), nullable=True, index=True)

    url = db.Column(db.String(1000), nullable=False)
    url_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    title = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text)
    published_at = db.Column(db.DateTime, index=True)
    fetched_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # 본문 + 이미지 — 사적이용 분석용 (이메일 출력 절대 금지)
    body = db.Column(db.Text)
    image_url = db.Column(db.String(1000), nullable=True)   # OG 이미지 URL (썸네일 표시용)
    body_fetched_at = db.Column(db.DateTime)
    body_status = db.Column(db.String(20), default="pending", nullable=False)
    # pending | success | failed | blocked | skipped

    embedding = db.Column(db.JSON)
    is_ai_relevant = db.Column(db.Boolean, default=True, nullable=False)

    def __repr__(self):
        return f"<Article {self.id} {self.title[:40]!r}>"


# ---------- Paper ----------
class Paper(db.Model):
    """AI 논문 — 뉴스와 완전히 별도 트랙."""
    __tablename__ = "papers"

    id = db.Column(db.Integer, primary_key=True)
    arxiv_id = db.Column(db.String(40), unique=True, index=True, nullable=False)
    source_type = db.Column(db.String(20), default="arxiv", nullable=False)

    title = db.Column(db.String(500), nullable=False)
    authors = db.Column(db.JSON, default=list)
    abstract = db.Column(db.Text)
    categories = db.Column(db.JSON, default=list)
    published_at = db.Column(db.DateTime, index=True)
    fetched_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    html_url = db.Column(db.String(500))
    pdf_url = db.Column(db.String(500))

    # 큐레이션 시그널
    hf_featured = db.Column(db.Boolean, default=False, nullable=False, index=True)
    hf_upvotes = db.Column(db.Integer, default=0)
    citation_count = db.Column(db.Integer, default=0)

    embedding = db.Column(db.JSON)

    # 구조화 요약 (이메일 렌더용)
    title_ko = db.Column(db.String(500))
    summary_ko = db.Column(db.Text)
    problem_ko = db.Column(db.Text)
    method_ko = db.Column(db.Text)
    results_ko = db.Column(db.Text)
    significance_ko = db.Column(db.Text)
    limitations_ko = db.Column(db.Text)
    summary_dirty = db.Column(db.Boolean, default=True, nullable=False)
    hidden_at = db.Column(db.DateTime, nullable=True, index=True)  # NULL = 표시, 값 = 숨김
    saved_at = db.Column(db.DateTime, nullable=True, index=True)   # NULL = 미저장, 값 = 저장됨
    figure_url = db.Column(db.String(1000), nullable=True)  # 논문 대표 이미지 (HF 썸네일 등)
    pinned_featured = db.Column(db.Boolean, default=False, nullable=False)  # 관리자 피처드 고정
    pinned_at = db.Column(db.DateTime, nullable=True)  # 고정 시각

    sent_at = db.Column(db.DateTime)  # 다이제스트 발송 이력 (중복 발송 방지)

    def __repr__(self):
        return f"<Paper {self.arxiv_id} {self.title[:40]!r}>"


# ---------- Contest ----------
class Contest(db.Model):
    """AI 공모전·취창업 경진대회 — 뉴스/논문과 완전히 별도 트랙.

    여러 플랫폼(위비티·씽굿·요즘것들·데이콘·K-Startup 등)에서 수집한
    AI 관련 공고를 통합. 수집·표시 모두 마감이 남은 것만 노출.
    """
    __tablename__ = "contests"

    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(30), nullable=False, index=True)  # wevity | thinkcontest | allforyoung | dacon | kstartup
    external_id = db.Column(db.String(120))  # 소스 native id (보조 dedup)

    url = db.Column(db.String(1000), nullable=False)
    url_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)  # sha256(url)
    title = db.Column(db.String(500), nullable=False)
    host = db.Column(db.String(300))  # 주최/주관

    image_url = db.Column(db.String(1000))  # 포스터 썸네일 (핫링크 or 업로드 경로, nullable)
    # 관리자 업로드 이미지의 타일 내 표시 조정 (object-position % + 확대 배율)
    image_pos_x = db.Column(db.Float, default=50.0, nullable=False)  # 0~100 (좌→우)
    image_pos_y = db.Column(db.Float, default=50.0, nullable=False)  # 0~100 (위→아래)
    image_scale = db.Column(db.Float, default=1.0, nullable=False)   # 1.0~ (확대)
    category = db.Column(db.String(40))  # 공모전 | 창업경진대회 | 해커톤 | 취업/채용 | 기타
    field_tags = db.Column(db.JSON, default=list)  # 원천 분야 라벨 ["AI", "빅데이터"]
    target = db.Column(db.String(300))  # 참가대상
    prize = db.Column(db.String(300))   # 시상/상금

    start_at = db.Column(db.Date)                       # 접수 시작
    deadline = db.Column(db.Date, index=True)           # 접수 마감 ← 핵심 필터 키
    posted_at = db.Column(db.Date)                      # 등록일
    fetched_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    is_ai_relevant = db.Column(db.Boolean, default=True, nullable=False)

    # LLM 정규화 (선택, Phase 2) — Paper.summary_dirty 패턴
    summary_ko = db.Column(db.Text)
    summary_dirty = db.Column(db.Boolean, default=True, nullable=False)

    hidden_at = db.Column(db.DateTime, nullable=True, index=True)  # NULL = 표시, 값 = 숨김
    saved_at = db.Column(db.DateTime, nullable=True, index=True)   # NULL = 미저장, 값 = 저장됨

    def __repr__(self):
        return f"<Contest {self.id} {self.source} {self.title[:30]!r}>"


# ---------- TechPost (기업 기술블로그) ----------
class TechPost(db.Model):
    """기업 기술블로그 "핫한 글" — 뉴스/논문/공모전과 완전히 별도 트랙.

    회사 기술블로그 RSS를 수집하고, GeekNews 등 2차 소스에서 "오늘 언급된" 글을
    매칭해 hot_score 를 매긴다. body 는 Article.body와 동일한 원칙으로 요약
    입력에만 쓰고 이메일/UI 에는 절대 노출하지 않는다 (README 원칙 1).
    """
    __tablename__ = "tech_posts"

    id = db.Column(db.Integer, primary_key=True)
    blog = db.Column(db.String(50), nullable=False, index=True)  # "naver_d2" | "toss" | "woowahan" ...
    url = db.Column(db.String(1000), nullable=False)
    url_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    title = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text)
    image_url = db.Column(db.String(1000), nullable=True)
    published_at = db.Column(db.DateTime, index=True)
    fetched_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    hot_score = db.Column(db.Float, default=0.0, index=True)
    mentioned_by = db.Column(db.JSON, default=list)   # ["geeknews", "yozm_it"]
    pinned_featured = db.Column(db.Boolean, default=False, nullable=False)
    pinned_at = db.Column(db.DateTime, nullable=True)

    summary_ko = db.Column(db.Text)          # 짧은 티저 요약 (본문 재현 금지 — README 원칙 1)
    key_points = db.Column(db.JSON, default=list)  # 뉴스 agreed_facts처럼 짧은 포인트 리스트
    summary_dirty = db.Column(db.Boolean, default=True, nullable=False)

    # 본문 — 요약 입력 전용 (Article.body와 동일한 원칙: 이메일/UI 렌더링 절대 금지)
    body = db.Column(db.Text)
    body_fetched_at = db.Column(db.DateTime)
    body_status = db.Column(db.String(20), default="pending", nullable=False)
    hidden_at = db.Column(db.DateTime, nullable=True, index=True)
    saved_at = db.Column(db.DateTime, nullable=True, index=True)

    def __repr__(self):
        return f"<TechPost {self.id} {self.blog} {self.title[:40]!r}>"


# ---------- Party (팀 빌딩) ----------
class Party(db.Model):
    """공모전 팀 빌딩 파티."""
    __tablename__ = "parties"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    contest_id = db.Column(db.Integer, db.ForeignKey("contests.id"), nullable=True)
    contest_title = db.Column(db.String(400), nullable=True)  # 공모전 삭제 대비 비정규화
    leader_id = db.Column(db.Integer, db.ForeignKey("admin_users.id"), nullable=False)
    description = db.Column(db.Text)
    max_members = db.Column(db.Integer, default=6, nullable=False)
    is_open = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    leader = db.relationship("AdminUser", foreign_keys=[leader_id], backref="led_parties")
    members = db.relationship("PartyMember", backref="party", cascade="all, delete-orphan")
    messages = db.relationship(
        "PartyMessage", backref="party", cascade="all, delete-orphan",
        order_by="PartyMessage.created_at",
    )
    contest = db.relationship("Contest", backref="parties")

    def __repr__(self):
        return f"<Party {self.id} {self.title!r}>"


class PartyMember(db.Model):
    """파티 구성원 (파티장 포함)."""
    __tablename__ = "party_members"

    id = db.Column(db.Integer, primary_key=True)
    party_id = db.Column(db.Integer, db.ForeignKey("parties.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("admin_users.id"), nullable=False)
    joined_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("AdminUser", backref="party_memberships")

    __table_args__ = (db.UniqueConstraint("party_id", "user_id"),)

    def __repr__(self):
        return f"<PartyMember party={self.party_id} user={self.user_id}>"


class PartyMessage(db.Model):
    """파티 내 채팅 메시지."""
    __tablename__ = "party_messages"

    id = db.Column(db.Integer, primary_key=True)
    party_id = db.Column(db.Integer, db.ForeignKey("parties.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("admin_users.id"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    user = db.relationship("AdminUser", backref="party_messages")

    def __repr__(self):
        return f"<PartyMessage {self.id} party={self.party_id}>"


# ---------- KarrotPost ----------
class KarrotPost(db.Model):
    """인사교당근 — 나눔 / 물물교환 게시글."""
    __tablename__ = "karrot_posts"

    id = db.Column(db.Integer, primary_key=True)
    post_type = db.Column(db.String(10), nullable=False, default="share")  # 'share' | 'trade' | 'loan'
    title = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text)
    image_url = db.Column(db.String(500))
    class_target = db.Column(db.Integer, nullable=True)  # None=전체, 1~7=특정 반
    loan_period = db.Column(db.String(50), nullable=True)  # 단기대여 기간 (무기한/사용만료시점까지/N일)
    status = db.Column(db.String(10), nullable=False, default="open")  # 'open' | 'completed'
    completed_at = db.Column(db.DateTime, nullable=True)
    matched_user_id = db.Column(db.Integer, db.ForeignKey("admin_users.id"), nullable=True)
    author_id = db.Column(db.Integer, db.ForeignKey("admin_users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    author = db.relationship("AdminUser", foreign_keys=[author_id], backref="karrot_posts")
    matched_user = db.relationship("AdminUser", foreign_keys=[matched_user_id], backref="karrot_matches")
    applications = db.relationship("KarrotApplication", backref="post", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<KarrotPost {self.id} {self.post_type!r} {self.title!r}>"


class KarrotApplication(db.Model):
    """인사교당근 신청."""
    __tablename__ = "karrot_applications"

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("karrot_posts.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("admin_users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("AdminUser", backref="karrot_applications")

    __table_args__ = (db.UniqueConstraint("post_id", "user_id"),)

    def __repr__(self):
        return f"<KarrotApplication post={self.post_id} user={self.user_id}>"


# ---------- JobRun ----------
class JobRun(db.Model):
    """백그라운드 잡 실행 이력 (스케줄러용)."""
    __tablename__ = "job_runs"

    id = db.Column(db.Integer, primary_key=True)
    job_name = db.Column(db.String(50), nullable=False, index=True)
    started_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    finished_at = db.Column(db.DateTime)
    status = db.Column(db.String(20), default="running", nullable=False)  # running | success | failed
    stats = db.Column(db.JSON, default=dict)
    error = db.Column(db.Text)
    triggered_by = db.Column(db.String(20), default="scheduler")  # scheduler | manual

    def __repr__(self):
        return f"<JobRun {self.job_name} {self.status}>"


# ---------- Glossary ----------
class GlossaryTerm(db.Model):
    """AI 분야 용어 사전.

    term: 영문 정식 표기 (예: "Transformer")
    term_ko: 한국 표기 (음역 또는 정착 번역어, 예: "트랜스포머")
    aliases: 다른 영문 표기·약어들 (예: ["self-attention"])
    explain_ko: 1-2문장 한국어 설명
    category: model | architecture | training | metric | rl | general
    """
    __tablename__ = "glossary_terms"

    id = db.Column(db.Integer, primary_key=True)
    term = db.Column(db.String(120), unique=True, nullable=False, index=True)
    term_ko = db.Column(db.String(120), nullable=False)
    aliases = db.Column(db.JSON, default=list)
    explain_ko = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(30), default="general", index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    source = db.Column(db.String(20), default="seed")  # seed | auto | manual

    def __repr__(self):
        return f"<GlossaryTerm {self.term}>"


# ---------- UserBookmark ----------
class UserBookmark(db.Model):
    """회원별 즐겨찾기 — contest / cluster / paper."""
    __tablename__ = "user_bookmarks"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("admin_users.id"), nullable=False, index=True)
    item_type = db.Column(db.String(10), nullable=False)  # contest | cluster | paper
    item_id = db.Column(db.Integer, nullable=False)
    saved_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("AdminUser", backref="bookmarks")

    __table_args__ = (db.UniqueConstraint("user_id", "item_type", "item_id"),)

    def __repr__(self):
        return f"<UserBookmark user={self.user_id} {self.item_type}={self.item_id}>"


# ---------- AppSetting ----------
class AppSetting(db.Model):
    """앱 전역 설정 (키-값 단순 저장소)."""
    __tablename__ = "app_settings"

    key = db.Column(db.String(60), primary_key=True)
    value = db.Column(db.String(200), nullable=False, default="")

    def __repr__(self):
        return f"<AppSetting {self.key}={self.value!r}>"


# ---------- Digest ----------
class Digest(db.Model):
    __tablename__ = "digests"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    scheduled_for = db.Column(db.DateTime, nullable=False, index=True)
    sent_at = db.Column(db.DateTime)
    cluster_ids = db.Column(db.JSON, default=list)
    paper_ids = db.Column(db.JSON, default=list)
    status = db.Column(db.String(20), default="pending", nullable=False, index=True)
    retry_count = db.Column(db.Integer, default=0, nullable=False)
    error = db.Column(db.Text)

    user = db.relationship("User", backref="digests")

    def __repr__(self):
        return f"<Digest user={self.user_id} status={self.status}>"
