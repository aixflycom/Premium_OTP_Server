from app import socketio
from flask import request
from core.auth import verify_api_key
from core.db import get_db_connection, release_db_connection
from core.utils import add_log_entry, upsert_bot_status, increment_stat
import time

def _get_user_from_socket(data=None):
    """Authenticate the SocketIO connection via X-API-Key header or payload."""
    # Try header first (standard path)
    api_key = request.headers.get('X-API-Key')
    # Fallback: api_key in event payload (for bots that can't pass headers reliably)
    if not api_key and data and isinstance(data, dict):
        api_key = data.get('api_key')
    if not api_key:
        return None
    return verify_api_key(api_key)

@socketio.on('connect')
def handle_connect():
    user = _get_user_from_socket()
    if not user:
        return False  # Reject unauthenticated connections
    print(f"[WS] Connected: {user['username']}")

@socketio.on('disconnect')
def handle_disconnect():
    print(f"[WS] Client disconnected")

@socketio.on('push_log')
def handle_push_log(data):
    user = _get_user_from_socket()
    if not user: return

    msg       = data.get("message")
    level     = data.get("level", "INFO")
    device_id = data.get("device_id", "Unknown")
    phone     = data.get("phone_number")
    bot_status = data.get("bot_status")

    if not msg:
        return

    conn = get_db_connection()
    try:
        add_log_entry(conn, user["id"], msg, level=level, device_id=device_id, phone_number=phone)
        upsert_bot_status(conn, user["id"], device_id=device_id, bot_status=bot_status, last_phone=phone, last_message=msg)
        conn.commit()  # ✅ Commit FIRST, then events are emitted by utils internally
    finally:
        release_db_connection(conn)

@socketio.on('heartbeat')
def handle_heartbeat(data):
    user = _get_user_from_socket()
    if not user: return

    device_id  = data.get("device_id", "Unknown")
    bot_status = data.get("bot_status", "RUNNING")
    last_phone = data.get("last_phone")
    msg        = data.get("message", "Heartbeat")

    conn = get_db_connection()
    try:
        upsert_bot_status(conn, user["id"], device_id=device_id, bot_status=bot_status, last_phone=last_phone, last_message=msg)
        conn.commit()  # ✅ Commit FIRST
    finally:
        release_db_connection(conn)

@socketio.on('update_status')
def handle_update_status(data):
    """Real-time status update from bot — deducts balance, emits events.
    Equivalent to the /api/v1/update-status HTTP endpoint but via SocketIO."""
    user = _get_user_from_socket(data)  # checks header first, then payload api_key
    if not user: return

    phone      = (data.get("phone_number") or "").strip()
    new_status = (data.get("status") or "").upper()
    device_id  = (data.get("device_id") or "Unknown")

    if not phone or new_status not in ("SENT", "FAILED", "READY"):
        return

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        user_tag_w = f"u{user['id']}_%"

        # Mark number as SENT/FAILED
        cursor.execute(
            """
            UPDATE numbers
            SET status = %s, device_id = %s, updated_at = CURRENT_TIMESTAMP
            WHERE phone_number = %s
              AND status = 'IN_USE'
              AND (
                user_id = %s
                OR (
                  user_id IN (SELECT id FROM users WHERE role = 'admin' LIMIT 1)
                  AND device_id LIKE %s
                )
              )
            """,
            (new_status, device_id, phone, user["id"], user_tag_w),
        )

        if cursor.rowcount > 0:
            new_balance = None
            if new_status == "SENT":
                cursor.execute("SELECT setting_value FROM system_settings WHERE setting_key = 'price_per_otp'")
                price_row = cursor.fetchone()
                price = float(price_row["setting_value"]) if price_row else 0.10
                cursor.execute("UPDATE users SET balance = balance - %s WHERE id = %s", (price, user["id"]))
                cursor.execute("SELECT balance FROM users WHERE id = %s", (user["id"],))
                new_balance = float(cursor.fetchone()["balance"] or 0)

            # ✅ COMMIT before emitting events so dashboard sees updated data
            conn.commit()
            cursor.close()

            add_log_entry(conn, user["id"],
                f"[WS] {phone} → {new_status}" + (f" | Balance: ${new_balance:.2f}" if new_balance is not None else ""),
                level="SUCCESS" if new_status == "SENT" else ("ERROR" if new_status == "FAILED" else "INFO"),
                device_id=device_id, phone_number=phone)
            upsert_bot_status(conn, user["id"], device_id=device_id, last_phone=phone,
                last_message=f"✅ {phone} → {new_status}" if new_status == "SENT" else f"❌ {phone} → {new_status}")
            increment_stat(conn, user["id"], new_status)

            # Emit balance update to dashboard
            try:
                socketio.emit("balance_update", {
                    "user_id": user["id"],
                    "balance": new_balance,
                    "status": new_status,
                    "phone": phone,
                })
            except Exception:
                pass

            conn.commit()
        else:
            cursor.close()
    finally:
        release_db_connection(conn)
