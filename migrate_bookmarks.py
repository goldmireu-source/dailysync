"""user_bookmarks 테이블 생성.

실행: python migrate_bookmarks.py
  - user_bookmarks 테이블이 없으면 생성
  - 이미 존재하면 건너뜀 (idempotent)
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

    cur.execute("PRAGMA table_info(user_bookmarks)")
    if not cur.fetchall():
        cur.execute("""
            CREATE TABLE user_bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES admin_users(id),
                item_type VARCHAR(10) NOT NULL,
                item_id INTEGER NOT NULL,
                saved_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, item_type, item_id)
            )
        """)
        cur.execute("CREATE INDEX ix_user_bookmarks_user_id ON user_bookmarks(user_id)")
        print("✓ user_bookmarks 테이블 생성 완료.")
    else:
        print("✓ user_bookmarks 이미 존재. 건너뜀.")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
