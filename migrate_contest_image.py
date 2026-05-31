"""contests 테이블에 이미지 표시 조정 컬럼 추가 (image_pos_x/pos_y/scale).

관리자가 업로드한 이미지의 타일 내 위치(object-position %)와 확대 배율 저장용.
실행: python migrate_contest_image.py
"""
import sqlite3
from pathlib import Path

DB_PATH = Path("data/app.db")

COLUMNS = [
    ("image_pos_x", "REAL DEFAULT 50.0"),
    ("image_pos_y", "REAL DEFAULT 50.0"),
    ("image_scale", "REAL DEFAULT 1.0"),
]


def main():
    if not DB_PATH.exists():
        print(f"DB 파일이 없습니다: {DB_PATH}")
        return

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(contests)")
    cols = [row[1] for row in cur.fetchall()]
    if not cols:
        print("contests 테이블이 없습니다. 앱을 한 번 실행해 테이블을 생성하세요.")
        conn.close()
        return

    for name, ddl in COLUMNS:
        if name in cols:
            print(f"✓ contests.{name} 이미 존재. 건너뜀.")
            continue
        cur.execute(f"ALTER TABLE contests ADD COLUMN {name} {ddl}")
        conn.commit()
        print(f"✓ contests.{name} 컬럼 추가 완료.")

    conn.close()


if __name__ == "__main__":
    main()
