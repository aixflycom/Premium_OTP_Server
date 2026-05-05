import re
import time
import secrets
from .db import get_db_connection, release_db_connection

def row_to_dict(row):
    return dict(row) if row else None

def serialize_user(row):
    if not row:
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "full_name": row["full_name"],
        "api_key": row["api_key"],
        "role": row["role"],
        "is_active": bool(row["is_active"]),
        "use_admin_numbers": bool(row["use_admin_numbers"]),
        "balance": float(row["balance"] or 0.0),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }

def sanitize_role(role):
    return "admin" if str(role).lower() == "admin" else "user"

def generate_api_key():
    return f"superotp_{secrets.token_hex(16)}"

def extract_numbers(raw_text):
    return re.findall(r"\+?\d{10,15}", raw_text or "")

def upsert_bot_status(conn, user_id, device_id, bot_status=None, last_phone=None, last_message=None):
    if not device_id:
        device_id = "Unknown"
    
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, bot_status, last_phone, last_message FROM bot_status WHERE user_id = %s AND device_id = %s",
        (user_id, device_id),
    )
    current = cursor.fetchone()
    
    final_status = bot_status or (current["bot_status"] if current else "IDLE")
    final_phone = last_phone or (current["last_phone"] if current else None)
    final_message = last_message or (current["last_message"] if current else None)

    if current:
        cursor.execute(
            """
            UPDATE bot_status
            SET bot_status = %s, last_phone = %s, last_message = %s, updated_at = CURRENT_TIMESTAMP, last_seen = CURRENT_TIMESTAMP
            WHERE user_id = %s AND device_id = %s
            """,
            (final_status, final_phone, final_message, user_id, device_id),
        )
    else:
        cursor.execute(
            """
            INSERT INTO bot_status (user_id, device_id, bot_status, last_phone, last_message)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (user_id, device_id, final_status, final_phone, final_message),
        )
    cursor.close()
    
    # Real-time update via SocketIO
    try:
        from app import socketio
        socketio.emit('bot_status_update', {
            'user_id': user_id,
            'device_id': device_id,
            'bot_status': final_status,
            'last_phone': final_phone,
            'last_message': final_message,
            'updated_at': time.strftime("%Y-%m-%d %H:%M:%S")
        })
    except Exception: pass

def add_log_entry(conn, user_id, message, level="INFO", device_id=None, phone_number=None):
    if not message:
        return
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO bot_logs (user_id, device_id, level, phone_number, message)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (user_id, device_id, str(level or "INFO").upper(), phone_number, str(message).strip()),
    )
    cursor.close()
    
    # Real-time log via SocketIO
    try:
        from app import socketio
        socketio.emit('new_bot_log', {
            'user_id': user_id,
            'device_id': device_id,
            'level': str(level or "INFO").upper(),
            'phone_number': phone_number,
            'message': str(message).strip(),
            'created_at': time.strftime("%Y-%m-%d %H:%M:%S")
        })
    except Exception: pass

def increment_stat(conn, user_id, new_status):
    today = time.strftime("%Y-%m-%d")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO user_stats (user_id, date, success_count, fail_count) VALUES (%s, %s, 0, 0) ON CONFLICT (user_id, date) DO NOTHING",
        (user_id, today),
    )
    if new_status == "SENT":
        cursor.execute(
            "UPDATE user_stats SET success_count = success_count + 1 WHERE user_id = %s AND date = %s",
            (user_id, today),
        )
    elif new_status == "FAILED":
        cursor.execute(
            "UPDATE user_stats SET fail_count = fail_count + 1 WHERE user_id = %s AND date = %s",
            (user_id, today),
        )
    cursor.close()
    
    # Notify dashboard to refresh stats
    try:
        from app import socketio
        # Get latest ready count for this user
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) AS count FROM numbers WHERE user_id = %s AND status = 'READY'", (user_id,))
        ready_count = cursor.fetchone()["count"]
        cursor.close()
        
        socketio.emit('stats_update', {
            'user_id': user_id, 
            'date': today, 
            'status': new_status,
            'ready_count': ready_count
        })
    except Exception: pass

def build_dashboard_payload(user, scoped_user_id, include_users=True):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM users WHERE id = %s", (scoped_user_id,))
        scoped_user = cursor.fetchone()

        # ✅ Use user_stats for SENT/FAILED (works for admin pool users too)
        # numbers table only tracks own numbers; admin pool numbers are under admin's user_id
        cursor.execute(
            """
            SELECT
                COALESCE(SUM(success_count), 0) AS total_sent,
                COALESCE(SUM(fail_count), 0)    AS total_failed
            FROM user_stats WHERE user_id = %s
            """,
            (scoped_user_id,),
        )
        stats_row = cursor.fetchone()
        sent_count   = int(stats_row["total_sent"]   or 0)
        failed_count = int(stats_row["total_failed"] or 0)
        
        # Calculate READY count (own + admin pool if enabled)
        cursor.execute("SELECT COUNT(*) AS count FROM numbers WHERE user_id = %s AND status = 'READY'", (scoped_user_id,))
        own_ready = cursor.fetchone()["count"]
        
        admin_ready = 0
        if scoped_user.get("use_admin_numbers"):
            cursor.execute("SELECT COUNT(*) AS count FROM numbers n JOIN users u ON n.user_id = u.id WHERE u.role = 'admin' AND n.status = 'READY'")
            admin_ready = cursor.fetchone()["count"]
        
        ready_count = own_ready + admin_ready

        # IN_USE count still from numbers table (own + admin pool tagged for this user)
        cursor.execute(
            """
            SELECT COUNT(*) AS count FROM numbers
            WHERE user_id = %s AND status = 'IN_USE'
            UNION ALL
            SELECT COUNT(*) AS count FROM numbers n
            JOIN users u ON n.user_id = u.id
            WHERE u.role = 'admin'
              AND n.status = 'IN_USE'
              AND n.device_id LIKE %s
            """,
            (scoped_user_id, f"u{scoped_user_id}_%"),
        )
        in_use_rows  = cursor.fetchall()
        in_use_count = sum(r["count"] for r in in_use_rows)

        counters = {
            "ready":   ready_count,
            "sent":    sent_count,
            "failed":  failed_count,
            "in_use":  in_use_count,
            "total":   ready_count + sent_count + failed_count + in_use_count
        }

        cursor.execute(
            "SELECT date, success_count, fail_count FROM user_stats WHERE user_id = %s ORDER BY date DESC LIMIT 7",
            (scoped_user_id,),
        )
        chart_rows = cursor.fetchall()

        cursor.execute(
            """
            SELECT id, phone_number, status, device_id, created_at, updated_at
            FROM numbers
            WHERE user_id = %s
            ORDER BY updated_at DESC, id DESC
            LIMIT 50
            """,
            (scoped_user_id,),
        )
        number_rows = cursor.fetchall()

        cursor.execute(
            """
            SELECT id, device_id, level, phone_number, message, created_at
            FROM bot_logs
            WHERE user_id = %s
            ORDER BY id DESC
            LIMIT 60
            """,
            (scoped_user_id,),
        )
        log_rows = cursor.fetchall()

        cursor.execute(
            """
            SELECT device_id, bot_status, last_phone, last_message, updated_at, last_seen
            FROM bot_status
            WHERE user_id = %s
              AND bot_status != 'IDLE'
              AND COALESCE(last_seen, updated_at) >= NOW() - INTERVAL '30 seconds'
            ORDER BY updated_at DESC
            LIMIT 20
            """,
            (scoped_user_id,),
        )
        status_rows = cursor.fetchall()

        admin_users = []
        if user["role"] == "admin" and include_users:
            cursor.execute(
                """
                SELECT
                    u.id,
                    u.username,
                    u.full_name,
                    u.api_key,
                    u.role,
                    u.is_active,
                    u.created_at,
                    COALESCE(SUM(CASE WHEN n.status = 'READY' THEN 1 ELSE 0 END), 0) AS ready_count,
                    COALESCE(SUM(CASE WHEN n.status = 'SENT' THEN 1 ELSE 0 END), 0) AS sent_count,
                    COALESCE(SUM(CASE WHEN n.status = 'FAILED' THEN 1 ELSE 0 END), 0) AS failed_count,
                    COALESCE(SUM(CASE WHEN n.status = 'IN_USE' THEN 1 ELSE 0 END), 0) AS in_use_count
                FROM users u
                LEFT JOIN numbers n ON n.user_id = u.id
                GROUP BY u.id
                ORDER BY u.role DESC, u.username ASC
                """
            )
            admin_users = cursor.fetchall()

        cursor.execute("SELECT message, type, created_at FROM notifications WHERE is_active = 1 ORDER BY id DESC LIMIT 1")
        notif = cursor.fetchone()

        cursor.close()
        return {
            "current_user": serialize_user(user),
            "scoped_user": serialize_user(scoped_user),
            "chart_data": [dict(row) for row in chart_rows][::-1],
            "counters": counters,
            "recent_activity": [dict(row) for row in number_rows],
            "live_logs": [dict(row) for row in log_rows],
            "bot_status": [dict(row) for row in status_rows],
            "users": [dict(row) for row in admin_users],
            "notification": dict(notif) if notif else None
        }
    except Exception as e:
        raise e
    finally:
        release_db_connection(conn)
