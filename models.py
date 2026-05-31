"""SQLAlchemy models.

원칙:
- Article.body 는 본인 사적이용 분석용으로만 사용한다.
  이메일·UI 출력에는 절대 노출하지 않는다 (헤드라인 + LLM 자체 요약 + 출처 링크만).
- Paper 는 뉴스와 완전히 별도 트랙으로 관리한다.
"""
from datetime import datetime
import secrets

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def _gen_token() -> str:
    return secrets.token_urlsafe(32)


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

    # 본문 — 사적이용 분석용 (이메일 출력 절대 금지)
    body = db.Column(db.Text)
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
