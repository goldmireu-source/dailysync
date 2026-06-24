"""Source.feed_type 칼럼 추가 마이그레이션.

실행:
    python migrate_feed_type.py
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "app.db"


def main():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cols = [row[1] for row in cur.execute("PRAGMA table_info(sources)")]
    if "feed_type" in cols:
        print("[migrate] feed_type 칼럼 이미 존재 — 스킵")
    else:
        cur.execute("ALTER TABLE sources ADD COLUMN feed_type VARCHAR(20) NOT NULL DEFAULT 'rss'")
        con.commit()
        print("[migrate] feed_type 칼럼 추가 완료")
    con.close()


if __name__ == "__main__":
    main()
