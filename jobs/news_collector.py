"""뉴스 RSS 수집기.

- requests + feedparser 조합으로 SSL 인증서 검증 문제 회피
- 수집 윈도우: KST 기준 당일 발행분만 (COLLECT_DAYS_BACK 환경변수로 확장)
- AI 필터링 정책:
    * Tier 2 (OpenAI/Anthropic/DeepMind 등 1차 출처) → 필터 면제
    * Tier 1 그 외 모든 매체 → AI 키워드 필터 통과해야만 적재
  → 종합 매체뿐 아니라 AI 전문지의 변두리 기사(단순 IT/기업 동향)도 걸러냄
"""
import hashlib
import logging
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import feedparser
import requests

from models import db, Source, Article

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; AINewsDigest/0.1; +personal-use)"
FETCH_TIMEOUT = 20
KST = timezone(timedelta(hours=9))

# DB 칼럼 너비에 맞춘 문자열 잘라내기 한도
_TITLE_MAX = 500
_DESCRIPTION_MAX = 5000
_SOURCE_ERROR_MAX = 1000

COLLECT_DAYS_BACK = int(os.getenv("COLLECT_DAYS_BACK", "0"))

# AI 키워드 (Tier 1 매체 전체에 적용)
# 종합지에서 AI 관련 보도 누락 최소화를 위해 광범위하게 등록
AI_KEYWORDS_EN = {
    # 일반
    "ai", "a.i.", "artificial intelligence", "agi", "asi",
    # 모델 클래스
    "llm", "vlm", "slm", "foundation model", "frontier model",
    "language model", "vision model",
    # 회사
    "openai", "anthropic", "deepmind", "google ai", "meta ai", "mistral",
    "perplexity", "cohere", "x.ai", "huggingface", "hugging face",
    "stability ai", "stability.ai", "deepseek", "scale ai",
    # 모델/제품명
    "gpt", "chatgpt", "gpt-4", "gpt-5", "claude", "gemini", "llama",
    "grok", "qwen", "kimi", "phi", "sora", "veo", "midjourney",
    "stable diffusion", "dall-e", "dall e", "flux", "runway",
    "copilot", "github copilot", "cursor", "windsurf",
    "o1", "o3", "o4", "r1",
    # 기술 개념
    "generative", "genai", "machine learning", "deep learning",
    "neural network", "transformer", "attention mechanism",
    "diffusion model", "agent", "agentic", "ai agent", "autonomous",
    "fine-tun", "pretraining", "pre-training", "rlhf", "dpo", "grpo",
    "prompt", "prompting", "rag", "retrieval-augmented",
    "embedding", "vector", "multimodal", "multi-modal",
    "chatbot", "inference", "training", "tokens",
    "reasoning", "chain-of-thought", "cot", "test-time",
    "fine-tuning", "lora", "quantization", "moe", "mixture of experts",
    "tool use", "function calling", "mcp", "model context protocol",
    "embodied", "world model", "robot", "humanoid", "self-driving",
    # 산업
    "ai chip", "ai accelerator", "nvidia", "tpu", "h100", "h200", "b100",
    "ai safety", "ai regulation", "ai act", "ai ethics",
    "synthetic data", "hallucination", "alignment",
}

AI_KEYWORDS_KO = {
    # 일반
    "ai", "인공지능", "에이아이", "agi", "범용인공지능", "인공일반지능",
    # 모델 클래스
    "거대언어모델", "거대 언어", "초거대", "기반 모델", "파운데이션 모델",
    "언어모델", "언어 모델", "프런티어 모델", "초거대모델",
    # 한국 모델/회사
    "하이퍼클로바", "하이퍼 클로바", "hyperclova", "엑사원", "exaone",
    "코지피티", "kogpt", "가우스", "솔라", "solar",
    "네이버 ai", "카카오 ai", "lg ai", "삼성 ai", "skt ai", "kt ai",
    # 해외 모델/제품
    "챗gpt", "챗 gpt", "chatgpt", "gpt", "지피티",
    "클로드", "claude", "제미나이", "gemini", "라마", "llama",
    "그록", "grok", "큐원", "qwen", "딥시크", "deepseek",
    "미스트랄", "mistral", "퍼플렉시티", "perplexity",
    "코파일럿", "copilot", "커서", "윈드서프",
    "소라", "sora", "베오", "veo", "미드저니", "midjourney",
    "스테이블 디퓨전", "stable diffusion", "달리", "dall-e",
    # 회사
    "오픈ai", "오픈 ai", "openai", "앤트로픽", "anthropic",
    "딥마인드", "deepmind", "허깅페이스", "메타 ai",
    # 기술 개념
    "생성형", "생성ai", "생성 ai", "genai", "generative",
    "머신러닝", "딥러닝", "신경망", "트랜스포머", "transformer",
    "어텐션", "attention", "임베딩", "벡터", "벡터db", "벡터 db",
    "디퓨전", "diffusion",
    "프롬프트", "prompt", "프롬프팅",
    "에이전트", "에이전틱", "agent", "ai 에이전트", "ai에이전트",
    "파인튜닝", "파인 튜닝", "fine-tuning",
    "rlhf", "dpo", "grpo", "rag", "검색증강", "검색 증강",
    "멀티모달", "multimodal", "multi-modal",
    "추론", "리즈닝", "사고 사슬", "체인오브쏘트", "cot",
    "양자화", "quantization", "lora", "moe", "전문가 혼합",
    "툴 유스", "함수 호출", "function calling", "mcp",
    "체화", "임바디드", "embodied", "월드 모델", "world model",
    "휴머노이드", "휴먼노이드", "로봇", "자율주행",
    "할루시네이션", "환각", "정렬", "alignment",
    "토큰", "token", "컨텍스트", "context window",
    "사전학습", "사전 학습", "pretraining",
    # 산업
    "ai반도체", "ai 반도체", "ai칩", "ai 칩", "ai 가속기",
    "엔비디아", "nvidia", "tpu", "h100", "h200", "b100",
    "데이터센터", "데이터 센터",
    "ai 안전", "ai 규제", "ai 윤리", "ai 거버넌스",
    "온디바이스", "on-device", "ax", "ai 전환",
    "llm", "vlm", "slm",
    # 챗봇/일반
    "챗봇", "ai 챗봇", "ai 비서", "ai 어시스턴트",
}


def _hash_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _is_ai_relevant(title: str, description: str, lang: str) -> bool:
    haystack = f"{title} {description}".lower()
    kws = AI_KEYWORDS_KO if lang == "ko" else AI_KEYWORDS_EN
    return any(kw in haystack for kw in kws)


def _should_filter(source: Source) -> bool:
    """필터 적용 여부.

    Tier 2 (OpenAI/Anthropic/DeepMind 등 1차 출처)는 면제 — 정의상 모두 AI 관련.
    그 외 모든 매체는 키워드 필터를 통과해야 함.
    """
    return source.tier != 2


def _parse_published(entry) -> datetime:
    for field in ("published_parsed", "updated_parsed"):
        ts = entry.get(field)
        if ts:
            try:
                return datetime(*ts[:6])
            except (TypeError, ValueError):
                continue
    return datetime.utcnow()


def _cutoff_utc_naive() -> datetime:
    today_kst = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    start_kst = today_kst - timedelta(days=COLLECT_DAYS_BACK)
    return start_kst.astimezone(timezone.utc).replace(tzinfo=None)


def _fetch_feed(rss_url: str):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    }
    resp = requests.get(rss_url, headers=headers, timeout=FETCH_TIMEOUT)
    resp.raise_for_status()
    return feedparser.parse(resp.content)


class _SitemapFeed:
    """Google News Sitemap을 feedparser 결과처럼 감싸는 경량 래퍼."""

    def __init__(self, entries):
        self.entries = entries
        self.bozo = False
        self.bozo_exception = None


def _parse_sitemap_date(date_str: str) -> datetime:
    """ISO 8601 날짜 문자열을 naive UTC datetime으로 변환."""
    # 예: 2026-06-24T08:30:00+09:00
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str[:25], fmt[:len(fmt)])
            if dt.tzinfo:
                return dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except ValueError:
            continue
    return datetime.utcnow()


def _fetch_sitemap(url: str) -> _SitemapFeed:
    """Google News Sitemap XML을 가져와 _SitemapFeed 로 반환."""
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(url, headers=headers, timeout=FETCH_TIMEOUT)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    ns = {
        "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
        "news": "http://www.google.com/schemas/sitemap-news/0.9",
    }

    entries = []
    for url_el in root.findall("sm:url", ns):
        loc = (url_el.findtext("sm:loc", namespaces=ns) or "").strip()
        if not loc:
            continue
        news_el = url_el.find("news:news", ns)
        if news_el is None:
            continue
        title = (news_el.findtext("news:title", namespaces=ns) or "").strip()
        pub_raw = (news_el.findtext("news:publication_date", namespaces=ns) or "").strip()
        pub_dt = _parse_sitemap_date(pub_raw) if pub_raw else datetime.utcnow()
        # feedparser의 published_parsed 형식(time.struct_time)으로 변환
        entries.append({
            "link": loc,
            "title": title,
            "summary": "",
            "published_parsed": pub_dt.timetuple(),
        })

    return _SitemapFeed(entries)


def collect_source(source: Source, cutoff: datetime, prefetched=None) -> dict:
    """RSS/Sitemap 수집. prefetched 가 있으면 feed 페치 단계 스킵 (이미 받았음)."""
    stats = {
        "source": source.name,
        "fetched": 0, "new": 0, "filtered": 0, "old_skipped": 0,
        "error": None,
    }
    apply_filter = _should_filter(source)

    try:
        if prefetched is not None:
            if isinstance(prefetched, Exception):
                raise prefetched
            feed = prefetched
        elif getattr(source, "feed_type", "rss") == "sitemap":
            feed = _fetch_sitemap(source.rss_url)
        else:
            feed = _fetch_feed(source.rss_url)

        if feed.bozo and not feed.entries:
            raise RuntimeError(f"feed parse error: {feed.bozo_exception}")

        stats["fetched"] = len(feed.entries)
        for entry in feed.entries:
            url = (entry.get("link") or "").strip()
            if not url:
                continue

            published = _parse_published(entry)
            if published < cutoff:
                stats["old_skipped"] += 1
                continue

            url_hash = _hash_url(url)
            if Article.query.filter_by(url_hash=url_hash).first():
                continue

            title = (entry.get("title") or "").strip()
            description = (entry.get("summary") or entry.get("description") or "").strip()[:_DESCRIPTION_MAX]

            if apply_filter and not _is_ai_relevant(title, description, source.lang):
                stats["filtered"] += 1
                continue

            db.session.add(Article(
                source_id=source.id,
                url=url,
                url_hash=url_hash,
                title=title[:_TITLE_MAX],
                description=description,
                published_at=published,
                is_ai_relevant=True,
            ))
            stats["new"] += 1

        source.last_fetched_at = datetime.utcnow()
        source.last_error = None
        db.session.commit()

    except Exception as e:
        db.session.rollback()
        stats["error"] = str(e)
        try:
            source.last_error = str(e)[:_SOURCE_ERROR_MAX]
            db.session.commit()
        except Exception:
            db.session.rollback()
        logger.exception(f"collect failed for {source.name}")

    return stats


def collect_all() -> list[dict]:
    """RSS 16개 소스 — 페치는 병렬, DB 쓰기는 순차."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    cutoff = _cutoff_utc_naive()
    sources = Source.query.filter_by(active=True).order_by(Source.tier, Source.id).all()
    if not sources:
        return []

    # 1단계: RSS/Sitemap 페치 병렬 (IO bound, 스레드 안전 — DB 미접근)
    prefetched: dict[int, object] = {}
    def _fetch_one(src):
        try:
            if getattr(src, "feed_type", "rss") == "sitemap":
                return src.id, _fetch_sitemap(src.rss_url)
            return src.id, _fetch_feed(src.rss_url)
        except Exception as e:
            return src.id, e

    max_workers = min(8, len(sources))
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_fetch_one, s) for s in sources]
        for fut in as_completed(futures):
            sid, result = fut.result()
            prefetched[sid] = result

    # 2단계: 메인 스레드에서 DB 쓰기 (Source 정렬 유지)
    return [collect_source(s, cutoff, prefetched=prefetched.get(s.id)) for s in sources]


if __name__ == "__main__":
    from app import create_app
    app = create_app()
    with app.app_context():
        print(f"수집 윈도우: KST {(datetime.now(KST) - timedelta(days=COLLECT_DAYS_BACK)).strftime('%Y-%m-%d')} 자정 이후 발행분")
        print(f"AI 필터: Tier 2(1차 출처) 제외 전 매체에 적용\n")
        results = collect_all()
        print(f"수집 결과 ({len(results)}개 소스):")
        for r in results:
            err = f"  ⚠️ {r['error'][:80]}" if r["error"] else ""
            print(
                f"  {r['source']:<28} "
                f"fetched={r['fetched']:>3} new={r['new']:>3} "
                f"filtered={r['filtered']:>3} old={r['old_skipped']:>3}{err}"
            )
        total_new = sum(r["new"] for r in results)
        print(f"\n총 신규 기사: {total_new}건")
