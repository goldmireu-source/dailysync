"""Paper 테이블에 title_ko 컬럼 추가 (Day 9).

실행: python migrate_paper_title_ko.py
이미 컬럼이 있으면 OK 메시지 후 종료.
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

    # 기존 컬럼 확인
    cur.execute("PRAGMA table_info(papers)")
    cols = [row[1] for row in cur.fetchall()]

    if "title_ko" in cols:
        print("✓ title_ko 컬럼이 이미 존재합니다. 마이그레이션 불필요.")
        conn.close()
        return

    # 컬럼 추가
    cur.execute("ALTER TABLE papers ADD COLUMN title_ko VARCHAR(500)")
    conn.commit()
    print("✓ title_ko 컬럼 추가 완료.")

    # 기존 데이터에 대해서는 다음 요약 잡 실행 시 채워짐
    cur.execute("SELECT COUNT(*) FROM papers WHERE summary_ko IS NOT NULL")
    n = cur.fetchone()[0]
    print(f"  요약 완료 논문 {n}편 — title_ko는 NULL 상태. 다음 요약 잡에서 갱신됩니다.")
    print("  또는 'python migrate_paper_title_ko.py --resummarize' 로 즉시 재요약 (Claude 호출).")

    conn.close()


def resummarize():
    """기존 요약 완료된 논문들의 title_ko를 즉시 채우기."""
    from app import create_app
    from models import db, Paper

    app = create_app(with_scheduler=False)
    with app.app_context():
        papers = Paper.query.filter(
            Paper.summary_ko.isnot(None),
            Paper.title_ko.is_(None),
        ).all()

        if not papers:
            print("재요약할 논문이 없습니다.")
            return

        print(f"{len(papers)}편 재요약 중...")

        from services.claude import generate_json
        for p in papers:
            try:
                prompt = (
                    f"다음 영문 논문 제목을 자연스러운 한국어 학술 제목으로 번역해 주세요. "
                    f"고유명사·약어·모델명은 영문 그대로 유지. \n\n"
                    f"원문: {p.title}\n\n"
                    f'JSON 형식으로만 답하세요: {{"title_ko": "<한국어 번역 한 줄>"}}'
                )
                result = generate_json(prompt)
                title_ko = (result.get("title_ko") or "").strip().strip('"').strip("'")
                if not title_ko:
                    print(f"  ⚠ {p.arxiv_id}: 빈 응답, 건너뜀")
                    continue
                p.title_ko = title_ko
                db.session.commit()
                print(f"  ✓ {p.arxiv_id}: {title_ko}")
            except Exception as e:
                print(f"  ✗ {p.arxiv_id}: {e}")
                db.session.rollback()

        print("완료.")


if __name__ == "__main__":
    import sys
    if "--resummarize" in sys.argv:
        main()  # 컬럼 보장
        resummarize()
    else:
        main()
