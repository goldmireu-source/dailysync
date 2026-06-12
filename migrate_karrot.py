"""karrot_posts 테이블 생성/업데이트 + karrot_applications 생성.

실행: python migrate_karrot.py
  - 최초 실행: karrot_posts 생성 + karrot_applications 생성
  - 재실행(이미 테이블 있음): 신규 컬럼 추가 + karrot_applications 없으면 생성
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

    # ── karrot_posts 생성 또는 신규 컬럼 추가 ──────────────────────────────
    cur.execute("PRAGMA table_info(karrot_posts)")
    existing_cols = {row[1] for row in cur.fetchall()}

    if not existing_cols:
        cur.execute("""
            CREATE TABLE karrot_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_type VARCHAR(10) NOT NULL DEFAULT 'share',
                title VARCHAR(100) NOT NULL,
                content TEXT,
                image_url VARCHAR(500),
                class_target INTEGER,
                status VARCHAR(10) NOT NULL DEFAULT 'open',
                completed_at DATETIME,
                matched_user_id INTEGER REFERENCES admin_users(id),
                author_id INTEGER NOT NULL REFERENCES admin_users(id),
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX ix_karrot_posts_created_at ON karrot_posts(created_at)")
        print("✓ karrot_posts 테이블 생성 완료.")
    else:
        new_cols = {
            "class_target":    "ALTER TABLE karrot_posts ADD COLUMN class_target INTEGER",
            "status":          "ALTER TABLE karrot_posts ADD COLUMN status VARCHAR(10) NOT NULL DEFAULT 'open'",
            "completed_at":    "ALTER TABLE karrot_posts ADD COLUMN completed_at DATETIME",
            "matched_user_id": "ALTER TABLE karrot_posts ADD COLUMN matched_user_id INTEGER REFERENCES admin_users(id)",
        }
        added = []
        for col, sql in new_cols.items():
            if col not in existing_cols:
                cur.execute(sql)
                added.append(col)
        if added:
            print(f"✓ karrot_posts 컬럼 추가: {', '.join(added)}")
        else:
            print("✓ karrot_posts 이미 최신 상태.")

    # ── karrot_applications 생성 ───────────────────────────────────────────
    cur.execute("PRAGMA table_info(karrot_applications)")
    if not cur.fetchall():
        cur.execute("""
            CREATE TABLE karrot_applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL REFERENCES karrot_posts(id),
                user_id INTEGER NOT NULL REFERENCES admin_users(id),
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(post_id, user_id)
            )
        """)
        cur.execute("CREATE INDEX ix_karrot_apps_post_id ON karrot_applications(post_id)")
        print("✓ karrot_applications 테이블 생성 완료.")
    else:
        print("✓ karrot_applications 이미 존재. 건너뜀.")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
