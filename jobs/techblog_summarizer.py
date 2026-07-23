"""테크블로그 글 핵심 포인트 요약 (Claude).

techblog_body_fetcher.py 가 가져온 본문(body)이 있으면 그걸 1차 입력으로
쓰고, 없거나 실패했으면 RSS 티저(description)로 폴백한다. body 자체는
요약 입력에만 쓰고 절대 그대로 노출하지 않는다 (README 원칙 1).

흐름:
  1. summary_dirty=True 인 TechPost 를 hot_score 내림차순으로 최대 N개 선정
  2. title + (body 또는 description) 을 Claude 에 전달해 핵심 포인트(내용만큼, 최대 8개) +
     문단형 상세 요약(최대 4문단, 자세히 보기 카드용) 요청
  3. key_points 저장, detail_paragraphs 는 "\n\n" 로 이어붙여 summary_ko 에 저장
     (cardnews.py 가 렌더링 시 다시 분리해 카드 매수를 정함), summary_dirty=False
"""
import logging

from bs4 import BeautifulSoup

from app import create_app
from config import Config
from models import db, TechPost
from services.claude import generate_json

logger = logging.getLogger(__name__)

DESCRIPTION_MAX = 1500
BODY_CONTENT_MAX = 4000
DEFAULT_LIMIT = 40


def _strip_html(html: str | None) -> str:
    if not html:
        return ""
    try:
        return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    except Exception:
        return html


def _build_prompt(post: TechPost) -> str:
    if post.body and post.body_status == "success":
        content = post.body[:BODY_CONTENT_MAX]
        source_label = "본문 발췌"
        depth_note = (
            "본문 발췌가 충분히 길다면, 다루는 배경·문제·구체적인 방법(기술 스택·수치·"
            "용어 등)·결과를 빠짐없이 반영하세요. 서로 다른 내용은 한 포인트에 욱여넣지 "
            "말고 별도 포인트로 나누세요(최대 8개) — 다만 포인트 하나의 길이를 무조건 "
            "한 문장으로 자르라는 뜻은 아닙니다. 그 내용 하나를 제대로 전달하는 데 "
            "필요한 만큼(보통 1~2문장, 필요하면 조금 더) 자연스럽게 쓰세요."
        )
    else:
        content = _strip_html(post.description)[:DESCRIPTION_MAX]
        source_label = "RSS 도입부(티저)"
        depth_note = "티저가 짧으면 그 안에서 확인되는 내용만으로 간결하게 작성하세요 (없는 내용을 부풀리지 마세요)."

    return f"""당신은 기업 기술블로그 글을 바쁜 개발자에게 소개하는 편집자입니다.
뉴스·논문 요약과 동일한 수준의 정보량을 목표로 하되, 아래 {source_label}에
없는 내용을 추측해서 지어내지 마세요. {depth_note}

블로그: {post.blog}
제목: {post.title}

{source_label}:
{content or '(내용 없음)'}

JSON 스키마:
{{
  "key_points": [
    "핵심 포인트 1 — 구체적인 기술·수치·용어를 포함해 그 내용을 제대로 전달할 만큼",
    "핵심 포인트 2",
    "핵심 포인트 3",
    "... 내용이 더 있으면 계속 (최대 8개)"
  ],
  "detail_paragraphs": [
    "첫 번째 문단 — 배경/문제 상황을 서술형으로 (2~4문장)",
    "두 번째 문단 — 접근 방식·과정 (있으면, 2~4문장)",
    "세 번째 문단 — 결과·의의 (있으면, 2~4문장)",
    "... 내용이 더 있으면 계속 (최대 4문단)"
  ]
}}
detail_paragraphs 는 key_points 를 그대로 나열하는 게 아니라, 카드뉴스의
'자세히 보기' 페이지에 실릴 문단형 서술입니다 — 문단 하나하나가 이야기처럼
자연스럽게 읽혀야 합니다. 내용이 짧으면 문단 1개로 충분하고, 풍부하면
문단을 나눠(최대 4개) 배경→과정→결과 흐름을 살리세요.
내용이 짧아 이만큼 뽑기 어려우면 있는 만큼만 반환해도 됩니다 (key_points 최소 1개).
"""


def summarize_techpost(post: TechPost) -> bool:
    has_body = post.body and post.body_status == "success"
    if not has_body and not (post.description or "").strip():
        # 본문도 티저도 없으면 근거가 없어 요약 불가 — 재시도 계속 시도되지 않도록 dirty 만 내림
        post.summary_dirty = False
        return False

    prompt = _build_prompt(post)
    try:
        result = generate_json(prompt)
    except Exception as e:
        logger.exception(f"summarize techpost {post.id} failed: {e}")
        return False

    points = [p.strip() for p in (result.get("key_points") or []) if p and p.strip()]
    post.key_points = points
    paragraphs = [p.strip() for p in (result.get("detail_paragraphs") or []) if p and p.strip()]
    # DB 컬럼은 Text 하나 — 문단 구분자(\n\n)로 이어붙이고, 렌더링 시 cardnews.py 가 다시 분리해 청킹
    post.summary_ko = "\n\n".join(paragraphs)
    post.summary_dirty = False
    return True


def summarize_pending(limit: int = DEFAULT_LIMIT) -> dict:
    dirty = (
        TechPost.query
        .filter_by(summary_dirty=True)
        .order_by(TechPost.hot_score.desc(), TechPost.fetched_at.desc())
        .limit(limit)
        .all()
    )
    stats = {"picked": len(dirty), "success": 0, "failed": 0}
    for i, post in enumerate(dirty, 1):
        print(f"  [{i}/{len(dirty)}] [{post.blog}] {post.title[:50]}")
        ok = summarize_techpost(post)
        if ok:
            stats["success"] += 1
        else:
            stats["failed"] += 1
        db.session.commit()
    return stats


def backfill_dirty_techposts() -> dict:
    """숨김 처리 안 된 TechPost 전부를 summary_dirty=True 로 리셋 후 본문 fetch + 재요약.

    body fetch 도입 이전(RSS 티저만으로 요약)에 이미 처리된 기존 글들을 소급
    개선하기 위한 1회성 백로그 청소 — pick 우선순위/limit 무시.
    """
    from jobs.techblog_body_fetcher import fetch_pending as fetch_bodies

    reset = TechPost.query.filter(TechPost.hidden_at.is_(None)).update({"summary_dirty": True})
    db.session.commit()

    body_stats = {"processed": 0, "success": 0, "failed": 0, "blocked": 0}
    while True:
        b = fetch_bodies(limit=40)
        if b["processed"] == 0:
            break
        for k in body_stats:
            body_stats[k] += b[k]

    dirty = TechPost.query.filter_by(summary_dirty=True).all()
    stats = {"reset": reset, "total": len(dirty), "success": 0, "failed": 0, "body": body_stats}
    for i, post in enumerate(dirty, 1):
        print(f"  [{i}/{len(dirty)}] [{post.blog}] {post.title[:50]}")
        ok = summarize_techpost(post)
        stats["success" if ok else "failed"] += 1
        db.session.commit()
    return stats


if __name__ == "__main__":
    app = create_app(with_scheduler=False)
    with app.app_context():
        print(f"요약 모델: {Config.CLAUDE_SUMMARY_MODEL}")
        stats = summarize_pending()
        print(f"\n요약 완료: picked={stats['picked']} success={stats['success']} failed={stats['failed']}")
