from flask import Blueprint, current_app, jsonify, render_template

web_bp = Blueprint("web", __name__)


def _monitor():
    return current_app.config["monitor_service"]


@web_bp.route("/")
def index():
    return render_template("index.html")


@web_bp.route("/api/status")
def api_status():
    return jsonify(_monitor().get_status())


@web_bp.route("/api/history")
def api_history():
    return jsonify(_monitor().get_history_last_24h())
