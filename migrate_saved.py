"""Cluster + Paper 테이블에 saved_at 컬럼 추가.

실행: python migrate_saved.py
"""
import sqlite3
from pathlib import Path

DB_PATH = Path("data/app.db")


def main():
    if not DB_PATH.exists():
        print(f"DB 파일이 없습니다: {DB_PATH}")
        return

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    for table in ["clusters", "papers"]:
        cur.execute(f"PRAGMA table_info({table})")
        cols = [row[1] for row in cur.fetchall()]
        if "saved_at" in cols:
            print(f"✓ {table}.saved_at 이미 존재. 건너뜀.")
            continue
        cur.execute(f"ALTER TABLE {table} ADD COLUMN saved_at DATETIME")
        cur.execute(f"CREATE INDEX IF NOT EXISTS ix_{table}_saved_at ON {table}(saved_at)")
        conn.commit()
        print(f"✓ {table}.saved_at 컬럼 추가 + 인덱스 생성 완료.")

    conn.close()


if __name__ == "__main__":
    main()
