from functools import wraps
from flask import request, jsonify, session
from .db import get_db_connection
from .utils import serialize_user

def get_user_by_api_key(api_key):
    if not api_key:
        return None
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE api_key = %s", (api_key,))
        user = cursor.fetchone()
        cursor.close()
        return user
    finally:
        from .db import release_db_connection
        release_db_connection(conn)

def verify_api_key(api_key):
    """Simple wrapper for API key verification, used by SocketIO."""
    user = get_user_by_api_key(api_key)
    if user and user["is_active"]:
        return user
    return None

def get_api_user_or_401():
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        return None, (jsonify({"error": "Missing API Key"}), 401)
    user = get_user_by_api_key(api_key)
    if not user:
        return None, (jsonify({"error": "Invalid API Key"}), 403)
    if not user["is_active"]:
        return None, (jsonify({"error": "User account is disabled"}), 403)
    return user, None

def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        cursor.close()
        return user
    finally:
        from .db import release_db_connection
        release_db_connection(conn)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = current_user()
        if not user:
            return jsonify({"error": "Login required"}), 401
        if not user["is_active"]:
            return jsonify({"error": "Account disabled"}), 403
        return f(user, *args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = current_user()
        if not user or user["role"] != "admin":
            return jsonify({"error": "Admin access required"}), 403
        return f(user, *args, **kwargs)
    return decorated_function
