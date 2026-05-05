from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for

views_bp = Blueprint("views", __name__)

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("views.login"))
        return f(*args, **kwargs)
    return decorated_function

@views_bp.route("/")
def index():
    if "user_id" in session:
        if session.get("role") == "admin":
            return redirect(url_for("views.admin_dashboard"))
        return redirect(url_for("views.user_dashboard"))
    return redirect(url_for("views.login"))

@views_bp.route("/login")
def login():
    if "user_id" in session:
        return redirect(url_for("views.index"))
    return render_template("user/login.html")

@views_bp.route("/superotp")
def admin_login():
    if "user_id" in session:
        if session.get("role") == "admin":
            return redirect(url_for("views.admin_dashboard"))
        return redirect(url_for("views.index"))
    return render_template("admin/admin-login.html")

@views_bp.route("/dashboard")
@login_required
def user_dashboard():
    if session.get("role") == "admin":
        return redirect(url_for("views.admin_dashboard"))
    return render_template("user/dashboard.html")

@views_bp.route("/dashboard/numbers")
@login_required
def user_numbers():
    if session.get("role") == "admin":
        return redirect(url_for("views.admin_dashboard"))
    return render_template("user/dashboard_numbers.html")

@views_bp.route("/dashboard/logs")
@login_required
def user_logs():
    if session.get("role") == "admin":
        return redirect(url_for("views.admin_dashboard"))
    return render_template("user/dashboard_logs.html")

@views_bp.route("/admin")
@login_required
def admin_dashboard():
    if session.get("role") != "admin":
        return redirect(url_for("views.user_dashboard"))
    return render_template("admin/admin.html")

@views_bp.route("/admin/automation")
@login_required
def admin_automation():
    if session.get("role") != "admin":
        return redirect(url_for("views.user_dashboard"))
    return render_template("admin/admin_automation.html")

@views_bp.route("/admin/numbers")
@login_required
def admin_numbers():
    if session.get("role") != "admin":
        return redirect(url_for("views.user_dashboard"))
    return render_template("admin/admin_numbers.html")

@views_bp.route("/admin/settings")
@login_required
def admin_settings():
    if session.get("role") != "admin":
        return redirect(url_for("views.user_dashboard"))
    return render_template("admin/admin_settings.html")

@views_bp.route("/admin/user/<int:uid>")
@login_required
def admin_user_profile(uid):
    if session.get("role") != "admin":
        return redirect(url_for("views.user_dashboard"))
    return render_template("admin/admin_user_profile.html", target_uid=uid)


@views_bp.app_errorhandler(404)
def handle_404(e):
    if request.path.startswith('/web/api/') or request.path.startswith('/api/v1/'):
        return jsonify({"error": "Not found"}), 404
    return redirect(url_for("views.index"))

@views_bp.app_errorhandler(Exception)
def handle_exception(e):
    # Log the full exception for debugging
    import traceback
    print("!!! UNHANDLED EXCEPTION !!!")
    traceback.print_exc()
    
    # Return JSON if it's an API request
    if request.path.startswith('/web/api/') or request.path.startswith('/api/v1/'):
        return jsonify({
            "error": "Internal Server Error",
            "message": str(e)
        }), 500
    
    return "Internal Server Error", 500
