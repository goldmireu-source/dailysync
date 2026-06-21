"""Paper.pinned_featured / pinned_at 컬럼 추가 마이그레이션."""
import sqlite3, pathlib

DB = pathlib.Path("data/app.db")

with sqlite3.connect(DB) as conn:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(papers)")}
    added = []
    if "pinned_featured" not in cols:
        conn.execute("ALTER TABLE papers ADD COLUMN pinned_featured BOOLEAN NOT NULL DEFAULT 0")
        added.append("pinned_featured")
    if "pinned_at" not in cols:
        conn.execute("ALTER TABLE papers ADD COLUMN pinned_at DATETIME")
        added.append("pinned_at")
    if added:
        print(f"papers 컬럼 추가: {', '.join(added)}")
    else:
        print("이미 존재 — 스킵")
