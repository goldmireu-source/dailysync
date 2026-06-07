"""clusters 테이블에 primary_article_id 컬럼 추가 (없을 때만).

실행:
    python migrate_primary_article.py
"""
import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(__file__), "app.db")

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cols = [row[1] for row in cur.execute("PRAGMA table_info(clusters)").fetchall()]
if "primary_article_id" not in cols:
    cur.execute("ALTER TABLE clusters ADD COLUMN primary_article_id INTEGER NULL")
    conn.commit()
    print("primary_article_id 컬럼 추가 완료")
else:
    print("primary_article_id 컬럼 이미 존재")

conn.close()
