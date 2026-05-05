import os
from flask import Blueprint, request, jsonify, session
from werkzeug.security import check_password_hash, generate_password_hash
from core.db import get_db_connection, release_db_connection
from core.auth import login_required, admin_required
from core.utils import (
    serialize_user, extract_numbers, build_dashboard_payload, 
    generate_api_key, sanitize_role
)

web_api_bp = Blueprint("web_api", __name__, url_prefix="/web/api")

@web_api_bp.route("/health", methods=["GET"])
def health_check():
    status = {"status": "ok", "database_connected": False}
    try:
        conn = get_db_connection()
        release_db_connection(conn)
        status["database_connected"] = True
    except Exception:
        pass
    return jsonify(status)

@web_api_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = data.get("username")
    password = data.get("password")

    if not username or not password:
        return jsonify({"error": "Missing credentials"}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        cursor.close()

        if user and check_password_hash(user["password_hash"], password):
            if not user["is_active"]:
                return jsonify({"error": "Account is disabled"}), 403
            if user["role"] == "admin":
                return jsonify({"error": "Admins must login via the Admin Portal"}), 403
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session.permanent = True
            return jsonify({"success": True, "user": serialize_user(user)})

        return jsonify({"error": "Invalid username or password"}), 401
    finally:
        release_db_connection(conn)

@web_api_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True})

@web_api_bp.route("/stats", methods=["GET"])
@login_required
def get_stats(user):
    scoped_user_id = request.args.get("user_id", default=user["id"], type=int)
    if user["role"] != "admin":
        scoped_user_id = user["id"]
    return jsonify(build_dashboard_payload(user, scoped_user_id))

@web_api_bp.route("/users/<int:target_user_id>/config", methods=["POST"])
@login_required
def update_user_config(user, target_user_id):
    data = request.get_json(silent=True) or {}
    
    # SECURITY FIX: Only Admins can change 'use_admin_numbers' or other core restrictions
    if "use_admin_numbers" in data:
        if user["role"] != "admin":
            return jsonify({"error": "Security Breach: Only administrators can modify number pool restrictions."}), 403
    
    if user["role"] != "admin" and user["id"] != target_user_id:
        return jsonify({"error": "Unauthorized access"}), 403
        
    use_admin = 1 if data.get("use_admin_numbers") else 0
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET use_admin_numbers = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (use_admin, target_user_id),
        )
        conn.commit()

        cursor.execute("SELECT * FROM users WHERE id = %s", (target_user_id,))
        updated = cursor.fetchone()
        cursor.close()
        return jsonify({"success": True, "user": serialize_user(updated)})
    finally:
        release_db_connection(conn)

@web_api_bp.route("/add-numbers", methods=["POST"])
@login_required
def add_numbers(user):
    data = request.get_json(silent=True) or {}
    raw_text = data.get("numbers", "")
    target_user_id = data.get("user_id", user["id"])

    if user["role"] != "admin":
        target_user_id = user["id"]
        if user.get("use_admin_numbers"):
            return jsonify({"error": "You are restricted to use Admin's numbers only."}), 403

    phones = extract_numbers(raw_text)
    if not phones:
        return jsonify({"error": "No valid numbers found"}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        added = 0
        for p in phones:
            try:
                cursor.execute(
                    "INSERT INTO numbers (user_id, phone_number, status) VALUES (%s, %s, 'READY') ON CONFLICT (phone_number) DO NOTHING",
                    (target_user_id, p),
                )
                if cursor.rowcount > 0:
                    added += 1
            except:
                pass
        conn.commit()
        cursor.close()
        return jsonify({"success": True, "added": added})
    finally:
        release_db_connection(conn)

@web_api_bp.route("/clear-numbers", methods=["POST"])
@login_required
def clear_numbers(user):
    data = request.get_json(silent=True) or {}
    target_user_id = data.get("user_id", user["id"])
    if user["role"] != "admin":
        target_user_id = user["id"]

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM numbers WHERE user_id = %s", (target_user_id,))
        conn.commit()
        cursor.close()
        return jsonify({"success": True})
    finally:
        release_db_connection(conn)

@web_api_bp.route("/numbers", methods=["GET"])
@login_required
def get_numbers(user):
    target_user_id = request.args.get("user_id", default=user["id"], type=int)
    if user["role"] != "admin":
        target_user_id = user["id"]

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # If user uses admin numbers, also fetch admin pool numbers tagged for this user
        user_tag_w = f"u{target_user_id}_%"
        cursor.execute(
            """
            SELECT id, phone_number, status, device_id, updated_at
            FROM numbers
            WHERE user_id = %s
            UNION ALL
            SELECT id, phone_number, status, device_id, updated_at
            FROM numbers
            WHERE user_id IN (SELECT id FROM users WHERE role='admin' LIMIT 1)
              AND device_id LIKE %s
            ORDER BY updated_at DESC
            LIMIT 100
            """,
            (target_user_id, user_tag_w),
        )
        rows = cursor.fetchall()
        cursor.close()
        return jsonify({"success": True, "numbers": [dict(r) for r in rows]})
    finally:
        release_db_connection(conn)


@web_api_bp.route("/logs", methods=["GET"])
@login_required
def get_logs(user):
    """Activity Logs page uses this endpoint — was missing, causing blank logs page."""
    target_user_id = request.args.get("user_id", default=user["id"], type=int)
    if user["role"] != "admin":
        target_user_id = user["id"]

    limit = request.args.get("limit", default=100, type=int)
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, device_id, level, phone_number, message,
                   created_at AT TIME ZONE 'UTC' AS created_at
            FROM bot_logs
            WHERE user_id = %s
            ORDER BY id DESC
            LIMIT %s
            """,
            (target_user_id, limit),
        )
        rows = cursor.fetchall()
        cursor.close()
        return jsonify({"success": True, "logs": [dict(r) for r in rows]})
    finally:
        release_db_connection(conn)

@web_api_bp.route("/admin/users/create", methods=["POST"])
@admin_required
def create_user(user):
    data = request.get_json(silent=True) or {}
    username = data.get("username")
    full_name = data.get("full_name") or username  # fallback to username if full_name missing
    password = data.get("password")
    role = sanitize_role(data.get("role", "user"))
    balance = float(data.get("balance", 0.0))

    if not username or not password:
        return jsonify({"success": False, "error": "Missing required fields"}), 400

    api_key = generate_api_key()
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
        if cursor.fetchone():
            return jsonify({"success": False, "error": "Username already exists"}), 400
        cursor.execute(
            """
            INSERT INTO users (username, full_name, password_hash, api_key, role, balance, use_admin_numbers)
            VALUES (%s, %s, %s, %s, %s, %s, 1)
            """,
            (username, full_name, generate_password_hash(password), api_key, role, balance)
        )
        conn.commit()
        cursor.close()
        return jsonify({"success": True, "message": "User created successfully"})
    finally:
        release_db_connection(conn)

@web_api_bp.route("/admin/users/<int:uid>/reset-key", methods=["POST"])
@admin_required
def reset_user_key(user, uid):
    new_key = generate_api_key()
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET api_key = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (new_key, uid),
        )
        conn.commit()
        cursor.execute("SELECT * FROM users WHERE id = %s", (uid,))
        updated = cursor.fetchone()
        cursor.close()
        return jsonify({"success": True, "api_key": new_key, "user": serialize_user(updated)})
    finally:
        release_db_connection(conn)

@web_api_bp.route("/admin/users/<int:uid>/status", methods=["POST"])
@admin_required
def toggle_user_status(user, uid):
    data = request.get_json(silent=True) or {}
    is_active = 1 if data.get("is_active") else 0
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET is_active = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (is_active, uid),
        )
        conn.commit()
        cursor.execute("SELECT * FROM users WHERE id = %s", (uid,))
        updated = cursor.fetchone()
        cursor.close()
        return jsonify({"success": True, "user": serialize_user(updated)})
    finally:
        release_db_connection(conn)

@web_api_bp.route("/admin/users/<int:uid>/delete", methods=["DELETE"])
@admin_required
def delete_user(user, uid):
    # Prevent deleting the main admin
    if uid == 1 or uid == user["id"]:
        return jsonify({"success": False, "error": "Cannot delete master admin or yourself."}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # Clean up related records before deleting user (to avoid FK constraints if not cascaded)
        cursor.execute("DELETE FROM user_stats WHERE user_id = %s", (uid,))
        cursor.execute("DELETE FROM bot_status WHERE user_id = %s", (uid,))
        cursor.execute("DELETE FROM bot_logs WHERE user_id = %s", (uid,))
        cursor.execute("DELETE FROM numbers WHERE user_id = %s", (uid,))
        
        # Finally delete the user
        cursor.execute("DELETE FROM users WHERE id = %s", (uid,))
        deleted_count = cursor.rowcount
        conn.commit()
        cursor.close()
        
        if deleted_count > 0:
            return jsonify({"success": True, "message": "User deleted successfully"})
        else:
            return jsonify({"success": False, "error": "User not found"}), 404
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        release_db_connection(conn)

@web_api_bp.route("/admin/users", methods=["GET"])
@admin_required
def list_users(user):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, full_name, role, is_active, balance, use_admin_numbers, is_tester, created_at FROM users ORDER BY id DESC")
        users = cursor.fetchall()
        cursor.close()
        return jsonify({"success": True, "users": [dict(u) for u in users]})
    finally:
        release_db_connection(conn)

@web_api_bp.route("/admin/users/<int:uid>/balance", methods=["POST"])
@admin_required
def update_user_balance(user, uid):
    data = request.get_json(silent=True) or {}
    new_balance = float(data.get("balance", 0.0))
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET balance = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (new_balance, uid),
        )
        conn.commit()
        cursor.execute("SELECT id, username, full_name, role, is_active, balance FROM users WHERE id = %s", (uid,))
        updated = cursor.fetchone()
        cursor.close()
        return jsonify({"success": True, "user": dict(updated)})
    finally:
        release_db_connection(conn)


@web_api_bp.route("/admin/users/<int:uid>/profile", methods=["GET"])
@admin_required
def user_profile(user, uid):
    from core.utils import build_dashboard_payload
    try:
        # Pass include_users=False to skip the heavy admin users aggregation query
        payload = build_dashboard_payload(user, uid, include_users=False)
        return jsonify({"success": True, "profile": payload})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@web_api_bp.route("/admin/notifications", methods=["POST"])
@admin_required
def create_notification(user):
    data = request.get_json(silent=True) or {}
    message = data.get("message")
    msg_type = data.get("type", "info")
    
    if not message:
        return jsonify({"success": False, "error": "Message is required"}), 400
        
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # Deactivate old notifications
        cursor.execute("UPDATE notifications SET is_active = 0")
        
        # Insert new notification
        cursor.execute(
            "INSERT INTO notifications (message, type, is_active) VALUES (%s, %s, 1)",
            (message, msg_type)
        )
        conn.commit()
        cursor.close()
        return jsonify({"success": True})
    finally:
        release_db_connection(conn)

@web_api_bp.route("/notifications", methods=["GET"])
@login_required
def get_notifications(user):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT message, type, created_at FROM notifications WHERE is_active = 1 ORDER BY id DESC LIMIT 1")
        notif = cursor.fetchone()
        cursor.close()
        return jsonify({"success": True, "notification": dict(notif) if notif else None})
    finally:
        release_db_connection(conn)

@web_api_bp.route("/admin/settings", methods=["GET"])
@admin_required
def get_settings(user):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT setting_key, setting_value FROM system_settings")
        settings = cursor.fetchall()
        cursor.close()
        return jsonify({"success": True, "settings": {row["setting_key"]: row["setting_value"] for row in settings}})
    finally:
        release_db_connection(conn)

@web_api_bp.route("/admin/settings", methods=["POST"])
@admin_required
def update_settings(user):
    data = request.get_json(silent=True) or {}
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        for k, v in data.items():
            cursor.execute(
                "INSERT INTO system_settings (setting_key, setting_value) VALUES (%s, %s) ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value",
                (k, str(v))
            )
            # If we updated the script, increment the version
            if k == "automation_script":
                cursor.execute(
                    "UPDATE system_settings SET setting_value = (CAST(setting_value AS INTEGER) + 1)::text WHERE setting_key = 'script_version'"
                )
            if k == "test_automation_script":
                cursor.execute(
                    "UPDATE system_settings SET setting_value = (CAST(setting_value AS INTEGER) + 1)::text WHERE setting_key = 'test_script_version'"
                )
        conn.commit()
        cursor.close()
        return jsonify({"success": True})
    finally:
        release_db_connection(conn)

@web_api_bp.route("/admin/users/<int:uid>/tester", methods=["POST"])
@admin_required
def toggle_user_tester(user, uid):
    data = request.get_json(silent=True) or {}
    is_tester_val = 1 if data.get("is_tester") else 0
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_tester = %s WHERE id = %s", (is_tester_val, uid))
        conn.commit()
        cursor.close()
        return jsonify({"success": True})
    finally:
        release_db_connection(conn)
