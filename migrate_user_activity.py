"""user_activity 테이블 마이그레이션 — 멱등성 보장."""
from app import create_app
from models import db
from sqlalchemy import text

app = create_app(with_scheduler=False)
with app.app_context():
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS user_activity (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER REFERENCES admin_users(id) ON DELETE SET NULL,
            username   VARCHAR(14),
            action     VARCHAR(40) NOT NULL,
            ip         VARCHAR(45),
            detail     VARCHAR(200),
            created_at DATETIME DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now'))
        )
    """))
    db.session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_user_activity_user_id   ON user_activity(user_id)"
    ))
    db.session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_user_activity_created_at ON user_activity(created_at DESC)"
    ))
    db.session.commit()
    print("✓ user_activity 테이블 마이그레이션 완료")
