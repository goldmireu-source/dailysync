"""파티 기능 마이그레이션.

- admin_users 에 class_num 컬럼 추가
- parties / party_members / party_messages 테이블 생성

실행: python migrate_party.py
"""
from app import create_app
from models import db

app = create_app(with_scheduler=False)

with app.app_context():
    con = db.engine.raw_connection()
    cur = con.cursor()

    # ── admin_users.class_num ──────────────────────────────────────
    cols = [r[1] for r in cur.execute("PRAGMA table_info(admin_users)")]
    if "class_num" not in cols:
        cur.execute("ALTER TABLE admin_users ADD COLUMN class_num INTEGER")
        print("admin_users.class_num 추가됨")
    else:
        print("admin_users.class_num 이미 존재")

    con.commit()
    con.close()

    # ── parties / party_members / party_messages ──────────────────
    db.create_all()
    print("parties / party_members / party_messages 테이블 생성(또는 기존 유지)")
    print("마이그레이션 완료")
