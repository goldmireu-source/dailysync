"""tech_posts 테이블 생성/업데이트 (기업 기술블로그 "핫한 글" 트랙).

실행: python migrate_techpost.py
  - 최초 실행: tech_posts 생성
  - 재실행(이미 테이블 있음): 신규 컬럼만 추가 (idempotent)
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

    cur.execute("PRAGMA table_info(tech_posts)")
    existing_cols = {row[1] for row in cur.fetchall()}

    if not existing_cols:
        cur.execute("""
            CREATE TABLE tech_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                blog VARCHAR(50) NOT NULL,
                url VARCHAR(1000) NOT NULL,
                url_hash VARCHAR(64) NOT NULL UNIQUE,
                title VARCHAR(500) NOT NULL,
                description TEXT,
                image_url VARCHAR(1000),
                published_at DATETIME,
                fetched_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                hot_score FLOAT DEFAULT 0.0,
                mentioned_by JSON,
                pinned_featured BOOLEAN NOT NULL DEFAULT 0,
                pinned_at DATETIME,
                summary_ko TEXT,
                key_points JSON,
                summary_dirty BOOLEAN NOT NULL DEFAULT 1,
                hidden_at DATETIME,
                saved_at DATETIME
            )
        """)
        cur.execute("CREATE INDEX ix_tech_posts_blog ON tech_posts(blog)")
        cur.execute("CREATE INDEX ix_tech_posts_url_hash ON tech_posts(url_hash)")
        cur.execute("CREATE INDEX ix_tech_posts_published_at ON tech_posts(published_at)")
        cur.execute("CREATE INDEX ix_tech_posts_hot_score ON tech_posts(hot_score)")
        cur.execute("CREATE INDEX ix_tech_posts_hidden_at ON tech_posts(hidden_at)")
        cur.execute("CREATE INDEX ix_tech_posts_saved_at ON tech_posts(saved_at)")
        print("tech_posts 테이블 생성 완료")
    else:
        new_cols = {
            "hot_score": "ALTER TABLE tech_posts ADD COLUMN hot_score FLOAT DEFAULT 0.0",
            "mentioned_by": "ALTER TABLE tech_posts ADD COLUMN mentioned_by JSON",
            "pinned_featured": "ALTER TABLE tech_posts ADD COLUMN pinned_featured BOOLEAN NOT NULL DEFAULT 0",
            "pinned_at": "ALTER TABLE tech_posts ADD COLUMN pinned_at DATETIME",
            "summary_ko": "ALTER TABLE tech_posts ADD COLUMN summary_ko TEXT",
            "key_points": "ALTER TABLE tech_posts ADD COLUMN key_points JSON",
            "summary_dirty": "ALTER TABLE tech_posts ADD COLUMN summary_dirty BOOLEAN NOT NULL DEFAULT 1",
            "hidden_at": "ALTER TABLE tech_posts ADD COLUMN hidden_at DATETIME",
            "saved_at": "ALTER TABLE tech_posts ADD COLUMN saved_at DATETIME",
        }
        added = []
        for col, sql in new_cols.items():
            if col not in existing_cols:
                cur.execute(sql)
                added.append(col)
        if added:
            print(f"tech_posts 컬럼 추가: {', '.join(added)}")
        else:
            print("tech_posts 이미 최신 상태.")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
