"""app_settings 테이블 생성 + 기본값 설정.

실행: python migrate_settings.py
  - app_settings 테이블이 없으면 생성
  - karrot_enabled 기본값 'false' 삽입 (이미 있으면 건너뜀)
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

    # 테이블 생성
    cur.execute("PRAGMA table_info(app_settings)")
    if not cur.fetchall():
        cur.execute("""
            CREATE TABLE app_settings (
                key VARCHAR(60) PRIMARY KEY,
                value VARCHAR(200) NOT NULL DEFAULT ''
            )
        """)
        print("✓ app_settings 테이블 생성 완료.")
    else:
        print("✓ app_settings 이미 존재.")

    # 기본값 삽입 (없을 때만)
    cur.execute("SELECT key FROM app_settings WHERE key='karrot_enabled'")
    if not cur.fetchone():
        cur.execute("INSERT INTO app_settings (key, value) VALUES ('karrot_enabled', 'false')")
        print("✓ karrot_enabled = false (기본값 삽입).")
    else:
        print("✓ karrot_enabled 이미 설정되어 있음. 건너뜀.")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
