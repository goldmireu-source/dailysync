"""Add hidden_at columns to clusters / papers (SQLite ALTER)."""
from sqlalchemy import text
from app import create_app
from models import db


def column_exists(table: str, column: str) -> bool:
    res = db.session.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in res)


def main():
    app = create_app(with_scheduler=False)
    with app.app_context():
        added = []
        for table in ("clusters", "papers"):
            if not column_exists(table, "hidden_at"):
                db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN hidden_at DATETIME"))
                added.append(table)
            else:
                print(f"[skip] {table}.hidden_at already exists")

        if added:
            db.session.commit()
            print(f"[ok] added hidden_at to: {', '.join(added)}")
        else:
            print("[ok] schema already up-to-date")


if __name__ == "__main__":
    main()
