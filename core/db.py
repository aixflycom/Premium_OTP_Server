import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool
import os
from werkzeug.security import generate_password_hash

# Fetch configuration from environment variables (No hardcoded fallbacks for security)
DATABASE_URL = os.environ.get("DATABASE_URL")
DEFAULT_ADMIN_USERNAME = os.environ.get("SUPEROTP_ADMIN_USER")
DEFAULT_ADMIN_PASSWORD = os.environ.get("SUPEROTP_ADMIN_PASS")
LEGACY_ADMIN_API_KEY = os.environ.get("SUPEROTP_ADMIN_API_KEY")

# Global connection pool
_db_pool = None

def validate_config():
    """Ensures all required environment variables are present and valid."""
    missing = []
    if not DATABASE_URL: missing.append("DATABASE_URL")
    if not DEFAULT_ADMIN_USERNAME: missing.append("SUPEROTP_ADMIN_USER")
    if not DEFAULT_ADMIN_PASSWORD: missing.append("SUPEROTP_ADMIN_PASS")
    if not LEGACY_ADMIN_API_KEY: missing.append("SUPEROTP_ADMIN_API_KEY")
    
    if missing:
        msg = f"CRITICAL ERROR: Missing required environment variables: {', '.join(missing)}. Please configure them in your Render Dashboard > Environment."
        print(msg)
        raise EnvironmentError(msg)

def get_db_pool():
    global _db_pool
    if _db_pool is None:
        validate_config()
        try:
            # Create a ThreadedConnectionPool (Safe for Flask)
            # minconn=1, maxconn=20 (increased to handle load)
            _db_pool = ThreadedConnectionPool(1, 20, DATABASE_URL, cursor_factory=RealDictCursor)
            print("Database connection pool created.")
        except Exception as e:
            print(f"CRITICAL: Failed to create database pool! {e}")
            raise
    return _db_pool

def get_db_connection():
    """Fetches a connection from the pool."""
    pool = get_db_pool()
    return pool.getconn()

def release_db_connection(conn):
    """Returns a connection to the pool."""
    if _db_pool and conn:
        _db_pool.putconn(conn)

def init_db():
    conn = None
    try:
        # Initial validation
        validate_config()
        
        conn = get_db_connection()
        cursor = conn.cursor()

        # Create tables
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                full_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                api_key TEXT UNIQUE NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                balance NUMERIC DEFAULT 0.00,
                is_active INTEGER NOT NULL DEFAULT 1,
                use_admin_numbers INTEGER NOT NULL DEFAULT 1,
                is_tester INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS numbers (
                id SERIAL PRIMARY KEY,
                user_id INTEGER,
                phone_number TEXT UNIQUE NOT NULL,
                status TEXT DEFAULT 'READY',
                device_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_stats (
                user_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                success_count INTEGER DEFAULT 0,
                fail_count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, date),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_logs (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                device_id TEXT,
                level TEXT DEFAULT 'INFO',
                phone_number TEXT,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_status (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                device_id TEXT NOT NULL,
                bot_status TEXT DEFAULT 'IDLE',
                last_phone TEXT,
                last_message TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, device_id),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id SERIAL PRIMARY KEY,
                message TEXT NOT NULL,
                type TEXT DEFAULT 'info',
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS system_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT NOT NULL
            )
            """
        )
        cursor.execute("INSERT INTO system_settings (setting_key, setting_value) VALUES ('public_signup', 'true') ON CONFLICT DO NOTHING")
        cursor.execute("INSERT INTO system_settings (setting_key, setting_value) VALUES ('price_per_otp', '0.10') ON CONFLICT DO NOTHING")
        cursor.execute("INSERT INTO system_settings (setting_key, setting_value) VALUES ('script_version', '1') ON CONFLICT DO NOTHING")
        cursor.execute("INSERT INTO system_settings (setting_key, setting_value) VALUES ('test_script_version', '1') ON CONFLICT DO NOTHING")
        
        # Try to load the automation script into DB on first run
        script_content = ""
        try:
            script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "modules", "automation_steps.py")
            with open(script_path, "r", encoding="utf-8") as f:
                script_content = f.read()
        except Exception as e:
            print(f"[WARN] Could not pre-load automation script into DB: {e}")
        
        cursor.execute(
            "INSERT INTO system_settings (setting_key, setting_value) VALUES ('automation_script', %s) ON CONFLICT (setting_key) DO NOTHING",
            (script_content,)
        )
        cursor.execute(
            "INSERT INTO system_settings (setting_key, setting_value) VALUES ('test_automation_script', %s) ON CONFLICT (setting_key) DO NOTHING",
            (script_content,)
        )
        conn.commit()

        # ════════════════════════════════════════════════════════════════
        # AUTO-MIGRATION: Runs on every startup — safe, idempotent
        # Any missing column/index/setting is added automatically.
        # Never need to manually touch the database again.
        # ════════════════════════════════════════════════════════════════

        def col_exists(table, column):
            cursor.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = %s",
                (table, column),
            )
            return cursor.fetchone() is not None

        def add_col(table, column, definition):
            if not col_exists(table, column):
                print(f"[DB-MIGRATE] Adding column '{column}' to '{table}'...")
                try:
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    print(f"[DB-MIGRATE] Warning: {e}")

        def ensure_index(index_name, table, columns):
            cursor.execute(
                "SELECT 1 FROM pg_indexes WHERE indexname = %s", (index_name,)
            )
            if not cursor.fetchone():
                print(f"[DB-MIGRATE] Creating index '{index_name}'...")
                try:
                    cursor.execute(
                        f"CREATE INDEX {index_name} ON {table} ({columns})"
                    )
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    print(f"[DB-MIGRATE] Index warning: {e}")

        def ensure_setting(key, default_value):
            cursor.execute(
                "INSERT INTO system_settings (setting_key, setting_value) "
                "VALUES (%s, %s) ON CONFLICT (setting_key) DO NOTHING",
                (key, str(default_value)),
            )

        # ── Column Migrations ─────────────────────────────────────────
        # users table
        add_col("users", "full_name",          "TEXT NOT NULL DEFAULT 'User'")
        add_col("users", "use_admin_numbers",  "INTEGER NOT NULL DEFAULT 1")
        add_col("users", "is_tester",          "INTEGER NOT NULL DEFAULT 0")
        add_col("users", "is_active",          "INTEGER NOT NULL DEFAULT 1")
        add_col("users", "balance",            "NUMERIC DEFAULT 0.00")
        add_col("users", "updated_at",         "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

        # numbers table
        add_col("numbers", "device_id",   "TEXT")
        add_col("numbers", "updated_at",  "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

        # bot_logs table
        add_col("bot_logs", "device_id",     "TEXT")
        add_col("bot_logs", "phone_number",  "TEXT")

        # bot_status table
        add_col("bot_status", "last_seen",  "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

        # notifications table
        add_col("notifications", "type",       "TEXT DEFAULT 'info'")
        add_col("notifications", "is_active",  "INTEGER DEFAULT 1")

        # ── Performance Indexes ───────────────────────────────────────
        ensure_index("idx_numbers_user_status",  "numbers",   "user_id, status")
        ensure_index("idx_numbers_phone",        "numbers",   "phone_number")
        ensure_index("idx_numbers_device_id",    "numbers",   "device_id")
        ensure_index("idx_bot_logs_user",        "bot_logs",  "user_id, id DESC")
        ensure_index("idx_bot_status_user",      "bot_status","user_id")

        # ── Default System Settings ───────────────────────────────────
        ensure_setting("public_signup",       "true")
        ensure_setting("price_per_otp",       "0.10")
        ensure_setting("max_in_use",          "10")      # admin pool limit per user
        ensure_setting("auto_release_minutes","30")      # auto-release stale IN_USE after N minutes
        ensure_setting("script_version",      "1")
        ensure_setting("test_script_version", "1")
        ensure_setting("automation_script",   "")
        ensure_setting("test_automation_script", "")
        conn.commit()

        # ── Ensure automation script is loaded ────────────────────────
        cursor.execute(
            "SELECT setting_value FROM system_settings WHERE setting_key = 'automation_script'"
        )
        row = cursor.fetchone()
        if not row or not row["setting_value"].strip():
            try:
                script_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "..", "..",
                    "modules", "automation_steps.py"
                )
                with open(script_path, "r", encoding="utf-8") as f:
                    script_content = f.read()
                cursor.execute(
                    "UPDATE system_settings SET setting_value = %s "
                    "WHERE setting_key IN ('automation_script','test_automation_script')",
                    (script_content,),
                )
                conn.commit()
                print("[DB-MIGRATE] Automation script loaded into database.")
            except Exception as e:
                print(f"[DB-MIGRATE] Could not load automation script: {e}")

        # ── Ensure default admin account ──────────────────────────────
        cursor.execute(
            "SELECT id, api_key FROM users WHERE username = %s",
            (DEFAULT_ADMIN_USERNAME,),
        )
        admin = cursor.fetchone()
        if not admin:
            print("[DB-INIT] Creating default administrator account...")
            cursor.execute(
                """
                INSERT INTO users (username, full_name, password_hash, api_key, role)
                VALUES (%s, %s, %s, %s, 'admin')
                RETURNING id
                """,
                (
                    DEFAULT_ADMIN_USERNAME,
                    "System Administrator",
                    generate_password_hash(DEFAULT_ADMIN_PASSWORD),
                    LEGACY_ADMIN_API_KEY,
                ),
            )
            admin = cursor.fetchone()
            conn.commit()
        else:
            # Ensure admin API key is up to date with env var
            if LEGACY_ADMIN_API_KEY and admin["api_key"] != LEGACY_ADMIN_API_KEY:
                cursor.execute(
                    "UPDATE users SET api_key = %s WHERE id = %s",
                    (LEGACY_ADMIN_API_KEY, admin["id"]),
                )
                conn.commit()

        # ── Fix orphan numbers ────────────────────────────────────────
        cursor.execute(
            "UPDATE numbers SET user_id = %s WHERE user_id IS NULL",
            (admin["id"],),
        )
        # Reset any stuck IN_USE numbers older than 2 hours back to READY
        cursor.execute(
            """
            UPDATE numbers
            SET status = 'READY', device_id = NULL
            WHERE status = 'IN_USE'
              AND updated_at < NOW() - INTERVAL '2 hours'
            """
        )
        conn.commit()
        cursor.close()
        release_db_connection(conn)
        print("[DB-INIT] ✅ Database ready. All migrations applied.")

    except Exception as e:
        print(f"[DB-INIT] ❌ Database Initialization Failed: {e}")
        if conn:
            try:
                conn.rollback()
                release_db_connection(conn)
            except Exception:
                pass
