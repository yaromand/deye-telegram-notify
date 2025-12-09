from flask import Flask

from app.web.routes import web_bp


def create_app(monitor_service) -> Flask:
    """
    Flask-приложение. MonitorService пробрасываем через config.
    """
    app = Flask(__name__, template_folder="../templates")
    app.config["monitor_service"] = monitor_service
    app.register_blueprint(web_bp)
    return app
