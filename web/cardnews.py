"""카드뉴스 데이터 빌더.

Cluster 또는 Paper 를 받아 슬라이드 카드 리스트로 변환.
"""
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


CATEGORY_KEYS = {
    "정책/규제": "policy",
    "산업/기업": "industry",
    "연구/모델": "research",
    "윤리/사회": "ethics",
}

# ====== 슬라이드 분할 — 텍스트 길이 기반 ======
# 카드 460px 컨텐츠 예산 ~340px. 짧으면 5~6개, 길면 2~3개 자동 조절.

FACT_BUDGET = 300      # 한 슬라이드의 fact 영역 가용 픽셀 (카드 높이 420 기준)
FACT_MIN_LEN = 5       # 5자 미만은 무효 fact (LLM 빈 응답 방지)
SRC_BUDGET = 240       # source-card 한 장 가용 픽셀 (links-mini 80px 빼고)


def _fact_cost(text: str) -> int:
    """fact 한 항목의 슬라이드 점유 비용(px) 추정."""
    n = len(text or "")
    # 줄 수 추정 (한 줄 ≈ 23자, 카드 폭 약 280px 기준)
    if n <= 25:
        lines = 1
    elif n <= 55:
        lines = 2
    elif n <= 90:
        lines = 3
    else:
        lines = 4
    # 1줄 22px + 패딩 18px = 40px(1줄), 2줄 62px, 3줄 84px, 4줄 106px
    return 18 + lines * 22 + 10  # gap 포함


def _chunk_facts(facts: list, budget: int = FACT_BUDGET) -> list[list]:
    """fact 리스트를 슬라이드 단위로 분할.

    각 슬라이드 안 fact 비용 합이 budget 을 넘으면 다음 슬라이드.
    빈 값/짧은 값 (FACT_MIN_LEN 미만) 은 제거.
    """
    # 정제
    clean = [f for f in (facts or []) if f and len(f.strip()) >= FACT_MIN_LEN]
    if not clean:
        return []

    chunks = []
    cur = []
    cur_cost = 0
    for f in clean:
        c = _fact_cost(f)
        # 새 슬라이드로 시작해도 한 fact 가 budget 을 초과하면 그대로 한 장에 담음
        if cur and cur_cost + c > budget:
            chunks.append(cur)
            cur = [f]
            cur_cost = c
        else:
            cur.append(f)
            cur_cost += c
    if cur:
        chunks.append(cur)
    return chunks


def _src_cost(claim: str) -> int:
    """source-card 한 장의 점유 비용 추정."""
    n = len(claim or "")
    if n <= 30:
        lines = 1
    elif n <= 70:
        lines = 2
    elif n <= 110:
        lines = 3
    else:
        lines = 4
    return 22 + lines * 22 + 10  # head 22px + 본문 + gap


def _chunk_sources(src_list: list, budget: int = SRC_BUDGET) -> list[list]:
    """source-card 리스트를 슬라이드 단위로 분할."""
    if not src_list:
        return []
    chunks = []
    cur = []
    cur_cost = 0
    for s in src_list:
        c = _src_cost(s.get("claim", ""))
        if cur and cur_cost + c > budget:
            chunks.append(cur)
            cur = [s]
            cur_cost = c
        else:
            cur.append(s)
            cur_cost += c
    if cur:
        chunks.append(cur)
    return chunks


def _dedup_links(members: list, max_links: int = 5) -> list[dict]:
    """매체명 기준 중복 제거 후 최대 max_links 개 링크 반환.

    표시명: [매체명] 기사제목 (40자 초과 시 말줄임)
    """
    seen: set[str] = set()
    result = []
    for a in members:
        src = a.source.name
        if src not in seen:
            seen.add(src)
            title = (a.title or "").strip()
            if title:
                label = f"[{src}] {title}" if len(title) <= 38 else f"[{src}] {title[:37]}…"
            else:
                label = src
            result.append({"name": label, "url": a.url})
            if len(result) >= max_links:
                break
    return result


def _category_key(categories: list) -> str:
    """클러스터의 주 카테고리 → CSS 클래스 키."""
    if not categories:
        return "default"
    return CATEGORY_KEYS.get(categories[0], "default")


def _kst_day_members(cluster, all_members: list) -> list:
    """클러스터가 속한 KST 날짜 범위의 기사만 반환.

    우선순위:
      1. cluster.first_shown_date (KST 날짜) → 해당 날짜의 UTC 범위로 필터
      2. first_shown_date 없으면 → 최신 기사의 KST 날짜 범위로 필터
      3. 필터 결과가 비면 → 전체 반환 (fallback)

    잘못 편입된 다른 날짜 기사가 링크·출처에 노출되는 것을 방지.
    """
    if not all_members:
        return all_members
    dated = [m for m in all_members if m.published_at]
    if not dated:
        return all_members

    # 기준 날짜 결정
    if cluster.first_shown_date:
        ref_date = cluster.first_shown_date
    else:
        latest_pub = max(m.published_at for m in dated)
        # naive UTC → KST date
        ref_date = (latest_pub + timedelta(hours=9)).date()

    # KST ref_date → UTC 범위
    start_utc = datetime(ref_date.year, ref_date.month, ref_date.day, tzinfo=KST)
    start_utc = start_utc.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = start_utc + timedelta(hours=24)

    filtered = [m for m in dated if start_utc <= m.published_at < end_utc]
    undated = [m for m in all_members if not m.published_at]
    result = filtered + undated
    return result if result else all_members


def build_cluster_cards(cluster) -> list[dict]:
    """클러스터 → 슬라이드 카드 리스트.

    구조:
      1. cover — 표지
      2. facts — 핵심 사실 (agreed_facts)
      3. detail — 본문 요약 + 인용구 (있으면)
      4. sources — 매체별 시각 (≥2 매체 클러스터만)
    """
    cards = []
    # 클러스터 속한 KST 날짜 범위 기사만 — 다른 날 잘못 편입된 기사 제외
    members = _kst_day_members(cluster, cluster.articles.all())
    sources = sorted(set(a.source.name for a in members))
    cat_key = _category_key(cluster.categories or [])

    # 발행시간 — 클러스터 내 가장 최신 기사 기준 (KST)
    pub_dt = None
    latest_article = max(
        (m for m in members if m.published_at),
        key=lambda a: a.published_at,
        default=None,
    )
    if latest_article:
        try:
            pub_dt = latest_article.published_at.replace(tzinfo=timezone.utc).astimezone(KST)
        except Exception:
            pub_dt = None

    # 인용구 추출 — divergences 중 짧고 임팩트 있는 것 또는 fact 첫 항목
    quote = None
    if cluster.divergences:
        for d in cluster.divergences:
            claim = d.get("claim", "")
            if 20 < len(claim) < 100:
                quote = {"text": claim, "attr": d.get("source", "")}
                break

    # 표지 리드 — 첫 핵심 사실(없으면 요약 앞부분)로 표지 공백 메움
    lead = ""
    facts0 = [f for f in (cluster.agreed_facts or []) if f and len(f.strip()) >= 5]
    if facts0:
        lead = facts0[0].strip()
    elif cluster.summary_ko:
        lead = cluster.summary_ko.strip()
    if len(lead) > 115:
        lead = lead[:114].rstrip() + "…"

    # ----- Card 1: Cover -----
    cards.append({
        "type": "cover",
        "category": cat_key,
        "categories": cluster.categories or [],
        "title": cluster.topic or "(제목 없음)",
        "lead": lead,
        "sources": sources,
        "n_sources": len(sources),
        "n_members": len(members),
        "importance": cluster.importance or 3,
        "date_str": pub_dt.strftime("%Y.%m.%d") if pub_dt else "",
    })

    # ----- Card 2~: Key Facts (텍스트 양에 따라 자동 분할) -----
    fact_chunks = _chunk_facts(cluster.agreed_facts)
    n_fact_slides = len(fact_chunks)
    for i, chunk in enumerate(fact_chunks):
        # 제목은 첫 슬라이드만 "이것만 알면 돼요", 이어지는 슬라이드는 "(계속)"
        if i == 0:
            title = "이것만 알면 돼요"
        else:
            title = f"이것만 알면 돼요 ({i + 1}/{n_fact_slides})"
        cards.append({
            "type": "facts",
            "category": cat_key,
            "title": title,
            "facts": chunk,
        })

    # ----- Card 3: Detail (본문 요약) -----
    cards.append({
        "type": "detail",
        "category": cat_key,
        "title": "자세히 보기",
        "summary": cluster.summary_ko or "",
        "quote": quote,
    })

    # ----- Card 4: Source Breakdown (다중 매체만) -----
    if len(sources) >= 2 and cluster.divergences:
        # date-filtered members 에 포함된 소스만 허용 (다른 날 기사의 divergences 제거)
        valid_sources = set(sources)
        by_src: dict = {}
        for d in cluster.divergences:
            src = d.get("source", "기타")
            if src not in valid_sources:
                continue
            by_src.setdefault(src, []).append(d.get("claim", ""))

        # 매체별 발행시각도 제공
        src_meta = {}
        for a in members:
            sn = a.source.name
            if sn not in src_meta:
                t = a.published_at
                if t:
                    try:
                        t_kst = t.replace(tzinfo=timezone.utc).astimezone(KST)
                        src_meta[sn] = t_kst.strftime("%H:%M")
                    except Exception:
                        src_meta[sn] = ""

        src_list = []
        for src, claims in by_src.items():
            src_list.append({
                "name": src,
                "time": src_meta.get(src, ""),
                "claim": " · ".join(claims)[:140],
            })

        # 텍스트 양에 따라 sources 슬라이드 분할
        src_chunks = _chunk_sources(src_list)
        links_info = _dedup_links(members)
        if src_chunks:
            n_src_slides = len(src_chunks)
            for i, chunk in enumerate(src_chunks):
                if i == 0:
                    title = "매체마다 본 각도가 달라요"
                else:
                    title = f"매체마다 본 각도가 달라요 ({i + 1}/{n_src_slides})"
                cards.append({
                    "type": "sources",
                    "category": cat_key,
                    "title": title,
                    "sources_detail": chunk,
                    # 마지막 슬라이드에만 links 박스 표시
                    "links": links_info if i == n_src_slides - 1 else [],
                })
        else:
            # divergences 가 모두 필터링된 경우 링크 카드 fallback
            cards.append({
                "type": "links",
                "category": cat_key,
                "title": "더 알아보기",
                "links": links_info,
            })
    else:
        # 단일 매체 또는 divergences 없음 — 링크 카드만 마지막에
        cards.append({
            "type": "links",
            "category": cat_key,
            "title": "더 알아보기",
            "links": _dedup_links(members),
        })

    return cards


# 카테고리 → 그라데이션/아이콘 fallback (포스터 이미지 없을 때)
_CONTEST_FALLBACK = {
    "공모전": ("🏆", "#6366f1", "#8b5cf6"),
    "창업경진대회": ("🚀", "#0ea5e9", "#2563eb"),
    "경진대회": ("📊", "#059669", "#10b981"),
    "해커톤": ("💻", "#db2777", "#f43f5e"),
    "취업/채용": ("💼", "#d97706", "#f59e0b"),
}


def build_contest_tile(contest) -> dict:
    """공모전 → 정사각형 타일용 dict (위 ⅔ 이미지 / 아래 ⅓ 제목).

    캐러셀이 아닌 단일 타일. 클릭 시 /contest/<id> 상세로 이동.
    """
    icon, c1, c2 = _CONTEST_FALLBACK.get(contest.category or "공모전", _CONTEST_FALLBACK["공모전"])

    # D-day 계산 (KST)
    dday = None
    dday_label = "상시"
    urgent = False
    if contest.deadline:
        days = (contest.deadline - datetime.now(KST).date()).days
        dday = days
        if days <= 0:
            dday_label = "D-DAY"
        else:
            dday_label = f"D-{days}"
        urgent = days <= 7

    return {
        "id": contest.id,
        "title": contest.title,
        "host": contest.host or "",
        "image_url": contest.image_url,
        "image_pos_x": contest.image_pos_x if contest.image_pos_x is not None else 50.0,
        "image_pos_y": contest.image_pos_y if contest.image_pos_y is not None else 50.0,
        "image_scale": contest.image_scale if contest.image_scale is not None else 1.0,
        "category": contest.category or "공모전",
        "deadline": contest.deadline,
        "deadline_str": contest.deadline.strftime("%Y.%m.%d") if contest.deadline else "마감 미정",
        "dday": dday,
        "dday_label": dday_label,
        "urgent": urgent,
        "url": contest.url,
        "source": contest.source,
        "fallback_icon": icon,
        "fallback_c1": c1,
        "fallback_c2": c2,
        "saved": contest.saved_at is not None,
    }


def build_paper_cards(paper) -> list[dict]:
    """논문 → 슬라이드 카드 리스트.

    구조:
      1. paper_cover — 표지 (제목, 저자, upvotes)
      2. problem — 문제
      3. method — 방법
      4. results — 결과 + 의의
      5. paper_links — arXiv 링크
    """
    cards = []
    authors = paper.authors or []
    authors_str = ", ".join(authors[:3])
    if len(authors) > 3:
        authors_str += f" 외 {len(authors) - 3}명"

    cards.append({
        "type": "paper_cover",
        "title": paper.title,
        "title_ko": paper.title_ko or "",
        "authors": authors_str,
        "upvotes": paper.hf_upvotes or 0,
        "hf_featured": paper.hf_featured,
        "categories": paper.categories or [],
        "summary": paper.summary_ko,
    })

    # 본문 3장 (problem / method / results+significance)
    if paper.problem_ko:
        cards.append({
            "type": "paper_section",
            "label": "PROBLEM",
            "title": "어떤 문제를 풀고 있나",
            "body": paper.problem_ko,
        })
    if paper.method_ko:
        cards.append({
            "type": "paper_section",
            "label": "METHOD",
            "title": "어떻게 풀었나",
            "body": paper.method_ko,
        })
    if paper.results_ko or paper.significance_ko:
        cards.append({
            "type": "paper_section",
            "label": "RESULTS",
            "title": "결과와 의의",
            "body": paper.results_ko or "",
            "extra": paper.significance_ko or "",
        })

    # 링크
    cards.append({
        "type": "paper_links",
        "title": "원문 보기",
        "html_url": paper.html_url,
        "pdf_url": paper.pdf_url,
        "arxiv_id": paper.arxiv_id,
        "source_type": paper.source_type,
    })

    return cards
