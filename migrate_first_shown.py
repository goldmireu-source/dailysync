"""Cluster 테이블에 first_shown_date 컬럼 추가.

실행: python migrate_first_shown.py
이미 컬럼이 있으면 OK 메시지 후 종료.
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

    cur.execute("PRAGMA table_info(clusters)")
    cols = [row[1] for row in cur.fetchall()]

    if "first_shown_date" in cols:
        print("✓ first_shown_date 컬럼이 이미 존재합니다. 마이그레이션 불필요.")
        conn.close()
        return

    cur.execute("ALTER TABLE clusters ADD COLUMN first_shown_date DATE")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_clusters_first_shown_date ON clusters(first_shown_date)")
    conn.commit()
    print("✓ first_shown_date 컬럼 추가 + 인덱스 생성 완료.")

    # 기존 클러스터 — 가장 오래된 article의 발행일(KST)로 first_shown_date 채움
    # 이렇게 하면 과거 페이지에서 보던 클러스터들이 그대로 그 날짜에 표시됨
    cur.execute("""
        UPDATE clusters
        SET first_shown_date = (
            SELECT DATE(MIN(a.published_at), '+9 hours')
            FROM articles a
            WHERE a.cluster_id = clusters.id
        )
        WHERE first_shown_date IS NULL
    """)
    n = cur.rowcount
    conn.commit()
    print(f"  기존 {n}개 클러스터 first_shown_date 자동 설정 (가장 오래된 기사 발행일 KST 기준).")

    conn.close()


if __name__ == "__main__":
    main()
