"""임베딩 + 뉴스 클러스터링.

흐름:
  1. embed_articles()  — 미임베딩 Article 의 임베딩 생성 (로컬 sentence-transformers)
  2. embed_papers()    — 미임베딩 Paper 의 임베딩 생성
  3. cluster_articles()— 클러스터 미할당 Article 을 기존 클러스터에 편입하거나 신규 생성

클러스터링 정책:
  - 코사인 유사도 ≥ CLUSTER_SIMILARITY_THRESHOLD → 편입
  - 윈도우: 최근 CLUSTER_TIME_WINDOW_HOURS 시간 내 updated 된 클러스터만 매칭 후보
  - centroid 는 incremental mean 으로 갱신
"""
import logging
from datetime import datetime, timedelta

import numpy as np

from app import create_app
from config import Config
from models import db, Article, Paper, Cluster
from services.local_embed import embed_texts

logger = logging.getLogger(__name__)

MAX_INPUT_CHARS = 6000
BODY_PREFIX_CHARS = 2000


# ---------- 텍스트 빌더 ----------
def _build_article_text(article: Article) -> str:
    parts = [article.title or ""]
    if article.body and article.body_status == "success":
        parts.append(article.body[:BODY_PREFIX_CHARS])
    elif article.description:
        parts.append(article.description)
    return " ".join(parts)[:MAX_INPUT_CHARS]


def _build_paper_text(paper: Paper) -> str:
    return f"{paper.title} {paper.abstract or ''}"[:MAX_INPUT_CHARS]


# ---------- 공통 배치 임베딩 ----------
def _embed_items(items, text_fn, input_type: str = "document") -> dict:
    if not items:
        return {"total": 0, "success": 0, "failed": 0}

    pairs = [(item, text_fn(item)) for item in items]
    valid = [(item, txt) for item, txt in pairs if txt.strip()]

    if not valid:
        return {"total": len(items), "success": 0, "failed": len(items)}

    valid_items = [v[0] for v in valid]
    valid_texts = [v[1] for v in valid]

    print(f"  로컬 임베딩 호출 ({len(valid_texts)}건)...")
    try:
        embeddings = embed_texts(valid_texts, input_type="document")
    except Exception as e:
        logger.exception("local embed failed")
        return {"total": len(items), "success": 0, "failed": len(items), "error": str(e)}

    for item, emb in zip(valid_items, embeddings):
        item.embedding = emb

    db.session.commit()
    return {
        "total": len(items),
        "success": len(valid_items),
        "failed": len(items) - len(valid_items),
    }


def embed_articles(limit: int = 500) -> dict:
    pending = (
        Article.query
        .filter(Article.embedding.is_(None))
        .filter(Article.is_ai_relevant.is_(True))
        .order_by(Article.published_at.asc())
        .limit(limit)
        .all()
    )
    return _embed_items(pending, _build_article_text, input_type="document")


def embed_papers(limit: int = 500) -> dict:
    pending = (
        Paper.query
        .filter(Paper.embedding.is_(None))
        .order_by(Paper.published_at.desc())
        .limit(limit)
        .all()
    )
    return _embed_items(pending, _build_paper_text, input_type="document")


# ---------- 클러스터링 ----------
def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    if norm == 0:
        return 0.0
    return float(np.dot(a, b) / norm)


def _detach_stale_cluster_articles(max_gap_hours: int = 48) -> int:
    """미저장 클러스터에서 최신 기사 KST 날짜와 다른 날의 기사를 cluster_id=NULL 로 분리.

    KST 날짜 기준으로 다른 날에 발행된 기사를 정리한다.
    분리 후 클러스터는 삭제 — 빈 껍데기 클러스터(stale centroid + old topic)가
    새 기사를 잘못 흡수해 카드 내용↔링크 불일치를 유발하는 것을 원천 차단.
    저장된 클러스터(saved_at IS NOT NULL)는 건드리지 않는다.
    """
    clusters = (
        Cluster.query
        .filter(Cluster.saved_at.is_(None))
        .all()
    )
    total_detached = 0
    for cluster in clusters:
        members = cluster.articles.all()
        if len(members) < 2:
            continue
        dated = [m for m in members if m.published_at]
        if not dated:
            continue
        latest_pub = max(m.published_at for m in dated)
        # KST 날짜 기준 — 최신 기사와 다른 KST 날짜의 기사는 분리
        latest_kst_date = (latest_pub + timedelta(hours=9)).date()
        stale = [m for m in dated if (m.published_at + timedelta(hours=9)).date() != latest_kst_date]
        if not stale:
            continue

        # 다른 날 기사가 있었으면 centroid 가 오염된 것 — 전체 기사 분리 후 클러스터 삭제.
        # 삭제하지 않으면 빈 클러스터가 stale centroid 를 가진 채 살아남아
        # 이후 클러스터링에서 무관한 새 기사를 흡수해 카드 내용/링크 불일치를 유발.
        for a in members:
            a.cluster_id = None
        total_detached += len(members)
        db.session.delete(cluster)

    if total_detached:
        db.session.commit()
    return total_detached


def cluster_articles() -> dict:
    threshold = Config.CLUSTER_SIMILARITY_THRESHOLD
    window_hours = Config.CLUSTER_TIME_WINDOW_HOURS
    cutoff = datetime.utcnow() - timedelta(hours=window_hours)

    # 0. 기존 클러스터에서 날짜 이상값 기사 분리 (오염된 기존 데이터 정리)
    detached = _detach_stale_cluster_articles(max_gap_hours=24)
    if detached:
        logger.info(f"cluster_articles: 날짜 이상값 기사 {detached}개 클러스터에서 분리")

    # 날짜 필터: 윈도우 내 기사만 클러스터링 대상으로 (오래된 미할당 기사가 새 클러스터로 편입되는 것 방지)
    unassigned = (
        Article.query
        .filter(Article.cluster_id.is_(None))
        .filter(Article.embedding.isnot(None))
        .filter(Article.published_at >= cutoff)
        .order_by(Article.published_at.asc())
        .all()
    )

    # 저장된 클러스터는 활성 풀에서 제외 — 저장 클러스터는 내용 동결, 새 기사 편입 금지
    active = (
        Cluster.query
        .filter(Cluster.updated_at >= cutoff)
        .filter(Cluster.saved_at.is_(None))
        .all()
    )
    state = []
    for c in active:
        if not c.centroid:
            continue
        # KST 날짜 산출 — 클러스터 기사들 중 최신 기사의 KST 날짜
        members_all = c.articles.all()
        # 기사 없는 빈 클러스터는 stale centroid 를 가진 채 무관한 기사를 흡수하므로 제외
        if not members_all:
            continue
        dated_members = [m for m in members_all if m.published_at]
        if dated_members:
            latest_pub = max(m.published_at for m in dated_members)
            kst_date = (latest_pub + timedelta(hours=9)).date()
        else:
            kst_date = (c.updated_at + timedelta(hours=9)).date() if c.updated_at else (datetime.utcnow() + timedelta(hours=9)).date()
        state.append({
            "id": c.id,
            "centroid": np.array(c.centroid, dtype=np.float32),
            "size": len(members_all),
            "updated_at": c.updated_at or datetime.utcnow(),
            "kst_date": kst_date,
        })

    stats = {"processed": 0, "joined": 0, "created": 0}

    for art in unassigned:
        emb = np.array(art.embedding, dtype=np.float32)
        art_pub = art.published_at or datetime.utcnow()

        # 기사의 KST 날짜 — 클러스터와 같은 날이어야만 편입 허용
        art_kst_date = (art_pub + timedelta(hours=9)).date()

        best_idx, best_sim = -1, 0.0
        for i, cs in enumerate(state):
            # KST 날짜가 다르면 다른 날의 사건 → 편입 금지
            if art_kst_date != cs["kst_date"]:
                continue
            sim = _cosine(emb, cs["centroid"])
            if sim > best_sim:
                best_sim = sim
                best_idx = i

        if best_idx >= 0 and best_sim >= threshold:
            cs = state[best_idx]
            cluster = db.session.get(Cluster, cs["id"])
            new_centroid = (cs["centroid"] * cs["size"] + emb) / (cs["size"] + 1)
            cluster.centroid = new_centroid.tolist()
            cluster.summary_dirty = True
            cluster.updated_at = datetime.utcnow()
            art.cluster_id = cluster.id

            cs["centroid"] = new_centroid
            cs["size"] += 1
            stats["joined"] += 1
        else:
            cluster = Cluster(
                centroid=art.embedding,
                topic=(art.title or "")[:300],
                summary_dirty=True,
            )
            db.session.add(cluster)
            db.session.flush()
            art.cluster_id = cluster.id

            state.append({"id": cluster.id, "centroid": emb, "size": 1, "updated_at": datetime.utcnow(), "kst_date": art_kst_date})
            stats["created"] += 1

        stats["processed"] += 1

    db.session.commit()

    # 사후 머지 — 동일 사건이 여러 클러스터로 쪼개진 경우 흡수
    merge_stats = merge_similar_clusters()
    stats["merged_groups"] = merge_stats.get("groups_merged", 0)
    stats["clusters_absorbed"] = merge_stats.get("clusters_absorbed", 0)
    return stats


# ---------- 사후 머지 (평행 클러스터 흡수) ----------
# 한 그룹에 묶일 수 있는 최대 클러스터 수 (전이성으로 인한 메가-그룹 방지)
MAX_MERGE_GROUP_SIZE = 4


def merge_similar_clusters(
    threshold: float | None = None,
    window_hours: int | None = None,
) -> dict:
    """동일 사건이 여러 클러스터로 분리된 경우 사후 병합.

    알고리즘: greedy pairwise.
      - 윈도우 내 클러스터들의 centroid 쌍쌍 유사도 계산
      - threshold 이상인 쌍을 유사도 내림차순 큐에 적재
      - 가장 가까운 쌍부터 처리:
          * 두 keeper 의 *현재* centroid 로 유사도 재계산 (이미 누가 흡수돼 centroid 갱신됐을 수 있음)
          * 재계산 유사도가 여전히 ≥ threshold 이고
          * 머지 후 그룹 size 가 MAX_MERGE_GROUP_SIZE 이하이며
          * saved 충돌 아닐 때만 합침
      - keeper 선택: saved_at 있는 쪽 > 멤버 수 많은 쪽 > id 작은 쪽

    이 방식의 장점:
      - 가장 강한 쌍부터 흡수하면서 centroid 가 평균쪽으로 이동
      - "메가-토픽" 클러스터(예: 저작권 종합)와 구체 사건이 transitive 로 끌려 들어가는 현상 억제
      - size cap 으로 한 그룹이 메가-클러스터가 되지 않게 마지막 안전장치

    반환: {pairs_over_threshold, groups_merged, clusters_absorbed, skipped_size, skipped_saved}
    """
    if threshold is None:
        threshold = Config.CLUSTER_MERGE_THRESHOLD
    if window_hours is None:
        window_hours = Config.CLUSTER_TIME_WINDOW_HOURS

    cutoff = datetime.utcnow() - timedelta(hours=window_hours)
    candidates = (
        Cluster.query
        .filter(Cluster.updated_at >= cutoff)
        .filter(Cluster.centroid.isnot(None))
        .all()
    )

    n = len(candidates)
    stats = {
        "pairs_over_threshold": 0,
        "groups_merged": 0,
        "clusters_absorbed": 0,
        "skipped_size": 0,
        "skipped_saved": 0,
        "skipped_date": 0,
    }
    if n < 2:
        return stats

    # np.atleast_1d 로 감싸 스칼라·0-d 배열도 안전하게 1-D 배열로 변환
    centroids_raw = [np.atleast_1d(np.array(c.centroid, dtype=np.float32)) for c in candidates]

    # 차원 불일치 centroid 필터링 — 모델 교체 등으로 구·신 차원이 혼재하면
    # np.array([...]) 가 object 배열(1-D)이 되어 @ 연산이 스칼라를 반환하고
    # sim[i, j] 에서 IndexError 가 발생하므로 같은 차원끼리만 남긴다.
    expected_dim = centroids_raw[0].shape[0] if centroids_raw else 0
    valid_mask = [v.ndim == 1 and v.shape[0] == expected_dim for v in centroids_raw]
    if not all(valid_mask):
        n_skip = sum(1 for ok in valid_mask if not ok)
        logger.warning(
            "merge_similar_clusters: centroid 차원 불일치 %d개 제외 (expected_dim=%d)",
            n_skip,
            expected_dim,
        )
        candidates = [c for c, ok in zip(candidates, valid_mask) if ok]
        centroids_raw = [v for v, ok in zip(centroids_raw, valid_mask) if ok]

    centroids = centroids_raw
    n = len(candidates)
    if n < 2:
        return stats

    sizes = [max(1, c.articles.count()) for c in candidates]
    saved_flags = [c.saved_at is not None for c in candidates]

    # 클러스터별 KST 날짜 사전 계산 (최신 기사 기준)
    kst_dates = []
    for c in candidates:
        dated = [a for a in c.articles.all() if a.published_at]
        if dated:
            latest = max(a.published_at for a in dated)
            kst_dates.append((latest + timedelta(hours=9)).date())
        elif c.updated_at:
            kst_dates.append((c.updated_at + timedelta(hours=9)).date())
        else:
            kst_dates.append((datetime.utcnow() + timedelta(hours=9)).date())

    # 초기 모든 쌍 유사도 → threshold 이상 + 같은 KST 날짜 + 미저장인 것만 큐에 적재
    norm_matrix = np.array([v / (np.linalg.norm(v) or 1.0) for v in centroids])
    sim = norm_matrix @ norm_matrix.T
    # norm_matrix 가 예상치 못한 형태(스칼라 등)가 되는 엣지케이스 최후 방어
    if np.ndim(sim) < 2:
        logger.warning("merge_similar_clusters: sim 행렬이 2D 가 아님, 머지 건너뜀")
        return stats
    pair_q: list[tuple[float, int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            if sim[i, j] < threshold:
                continue
            # 저장된 클러스터는 머지 후보에서 제외
            if saved_flags[i] or saved_flags[j]:
                stats["skipped_saved"] += 1
                continue
            # 두 클러스터의 KST 날짜가 다르면 다른 날의 사건 — 머지 금지
            if kst_dates[i] != kst_dates[j]:
                stats["skipped_date"] += 1
                continue
            pair_q.append((float(sim[i, j]), i, j))
    pair_q.sort(reverse=True, key=lambda x: x[0])
    stats["pairs_over_threshold"] = len(pair_q)

    # 인덱스 i 가 어느 keeper 로 흡수됐는지 — 없으면 자기 자신이 keeper
    absorbed_into: dict[int, int] = {}
    # keeper 별 현재 상태 (centroid·size·members)
    cur_centroid = {i: centroids[i].copy() for i in range(n)}
    cur_size = {i: sizes[i] for i in range(n)}
    cur_members = {i: [i] for i in range(n)}
    cur_saved = {i: saved_flags[i] for i in range(n)}

    def root_of(x: int) -> int:
        while x in absorbed_into:
            x = absorbed_into[x]
        return x

    def cosine(a: np.ndarray, b: np.ndarray) -> float:
        na = np.linalg.norm(a)
        nb = np.linalg.norm(b)
        if na == 0 or nb == 0:
            return 0.0
        return float(np.dot(a, b) / (na * nb))

    for _orig_sim, i, j in pair_q:
        ri, rj = root_of(i), root_of(j)
        if ri == rj:
            continue

        # 사이즈 캡 — 합치면 MAX 초과면 skip
        if len(cur_members[ri]) + len(cur_members[rj]) > MAX_MERGE_GROUP_SIZE:
            stats["skipped_size"] += 1
            continue

        # 저장된 클러스터 방어 — pair_q 구성 시 이미 걸렀지만, root 이동 후 재확인
        if cur_saved.get(ri) or cur_saved.get(rj):
            stats["skipped_saved"] += 1
            continue

        # 현재 centroid 로 유사도 재계산
        s_now = cosine(cur_centroid[ri], cur_centroid[rj])
        if s_now < threshold:
            continue

        # keeper 결정 — 둘 다 미저장 (saved 는 앞에서 이미 skip)
        # 멤버 수 큰 쪽, 동률이면 id 작은 쪽
        ki = candidates[ri]
        kj = candidates[rj]
        if cur_size[ri] > cur_size[rj]:
            keep, absorb = ri, rj
        elif cur_size[ri] < cur_size[rj]:
            keep, absorb = rj, ri
        else:
            keep, absorb = (ri, rj) if ki.id <= kj.id else (rj, ri)

        # centroid 가중 평균
        new_size = cur_size[keep] + cur_size[absorb]
        new_centroid = (
            cur_centroid[keep] * cur_size[keep]
            + cur_centroid[absorb] * cur_size[absorb]
        ) / new_size

        cur_centroid[keep] = new_centroid
        cur_size[keep] = new_size
        cur_members[keep].extend(cur_members[absorb])
        absorbed_into[absorb] = keep
        # 흡수된 keeper 의 상태 정리
        del cur_centroid[absorb]
        del cur_size[absorb]
        del cur_members[absorb]
        del cur_saved[absorb]

    # 실제 DB 머지
    for keep_idx, members in cur_members.items():
        if len(members) < 2:
            continue
        keeper_cluster = candidates[keep_idx]
        absorbed_clusters = [candidates[m] for m in members if m != keep_idx]
        absorbed_ids = [c.id for c in absorbed_clusters]

        Article.query.filter(Article.cluster_id.in_(absorbed_ids)).update(
            {Article.cluster_id: keeper_cluster.id}, synchronize_session=False
        )

        keeper_cluster.centroid = cur_centroid[keep_idx].tolist()
        keeper_cluster.summary_dirty = True
        keeper_cluster.updated_at = datetime.utcnow()
        # 머지 결과는 사실상 새 합본이므로 오늘 다시 노출 가능하게 리셋
        # (keeper 가 saved_at 가지면 유지)
        keeper_cluster.first_shown_date = None

        for c in absorbed_clusters:
            db.session.delete(c)

        stats["groups_merged"] += 1
        stats["clusters_absorbed"] += len(absorbed_clusters)

    db.session.commit()
    return stats


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        print(f"임베딩 모델: {Config.LOCAL_EMBEDDING_MODEL}")
        print(f"클러스터 편입 임계값: {Config.CLUSTER_SIMILARITY_THRESHOLD}")
        print(f"클러스터 머지 임계값: {Config.CLUSTER_MERGE_THRESHOLD}")
        print()

        print("=== 1. 뉴스 기사 임베딩 ===")
        s1 = embed_articles(limit=500)
        print(f"  total={s1['total']} success={s1['success']} failed={s1['failed']}")

        print("\n=== 2. 논문 임베딩 ===")
        s2 = embed_papers(limit=500)
        print(f"  total={s2['total']} success={s2['success']} failed={s2['failed']}")

        print("\n=== 3. 뉴스 클러스터링 + 사후 머지 ===")
        s3 = cluster_articles()
        print(f"  processed={s3['processed']} joined={s3['joined']} created={s3['created']}")
        print(f"  merged_groups={s3.get('merged_groups', 0)} clusters_absorbed={s3.get('clusters_absorbed', 0)}")

        total_clusters = Cluster.query.count()
        multi = sum(1 for c in Cluster.query.all() if c.articles.count() >= 2)
        big = sum(1 for c in Cluster.query.all() if c.articles.count() >= 3)
        print(f"\n총 클러스터: {total_clusters}  ≥2개 매체: {multi}  ≥3개 매체: {big}")
