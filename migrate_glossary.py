"""glossary_terms 테이블 생성 + 시드 데이터 로딩.

실행:
    python migrate_glossary.py        # 테이블 생성 + 시드 로딩
    python migrate_glossary.py --reset  # 기존 시드 갱신 (auto 항목은 유지)
"""
import json
import sys
from pathlib import Path

from app import create_app
from models import db, GlossaryTerm

SEED_PATH = Path("data/glossary_seed.json")


def load_seed(reset: bool = False):
    if not SEED_PATH.exists():
        print(f"⚠ 시드 파일 없음: {SEED_PATH}")
        return

    with open(SEED_PATH, encoding="utf-8") as f:
        seeds = json.load(f)

    print(f"시드 {len(seeds)}개 로딩...")

    app = create_app(with_scheduler=False)
    with app.app_context():
        db.create_all()  # 테이블 보장

        # reset 옵션: 기존 seed 항목 모두 삭제 후 재로딩 (auto/manual 항목 보존)
        if reset:
            n_del = GlossaryTerm.query.filter_by(source="seed").delete()
            db.session.commit()
            print(f"기존 seed {n_del}개 삭제")

        added, skipped = 0, 0
        for item in seeds:
            existing = GlossaryTerm.query.filter_by(term=item["term"]).first()
            if existing:
                skipped += 1
                continue
            entry = GlossaryTerm(
                term=item["term"],
                term_ko=item["term_ko"],
                aliases=item.get("aliases", []),
                explain_ko=item["explain_ko"],
                category=item.get("category", "general"),
                source="seed",
            )
            db.session.add(entry)
            added += 1

        db.session.commit()
        total = GlossaryTerm.query.count()
        print(f"✓ 시드 로딩 완료: 추가 {added}개, 건너뜀 {skipped}개")
        print(f"  전체 글로서리: {total}개")


if __name__ == "__main__":
    reset = "--reset" in sys.argv
    load_seed(reset=reset)
