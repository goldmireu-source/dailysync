# AI News Digest

> 국내외 AI 뉴스를 매일 수집·클러스터링하고, LLM이 교차검증 요약한 한국어 다이제스트를 제공하는 개인용 큐레이션 서비스

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/flask-3.x-lightgrey.svg)](https://flask.palletsprojects.com/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## ✨ 주요 기능

- **자동 수집** — RSS 피드(한·영 16개 소스)를 매시 수집; Tier-2(OpenAI·Anthropic·DeepMind 공식)는 전량 수집, Tier-1 일반 매체는 AI 키워드 필터 적용
- **논문 트래킹** — arXiv 및 Hugging Face Papers에서 최신 AI 논문 수집
- **임베딩·클러스터링** — BGE-M3(ONNX) 로컬 임베딩 → 코사인 유사도 기반 증분 클러스터링으로 같은 사건의 기사를 묶음
- **LLM 요약** — Claude Haiku가 각 클러스터를 교차검증 후 한국어 3–5줄 요약 생성
- **Flask 대시보드** — 날짜별 클러스터·논문 뷰, 북마크(saved), 숨기기(hidden), 용어집(/glossary)
- **공모전 수집** — AI·데이터 관련 공모전을 하루 2회(07:30·19:30 KST) 수집; 북마크·숨기기 지원
- **관리자 패널** — 파이프라인 수동 트리거, 잡 실행 현황 폴링
- **자동 배포** — GitHub Actions → rsync → Oracle Cloud (KST 기준 6시간마다 전체 파이프라인)

---

## 🛠 기술 스택

| 영역 | 기술 |
| --- | --- |
| 웹 프레임워크 | Flask 3 + APScheduler (단일 프로세스) |
| ORM / DB | SQLAlchemy 2 · SQLite |
| 임베딩 모델 | BGE-M3 via fastembed (ONNX Runtime, 로컬 실행) |
| LLM | Claude Haiku 4.5 (Anthropic SDK) |
| RSS 파싱 | feedparser · requests |
| 본문 추출 | trafilatura (클러스터링 입력 전용 — UI/이메일 미노출) |
| 배포 | Oracle Cloud VM · GitHub Actions · rsync · systemd |

---

## 🚀 설치 및 실행

### 요구사항

- Python 3.11+
- `ANTHROPIC_API_KEY` (필수 — 요약 생성에 사용)

### 1. 환경 설정

```bash
git clone https://github.com/your-username/ai-news-digest.git
cd ai-news-digest

python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 2. 환경 변수

```bash
cp .env.example .env
# .env 파일을 열어 ANTHROPIC_API_KEY 를 채워넣으세요
```

| 변수 | 필수 | 설명 |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | ✅ | Claude API 키 |
| `SECRET_KEY` | ✅ | Flask 세션 서명 키 (`python -c "import secrets; print(secrets.token_hex(32))"`) |
| `ADMIN_TOKEN` | ─ | 미설정 시 개발 모드(전원 admin) |
| `CLUSTER_SIMILARITY_THRESHOLD` | ─ | 클러스터 유사도 임계값 (기본 0.80) |
| `COLLECT_DAYS_BACK` | ─ | 수집 윈도우 (기본 0 = 당일만) |

### 3. DB 초기화

```bash
# 스키마 생성 + data/sources.yaml 에서 RSS 소스 시드
python init_db.py

# 초기화 후 마이그레이션 (기존 DB가 있을 경우)
python migrate_first_shown.py
python migrate_glossary.py
python migrate_saved.py
python migrate_hidden.py
```

### 4. 실행

```bash
python app.py
# → http://localhost:5001
# → http://localhost:5001/healthz  (헬스체크)
# → http://localhost:5001/admin    (관리자 패널)
```

### 5. 파이프라인 수동 실행

앱 실행 중이라면 관리자 패널에서 버튼 클릭, 또는:

```bash
python -c "
from app import create_app
from jobs.pipeline import job_summarize_news
app = create_app(with_scheduler=False)
with app.app_context():
    job_summarize_news(triggered_by='manual')
"
```

---

## 🗂 디렉토리 구조

```
ai-news-digest/
├── app.py                  # Flask 앱 팩토리 + 스케줄러 시작점
├── config.py               # 환경설정 (Config 클래스)
├── models.py               # SQLAlchemy 모델 (Source, Article, Cluster, Paper, ...)
├── scheduler.py            # APScheduler 잡 등록 (KST 기준)
├── init_db.py              # DB 초기화 + RSS 소스 시드
├── migrate_*.py            # 단계별 스키마 마이그레이션
├── data/
│   ├── sources.yaml        # RSS 소스 목록 (UPSERT 기준)
│   └── glossary_seed.json  # 용어집 초기 데이터
├── jobs/
│   ├── pipeline.py         # 파이프라인 오케스트레이터 + JobRun 추적
│   ├── news_collector.py   # RSS 수집 + AI 키워드 필터
│   ├── paper_collector.py  # arXiv · HF Papers 수집
│   ├── embedder.py         # 임베딩 + 클러스터링
│   ├── summarizer.py       # Claude 요약 생성
│   └── cleanup.py          # 오래된 데이터 정리
├── services/
│   ├── local_embed.py      # BGE-M3 (fastembed) 래퍼
│   └── claude.py           # Anthropic SDK 래퍼 (스로틀 포함)
├── web/
│   ├── routes.py           # 모든 Flask 라우트 (단일 blueprint)
│   └── cardnews.py         # 카드뉴스 페이로드 빌더
└── templates/              # Jinja2 HTML 템플릿
```

---

## ⚖️ 합법성 원칙

1. **RSS · 공식 API만** — 무단 크롤링 없음
2. **본문 미노출** — `Article.body`는 클러스터링 입력 전용; UI·이메일에 일절 표시 안 함
3. **명시적 옵트인** — 이메일 수신자는 반드시 동의 후 등록, 1클릭 수신거부 토큰 제공

---

## 📄 라이선스

MIT License — 자세한 내용은 [LICENSE](LICENSE) 참조.
