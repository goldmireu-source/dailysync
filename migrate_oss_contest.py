"""2026 공개소프트웨어 개발자대회 — 멱등 삽입 마이그레이션.

이미 존재하면(url_hash 중복) 건너뜀.
"""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB = Path(__file__).parent / "data" / "app.db"
URL = "https://osscontest.kr/"


def run() -> None:
    url_hash = hashlib.sha256(URL.encode()).hexdigest()
    con = sqlite3.connect(DB)
    cur = con.cursor()

    cur.execute("SELECT id FROM contests WHERE url_hash=?", (url_hash,))
    if cur.fetchone():
        print("migrate_oss_contest: 이미 존재 — 건너뜀")
        con.close()
        return

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("""
        INSERT INTO contests
          (source, external_id, url, url_hash, title, host,
           image_url, category, field_tags, target, prize,
           start_at, deadline, posted_at, fetched_at,
           is_ai_relevant, summary_dirty,
           image_pos_x, image_pos_y, image_scale)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        "oss.kr",
        "oss:2026-developer-contest",
        URL, url_hash,
        "2026 공개소프트웨어 개발자대회",
        "과학기술정보통신부 · NIPA 정보통신산업진흥원",
        "https://www.oss.kr/uploads/html/img/academy_main_image.png",
        "공모전",
        json.dumps(["오픈소스", "AI", "IoT", "클라우드", "보안"], ensure_ascii=False),
        "국내외 학생(대학원생 포함) 및 일반인",
        "총 6,700만원 (대상 각 1,000만원)",
        "2026-06-15", "2026-07-17", "2026-06-15",
        now,
        1, 1,
        50.0, 50.0, 1.0,
    ))
    con.commit()
    cur.execute("SELECT id FROM contests WHERE url_hash=?", (url_hash,))
    new_id = cur.fetchone()[0]
    print(f"migrate_oss_contest: 삽입 완료 (id={new_id})")
    con.close()


if __name__ == "__main__":
    run()
