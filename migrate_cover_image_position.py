"""clusters.cover_image_position 컬럼 추가 마이그레이션."""
import sqlite3, pathlib

DB = pathlib.Path("news.db")
conn = sqlite3.connect(DB)
cols = {r[1] for r in conn.execute("PRAGMA table_info(clusters)")}
if "cover_image_position" not in cols:
    conn.execute("ALTER TABLE clusters ADD COLUMN cover_image_position VARCHAR(32)")
    conn.commit()
    print("cover_image_position 컬럼 추가 완료")
else:
    print("이미 존재함")
conn.close()
