"""SQLite DB 자동 백업 — 파괴적 작업(cleanup, init --reset) 직전 보존.

복구: `data/backups/app.db.<timestamp>` 를 `data/app.db` 로 복사.
"""
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path("data/app.db")
BACKUP_DIR = Path("data/backups")


def backup_database(keep_days: int = 7) -> dict:
    """data/app.db 를 data/backups/app.db.YYYYMMDD-HHMMSS 로 복사.

    sqlite3 backup API 사용 → 라이브 DB에서 안전 (WAL/락 처리 OK).
    keep_days 보다 mtime 오래된 백업 파일은 삭제.
    """
    if not DB_PATH.exists():
        logger.warning(f"백업 스킵 — DB 파일 없음: {DB_PATH}")
        return {"backup": None, "size_mb": 0, "removed_old": 0}

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = BACKUP_DIR / f"app.db.{ts}"

    src = sqlite3.connect(str(DB_PATH))
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    size_mb = target.stat().st_size / (1024 * 1024)

    cutoff = datetime.now() - timedelta(days=keep_days)
    removed = 0
    for f in BACKUP_DIR.glob("app.db.*"):
        try:
            if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            pass

    logger.info(f"DB 백업 생성: {target.name} ({size_mb:.1f} MB), 만료 정리 {removed}개")
    return {"backup": target.name, "size_mb": round(size_mb, 2), "removed_old": removed}


if __name__ == "__main__":
    info = backup_database(keep_days=30)
    print(f"백업: data/backups/{info['backup']} ({info['size_mb']} MB), 정리 {info['removed_old']}개")
