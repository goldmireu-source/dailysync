"""클러스터·논문 요약 결과를 콘솔에 펼쳐 확인하는 검수 유틸.

Usage:
    python inspect_summaries.py              # 클러스터 5건 + 논문 3편
    python inspect_summaries.py --n 10       # 클러스터 10건 + 논문 10편
    python inspect_summaries.py --multi      # 멤버 ≥2개 클러스터만
    python inspect_summaries.py --papers     # 논문만
    python inspect_summaries.py --clusters   # 클러스터만
"""
import argparse
import sys

from app import create_app
from models import Cluster, Paper


def show_clusters(n: int, multi_only: bool):
    print("=" * 70)
    print("📰 뉴스 클러스터 요약 결과")
    print("=" * 70)

    q = Cluster.query.filter(
        Cluster.summary_ko.isnot(None),
        Cluster.summary_ko != ""
    )
    items = q.order_by(Cluster.importance.desc(), Cluster.id.desc()).all()

    if multi_only:
        items = [c for c in items if c.articles.count() >= 2]

    items = items[:n]

    for c in items:
        members = c.articles.all()
        sources = sorted(set(a.source.name for a in members))
        cats = ", ".join(c.categories or [])

        print(f"\n[Cluster {c.id}] importance={c.importance}  매체 {len(sources)}개 / {len(members)}건  [{cats}]")
        print(f"  📌 토픽: {c.topic}")
        print(f"  📝 요약: {c.summary_ko}")

        if c.agreed_facts:
            print(f"  ✓ 공통 사실:")
            for f in c.agreed_facts:
                print(f"      - {f}")

        if c.divergences:
            print(f"  ⚠ 매체별 차이:")
            for d in c.divergences:
                print(f"      - [{d.get('source', '?')}] {d.get('claim', '')}")

        if len(sources) > 1:
            print(f"  📡 매체: {', '.join(sources)}")


def show_papers(n: int):
    print("\n" + "=" * 70)
    print("📄 논문 요약 결과")
    print("=" * 70)

    items = (
        Paper.query
        .filter(Paper.summary_ko.isnot(None), Paper.summary_ko != "")
        .order_by(Paper.hf_upvotes.desc(), Paper.published_at.desc())
        .limit(n)
        .all()
    )

    for p in items:
        tag = "⭐" if p.hf_featured else "  "
        cats = ", ".join(p.categories or [])
        authors = ", ".join((p.authors or [])[:3])
        if len(p.authors or []) > 3:
            authors += f" 외 {len(p.authors) - 3}명"

        print(f"\n[{p.arxiv_id}] {tag} upvotes={p.hf_upvotes}  [{cats}]")
        print(f"  📌 제목: {p.title}")
        print(f"  👥 저자: {authors}")
        print(f"  📝 요약: {p.summary_ko}")
        print(f"  ❓ 문제: {p.problem_ko}")
        print(f"  🔧 방법: {p.method_ko}")
        print(f"  📊 결과: {p.results_ko}")
        print(f"  💡 의의: {p.significance_ko}")
        if p.limitations_ko:
            print(f"  ⚠ 한계: {p.limitations_ko}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=5)
    parser.add_argument("--multi", action="store_true")
    parser.add_argument("--papers", action="store_true")
    parser.add_argument("--clusters", action="store_true")
    args = parser.parse_args()

    # Windows 콘솔 한글 출력 안전화
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    show_p = args.papers or not args.clusters
    show_c = args.clusters or not args.papers

    app = create_app()
    with app.app_context():
        if show_c:
            show_clusters(args.n if not args.papers else 5, args.multi)
        if show_p:
            show_papers(args.n if not args.clusters else 3)


if __name__ == "__main__":
    main()
