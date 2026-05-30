"""Initialize the database and seed Source rows from data/sources.yaml.

Usage:
    python init_db.py            # 스키마 생성 + 출처 시드 (기존 데이터 유지)
    python init_db.py --reset    # 모든 테이블 drop 후 재생성
"""
import argparse
from pathlib import Path

import yaml

from app import create_app
from models import db, Source

BASE_DIR = Path(__file__).resolve().parent
SOURCES_FILE = BASE_DIR / "data" / "sources.yaml"


def load_sources():
    if not SOURCES_FILE.exists():
        print(f"[WARN] sources file not found: {SOURCES_FILE}")
        return []
    with open(SOURCES_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or []
    return data


def seed_sources():
    items = load_sources()
    created, updated = 0, 0
    for item in items:
        active = item.get("active", True)
        existing = Source.query.filter_by(rss_url=item["url"]).first()
        if existing:
            existing.name = item["name"]
            existing.lang = item["lang"]
            existing.tier = item["tier"]
            existing.needs_ai_filter = item.get("needs_ai_filter", False)
            existing.active = active
            updated += 1
        else:
            db.session.add(Source(
                name=item["name"],
                rss_url=item["url"],
                lang=item["lang"],
                tier=item["tier"],
                needs_ai_filter=item.get("needs_ai_filter", False),
                active=active,
            ))
            created += 1
    db.session.commit()
    print(f"[seed] sources — created: {created}, updated: {updated}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="Drop all tables first")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        if args.reset:
            confirm = input("⚠️  Drop ALL tables? Type 'yes' to confirm: ")
            if confirm.strip().lower() != "yes":
                print("Aborted.")
                return
            # drop_all 직전 안전 백업 (30일 보존) — 사고/오발에 대비.
            from jobs.backup import backup_database
            info = backup_database(keep_days=30)
            if info["backup"]:
                print(f"[backup] 안전 복사 생성: data/backups/{info['backup']} ({info['size_mb']} MB)")
            db.drop_all()
            print("[reset] dropped all tables")

        db.create_all()
        print("[init] schema created")
        seed_sources()

        sources = Source.query.order_by(Source.tier, Source.id).all()
        print(f"\n총 {len(sources)}개 출처 등록됨:")
        for s in sources:
            flag = "[필터]" if s.needs_ai_filter else "[전수]"
            print(f"  T{s.tier} {flag} {s.name:<30} {s.rss_url}")


if __name__ == "__main__":
    main()
