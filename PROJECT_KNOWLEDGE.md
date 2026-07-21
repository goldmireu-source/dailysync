# ai-news-digest (dailysync) — 프로젝트 지식 문서

> Claude.ai 프로젝트의 "프로젝트 지식(Project Knowledge)"에 그대로 업로드/붙여넣기용으로 작성됨.
> 이 문서 하나로 매 대화마다 프로젝트를 처음부터 설명하지 않아도 되도록, 실제 코드를 직접 확인해 정리함
> (README.md/CLAUDE.md에 없는 최신 기능 포함, 오래된 서술은 바로잡음). 작성 시점: 2026-07-21.

---

## 이게 뭔가

원래는 "개인용 AI 뉴스 큐레이션 서비스"로 시작했지만, 현재는 그보다 훨씬 큰 앱이 됐다:

1. **AI 뉴스 다이제스트** — RSS 수집 → 임베딩·클러스터링 → LLM 교차검증 요약 → 한국어 대시보드
2. **AI 논문 트래킹** — arXiv/Hugging Face Papers, 뉴스와 완전 별도 트랙
3. **AI 공모전 수집** — 위비티·씽굿·요즘것들·데이콘·K-Startup·콘테스트코리아·캠퍼스픽·라우드 등에서 수집, 마감 임박순 노출
4. **공모전 팀 빌딩 ("파티")** — 로그인 사용자끼리 공모전 팀을 모집·채팅
5. **"인사교당근"** — 특정 학교/반 커뮤니티용 나눔·물물교환·단기대여 게시판 (당근마켓 스타일). `AdminUser.class_num`(1~7반)로 반별 타겟팅

즉 실질적으로는 "개인 AI 뉴스 서비스 + 학교/반 단위 커뮤니티 플랫폼"이 합쳐진 형태다. 단일 사용자/소규모 베타, UI·주석·LLM 프롬프트는 전부 한국어.

**주의**: 저장소의 `README.md`, `CLAUDE.md`는 뉴스/논문 트랙 위주로 작성돼 있고 공모전/파티/당근 기능, 실제 임베딩 모델명, 최신 라우트 구조 등이 반영 안 돼 있어 최신 상태와 어긋나는 부분이 있음 (아래 "문서와 실제 코드가 다른 부분" 참고).

---

## 배포 환경

- **호스팅**: Oracle Cloud VM (Free Tier, ARM A1) — **집 PC 아님**
  - IP: `168.107.21.112`, 포트 `5001`
  - 도메인: `ainews.kro.kr` (무료 동적 DNS)
- **프로세스 관리**: systemd 서비스로 상시 구동 (`daily-sync` 또는 `dailysync` 유닛 — 서버마다 확인 필요)
- **배포 파이프라인**: GitHub Actions → `rsync`로 변경 파일만 전송 → SSH로 `systemctl restart`
  - **중요**: `migrate_*.py`를 새로 추가하면 `.github/workflows/deploy.yml`의 마이그레이션 실행 블록에도 반드시 같이 등록해야 함. 안 하면 자동배포 시 실행이 안 돼서 수동 SSH 접속이 필요해짐 (실제로 겪은 문제).
- 로그 위치: `~/ai-news-digest/ai-news-digest/logs/app.log` (자정 rotation, 7일 보관)

---

## 기술 스택

| 영역 | 기술 |
|---|---|
| 웹 프레임워크 | Flask 3 + APScheduler (`BackgroundScheduler`), 단일 프로세스 — Celery/워커 큐 없음 |
| ORM/DB | SQLAlchemy 2 + SQLite (`data/app.db`) |
| 로그인 | Flask-Login (`AdminUser` 테이블, bcrypt 해시) |
| 임베딩 | **fastembed (ONNX Runtime)**, 기본 모델 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384차원) — `services/local_embed.py`. *(README는 "BGE-M3"라고 적혀 있으나 실제 `config.py` 기본값과 다름 — 문서가 오래됨)* |
| LLM 요약 | Anthropic Claude (`services/claude.py`), 기본 모델 `claude-haiku-4-5`. `google-genai`/Voyage 연동 코드는 있지만 **현재 미사용**(예비 프로바이더) |
| RSS 파싱 | feedparser, requests (일부 소스는 Google News Sitemap XML) |
| 본문 추출 | trafilatura (클러스터링 입력 전용, UI/이메일 미노출) |
| 크롤링(공모전) | requests + BeautifulSoup/lxml, 일부 소스는 Playwright(JS 렌더링 필요한 곳) |
| 논문 썸네일 | PyMuPDF (arXiv PDF 첫 페이지 렌더링) |
| 배포 | Oracle Cloud VM, GitHub Actions, rsync, systemd |

---

## 프로세스 구조 & 스케줄 (KST 기준, `scheduler.py`)

한 프로세스(Flask + APScheduler)에서 다음 잡들이 크론으로 돈다:

| 시각 | 잡 | 내용 |
|---|---|---|
| 08~22시 매시 정각 | `collect_news_hourly` | RSS 뉴스 수집 |
| 08~22시 2시간마다 :05 | `fetch_bodies` | trafilatura 본문 페치 (분석용) |
| 00/06/12/18시 정각 | `refresh_6h` (`job_refresh_now`) | 전체 파이프라인: 수집→논문→(변경 없으면 스킵)→본문→임베딩/클러스터링→뉴스요약→논문요약 |
| 00/06/12/18시 :30 | `thumb_papers_6h` | 논문 PDF 썸네일 생성 (`jobs/pdf_thumbnailer.py`, PyMuPDF) |
| 00/06/12/18시 :45 | `screenshot_articles_6h` | 기사 스크린샷 (`jobs/article_screenshotter.py`, 일부 봇 차단 소스는 og:image로 대체) |
| 07:30, 19:30 | `collect_contests_daily` | 공모전 수집 (하루 2회로 충분) |
| 04:00 | `cleanup_old_data_daily` | 4일 지난 Article/Cluster/Paper 삭제 (saved 항목은 보존) |
| 매시 :30 | `karrot_cleanup_hourly` | 완료된 당근 게시글 24시간 후 자동 삭제 |

수동 트리거는 `scheduler.trigger_job_now(job_id, app, run_id)` 하나로 통일 — job_id 키: `collect_news`, `fetch_bodies`, `collect_papers`, `embed_and_cluster`, `summarize_news`, `summarize_papers`, `morning_pipeline`, `refresh_now`, `backfill_papers`, `cleanup_old_data`, `collect_contests`, `thumb_papers`, `screenshot_articles`. 모든 잡은 `jobs/pipeline.py`의 `_track()` 컨텍스트매니저로 감싸져 `JobRun` row(상태 큐→진행→성공/실패, `stats` JSON)를 남김.

---

## 데이터 모델 핵심 (`models.py`)

- **뉴스 트랙**: `Source` → `Article`(≤1 Cluster) → `Cluster`(증분 클러스터링, centroid는 running mean)
- **논문 트랙**: `Paper` — 뉴스와 완전 별도, arXiv/HF 큐레이션 시그널(`hf_featured`, `hf_upvotes`, `citation_count`) 보유
- **공모전 트랙**: `Contest` — `source`(wevity/thinkcontest/allforyoung/dacon/kstartup/contestkorea/campuspick/loud 등), `deadline`이 핵심 필터 키(마감 지난 건 숨김), `image_pos_x/y`+`image_scale`로 관리자가 썸네일 크롭 조정 가능
- **파티(팀빌딩)**: `Party` → `PartyMember`, `PartyMessage` — 공모전(`contest_id`) 단위로 리더가 개설, 정원(`max_members`, 기본 6)
- **당근(나눔)**: `KarrotPost`(`share`/`trade`/`loan`) → `KarrotApplication` — `class_target`으로 반별 공개 범위 제한 가능. `AppSetting("karrot_enabled")`로 탭 전체를 켜고 끌 수 있음(`/admin/toggle-karrot`), `jobs/cleanup.py:cleanup_completed_karrot`가 매시 :30에 완료 게시글 24시간 후 삭제
- **사용자**: `AdminUser`(로그인 계정, `role` admin/user, `class_num` 1~7반) — 뉴스레터 구독자용 `User` 테이블과는 완전 별개. `UserActivity`가 로그인/가입 등 이력 기록
- **공통 패턴**: `hidden_at`/`saved_at`은 nullable timestamp(NULL=기본, 값=상태), `summary_dirty`는 "재요약 필요" 플래그 — Cluster/Paper/Contest 세 트랙 모두 동일 패턴 반복
- **기타**: `UserBookmark`(item_type: contest/cluster/paper 통합 북마크), `GlossaryTerm`(`/glossary`), `AppSetting`(전역 키-값), `Digest`(이메일 발송용, 아직 미가동), `JobRun`(잡 실행 이력)

---

## 웹 구조 (`web/routes.py`, 단일 블루프린트, 2400줄+)

- **다이제스트 뷰**: `/`, `/cluster/<id>`, `/paper/<id>`, `/saved`, `/glossary`, `/search` — 메인 페이지는 `tab` 쿼리 파라미터로 뉴스/논문/공모전 탭 전환, **기본 탭이 "공모전"**(뉴스 아님)
- **공모전**: `/contest/<id>`, `/contest/new`(수동 등록), `/api/contest/...`(hide/show/save/unsave/image/image-adjust/delete)
- **파티**: `/parties`, `/parties/<id>`, `/api/parties/...`(생성/참가/탈퇴/마감/삭제/채팅)
- **당근**: `/karrot/<id>`, `/api/karrot/...`(작성/수정/삭제/신청/매칭)
- **인증**: `/admin-login`, `/admin-register`, `/admin-logout`
- **관리자**: `/admin`(대시보드), `/admin/run/<job_id>`(수동 잡 트리거), `/admin/toggle-karrot`(당근탭 공개 토글), `/api/job/...`(폴링)

**관리자 인증**: `Config.ADMIN_TOKEN` 미설정 시 개발 모드(전원 admin). 설정 시 `admin_required` 데코레이터가 체크 — `/api/*`, `/admin/run/*`은 403 JSON, 나머지는 `/`로 리다이렉트.

---

## 프로젝트 원칙 (README 명시, 협상 불가)

1. **본문 미노출** — `Article.body`는 trafilatura로 클러스터링/분석 입력 전용. 이메일·UI에는 헤드라인+LLM 요약+출처 링크만 노출 (법적 제약, 스타일 문제 아님)
2. **RSS·공식 API만** — trafilatura로 원 소스 본문 추출하는 것 외의 무단 크롤링 금지
3. **명시적 opt-in** — 이메일 수신자는 반드시 동의 등록, 1클릭 수신거부 토큰(`User.unsubscribe_token`) 필수

---

## 코딩 컨벤션

- 모든 주석·프롬프트·UI 텍스트는 **한국어** 유지
- 시간 로직은 **KST**(`timezone(timedelta(hours=9))`, `scheduler.py`/`web/routes.py`에 각각 정의됨), DB 타임스탬프는 naive UTC(`datetime.utcnow()`)
- `db.create_all()`은 앱 시작 시 실행되지만 **기존 row는 마이그레이션 안 됨** — 컬럼 추가 시 `migrate_*.py` 스크립트 작성 필요 (기존 파일들이 패턴 참고용: `PRAGMA table_info` 체크 후 없으면 `ALTER TABLE ADD COLUMN`)
- 새 `migrate_*.py` 만들 때마다 `.github/workflows/deploy.yml`에도 등록 필수 (위 배포 섹션 참고). 현재 `migrate_*.py`가 15개 이상 있음(contest_image, oss_contest, cover_image_position, karrot, party, primary_article, reembed, saved, settings, bookmarks, paper_pin, paper_figure, cluster_pin, feed_type, user_activity, glossary 등) — CLAUDE.md는 이 중 5개만 나열
- `data/sources.yaml`을 고치고 `init_db.py` 재실행하면 UPSERT로 반영됨 (RSS 소스 추가/수정 워크플로우)
- 테스트 스위트/린터/포매터 없음 — 새로 만들지 말 것

---

## 환경 변수 (`.env`)

필수: `ANTHROPIC_API_KEY`, `SECRET_KEY`
선택: `ADMIN_TOKEN`(비우면 개발모드), `CLUSTER_SIMILARITY_THRESHOLD`(기본 0.80), `CLUSTER_MERGE_THRESHOLD`(기본 0.90), `COLLECT_DAYS_BACK`(기본 0=당일만), `DATA_GO_KR_KEY`(K-Startup API), `LOUD_EMAIL`/`LOUD_PASSWORD`(라우드 로그인, 미설정시 비로그인 폴백), `CONTEST_RETENTION_DAYS`(기본 2), `CARDNEWS_BOT_URL`/`CARDNEWS_API_KEY`(카드뉴스 스튜디오 연동, 별도 봇 서버)
미사용(예비): `GEMINI_API_KEY`, `VOYAGE_API_KEY`, `GMAIL_*`

---

## 문서와 실제 코드가 다른 부분 (참고용)

작업 중 실제 코드를 확인하며 발견한, README.md/CLAUDE.md가 오래돼서 안 맞는 부분:

- **임베딩 모델**: 문서는 "BGE-M3"라고 하지만 `config.py` 실제 기본값은 `paraphrase-multilingual-MiniLM-L12-v2` (384차원)
- **공모전/파티/당근 기능 전체**: `CLAUDE.md`에 전혀 언급 없음 (README에는 한 줄 요약만 있음)
- **`web/routes.py` 크기**: CLAUDE.md는 "~566줄"이라 하지만 실제로는 2400줄+
- **스케줄러 잡 개수**: CLAUDE.md는 4개 크론이라 하지만 실제로는 8개 (썸네일·스크린샷·공모전·당근정리 추가됨)
- 위 항목들은 실제 코드 기준으로 이 문서에 반영해뒀음. 코드가 계속 바뀌는 저장소이므로, 이 문서도 오래되면 다시 어긋날 수 있음 — 의심되면 `models.py`/`scheduler.py`/`config.py`를 직접 확인할 것.
