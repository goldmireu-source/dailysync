"""클러스터링 결과를 콘솔에 펼쳐 확인하는 검수 유틸.

Usage:
    python inspect_clusters.py
    python inspect_clusters.py --min 2     # 멤버 2개 이상 클러스터만
    python inspect_clusters.py --full      # 멤버 전부 표시 (기본 10개 제한)
"""
import argparse

from app import create_app
from models import Cluster


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min", type=int, default=1, help="최소 멤버 수")
    parser.add_argument("--full", action="store_true", help="멤버 전부 표시")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        clusters = Cluster.query.order_by(Cluster.id).all()
        filtered = [c for c in clusters if c.articles.count() >= args.min]

        print(f"전체 클러스터: {len(clusters)}개  (≥{args.min}건: {len(filtered)}개)\n")

        for c in filtered:
            members = c.articles.all()
            sources = sorted(set(a.source.name for a in members))
            print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print(f"[Cluster {c.id}]  {len(members)}건  /  매체 {len(sources)}개")
            print(f"  토픽: {(c.topic or '-')[:90]}")
            print(f"  매체: {', '.join(sources)}")
            limit = len(members) if args.full else 10
            for a in members[:limit]:
                print(f"    - [{a.source.name:<22}] {a.title[:65]}")
            if not args.full and len(members) > limit:
                print(f"    ... and {len(members) - limit} more")
            print()


if __name__ == "__main__":
    main()
