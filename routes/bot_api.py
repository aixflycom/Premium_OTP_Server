from flask import Blueprint, request, jsonify
from core.db import get_db_connection, release_db_connection
from core.auth import get_api_user_or_401
from core.utils import add_log_entry, upsert_bot_status, increment_stat

bot_api_bp = Blueprint("bot_api", __name__, url_prefix="/api/v1")

def _get_active_notification(conn):
    """Fetch the current active notification message for bots."""
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT message, type FROM notifications WHERE is_active = 1 ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        cursor.close()
        return {"message": row["message"], "type": row["type"]} if row else None
    except Exception:
        return None

@bot_api_bp.route("/get-numbers", methods=["GET"])
def get_numbers():
    user, error = get_api_user_or_401()
    if error:
        return error

    count     = request.args.get("count", default=10, type=int)
    device_id = request.args.get("device_id", default="Unknown", type=str)

    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        # ── 1. Balance check ────────────────────────────────────────────────
        cursor.execute("SELECT balance FROM users WHERE id = %s", (user["id"],))
        current_balance = float(cursor.fetchone()["balance"] or 0)

        # ── 2. Determine number source (own vs admin pool) ──────────────────
        fetch_user_id  = user["id"]
        using_admin    = False
        ADMIN_POOL_MAX = 10   # Hard limit: max numbers from admin pool per user

        if user["use_admin_numbers"]:
            cursor.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1")
            admin_row = cursor.fetchone()
            if admin_row:
                fetch_user_id = admin_row["id"]
                using_admin   = True

        # user_tag must be defined before auto-release block
        user_tag   = f"u{user['id']}_"    # e.g. "u7_"
        user_tag_w = f"u{user['id']}_%"   # LIKE pattern

        # ── 3. Auto-release IN_USE numbers from this session / stale sessions ─
        #
        #   PHASE 1 — Immediate: If this specific device_id has IN_USE numbers,
        #   release them NOW. Bot calling get-numbers means it just started fresh.
        #
        #   PHASE 2 — Time-based: Release any IN_USE from OTHER devices/sessions
        #   for this user that have been stuck > auto_release_minutes.
        #
        cursor.execute(
            "SELECT setting_value FROM system_settings WHERE setting_key = 'auto_release_minutes'"
        )
        rel_row = cursor.fetchone()
        auto_release_min = int(rel_row["setting_value"]) if rel_row else 30

        released = 0

        if using_admin:
            this_device_tag = f"{user_tag}{device_id}" if device_id != "Unknown" else None

            # Phase 1: Immediately release numbers held by THIS device
            if this_device_tag:
                cursor.execute(
                    """
                    UPDATE numbers
                    SET status = 'READY', device_id = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = %s
                      AND status = 'IN_USE'
                      AND device_id = %s
                    """,
                    (fetch_user_id, this_device_tag),
                )
                released += cursor.rowcount

            # Phase 2: Release stale numbers from other devices (time-based)
            cursor.execute(
                """
                UPDATE numbers
                SET status = 'READY', device_id = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = %s
                  AND status = 'IN_USE'
                  AND device_id LIKE %s
                  AND updated_at < NOW() - (INTERVAL '1 minute' * %s)
                """,
                (fetch_user_id, user_tag_w, auto_release_min),
            )
            released += cursor.rowcount
        else:
            # Phase 1: Immediately release numbers held by THIS device
            if device_id and device_id != "Unknown":
                cursor.execute(
                    """
                    UPDATE numbers
                    SET status = 'READY', device_id = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = %s
                      AND status = 'IN_USE'
                      AND device_id = %s
                    """,
                    (user["id"], device_id),
                )
                released += cursor.rowcount

            # Phase 2: Release stale numbers from any device (time-based)
            cursor.execute(
                """
                UPDATE numbers
                SET status = 'READY', device_id = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = %s
                  AND status = 'IN_USE'
                  AND updated_at < NOW() - (INTERVAL '1 minute' * %s)
                """,
                (user["id"], auto_release_min),
            )
            released += cursor.rowcount

        if released > 0:
            conn.commit()
            add_log_entry(
                conn, user["id"],
                f"Auto-released {released} IN_USE number(s) → READY (device restart or timeout)",
                level="INFO", device_id=device_id,
            )


        # ── 4. Per-user admin-pool IN_USE count (key fix) ───────────────────
        if using_admin:
            cursor.execute(
                """
                SELECT COUNT(*) AS active_count
                FROM numbers
                WHERE user_id = %s AND status = 'IN_USE'
                  AND device_id LIKE %s
                """,
                (fetch_user_id, user_tag_w),
            )
            active_admin = cursor.fetchone()["active_count"]
            if active_admin >= ADMIN_POOL_MAX:
                return jsonify({
                    "error": f"Admin Pool Limit: You already have {active_admin}/{ADMIN_POOL_MAX} numbers in progress. "
                             f"Wait for them to complete before fetching more."
                }), 403
            # Don't allow fetching more than the remaining quota
            count = min(count, ADMIN_POOL_MAX - active_admin)
        else:
            # Own-number IN_USE check (original logic)
            cursor.execute(
                "SELECT COUNT(*) AS active_count FROM numbers WHERE user_id = %s AND status = 'IN_USE'",
                (user["id"],),
            )
            active_own = cursor.fetchone()["active_count"]
            cursor.execute("SELECT setting_value FROM system_settings WHERE setting_key = 'max_in_use'")
            lim_row   = cursor.fetchone()
            max_in_use = int(lim_row["setting_value"]) if lim_row else 10
            if active_own >= max_in_use:
                return jsonify({
                    "error": f"Security Limit: You have {active_own} active numbers pending. "
                             f"Please wait or report status first."
                }), 403

        # ── 4. Balance vs price check ────────────────────────────────────────
        cursor.execute("SELECT setting_value FROM system_settings WHERE setting_key = 'price_per_otp'")
        price_row       = cursor.fetchone()
        cost_per_number = float(price_row["setting_value"]) if price_row else 0.10

        if cost_per_number > 0:
            if current_balance < cost_per_number:
                return jsonify({"error": "Insufficient balance. Please contact admin to recharge."}), 402
            max_can_afford = int(current_balance / cost_per_number)
            count = min(count, max_can_afford)

        count = max(1, count)   # always fetch at least 1

        # ── 5. Fetch READY numbers ───────────────────────────────────────────
        cursor.execute(
            """
            SELECT id, phone_number
            FROM numbers
            WHERE user_id = %s AND status = 'READY'
            ORDER BY id ASC
            LIMIT %s
            """,
            (fetch_user_id, count),
        )
        rows = cursor.fetchall()
        ids  = [row["id"] for row in rows]

        if ids:
            # Build device tag:
            #   Admin pool  → "u{user_id}_{device_id}"   (tracks which user holds it)
            #   Own numbers → device_id as-is (or BATCH_ALLOCATED if Unknown)
            if using_admin:
                real_dev = device_id if device_id != "Unknown" else "bot"
                alloc_device = f"{user_tag}{real_dev}"   # e.g. "u7_emulator-5554"
            else:
                alloc_device = device_id if device_id != "Unknown" else "BATCH_ALLOCATED"

            cursor.execute(
                """
                UPDATE numbers
                SET status = 'IN_USE', device_id = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = ANY(%s)
                """,
                (alloc_device, ids),
            )

            source_label = "Admin Pool" if using_admin else "Own Pool"
            add_log_entry(
                conn, user["id"],
                f"Allocated {len(ids)} numbers from {source_label} (device: {device_id})",
                level="INFO",
                device_id=device_id,
            )

            # Only update bot_status with a real device_id (skip 'Unknown')
            if device_id != "Unknown":
                upsert_bot_status(
                    conn, user["id"],
                    device_id=device_id,
                    bot_status="RUNNING",
                    last_message=f"Started: {len(ids)} numbers from {source_label}",
                )

            conn.commit()
            cursor.close()

            # Emit real-time stats update for Dashboard
            try:
                from app import socketio
                socketio.emit('stats_update', {
                    'user_id': user["id"],
                    'status': 'FETCHED',
                    'ready_count': ready_count,
                    'in_use_count': len(ids)
                })
            except Exception: pass

            cursor = conn.cursor()
            cursor.execute("SELECT balance FROM users WHERE id = %s", (user["id"],))
            updated_balance = float(cursor.fetchone()["balance"] or 0)

            cursor.execute(
                "SELECT COUNT(*) AS total FROM numbers WHERE user_id = %s AND status = 'READY'",
                (fetch_user_id,),
            )
            ready_count = cursor.fetchone()["total"]
            cursor.close()

            notif  = _get_active_notification(conn)
            return jsonify({
                "numbers":              [row["phone_number"] for row in rows],
                "balance":              updated_balance,
                "ready_count":          ready_count,
                "notification":         notif,
                "using_admin_numbers":  using_admin,
            })
        
        # ── 6. Build response ────────────────────────────────────────────────
        cursor = conn.cursor()
        cursor.execute("SELECT balance FROM users WHERE id = %s", (user["id"],))
        updated_balance = float(cursor.fetchone()["balance"] or 0)

        cursor.execute(
            "SELECT COUNT(*) AS total FROM numbers WHERE user_id = %s AND status = 'READY'",
            (fetch_user_id,),
        )
        ready_count = cursor.fetchone()["total"]
        cursor.close()

        notif  = _get_active_notification(conn)
        result = {
            "numbers":              [row["phone_number"] for row in rows],
            "balance":              updated_balance,
            "ready_count":          ready_count,
            "using_admin_numbers":  using_admin,
        }
        if notif:
            result["notification"] = notif
        return jsonify(result)
    finally:
        release_db_connection(conn)

@bot_api_bp.route("/update-status", methods=["POST"])
def update_status():
    user, error = get_api_user_or_401()
    if error:
        return error

    data = request.get_json(silent=True) or {}
    phone = data.get("phone_number")
    new_status = data.get("status")
    device_id = data.get("device_id", "Unknown")

    if not phone or not new_status:
        return jsonify({"error": "Missing phone_number or status"}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        # User tag used during allocation: "u{user_id}_"
        user_tag   = f"u{user['id']}_"     # e.g. "u7_"
        user_tag_w = f"u{user['id']}_%"    # LIKE pattern for admin-pool numbers

        # For admin-pool numbers: preserve the user tag in device_id after completion
        # e.g. "u7_emulator-5554" stays as "u7_emulator-5554" (not just "emulator-5554")
        # This ensures /web/api/numbers can still identify which user processed it.
        tagged_device = f"{user_tag}{device_id}"   # always safe to use for admin-pool

        # Match own numbers OR admin-pool numbers tagged for this user
        cursor.execute(
            """
            UPDATE numbers 
            SET status = %s,
                device_id = CASE
                    WHEN user_id = %s THEN %s              -- own: use raw device_id
                    ELSE %s                                 -- admin pool: keep user tag
                END,
                updated_at = CURRENT_TIMESTAMP 
            WHERE phone_number = %s 
              AND status = 'IN_USE'
              AND (
                user_id = %s                               -- own numbers
                OR (
                  user_id IN (SELECT id FROM users WHERE role = 'admin' LIMIT 1)
                  AND device_id LIKE %s                    -- admin-pool tagged for this user
                )
              )
            """,
            (new_status, user["id"], device_id, tagged_device, phone, user["id"], user_tag_w),
        )
        
        if cursor.rowcount > 0:
            # Deduct balance ONLY on SUCCESS
            new_balance = None
            if new_status == "SENT":
                cursor.execute("SELECT setting_value FROM system_settings WHERE setting_key = 'price_per_otp'")
                price_row = cursor.fetchone()
                price = float(price_row["setting_value"]) if price_row else 0.10
                cursor.execute("UPDATE users SET balance = balance - %s WHERE id = %s", (price, user["id"]))
                cursor.execute("SELECT balance FROM users WHERE id = %s", (user["id"],))
                new_balance = float(cursor.fetchone()["balance"] or 0)

            # COMMIT FIRST so balance is in DB before dashboard refreshes
            conn.commit()
            cursor.close()

            add_log_entry(
                conn,
                user["id"],
                f"Number {phone} -> {new_status}",
                level="SUCCESS" if new_status == "SENT" else ("ERROR" if new_status == "FAILED" else "INFO"),
                device_id=device_id,
                phone_number=phone,
            )
            upsert_bot_status(
                conn,
                user["id"],
                device_id=device_id,
                last_phone=phone,
                last_message=f"✅ {phone} → {new_status}" if new_status == "SENT" else f"❌ {phone} → {new_status}",
            )
            increment_stat(conn, user["id"], new_status)
            # Emit balance_update so dashboard updates instantly
            try:
                from app import socketio
                socketio.emit("balance_update", {
                    "user_id": user["id"],
                    "balance": new_balance,
                    "status": new_status,
                    "phone": phone,
                })
            except Exception:
                pass
            conn.commit()
            return jsonify({"success": True, "new_balance": new_balance})

        # Fallback: Number might not be IN_USE (already processed or wrong state)
        cursor.close()
        return jsonify({"warning": "Number not found in IN_USE state"}), 200
    finally:
        release_db_connection(conn)

@bot_api_bp.route("/push-log", methods=["POST"])
def push_log():
    user, error = get_api_user_or_401()
    if error:
        return error

    data = request.get_json(silent=True) or {}
    msg = data.get("message")
    level = data.get("level", "INFO")
    device_id = data.get("device_id", "Unknown")
    phone = data.get("phone_number")
    bot_status = data.get("bot_status")

    if not msg:
        return jsonify({"error": "Missing message"}), 400

    conn = get_db_connection()
    try:
        add_log_entry(conn, user["id"], msg, level=level, device_id=device_id, phone_number=phone)
        upsert_bot_status(
            conn,
            user["id"],
            device_id=device_id,
            bot_status=bot_status,
            last_phone=phone,
            last_message=msg,
        )
        conn.commit()
        return jsonify({"success": True})
    finally:
        release_db_connection(conn)

@bot_api_bp.route("/heartbeat", methods=["POST"])
def heartbeat():
    user, error = get_api_user_or_401()
    if error:
        return error

    data = request.get_json(silent=True) or {}
    device_id = data.get("device_id", "Unknown")
    bot_status = data.get("bot_status", "RUNNING")
    last_phone = data.get("last_phone")
    msg = data.get("message", "Heartbeat")

    conn = get_db_connection()
    try:
        upsert_bot_status(
            conn,
            user["id"],
            device_id=device_id,
            bot_status=bot_status,
            last_phone=last_phone,
            last_message=msg,
        )
        conn.commit()
        notif = _get_active_notification(conn)
        result = {"success": True}
        if notif:
            result["notification"] = notif
        return jsonify(result)
    finally:
        release_db_connection(conn)

@bot_api_bp.route("/automation-script", methods=["GET"])
def get_automation_script():
    user, error = get_api_user_or_401()
    if error: return error
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # Determine which script keys to fetch based on user's tester status
        script_key = "automation_script"
        version_key = "script_version"
        
        if user.get("is_tester") == 1:
            script_key = "test_automation_script"
            version_key = "test_script_version"
        
        cursor.execute(
            "SELECT setting_key, setting_value FROM system_settings WHERE setting_key IN (%s, %s)",
            (script_key, version_key)
        )
        rows = cursor.fetchall()
        cursor.close()
        
        settings = {row["setting_key"]: row["setting_value"] for row in rows}
        return jsonify({
            "success": True,
            "script": settings.get(script_key, ""),
            "version": int(settings.get(version_key, "1")),
            "is_test_mode": (user.get("is_tester") == 1)
        })
    finally:
        release_db_connection(conn)

@bot_api_bp.route("/notification", methods=["GET"])
def get_bot_notification():
    """Dedicated endpoint for bots to poll the latest admin notification."""
    user, error = get_api_user_or_401()
    if error:
        return error
    conn = get_db_connection()
    try:
        notif = _get_active_notification(conn)
        return jsonify({"success": True, "notification": notif})
    finally:
        release_db_connection(conn)
