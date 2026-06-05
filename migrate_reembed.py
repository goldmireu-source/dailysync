"""임베딩 모델 교체 후 기존 임베딩 전체 초기화 스크립트.

BGE-M3 (1024차원) → paraphrase-multilingual-MiniLM-L12-v2 (384차원) 변경 시
차원이 달라 기존 임베딩과 클러스터 centroid 가 호환되지 않으므로 전부 리셋한다.

실행 후 app.py 를 띄우거나 jobs/embedder.py 를 직접 실행하면
새 모델로 재임베딩 + 재클러스터링이 자동으로 이루어진다.

저장된(saved_at IS NOT NULL) 클러스터도 centroid 만 NULL 로 초기화하고
기사 연결(cluster_id)은 유지한다 — 저장 목록에서 카드가 사라지지 않게.
재클러스터링 대상은 미저장 클러스터 해체 후 떠 있는 기사들만.
"""
import sys

from app import create_app
from models import db, Article, Paper, Cluster


def main(dry_run: bool = False) -> None:
    app = create_app(with_scheduler=False)
    with app.app_context():
        article_count = Article.query.filter(Article.embedding.isnot(None)).count()
        paper_count = Paper.query.filter(Paper.embedding.isnot(None)).count()
        unsaved_cluster_count = Cluster.query.filter(Cluster.saved_at.is_(None)).count()
        saved_cluster_count = Cluster.query.filter(Cluster.saved_at.isnot(None)).count()

        print("=== 임베딩 초기화 대상 ===")
        print(f"  Article 임베딩 보유: {article_count}건")
        print(f"  Paper 임베딩 보유:   {paper_count}건")
        print(f"  미저장 클러스터:     {unsaved_cluster_count}개 (해체 → 기사 분리)")
        print(f"  저장 클러스터:       {saved_cluster_count}개 (centroid 만 초기화)")

        if dry_run:
            print("\n[dry-run] 실제 변경 없음.")
            return

        confirm = input("\n계속하려면 'yes' 입력: ").strip().lower()
        if confirm != "yes":
            print("취소.")
            return

        # 1. 미저장 클러스터 해체 — 기사의 cluster_id 를 NULL 로 돌려 재클러스터링 대상으로
        if unsaved_cluster_count:
            unsaved_ids = [
                c.id for c in Cluster.query.filter(Cluster.saved_at.is_(None)).all()
            ]
            Article.query.filter(Article.cluster_id.in_(unsaved_ids)).update(
                {Article.cluster_id: None}, synchronize_session=False
            )
            Cluster.query.filter(Cluster.id.in_(unsaved_ids)).delete(
                synchronize_session=False
            )
            print(f"  미저장 클러스터 {unsaved_cluster_count}개 삭제 완료.")

        # 2. 저장 클러스터 — centroid 만 NULL (기사 연결 유지)
        if saved_cluster_count:
            Cluster.query.filter(Cluster.saved_at.isnot(None)).update(
                {Cluster.centroid: None, Cluster.summary_dirty: True},
                synchronize_session=False,
            )
            print(f"  저장 클러스터 {saved_cluster_count}개 centroid 초기화 완료.")

        # 3. 모든 Article/Paper 임베딩 초기화
        Article.query.update({Article.embedding: None}, synchronize_session=False)
        Paper.query.update({Paper.embedding: None}, synchronize_session=False)

        db.session.commit()
        print(f"\n완료: Article {article_count}건, Paper {paper_count}건 임베딩 초기화.")
        print("이제 app.py 실행 또는 jobs/embedder.py 직접 실행으로 재임베딩하세요.")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    main(dry_run=dry)
