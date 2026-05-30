"""논문 구조화 요약 (Claude).

흐름:
  1. pick_papers_to_summarize() 로 우선순위 N편 (DAILY_PAPER_PICK) 선정
  2. abstract 입력으로 Claude 에 구조화 요약 요청
  3. problem/method/results/significance/limitations + summary_ko 6필드 저장

선정 우선순위:
  1순위. summary_dirty + hf_featured + PAPER_RECENT_DAYS 내 (upvotes DESC)
  2순위. summary_dirty + PAPER_RECENT_DAYS 내 + cs.AI/CL/LG 카테고리
  3순위. summary_dirty + PAPER_RECENT_DAYS 내 + 나머지
"""
import logging
from datetime import datetime, timedelta

from app import create_app
from config import Config
from models import db, Paper
from services.claude import generate_json

logger = logging.getLogger(__name__)

PAPER_CONTENT_MAX = 4000
PRIORITY_CATS = {"cs.AI", "cs.CL", "cs.LG"}


def _build_prompt(paper: Paper) -> str:
    authors = ", ".join((paper.authors or [])[:5])
    if len(paper.authors or []) > 5:
        authors += f" 외 {len(paper.authors) - 5}명"
    cats = ", ".join(paper.categories or [])
    pub_str = paper.published_at.strftime("%Y-%m-%d") if paper.published_at else "N/A"

    # 글로서리 기반 음역 규칙
    try:
        from services.glossary import get_transliteration_rules
        rules = get_transliteration_rules(max_items=40)
        rules_block = f"\n다음 용어는 반드시 표기 규칙을 따르세요 (일관성 유지):\n{rules}\n" if rules else ""
    except Exception:
        rules_block = ""

    return f"""당신은 AI 분야 논문을 일반 독자에게 소개하는 전문 편집자입니다.

다음 논문을 한국어로 구조화 요약해주세요.

제목: {paper.title}
저자: {authors}
카테고리: {cats}
발행: {pub_str}

초록 (Abstract):
{(paper.abstract or '')[:PAPER_CONTENT_MAX]}

각 필드는 자연스러운 한국어로 작성하세요.
{rules_block}
JSON 스키마:
{{
  "title_ko": "영문 제목의 자연스러운 한국어 번역 (학술 톤). 위 표기 규칙을 따르세요. 한 줄.",
  "problem": "이 논문이 풀려는 문제 (1~2문장)",
  "method": "어떻게 풀었나, 핵심 방법론 (1~3문장)",
  "results": "구체적 결과·성능 (1~2문장)",
  "significance": "왜 중요한가, 어떤 함의 (1~2문장)",
  "limitations": "한계 또는 향후 과제 (없으면 빈 문자열)",
  "summary_ko": "위 내용을 자연스럽게 잇는 3~4문장 통합 요약"
}}
"""


def summarize_paper(paper: Paper) -> bool:
    if not paper.abstract:
        logger.warning(f"paper {paper.arxiv_id} has no abstract, skip")
        return False

    prompt = _build_prompt(paper)
    try:
        result = generate_json(prompt)
    except Exception as e:
        logger.exception(f"summarize paper {paper.arxiv_id} failed: {e}")
        return False

    paper.title_ko = (result.get("title_ko") or "").strip().strip('"').strip("'")
    paper.problem_ko = result.get("problem") or ""
    paper.method_ko = result.get("method") or ""
    paper.results_ko = result.get("results") or ""
    paper.significance_ko = result.get("significance") or ""
    paper.limitations_ko = result.get("limitations") or ""
    paper.summary_ko = result.get("summary_ko") or ""
    paper.summary_dirty = False
    return True


def pick_papers_to_summarize(n: int | None = None) -> list[Paper]:
    if n is None:
        n = Config.DAILY_PAPER_PICK

    cutoff = datetime.utcnow() - timedelta(days=Config.PAPER_RECENT_DAYS)

    tier1 = (
        Paper.query
        .filter_by(summary_dirty=True, hf_featured=True)
        .filter(Paper.published_at >= cutoff)
        .order_by(Paper.hf_upvotes.desc(), Paper.published_at.desc())
        .limit(n)
        .all()
    )
    if len(tier1) >= n:
        return tier1[:n]

    chosen_ids = {p.id for p in tier1}

    candidates = (
        Paper.query
        .filter_by(summary_dirty=True)
        .filter(Paper.published_at >= cutoff)
        .filter(~Paper.id.in_(chosen_ids) if chosen_ids else True)
        .order_by(Paper.published_at.desc())
        .all()
    )

    needed = n - len(tier1)
    tier2 = [p for p in candidates if any(c in PRIORITY_CATS for c in (p.categories or []))][:needed]

    if len(tier1) + len(tier2) >= n:
        return tier1 + tier2

    chosen_ids.update(p.id for p in tier2)
    needed2 = n - len(tier1) - len(tier2)
    tier3 = [p for p in candidates if p.id not in chosen_ids][:needed2]

    return tier1 + tier2 + tier3


def _apply_result_to_paper(paper: Paper, result: dict) -> None:
    paper.title_ko = (result.get("title_ko") or "").strip().strip('"').strip("'")
    paper.problem_ko = result.get("problem") or ""
    paper.method_ko = result.get("method") or ""
    paper.results_ko = result.get("results") or ""
    paper.significance_ko = result.get("significance") or ""
    paper.limitations_ko = result.get("limitations") or ""
    paper.summary_ko = result.get("summary_ko") or ""
    paper.summary_dirty = False


def backfill_dirty_papers(
    limit: int | None = None,
    max_workers: int = 6,
    run_id_for_progress: int | None = None,
) -> dict:
    """summary_dirty=True 인 모든 논문을 병렬 요약.

    pick_papers_to_summarize() 와 달리 cutoff/우선순위 무시 — backlog 비우기 전용.
    1.2s 글로벌 throttle (services/claude.py) 때문에 워커 수와 무관하게 ~50/min.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    q = Paper.query.filter_by(summary_dirty=True).filter(Paper.abstract.isnot(None))
    if limit:
        q = q.limit(limit)
    dirty = [p for p in q.all() if (p.abstract or "").strip()]

    stats = {"total": len(dirty), "success": 0, "failed": 0}
    if not dirty:
        return stats

    # 1단계: 프롬프트 빌드 (메인 스레드)
    tasks = []
    paper_by_id = {}
    for p in dirty:
        tasks.append((p.id, _build_prompt(p)))
        paper_by_id[p.id] = p

    print(f"  → {len(tasks)}편 백필 시작 (워커 {max_workers}, 예상 {len(tasks) * 1.2 / 60:.1f}분)")

    def _call(pid, prompt):
        try:
            return pid, generate_json(prompt), None
        except Exception as e:
            return pid, None, e

    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_call, pid, pr) for pid, pr in tasks]
        for fut in as_completed(futures):
            pid, result, err = fut.result()
            done += 1
            if err is not None:
                logger.exception(f"backfill paper {pid} failed: {err}")
                stats["failed"] += 1
            else:
                p = paper_by_id.get(pid)
                try:
                    _apply_result_to_paper(p, result)
                    db.session.commit()
                    stats["success"] += 1
                except Exception:
                    db.session.rollback()
                    stats["failed"] += 1
                    logger.exception(f"apply backfill result to paper {pid} failed")

            if done % 25 == 0 or done == len(tasks):
                msg = f"{done}/{len(tasks)} (성공 {stats['success']}, 실패 {stats['failed']})"
                print(f"  진행: {msg}")
                if run_id_for_progress:
                    from jobs.pipeline import _update_phase
                    _update_phase(run_id_for_progress, f"논문 백필 {msg}")

    return stats


def summarize_today_picks() -> dict:
    picks = pick_papers_to_summarize()
    stats = {"picked": len(picks), "success": 0, "failed": 0}

    for i, p in enumerate(picks, 1):
        tag = "⭐" if p.hf_featured else "  "
        cats = ",".join(p.categories or [])[:30]
        print(f"  [{i}/{len(picks)}] {tag} {p.arxiv_id} [{cats}] {p.title[:55]}")
        ok = summarize_paper(p)
        if ok:
            stats["success"] += 1
        else:
            stats["failed"] += 1
        db.session.commit()

    return stats


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        print(f"요약 모델: {Config.CLAUDE_SUMMARY_MODEL}")
        print(f"일일 픽: {Config.DAILY_PAPER_PICK}편 / 최근 {Config.PAPER_RECENT_DAYS}일\n")

        stats = summarize_today_picks()
        print(f"\n요약 완료: picked={stats['picked']} "
              f"success={stats['success']} failed={stats['failed']}")
