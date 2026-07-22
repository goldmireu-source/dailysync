"""테크블로그 글 핵심 포인트 요약 (Claude).

techblog_body_fetcher.py 가 가져온 본문(body)이 있으면 그걸 1차 입력으로
쓰고, 없거나 실패했으면 RSS 티저(description)로 폴백한다. body 자체는
요약 입력에만 쓰고 절대 그대로 노출하지 않는다 (README 원칙 1).

흐름:
  1. summary_dirty=True 인 TechPost 를 hot_score 내림차순으로 최대 N개 선정
  2. title + (body 또는 description) 을 Claude 에 전달해 핵심 포인트 2~4개 + 짧은 요약 요청
  3. key_points / summary_ko 저장, summary_dirty=False
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
    else:
        content = _strip_html(post.description)[:DESCRIPTION_MAX]
        source_label = "RSS 도입부(티저)"

    return f"""당신은 기업 기술블로그 글을 바쁜 개발자에게 소개하는 편집자입니다.

아래는 글 제목과 {source_label}입니다. 여기 없는 내용을 추측해서 지어내지
마세요.

블로그: {post.blog}
제목: {post.title}

{source_label}:
{content or '(내용 없음)'}

JSON 스키마:
{{
  "key_points": ["이 글의 핵심 포인트 1 (한 문장)", "핵심 포인트 2", "핵심 포인트 3(있으면)", "핵심 포인트 4(있으면)"],
  "summary_ko": "1~2문장으로 이 글을 소개하는 짧은 티저 요약"
}}
내용이 너무 짧아 핵심 포인트를 뽑기 어려우면 key_points 는 1개만 반환해도 됩니다.
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
    post.summary_ko = (result.get("summary_ko") or "").strip()
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


if __name__ == "__main__":
    app = create_app(with_scheduler=False)
    with app.app_context():
        print(f"요약 모델: {Config.CLAUDE_SUMMARY_MODEL}")
        stats = summarize_pending()
        print(f"\n요약 완료: picked={stats['picked']} success={stats['success']} failed={stats['failed']}")
