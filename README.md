# AI News Digest

신뢰성 있는 국내외 AI 뉴스를 RSS로 수집해, 같은 사건을 다룬 기사들을 교차검증·합본 요약하여 이메일로 매일 한 번 전달하는 개인용 큐레이션 서비스.

## MVP 단계 (현재)

- 합법적 출처(RSS · 공식 API)만 사용
- 본문 미저장 — 헤드라인 + 자체 요약 + 출처 링크만
- 베타 5~10명 대상

## 빠른 시작

```bash
# 1. 가상환경
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 2. 의존성
pip install -r requirements.txt

# 3. 환경변수
cp .env.example .env
# .env 를 편집해 최소한 GEMINI_API_KEY, GMAIL_USER, GMAIL_APP_PASSWORD 채우기

# 4. DB 초기화 + 출처 시드
python init_db.py

# 5. Flask 헬스체크
python app.py
# → http://localhost:5000/healthz
```

## 디렉토리

```
ai-news-digest/
├── app.py              # Flask 진입점
├── config.py           # 환경설정
├── models.py           # SQLAlchemy 모델
├── init_db.py          # DB 초기화 + 시드
├── data/
│   ├── sources.yaml    # RSS 출처 시드
│   └── app.db          # SQLite (자동 생성)
├── jobs/               # 백그라운드 잡 (Day 2~)
├── services/           # 외부 API 래퍼 (Day 3~)
└── templates/          # 이메일 템플릿 (Day 5~)
```

## 다음 단계 (1단계 MVP 로드맵)

- **Day 2** — `jobs/collector.py` : RSS 수집 + AI 필터링
- **Day 3** — `jobs/embedder.py` : Gemini 임베딩 + 클러스터링
- **Day 4** — `jobs/summarizer.py` : 교차검증 요약 프롬프트
- **Day 5** — `services/gmail.py` + 이메일 템플릿
- **Day 6** — `scheduler.py` : APScheduler 통합
- **Day 7** — 가입 · 수신거부 라우트 + 베타 초대

## 합법성 원칙 (절대 어기지 않는 규칙)

1. RSS · 공식 API 이외의 어떤 본문도 fetch · 저장하지 않는다.
2. 발송 이메일에 본문 인용 0건 — 헤드라인 + 자체 요약 + 원문 링크만 포함한다.
3. 모든 회원은 명시적 opt-in 이후에만 등록되며, 1클릭 수신거부 토큰을 가진다.
4. 발신자 정보 · 수신동의 일시 · 수신거부 링크를 모든 메일 하단에 명시한다.
