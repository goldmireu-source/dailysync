"""Flask 진입점 — 웹 대시보드 + 스케줄러 통합."""
import logging
import sys
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from flask import Flask, jsonify
from flask_login import LoginManager
from werkzeug.middleware.proxy_fix import ProxyFix

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
from models import db, JobRun
from web.routes import bp as web_bp

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=_LOG_FORMAT,
    datefmt="%H:%M:%S",
)

# 파일 로그: 자정 rotation, 7일 보관 (실패 원인 추적용)
_log_dir = Path(__file__).resolve().parent / "logs"
_log_dir.mkdir(exist_ok=True)
_file_handler = TimedRotatingFileHandler(
    _log_dir / "app.log",
    when="midnight",
    backupCount=7,
    encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
logging.getLogger().addHandler(_file_handler)

# werkzeug HTTP 요청 로그는 WARNING 이상만 (폴링 도배 방지)
# 잡 로그는 그대로 보임
logging.getLogger("werkzeug").setLevel(logging.WARNING)


def create_app(config_class=Config, with_scheduler: bool = True) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    # 리버스 프록시(nginx) 뒤에서 X-Forwarded-For 신뢰 (1홉)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)

    # Flask-Login 초기화
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = "web.admin_login"  # type: ignore[assignment]

    @login_manager.user_loader
    def load_user(user_id: str):
        from models import AdminUser
        return db.session.get(AdminUser, int(user_id))

    app.register_blueprint(web_bp)

    @app.context_processor
    def inject_globals():
        return {"now_year": datetime.now().year}

    @app.route("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.errorhandler(404)
    def handle_404(e):
        return jsonify({"error": "not_found"}), 404

    @app.errorhandler(500)
    def handle_500(e):
        return jsonify({"error": "internal_server_error"}), 500

    # 스키마 보장 (JobRun 추가됐을 수 있음)
    with app.app_context():
        db.create_all()
        # 앱 재시작 전 orphan 상태(queued/running) 잡 → failed 로 정리
        orphans = JobRun.query.filter(JobRun.status.in_(["queued", "running"])).all()
        if orphans:
            for run in orphans:
                run.status = "failed"
                run.finished_at = datetime.utcnow()
                run.error = "앱 재시작으로 중단됨"
            db.session.commit()
            logging.getLogger(__name__).info(
                "시작 시 orphan JobRun %d건 → failed 처리", len(orphans)
            )

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
