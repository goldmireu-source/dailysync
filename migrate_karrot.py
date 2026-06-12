"""karrot_posts 테이블 생성 마이그레이션.

실행: python migrate_karrot.py
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

    cur.execute("PRAGMA table_info(karrot_posts)")
    if cur.fetchall():
        print("✓ karrot_posts 이미 존재. 건너뜀.")
        conn.close()
        return

    cur.execute("""
        CREATE TABLE karrot_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_type VARCHAR(10) NOT NULL DEFAULT 'share',
            title VARCHAR(100) NOT NULL,
            content TEXT,
            image_url VARCHAR(500),
            author_id INTEGER NOT NULL REFERENCES admin_users(id),
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("CREATE INDEX ix_karrot_posts_created_at ON karrot_posts(created_at)")
    conn.commit()
    print("✓ karrot_posts 테이블 + 인덱스 생성 완료.")
    conn.close()


if __name__ == "__main__":
    main()
