"""tech_posts 테이블에 body/body_fetched_at/body_status 컬럼 추가.

RSS 티저만으로는 요약 입력이 너무 짧아(500자) key_points/summary_ko가
빈약해지는 문제 — Article.body와 동일한 방식(trafilatura)으로 본문을
가져와 요약 입력으로만 쓰기 위함 (렌더링은 여전히 금지, README 원칙 1).

실행: python migrate_techpost_body.py
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

    new_cols = {
        "body": "ALTER TABLE tech_posts ADD COLUMN body TEXT",
        "body_fetched_at": "ALTER TABLE tech_posts ADD COLUMN body_fetched_at DATETIME",
        "body_status": "ALTER TABLE tech_posts ADD COLUMN body_status VARCHAR(20) NOT NULL DEFAULT 'pending'",
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
