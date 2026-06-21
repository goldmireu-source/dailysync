"""Paper.figure_url 컬럼 추가 마이그레이션."""
import sqlite3, pathlib

DB = pathlib.Path("data/app.db")

with sqlite3.connect(DB) as conn:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(papers)")}
    if "figure_url" not in cols:
        conn.execute("ALTER TABLE papers ADD COLUMN figure_url VARCHAR(1000)")
        print("papers.figure_url 컬럼 추가 완료")
    else:
        print("이미 존재 — 스킵")
