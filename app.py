"""Flask 진입점 — 웹 대시보드 + 스케줄러 통합."""
import logging
import sys
from datetime import datetime

from flask import Flask

# Windows 콘솔(cp949) 에서 print 가 한글/이모지 인코딩 실패하지 않도록 stdout/stderr UTF-8 강제.
# 잡 로그의 ✓/❌/⭐ 같은 문자가 cp949 로 인코딩 안 되면 print 가 예외를 던져
# summarize_pending 의 stats["failed"] 가 잘못 증가하는 부작용이 있었음.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from config import Config
from models import db
from web.routes import bp as web_bp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
# werkzeug HTTP 요청 로그는 WARNING 이상만 (폴링 도배 방지)
# 잡 로그는 그대로 보임
logging.getLogger("werkzeug").setLevel(logging.WARNING)


def create_app(config_class=Config, with_scheduler: bool = True) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    app.register_blueprint(web_bp)

    @app.context_processor
    def inject_globals():
        return {"now_year": datetime.now().year}

    @app.route("/healthz")
    def healthz():
        return {"status": "ok"}

    # 스키마 보장 (JobRun 추가됐을 수 있음)
    with app.app_context():
        db.create_all()

    # 스케줄러 시작
    if with_scheduler:
        from scheduler import init_scheduler
        init_scheduler(app)

    return app


if __name__ == "__main__":
    # debug=False 권장 (reloader 가 스케줄러 두 번 띄우는 문제 회피).
    # 코드 수정 시 수동 재시작.
    app = create_app()
    app.run(debug=False, port=5001, host="127.0.0.1")
