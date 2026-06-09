"""Application configuration loaded from environment variables (.env)."""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class Config:
    # Flask
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")

    # Database
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{BASE_DIR / 'data' / 'app.db'}",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- 로컬 임베딩 (paraphrase-multilingual-MiniLM-L12-v2, 384차원) ---
    LOCAL_EMBEDDING_MODEL = os.getenv("LOCAL_EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    EMBEDDING_DEVICE = os.getenv("EMBEDDING_DEVICE") or None
    EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "16"))

    # --- Claude API (요약 메인) ---
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    # Haiku 4.5: 한국어 강함, $1/M in, $5/M out, 분당 50회+
    CLAUDE_SUMMARY_MODEL = os.getenv("CLAUDE_SUMMARY_MODEL", "claude-haiku-4-5")

    # --- Gemini API (요약 메인 — Claude 대체, 무료 한도 분당 15회/일 1500회) ---
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_SUMMARY_MODEL = os.getenv("GEMINI_SUMMARY_MODEL", "gemini-2.0-flash")

    # --- (선택) Voyage AI ---
    VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY", "")
    VOYAGE_EMBEDDING_MODEL = os.getenv("VOYAGE_EMBEDDING_MODEL", "voyage-3.5-lite")

    # Gmail SMTP
    GMAIL_USER = os.getenv("GMAIL_USER", "")
    GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
    GMAIL_SENDER_NAME = os.getenv("GMAIL_SENDER_NAME", "AI News Digest")

    # Clustering
    # 신규 기사 편입 임계값 — 같은 구체적 사건/발표 기사끼리만 묶이도록 0.80으로 상향
    # (0.72는 너무 낮아 "엔비디아" 같은 회사명만 공유해도 묶이는 문제 발생)
    CLUSTER_SIMILARITY_THRESHOLD = float(os.getenv("CLUSTER_SIMILARITY_THRESHOLD", "0.80"))
    # 기존 클러스터끼리 사후 병합 임계값
    # 0.82는 너무 낮아 서로 다른 AI 뉴스 사건들이 하나의 메가클러스터로 합쳐짐 → 0.90으로 상향
    CLUSTER_MERGE_THRESHOLD = float(os.getenv("CLUSTER_MERGE_THRESHOLD", "0.90"))
    CLUSTER_TIME_WINDOW_HOURS = int(os.getenv("CLUSTER_TIME_WINDOW_HOURS", "72"))

    # 논문
    PAPER_RECENT_DAYS = int(os.getenv("PAPER_RECENT_DAYS", "3"))
    DAILY_PAPER_PICK = int(os.getenv("DAILY_PAPER_PICK", "5"))

    BASE_URL = os.getenv("BASE_URL", "http://localhost:5000")

    # --- 공모전 수집 ---
    # data.go.kr 서비스키 (K-Startup 공고 API용). 비어 있으면 해당 소스만 skip.
    DATA_GO_KR_KEY = os.getenv("DATA_GO_KR_KEY", "")
    # 라우드(loud.kr) 로그인 — 로그인 시에만 실제 공모전 포스터·참여가능 여부가 보임.
    # 비어 있으면 비로그인 동작(이미지 fallback). .env 에 직접 입력(비커밋).
    LOUD_EMAIL = os.getenv("LOUD_EMAIL", "")
    LOUD_PASSWORD = os.getenv("LOUD_PASSWORD", "")
    # 마감 지난 공모전 보존 유예일 (cleanup 시 deadline 이 N일 이상 지난 것만 삭제)
    CONTEST_RETENTION_DAYS = int(os.getenv("CONTEST_RETENTION_DAYS", "2"))
    # 공모전 이미지 업로드 한도 (8MB)
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", str(8 * 1024 * 1024)))

    # --- 카드뉴스 스튜디오 단축 링크 ---
    # 데일리싱크 카드의 "📐 카드뉴스" 버튼이 여는 URL.
    # 기본: 로컬 cardnews_bot 서버. 다른 호스트면 .env 에 CARDNEWS_BOT_URL=https://... 설정.
    CARDNEWS_BOT_URL = os.getenv("CARDNEWS_BOT_URL", "http://localhost:5050")

    # --- 카드뉴스 봇 → 데일리싱크 API 인증키 ---
    # 카드뉴스 봇이 /api/cluster/<id> 등을 호출할 때 헤더(X-Api-Key)로 전달.
    # 비어있으면 인증 없이 허용 (로컬 개발 전용).
    CARDNEWS_API_KEY = os.getenv("CARDNEWS_API_KEY", "")
