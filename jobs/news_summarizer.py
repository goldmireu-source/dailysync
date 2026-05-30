"""클러스터 단위 교차검증 요약 (Claude).

흐름:
  1. summary_dirty=True 인 클러스터 가져옴 (멤버 있는 것만)
  2. 멤버 기사들의 정보 묶어서 프롬프트 생성
  3. JSON 응답 파싱 후 Cluster 컬럼에 저장
  4. summary_dirty=False 로 마킹

다중 매체 클러스터는 교차검증 항목 포함, 단일 매체는 단순 요약.
"""
import logging

from app import create_app
from config import Config
from models import db, Cluster, Article
from services.claude import generate_json

logger = logging.getLogger(__name__)

ARTICLE_CONTENT_MAX = 2500


def _build_article_block(article: Article, idx: int) -> str:
    if article.body and article.body_status == "success":
        content = article.body[:ARTICLE_CONTENT_MAX]
        label = "본문"
    elif article.description:
        content = article.description[:ARTICLE_CONTENT_MAX]
        label = "요약(RSS)"
    else:
        content = "(내용 없음)"
        label = "내용"

    pub_str = article.published_at.strftime("%Y-%m-%d %H:%M") if article.published_at else "N/A"
    return (
        f"[기사 {idx}]\n"
        f"매체: {article.source.name} (티어 {article.source.tier}, lang={article.source.lang})\n"
        f"제목: {article.title}\n"
        f"발행: {pub_str}\n"
        f"{label}:\n{content}"
    )


def _build_prompt(articles: list[Article]) -> str:
    n = len(articles)
    blocks = "\n\n---\n\n".join(_build_article_block(a, i + 1) for i, a in enumerate(articles))

    if n >= 2:
        intro = f"아래는 같은 사건을 보도한 {n}개 매체의 기사입니다."
        cross_check = (
            "\n[교차 검증 지침]\n"
            "- agreed_facts: 모든 매체가 공통으로 보도한 핵심 사실 (3~5개)\n"
            "- divergences: 한 매체에서만 보도되었거나 매체 간 관점이 다른 항목 (없으면 빈 배열)\n"
            "- 어느 한 매체에서도 확인되지 않은 사실은 절대 추가하지 마세요\n"
        )
    else:
        intro = "아래는 한 개 매체에서 보도한 기사입니다."
        cross_check = (
            "\n[지침]\n"
            "- agreed_facts: 기사에 명시된 핵심 사실 (3~5개)\n"
            "- divergences: 빈 배열로 두세요 (단일 매체이므로)\n"
        )

    # 글로서리 기반 음역 규칙
    try:
        from services.glossary import get_transliteration_rules
        rules = get_transliteration_rules(max_items=40)
        rules_block = f"\n[표기 규칙 — 다음 용어는 반드시 이대로 표기]\n{rules}\n" if rules else ""
    except Exception:
        rules_block = ""

    return f"""당신은 AI 분야 뉴스를 다루는 전문 편집자입니다. {intro}

{blocks}

다음 규칙을 엄격히 지키세요:
- 사실과 의견을 명확히 구분
- 자연스러운 한국어 신문 기사체로 간결하게 작성
- final_summary_ko 는 4~6문장
- topic 은 사건 핵심을 한 문장 (한국어, 60자 이내)
- categories 는 ["연구/모델", "산업/기업", "정책/규제", "윤리/사회"] 중 1~2개 선택

[importance 산정 기준 — 엄격히 적용]
- 5 (매우 중요): 주요 모델·서비스 출시 (GPT/Claude/Gemini 신모델, Sora 등),
  AI 대형 M&A·투자 (수조원대), 정부의 핵심 AI 정책 발표·법안 통과,
  업계 전반에 파급 효과가 큰 사건 (대규모 데이터 유출, 안전성 사고 등)
- 4 (중요): 주요 신기능 출시, 빅테크 핵심 인사 이동, 의미 있는 벤치마크 결과,
  주요 국가 정책·규제 동향, 산업 구도 변화 신호
- 3 (보통): 일반 제품 업데이트, 연구 결과·논문 발표, 중견 기업 동향,
  사회적 관심을 끄는 사례 보도
- 2 (낮음): 마이너 업데이트, 사소한 인사이동, 일상적 비즈니스 소식
- 1 (매우 낮음): 추측·루머, 확인되지 않은 정보, 단순 의견·칼럼
{cross_check}{rules_block}
JSON 스키마:
{{
  "topic": "사건 핵심 한 문장",
  "agreed_facts": ["사실1", "사실2", "사실3"],
  "divergences": [{{"source": "매체명", "claim": "추가 정보 또는 다른 관점"}}],
  "final_summary_ko": "4~6문장 한국어 요약",
  "categories": ["연구/모델"],
  "importance": 3
}}
"""


def _build_and_call(cluster_id: int, prompt: str):
    """Claude 호출만 — 스레드에서 실행. DB 미접근."""
    try:
        result = generate_json(prompt)
        return cluster_id, result, None
    except Exception as e:
        return cluster_id, None, e


def _apply_summary_to_cluster(cluster: Cluster, result: dict) -> bool:
    """API 결과를 DB 모델에 반영 — 메인 스레드."""
    cluster.topic = (result.get("topic") or cluster.topic or "")[:300]
    cluster.summary_ko = result.get("final_summary_ko") or ""
    cluster.agreed_facts = result.get("agreed_facts") or []
    cluster.divergences = result.get("divergences") or []
    cluster.categories = result.get("categories") or []
    try:
        cluster.importance = int(result.get("importance", 3))
    except (TypeError, ValueError):
        cluster.importance = 3
    cluster.summary_dirty = False
    return True


def summarize_cluster(cluster: Cluster) -> bool:
    """단건 요약 (병렬화 안 함, 단독 호출용)."""
    articles = cluster.articles.all()
    if not articles:
        return False
    prompt = _build_prompt(articles)
    try:
        result = generate_json(prompt)
    except Exception as e:
        logger.exception(f"summarize cluster {cluster.id} failed: {e}")
        return False
    return _apply_summary_to_cluster(cluster, result)


def summarize_pending(limit: int = 100, max_workers: int = 6) -> dict:
    """병렬 요약 — Claude API 호출은 ThreadPool, DB 쓰기는 메인 스레드.

    Claude Haiku 4.5 는 분당 50회+ 처리 가능 → max_workers=6 안전.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    dirty = Cluster.query.filter_by(summary_dirty=True).all()
    dirty = [c for c in dirty if c.articles.count() > 0][:limit]

    stats = {"total": len(dirty), "success": 0, "failed": 0}
    if not dirty:
        return stats

    # 1단계: 프롬프트 빌드 (메인 스레드 — DB 접근 필요)
    tasks = []  # [(cluster_id, prompt)]
    cluster_by_id = {}
    for c in dirty:
        articles = c.articles.all()
        if not articles:
            continue
        prompt = _build_prompt(articles)
        tasks.append((c.id, prompt))
        cluster_by_id[c.id] = c

    # 2단계: API 호출 병렬
    print(f"  → {len(tasks)}개 클러스터 병렬 요약 시작 (워커 {max_workers})")
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_build_and_call, cid, prompt) for cid, prompt in tasks]
        for fut in as_completed(futures):
            cid, result, err = fut.result()
            done += 1
            if err is not None:
                logger.exception(f"summarize cluster {cid} failed: {err}")
                stats["failed"] += 1
                print(f"  [{done}/{len(tasks)}] Cluster {cid} ❌ {err}")
                continue
            # 3단계: DB 적용 (메인 스레드)
            c = cluster_by_id.get(cid)
            if c is None:
                stats["failed"] += 1
                continue
            try:
                _apply_summary_to_cluster(c, result)
                db.session.commit()
                stats["success"] += 1
                print(f"  [{done}/{len(tasks)}] Cluster {cid} ✓")
            except Exception:
                db.session.rollback()
                stats["failed"] += 1
                logger.exception(f"apply summary to cluster {cid} failed")

    return stats


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        print(f"요약 모델: {Config.CLAUDE_SUMMARY_MODEL}")
        print(f"클러스터 요약 시작\n")

        stats = summarize_pending(limit=100)
        print(f"\n요약 완료: total={stats['total']} "
              f"success={stats['success']} failed={stats['failed']}")
