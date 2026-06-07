"""clusters 테이블에 primary_article_id 컬럼 추가 및 기존 클러스터 역추적.

실행:
    python migrate_primary_article.py

전략:
  - 신규 클러스터: embedder.py 에서 생성 시 primary_article_id=art.id 저장.
  - 기존 클러스터 (primary_article_id NULL): cluster.created_at 과
    article.published_at 의 시간 차이가 가장 작은 멤버 기사를 원문으로 추정.
    → 클러스터는 첫 기사 수집 직후 생성되므로 published_at 이 created_at 에
      가장 가까운 기사가 원본 기사일 가능성이 가장 높음.
"""
import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(__file__), "app.db")

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# 1) 컬럼 추가 (없을 때만)
cols = [row[1] for row in cur.execute("PRAGMA table_info(clusters)").fetchall()]
if "primary_article_id" not in cols:
    cur.execute("ALTER TABLE clusters ADD COLUMN primary_article_id INTEGER NULL")
    conn.commit()
    print("primary_article_id 컬럼 추가 완료")
else:
    print("primary_article_id 컬럼 이미 존재")

# 2) 기존 클러스터 역추적 (primary_article_id IS NULL 인 것만)
cur.execute("""
    UPDATE clusters
    SET primary_article_id = (
        SELECT a.id
        FROM articles a
        WHERE a.cluster_id = clusters.id
          AND a.published_at IS NOT NULL
        ORDER BY ABS(JULIANDAY(a.published_at) - JULIANDAY(clusters.created_at))
        LIMIT 1
    )
    WHERE primary_article_id IS NULL
      AND EXISTS (
          SELECT 1 FROM articles
          WHERE cluster_id = clusters.id
            AND published_at IS NOT NULL
      )
""")
updated = cur.rowcount
conn.commit()

# 3) published_at 없는 기사만 있는 클러스터: fetched_at 기준 fallback
cur.execute("""
    UPDATE clusters
    SET primary_article_id = (
        SELECT a.id
        FROM articles a
        WHERE a.cluster_id = clusters.id
        ORDER BY ABS(JULIANDAY(a.fetched_at) - JULIANDAY(clusters.created_at))
        LIMIT 1
    )
    WHERE primary_article_id IS NULL
      AND EXISTS (SELECT 1 FROM articles WHERE cluster_id = clusters.id)
""")
updated2 = cur.rowcount
conn.commit()

conn.close()
print(f"기존 클러스터 역추적 완료: {updated}개 (published_at 기준), {updated2}개 (fetched_at 기준 fallback)")
