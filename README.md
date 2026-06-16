# AI News Digest (dailysync)

> 국내외 AI 뉴스를 매일 수집·클러스터링하고, LLM이 교차검증 요약한 한국어 다이제스트를 제공하는 개인용 큐레이션 서비스

---

## 개발 배경 & 페인포인트

### 왜 만들었는가

AI 분야는 매일 수십 건의 논문·뉴스·제품 발표가 쏟아집니다. 개인 개발자나 연구자가 이를 직접 소화하려면 다음과 같은 문제에 부딪힙니다.

- **정보 과부하**: RSS 리더에 구독 피드를 추가해도 하루에 100건 이상의 기사가 쌓이고, 중복 기사(같은 사건을 다른 매체가 보도)가 60–70%를 차지합니다. 정작 핵심 정보를 찾으려면 직접 읽어야 하는 아이러니가 생깁니다.
- **영어 장벽**: 주요 AI 뉴스·논문은 대부분 영어입니다. 실시간으로 한국어 요약을 제공하는 개인화된 서비스가 없어, 매일 번역·정리에 30분 이상이 소모됩니다.
- **출처 신뢰성 불균일**: SNS·큐레이션 뉴스레터는 편집자의 편향이 들어가거나, 광고성 기사와 진짜 뉴스가 섞여 있습니다. 공식 소스(OpenAI·Anthropic·DeepMind 블로그 등)와 일반 매체를 동일하게 다루면 중요도 판단이 어렵습니다.
- **논문 트래킹 분리**: arXiv·Hugging Face 논문 트래킹과 뉴스 읽기를 별도 도구로 해야 하는 번거로움이 있습니다.
- **공모전 정보 파편화**: AI·데이터 공모전 정보가 각 기관 사이트에 분산돼 있어, 기회를 놓치는 경우가 많습니다.

### 해결 방향

**수집 → 클러스터링 → LLM 요약**의 3단계 파이프라인으로, 매일 아침 "읽을 만한 것만 골라진" 한국어 다이제스트를 자동 생성합니다. Tier 구조로 공식 소스와 일반 매체를 구분하고, 같은 사건의 기사는 하나의 클러스터로 묶어 중복 없이 보여줍니다.

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

| 영역 | 기술 | 선택 이유 |
|------|------|-----------|
| 웹 프레임워크 | Flask 3 + APScheduler | 단일 프로세스에서 웹 서빙과 스케줄 잡을 함께 운영. Celery+Redis 없이 인프라 비용 0으로 충분한 처리량 확보 |
| ORM / DB | SQLAlchemy 2 · SQLite | 단일 VM 운영 환경에서 PostgreSQL 운용 비용 없이 충분. 마이그레이션은 별도 migrate_*.py 스크립트로 관리 |
| 임베딩 모델 | BGE-M3 via fastembed (ONNX Runtime) | 로컬 실행으로 API 비용 0, 한·영 다국어 임베딩 품질 우수. ONNX 런타임으로 GPU 없이 CPU에서도 빠른 추론 |
| LLM | Claude Haiku 4.5 (Anthropic SDK) | 요약 품질과 비용의 균형. 클러스터당 3–5줄 요약이라는 단순 반복 태스크에서 Sonnet 대비 약 8배 저렴하고 품질 차이 미미 |
| RSS 파싱 | feedparser · requests | feedparser는 RSS/Atom 표준을 가장 폭넓게 지원하는 Python 라이브러리. requests로 커스텀 헤더 처리 |
| 본문 추출 | trafilatura | 클러스터링 입력 전용. boilerplate 제거 정확도가 BeautifulSoup 직접 파싱 대비 높고, 저작권 이슈 방지를 위해 UI에는 미노출 |
| 배포 | Oracle Cloud VM · GitHub Actions · rsync · systemd | Oracle Cloud Free Tier(ARM A1)로 비용 0. GitHub Actions에서 rsync로 코드만 전송하고 systemd로 프로세스 관리 |

### 기술 선택 상세 과정

#### BGE-M3 임베딩 모델 선택

임베딩 모델 후보는 세 가지였습니다.

1. **OpenAI text-embedding-3-small** — 품질 우수하지만 API 호출마다 비용 발생. 하루 수백 건 기사를 처리하면 월 수 달러가 누적됩니다.
2. **sentence-transformers/all-MiniLM-L6-v2** — 영어 전용, 한국어 기사 임베딩 품질이 낮아 클러스터링 정확도가 떨어졌습니다.
3. **BGE-M3 (via fastembed)** — 한·영 다국어 지원, ONNX 런타임으로 CPU 추론 가능, fastembed 패키지로 모델 다운로드·관리가 단순합니다. 테스트 결과 한국어 기사 클러스터링 정확도가 가장 높았습니다.

Oracle Cloud Free Tier의 ARM CPU 환경에서 GPU 없이 안정적으로 실행할 수 있다는 점이 BGE-M3를 최종 선택한 결정적 이유입니다.

#### 증분 클러스터링 설계

매시간 새 기사가 들어올 때마다 전체 재클러스터링을 하면 연산 비용이 선형 증가합니다. 대신 **증분 방식**을 채택했습니다.

- 신규 기사 임베딩 → 기존 클러스터 중심(centroid)과 코사인 유사도 비교
- 임계값(기본 0.80) 이상이면 기존 클러스터에 편입, 미만이면 새 클러스터 생성
- 클러스터에 기사가 추가될 때만 LLM 요약 재생성

이 방식으로 연산량을 O(n²) → O(n·k)(k: 클러스터 수)로 줄였습니다.

#### Oracle Cloud Free Tier + GitHub Actions 배포

VPS 비용 절감을 위해 Oracle Cloud Free Tier(ARM A1, 4 vCPU, 24GB RAM)를 선택했습니다. 배포 파이프라인은 단순합니다.

- GitHub Actions에서 `rsync`로 변경된 파일만 전송
- SSH로 `systemctl restart dailysync` 실행
- 별도 Docker·Kubernetes 불필요 — systemd 단일 유닛으로 프로세스 관리

단순한 구조 덕분에 배포 시간이 30초 이내이고, 장애 발생 시 `journalctl`로 즉시 로그 확인이 가능합니다.

#### Tier 구조로 소스 신뢰도 관리

모든 RSS 피드를 동일하게 처리하면 저품질 기사가 클러스터링을 오염시킵니다.

- **Tier-2 (공식 소스)**: OpenAI, Anthropic, DeepMind, Google DeepMind 공식 블로그 등 — AI 키워드 필터 없이 전량 수집
- **Tier-1 (일반 매체)**: TechCrunch, VentureBeat 등 — 제목/설명에 AI 관련 키워드가 포함된 기사만 수집

이 구조로 노이즈를 줄이면서도 중요한 공식 발표는 빠짐없이 수집합니다.

---

## AI 모델 선택 과정

### 요약 태스크에 Haiku를 선택한 이유

뉴스 클러스터 요약은 구조가 단순하고 반복적입니다. "3–5개 기사를 읽고 한국어로 3–5줄로 요약하라"는 지시를 따르는 태스크이므로, 모델의 추론 깊이보다 **비용과 처리 속도**가 더 중요합니다.

| 모델 | 요약 품질(10건 평가) | 한국어 자연스러움 | 비용(클러스터당) | 처리 속도 |
|------|---------------------|-----------------|-----------------|-----------|
| Claude Sonnet 4 | 매우 높음 | 높음 | ~$0.008 | 보통 |
| Claude Haiku 4.5 | 높음 | 중상 | ~$0.001 | 빠름 |
| GPT-4o mini | 높음 | 중간 | ~$0.001 | 빠름 |
| Gemini 1.5 Flash | 보통 | 낮음 | ~$0.0005 | 매우 빠름 |

> 평가 기준: 동일한 클러스터(3–5개 기사)를 각 모델로 10회 요약 후, 핵심 정보 포함 여부·한국어 문체·형식 준수율을 종합 평가.

Haiku 4.5는 Sonnet 대비 약 8배 저렴하면서 뉴스 요약 품질 차이가 거의 없었습니다. 하루 수십~수백 개 클러스터를 처리하는 환경에서 비용 효율이 결정적이었습니다. GPT-4o mini와 비교했을 때는 한국어 문체 자연스러움이 더 좋았고, Anthropic SDK의 타입 안정성·스로틀 처리가 구현 편의성 면에서 앞섰습니다.

### LLM 교차검증 방식

단일 기사 요약이 아닌 **클러스터 전체를 입력**으로 주는 방식을 선택했습니다.

- 여러 출처의 기사를 함께 보여줌으로써 특정 매체의 편향을 희석
- 공통적으로 언급된 사실만 요약에 포함되도록 프롬프트 설계
- 각 기사의 출처를 함께 제공해 모델이 신뢰도를 자체 판단하도록 유도

이 방식을 "교차검증 요약"이라 부르며, 단순 번역·요약 대비 팩트 오류가 줄어드는 것을 체감했습니다.

---

## 🗂 디렉토리 구조

```
ai-news-digest/
├── app.py                 # Flask 앱 팩토리 + 스케줄러 시작점
├── config.py              # 환경설정 (Config 클래스)
├── models.py              # SQLAlchemy 모델 (Source, Article, Cluster, Paper, ...)
├── scheduler.py           # APScheduler 잡 등록 (KST 기준)
├── init_db.py             # DB 초기화 + RSS 소스 시드
├── migrate_*.py           # 단계별 스키마 마이그레이션
├── data/
│   ├── sources.yaml         # RSS 소스 목록 (UPSERT 기준)
│   └── glossary_seed.json   # 용어집 초기 데이터
├── jobs/
│   ├── pipeline.py          # 파이프라인 오케스트레이터 + JobRun 추적
│   ├── news_collector.py    # RSS 수집 + AI 키워드 필터
│   ├── paper_collector.py   # arXiv · HF Papers 수집
│   ├── embedder.py          # 임베딩 + 클러스터링
│   ├── summarizer.py        # Claude 요약 생성
│   └── cleanup.py           # 오래된 데이터 정리
├── services/
│   ├── local_embed.py       # BGE-M3 (fastembed) 래퍼
│   └── claude.py            # Anthropic SDK 래퍼 (스로틀 포함)
├── web/
│   ├── routes.py            # 모든 Flask 라우트 (단일 blueprint)
│   └── cardnews.py          # 카드뉴스 페이로드 빌더
└── templates/               # Jinja2 HTML 템플릿
```

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
|------|------|------|
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

```python
python -c "
from app import create_app
from jobs.pipeline import job_summarize_news
app = create_app(with_scheduler=False)
with app.app_context():
    job_summarize_news(triggered_by='manual')
"
```

---

## ⚖️ 합법성 원칙

- **RSS · 공식 API만** — 무단 크롤링 없음
- **본문 미노출** — `Article.body`는 클러스터링 입력 전용; UI·이메일에 일절 표시 안 함
- **명시적 옵트인** — 이메일 수신자는 반드시 동의 후 등록, 1클릭 수신거부 토큰 제공

---

## 📄 라이선스

MIT License — 자세한 내용은 LICENSE 참조.
