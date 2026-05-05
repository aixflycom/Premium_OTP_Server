from flask import Blueprint, request, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash
import uuid
from core.db import get_db_connection, release_db_connection

auth_api_bp = Blueprint("auth_api", __name__)

@auth_api_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    if not data or not data.get("username") or not data.get("password"):
        return jsonify({"success": False, "message": "Missing credentials"}), 400
        
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM users WHERE username = %s", (data["username"],))
        user = cursor.fetchone()
        
        if user and check_password_hash(user["password_hash"], data["password"]):
            if user["role"] == "admin":
                return jsonify({"success": False, "message": "Admins must login via the Admin Portal"}), 403
                
            if user["is_active"] == 0:
                return jsonify({"success": False, "message": "Account is disabled. Contact Admin."}), 403
                
            # Set session
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session.permanent = True
            
            return jsonify({
                "success": True, 
                "message": "Login successful", 
                "role": user["role"]
            })
        else:
            return jsonify({"success": False, "message": "Invalid username or password"}), 401
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        release_db_connection(conn)

@auth_api_bp.route("/admin-login", methods=["POST"])
def admin_login():
    data = request.get_json()
    if not data or not data.get("username") or not data.get("password"):
        return jsonify({"success": False, "message": "Missing credentials"}), 400
        
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM users WHERE username = %s AND role = 'admin'", (data["username"],))
        user = cursor.fetchone()
        
        if user and check_password_hash(user["password_hash"], data["password"]):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session.permanent = True
            
            return jsonify({
                "success": True, 
                "message": "Admin Login successful", 
                "role": user["role"]
            })
        else:
            return jsonify({"success": False, "message": "Invalid admin credentials or not an admin"}), 401
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        release_db_connection(conn)

@auth_api_bp.route("/register", methods=["POST"])
def register():
    data = request.get_json()
    if not data or not data.get("username") or not data.get("password") or not data.get("full_name"):
        return jsonify({"success": False, "message": "Missing required fields"}), 400
        
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Check if public signups are allowed
        cursor.execute("SELECT setting_value FROM system_settings WHERE setting_key = 'public_signup'")
        setting = cursor.fetchone()
        if setting and setting["setting_value"] != "true":
            return jsonify({"success": False, "message": "Signups are currently disabled by Admin."}), 403

        # Check if username exists
        cursor.execute("SELECT id FROM users WHERE username = %s", (data["username"],))
        if cursor.fetchone():
            return jsonify({"success": False, "message": "Username already exists"}), 400
            
        # Generate a unique API key for the new user
        new_api_key = str(uuid.uuid4())
        hashed_pw = generate_password_hash(data["password"])
        
        cursor.execute(
            """
            INSERT INTO users (username, full_name, password_hash, api_key, role, balance, use_admin_numbers)
            VALUES (%s, %s, %s, %s, 'user', 0.00, 1)
            RETURNING id, username, role
            """,
            (data["username"], data["full_name"], hashed_pw, new_api_key)
        )
        new_user = cursor.fetchone()
        conn.commit()
        
        # Auto login
        session["user_id"] = new_user["id"]
        session["username"] = new_user["username"]
        session["role"] = new_user["role"]
        session.permanent = True
        
        return jsonify({
            "success": True, 
            "message": "Registration successful",
            "role": new_user["role"]
        })
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        release_db_connection(conn)

@auth_api_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True, "message": "Logged out successfully"})

@auth_api_bp.route("/me", methods=["GET"])
def get_me():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Support both API key (for bots) and session (for web users)
        api_key = request.headers.get("X-API-Key")
        if api_key:
            cursor.execute(
                "SELECT id, username, full_name, api_key, role, balance, is_active, use_admin_numbers FROM users WHERE api_key = %s",
                (api_key.strip(),)
            )
        elif "user_id" in session:
            cursor.execute(
                "SELECT id, username, full_name, api_key, role, balance, is_active, use_admin_numbers FROM users WHERE id = %s",
                (session["user_id"],)
            )
        else:
            return jsonify({"success": False, "message": "Not authenticated"}), 401

        user = cursor.fetchone()
        if not user:
            return jsonify({"success": False, "message": "User not found"}), 404
        if not user["is_active"]:
            return jsonify({"success": False, "message": "Account disabled"}), 403
            
        # Get count of numbers
        cursor.execute("SELECT COUNT(*) AS count FROM numbers WHERE user_id = %s AND status = 'READY'", (user["id"],))
        user_dict = dict(user)
        user_dict["ready_count"] = cursor.fetchone()["count"]
        
        # Fetch latest active notification
        cursor.execute("SELECT message, type FROM notifications ORDER BY id DESC LIMIT 1")
        notif = cursor.fetchone()
        user_dict["notification"] = dict(notif) if notif else None
        
        resp = jsonify(user_dict)
        resp.headers['X-App-Version'] = '2.1-NOTIF'
        return resp
    finally:
        cursor.close()
        release_db_connection(conn)

